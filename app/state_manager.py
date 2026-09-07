import json
import os
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from datetime import datetime
from hashlib import sha256
from .config import Config
from .errors import UnsafeState


def timestamp() -> str:
    return datetime.now().astimezone().isoformat()


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    with temp.open('w', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def pointer_path(config: Config) -> Path:
    return config.profile_dir / '.task-states' / (sha256(config.chat_url.encode()).hexdigest() + '.json')


class StateManager:
    def __init__(self, path: Path, config: Config, resume: bool = False,
                 on_change: Callable[[dict], None] | None = None):
        self.path = path
        self.on_change = on_change
        if resume:
            self.data = json.loads(path.read_text(encoding='utf-8'))
            if self.data['fingerprint'] != config.fingerprint:
                raise UnsafeState('配置提示词、对话或登录目录已变化；拒绝套用旧断点')
        else:
            self.data = dict(version=1, task_name=config.task_name, fingerprint=config.fingerprint,
                             chat_url=config.chat_url, created_at=timestamp(), planned_start=config.start_time.isoformat(),
                             status='WAITING', current_step=None, completed_steps=[], steps={})
            for step in config.steps:
                self.data['steps'][step.id] = dict(name=step.name, status='WAITING', click_intent=False)
            self.save()
        atomic_json(pointer_path(config), {'status_file': str(path.resolve())})
        if resume and self.on_change:
            self.on_change(deepcopy(self.data))

    def save(self) -> None:
        self.data['updated_at'] = timestamp()
        atomic_json(self.path, self.data)
        if getattr(self, 'on_change', None):
            self.on_change(deepcopy(self.data))

    def update(self, sid: str, status: str, **values: object) -> None:
        self.data['current_step'] = sid
        self.data['status'] = status
        self.data['steps'][sid].update(status=status, **values)
        if status == 'COMPLETED' and sid not in self.data['completed_steps']:
            self.data['completed_steps'].append(sid)
        self.save()


def unfinished(config: Config) -> list[Path]:
    matches = []
    candidates = set(config.log_dir.glob('*/status.json'))
    pointer = pointer_path(config)
    if pointer.exists():
        try:
            candidates.add(Path(json.loads(pointer.read_text(encoding='utf-8'))['status_file']))
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise UnsafeState(f'断点索引损坏，请人工核对：{pointer}') from exc
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (ValueError, OSError) as exc:
            raise UnsafeState(f'断点文件无法读取，请先人工检查：{path}') from exc
        if not isinstance(data, dict) or 'steps' not in data or 'status' not in data:
            raise UnsafeState(f'断点结构不完整，请人工核对：{path}')
        if data.get('chat_url') == config.chat_url and data.get('status') not in {'FINISHED', 'FINISHED_WITH_ERRORS', 'ABANDONED'}:
            matches.append(path)
    return sorted(matches, key=lambda p: p.stat().st_mtime, reverse=True)


class RunLock:
    """OS lock is automatically released after a crash; lock file is not a PID flag."""
    def __init__(self, path: Path):
        self.path = path
        self.stream = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open('a+b')
        try:
            if os.fstat(self.stream.fileno()).st_size == 0:
                self.stream.write(b'0')
                self.stream.flush()
            self.stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.stream.close()
            raise UnsafeState('另一个任务正在使用此浏览器目录，请先关闭它') from exc
        return self

    def __exit__(self, *_):
        if self.stream:
            self.stream.close()
