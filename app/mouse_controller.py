"""Mouse capture/replay worker. No keyboard text is recorded."""
import math
import queue
import threading
from time import monotonic
from .mouse_recording import MouseEvent, MouseRecording, screen_signature, validate, MAX_EVENTS, MAX_DURATION


def replay(recording: MouseRecording, controller, buttons: dict, stopped: threading.Event,
           screen=screen_signature, report=lambda *_: None) -> None:
    validate(recording)
    if recording.screen != screen():
        raise ValueError('当前屏幕尺寸或系统缩放与录制时不同，拒绝回放；请恢复显示设置后再试')
    held = set()
    started = monotonic()
    try:
        for index, event in enumerate(recording.events):
            if stopped.wait(max(0, started+event.at-monotonic())):
                return
            if recording.screen != screen():
                raise ValueError('回放过程中屏幕设置变化，已停止')
            controller.position = (event.x, event.y)
            if stopped.is_set():
                return
            if event.kind == 'click':
                button = buttons[event.button]
                if event.pressed:
                    held.add(button)
                    controller.press(button)
                else:
                    controller.release(button)
                    held.discard(button)
            elif event.kind == 'scroll':
                controller.scroll(event.dx, event.dy)
            if index % 30 == 0:
                report('progress', (index+1, len(recording.events)))
        stopped.wait(max(0, started+recording.duration-monotonic()))
    finally:
        for button in held:
            try:
                controller.release(button)
            except Exception:
                pass


class MouseController:
    def __init__(self):
        self.events: queue.Queue = queue.Queue()
        self.recording: MouseRecording | None = None
        self.thread: threading.Thread | None = None
        self.cancel = threading.Event()
        self.guard = threading.Lock()
        self.kind = 'idle'

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self, kind: str, replay_count: int = 1, replay_interval_seconds: float = 0) -> None:
        if self.running:
            raise ValueError('鼠标任务尚未停止')
        if kind not in {'record', 'replay'}:
            raise ValueError('未知鼠标任务')
        if kind == 'replay' and self.recording is None:
            raise ValueError('请先录制或加载鼠标操作')
        if type(replay_count) is not int or not 1 <= replay_count <= 1000:
            raise ValueError('回放次数必须是 1 到 1000 的整数')
        if type(replay_interval_seconds) not in (int, float) or replay_interval_seconds < 0 or replay_interval_seconds > 86400:
            raise ValueError('回放间隔必须是 0 到 86400 秒')
        self.cancel.clear()
        self.kind = kind
        self.thread = threading.Thread(target=self._run, args=(kind, replay_count, float(replay_interval_seconds)), daemon=False)
        self.thread.start()

    def stop(self) -> None:
        self.cancel.set()

    def _run(self, kind: str, replay_count: int, replay_interval_seconds: float) -> None:
        error = ''
        previous = self.recording
        try:
            from pynput import mouse
            for seconds in (3, 2, 1):
                self.events.put(('phase', f'{seconds} 秒后开始，请切换到目标窗口；Esc 可取消'))
                if self.cancel.wait(1):
                    return
            if kind == 'record':
                self._record(mouse)
            else:
                controller = mouse.Controller()
                buttons = {name: getattr(mouse.Button, name) for name in ('left', 'right', 'middle')}
                for cycle in range(1, replay_count + 1):
                    if self.cancel.is_set():
                        break
                    self.events.put(('phase', f'正在回放第 {cycle}/{replay_count} 次 · 按 Esc 立即停止'))
                    replay(self.recording, controller, buttons, self.cancel,
                           report=lambda kind_name, value: self.events.put(('progress', (cycle, replay_count, value[0], value[1]))))
                    if self.cancel.is_set() or cycle == replay_count:
                        break
                    remaining = replay_interval_seconds
                    last_display = None
                    while remaining > 0 and not self.cancel.is_set():
                        display = (float(math.ceil(remaining)) if replay_interval_seconds > 10
                                   else round(remaining, 1))
                        if display != last_display:
                            self.events.put(('interval', (cycle, replay_count, display)))
                            last_display = display
                        wait_for = min(0.2, remaining)
                        if self.cancel.wait(wait_for):
                            break
                        remaining -= wait_for
        except Exception as exc:
            error = str(exc)
        finally:
            self.events.put(('finished', {'kind': kind, 'error': error, 'stopped': self.cancel.is_set(),
                                         'new_recording': kind == 'record' and self.recording is not previous}))

    def _record(self, mouse) -> None:
        screen = screen_signature()
        events = []
        held: dict[str, tuple[int, int]] = {}
        started = monotonic()
        last_move = -1.0
        last_position = (0, 0)
        def add(kind, x, y, button=None, pressed=None, dx=0, dy=0):
            nonlocal last_move, last_position
            with self.guard:
                if self.cancel.is_set():
                    return
                last_position = (int(x), int(y))
                elapsed = monotonic()-started
                if len(events) >= MAX_EVENTS-4 or elapsed >= MAX_DURATION-.1:
                    self.cancel.set()
                    return
                if kind == 'move' and elapsed-last_move < 1/30:
                    return
                if kind == 'move':
                    last_move = elapsed
                if kind == 'click':
                    if button not in {'left', 'right', 'middle'}:
                        return
                    if pressed:
                        if button in held:
                            return
                        held[button] = (int(x), int(y))
                    else:
                        if button not in held:
                            return
                        held.pop(button)
                events.append(MouseEvent(elapsed, kind, int(x), int(y), button, pressed, int(dx), int(dy)))
        listener = mouse.Listener(on_move=lambda x,y: add('move',x,y),
            on_click=lambda x,y,b,p: add('click',x,y,b.name,p), on_scroll=lambda x,y,dx,dy: add('scroll',x,y,dx=dx,dy=dy))
        failure = None
        screen_changed = False
        try:
            listener.start()
            self.events.put(('phase', '正在录制 · F8 或 Esc 停止；建议用快捷键，避免录入停止按钮点击'))
            while not self.cancel.wait(.2):
                if not listener.is_alive():
                    raise RuntimeError('鼠标监听已停止，请重新录制')
                if screen_signature() != screen:
                    screen_changed = True
                    raise ValueError('录制过程中屏幕设置变化，录制无效')
                if monotonic()-started >= MAX_DURATION-.1:
                    self.cancel.set()
                self.events.put(('progress', (len(events), monotonic()-started)))
        except Exception as exc:
            failure = exc
        finally:
            self.cancel.set()
            # Listener.join can re-raise an earlier callback exception. It must
            # not skip finalizing already captured, valid events.
            for cleanup in (listener.stop, lambda: listener.join(timeout=2)):
                try:
                    cleanup()
                except Exception as exc:
                    failure = failure or exc
        if screen_changed or screen_signature() != screen:
            raise ValueError('录制过程中屏幕设置变化；本次数据不可回放，未覆盖上次录制')
        with self.guard:
            duration = min(monotonic()-started, MAX_DURATION)
            for button in held:
                x, y = last_position
                events.append(MouseEvent(duration, 'click', x, y, button, False))
            if events:
                result = MouseRecording(screen, tuple(events), duration)
                validate(result)
                self.recording = result
                self.events.put(('recorded', result))
        if failure:
            raise RuntimeError(f'鼠标监听结束异常：{failure}') from failure
