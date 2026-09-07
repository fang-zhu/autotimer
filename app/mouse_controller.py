"""Mouse capture/replay worker. No keyboard text is recorded."""
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

    def start(self, kind: str) -> None:
        if self.running:
            raise ValueError('鼠标任务尚未停止')
        if kind not in {'record', 'replay'}:
            raise ValueError('未知鼠标任务')
        if kind == 'replay' and self.recording is None:
            raise ValueError('请先录制或加载鼠标操作')
        self.cancel.clear()
        self.kind = kind
        self.thread = threading.Thread(target=self._run, args=(kind,), daemon=False)
        self.thread.start()

    def stop(self) -> None:
        self.cancel.set()

    def _run(self, kind: str) -> None:
        error = ''
        try:
            from pynput import mouse
            for seconds in (3, 2, 1):
                self.events.put(('phase', f'{seconds} 秒后开始，请切换到目标窗口；Esc 可取消'))
                if self.cancel.wait(1):
                    return
            if kind == 'record':
                self._record(mouse)
            else:
                self.events.put(('phase', '正在回放 · 按 Esc 立即停止'))
                replay(self.recording, mouse.Controller(), {name: getattr(mouse.Button, name) for name in ('left', 'right', 'middle')}, self.cancel,
                       report=lambda *event: self.events.put(event))
        except Exception as exc:
            error = str(exc)
        finally:
            self.events.put(('finished', {'kind': kind, 'error': error, 'stopped': self.cancel.is_set()}))

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
        try:
            listener.start()
            self.events.put(('phase', '正在录制 · F8 或 Esc 停止；建议用快捷键，避免录入停止按钮点击'))
            while not self.cancel.wait(.2):
                if not listener.is_alive():
                    raise RuntimeError('鼠标监听已停止，请重新录制')
                if screen_signature() != screen:
                    raise ValueError('录制过程中屏幕设置变化，录制无效')
                if monotonic()-started >= MAX_DURATION-.1:
                    self.cancel.set()
                self.events.put(('progress', (len(events), monotonic()-started)))
        finally:
            self.cancel.set()
            listener.stop()
            listener.join(timeout=2)
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
