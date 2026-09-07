"""Validated, non-executable mouse recording format."""
import json
import math
import os
from dataclasses import dataclass, asdict
from pathlib import Path

MAX_EVENTS = 100000
MAX_DURATION = 1800.0
MAX_FILE_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class MouseEvent:
    at: float
    kind: str
    x: int
    y: int
    button: str | None = None
    pressed: bool | None = None
    dx: int = 0
    dy: int = 0


@dataclass(frozen=True)
class MouseRecording:
    screen: tuple[int, ...]
    events: tuple[MouseEvent, ...]
    duration: float


def screen_signature() -> tuple[int, ...]:
    import ctypes
    user32 = ctypes.windll.user32
    try:
        dpi = user32.GetDpiForSystem()
    except AttributeError:
        dpi = 96
    return tuple(user32.GetSystemMetrics(i) for i in (76, 77, 78, 79)) + (dpi,)


def validate(recording: MouseRecording) -> None:
    if len(recording.screen) != 5 or any(type(v) is not int for v in recording.screen):
        raise ValueError('屏幕信息无效')
    left, top, width, height, dpi = recording.screen
    if width <= 0 or height <= 0 or dpi <= 0:
        raise ValueError('屏幕尺寸无效')
    if not recording.events or len(recording.events) > MAX_EVENTS:
        raise ValueError('录制为空或超过 100000 个事件')
    if type(recording.duration) not in (int, float) or not math.isfinite(recording.duration) or not 0 <= recording.duration <= MAX_DURATION:
        raise ValueError('录制时长无效，最多 30 分钟')
    previous = 0.0
    held = set()
    for event in recording.events:
        if type(event.at) not in (int, float) or not math.isfinite(event.at) or not previous <= event.at <= recording.duration:
            raise ValueError('事件时间无效或顺序错误')
        previous = event.at
        if type(event.x) is not int or type(event.y) is not int or not (left <= event.x < left+width and top <= event.y < top+height):
            raise ValueError('鼠标坐标超出录制时的屏幕范围')
        if event.kind not in {'move', 'click', 'scroll'}:
            raise ValueError('不支持的鼠标事件')
        if type(event.dx) is not int or type(event.dy) is not int or abs(event.dx) > 100 or abs(event.dy) > 100:
            raise ValueError('滚轮幅度无效')
        if event.kind == 'click':
            if event.button not in {'left', 'right', 'middle'} or type(event.pressed) is not bool:
                raise ValueError('点击事件无效')
            if event.pressed:
                if event.button in held:
                    raise ValueError('重复的鼠标按下事件')
                held.add(event.button)
            else:
                if event.button not in held:
                    raise ValueError('缺少对应的鼠标按下事件')
                held.remove(event.button)
        elif event.button is not None or event.pressed is not None:
            raise ValueError('非点击事件不能包含按钮状态')
    if held:
        raise ValueError('录制末尾有未释放的鼠标按钮')


def save_recording(path: Path, recording: MouseRecording) -> None:
    validate(recording)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix+'.tmp')
    try:
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump({'version': 1, **asdict(recording)}, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_recording(path: Path) -> MouseRecording:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError('录制文件超过 20 MB，请选择本程序保存的文件')
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or set(data) != {'version', 'screen', 'events', 'duration'} or data['version'] != 1:
        raise ValueError('不支持的录制文件格式')
    try:
        result = MouseRecording(tuple(data['screen']), tuple(MouseEvent(**item) for item in data['events']), data['duration'])
        validate(result)
        return result
    except (TypeError, KeyError) as exc:
        raise ValueError('录制文件内容不完整或含有未知字段') from exc
