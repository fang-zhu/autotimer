import queue
import json
from datetime import datetime
import tkinter as tk
from pathlib import Path
from tkinter import ttk, filedialog, messagebox
from .mouse_controller import MouseController
from .mouse_recording import save_recording, load_recording


class MousePanel(ttk.Frame):
    def __init__(self, parent, root, directory: Path, on_busy, chat_running,
                 replay_count: int = 1, replay_interval_seconds: float = 0,
                 on_settings_change=lambda: None):
        super().__init__(parent, padding=16)
        self.root, self.directory = root, directory
        self.on_busy, self.chat_running = on_busy, chat_running
        self.on_settings_change = on_settings_change
        self.controller = MouseController()
        self.busy = False
        self.dirty = False
        self.keyboard = None
        self.hotkeys = tk.BooleanVar(value=False)
        self.replay_count = tk.StringVar(value=str(replay_count))
        self.replay_interval = tk.StringVar(value=str(replay_interval_seconds).rstrip('0').rstrip('.') if replay_interval_seconds else '0')
        self.replay_count.trace_add('write', lambda *_: self.on_settings_change())
        self.replay_interval.trace_add('write', lambda *_: self.on_settings_change())
        self.status = tk.StringVar(value='未录制 · 点击开始后有 3 秒准备时间')
        self.summary = tk.StringVar(value='录制鼠标移动、左右/中键点击、拖动和滚轮；不录制键盘文字。')
        self.pending = None
        self.received_recording = False
        self.closed = False
        self.timer = None
        ttk.Label(self, text='鼠标录制与回放', font=('Microsoft YaHei UI', 15, 'bold')).pack(anchor='w')
        ttk.Label(self, textvariable=self.summary, wraplength=850).pack(anchor='w', pady=(10, 15))
        buttons = ttk.Frame(self)
        buttons.pack(fill='x')
        self.record_button = ttk.Button(buttons, text='开始录制（F8）', command=self.record)
        self.record_button.pack(side='left', padx=(0, 8))
        self.play_button = ttk.Button(buttons, text='开始回放（F9）', command=self.play)
        self.play_button.pack(side='left', padx=8)
        self.stop_button = ttk.Button(buttons, text='停止（Esc）', command=self.stop, state='disabled')
        self.stop_button.pack(side='left', padx=8)
        files = ttk.Frame(self)
        files.pack(fill='x', pady=12)
        self.save_button = ttk.Button(files, text='保存录制…', command=self.save)
        self.save_button.pack(side='left', padx=(0, 8))
        self.load_button = ttk.Button(files, text='加载录制…', command=self.load)
        self.load_button.pack(side='left', padx=8)
        options = ttk.Frame(self)
        options.pack(fill='x', pady=(0, 10))
        ttk.Label(options, text='回放次数').pack(side='left')
        self.count_entry = ttk.Entry(options, textvariable=self.replay_count, width=8)
        self.count_entry.pack(side='left', padx=(6, 20))
        ttk.Label(options, text='每次间隔（秒）').pack(side='left')
        self.interval_entry = ttk.Entry(options, textvariable=self.replay_interval, width=8)
        self.interval_entry.pack(side='left', padx=6)
        ttk.Label(options, text='默认 1 次、0 秒；间隔从上一轮完整结束后计算。', style='Muted.TLabel').pack(side='left', padx=10)
        self.hotkey_button = ttk.Checkbutton(self, text='启用全局快捷键 F8 / F9 / Esc（开始录制或回放时自动启用）', variable=self.hotkeys, command=self.toggle_hotkeys)
        self.hotkey_button.pack(anchor='w', pady=10)
        ttk.Label(self, textvariable=self.status, font=('Microsoft YaHei UI', 11, 'bold'), wraplength=850).pack(anchor='w', pady=10)
        ttk.Label(self, text='使用顺序：开始录制 → 3 秒内切到目标窗口 → 操作鼠标 → 按 F8 或 Esc 停止 → 保存。\n回放前把目标窗口放回原位置；3 秒倒计时后按设置回放多次，按 Esc 随时停止并取消后续轮次。\n坐标录制不识别网页元素，窗口移动、内容变化都可能导致点错。屏幕尺寸或缩放变化时拒绝回放。\n建议在空白测试窗口先试用；不要录入登录、安全验证、付款或删除操作。\n停止按钮本身的点击可能被录入，推荐使用快捷键停止。最多录制 30 分钟 / 100000 个事件。',
                  style='Muted.TLabel', wraplength=880, justify='left').pack(anchor='w', pady=12)
        self._refresh()
        self.timer = root.after(100, self._poll)

    def _refresh(self):
        locked = self.busy or self.chat_running()
        for button in (self.record_button, self.load_button):
            button.configure(state='disabled' if locked else 'normal')
        for button in (self.play_button, self.save_button):
            button.configure(state='normal' if not locked and self.controller.recording else 'disabled')
        self.stop_button.configure(state='normal' if self.busy else 'disabled')
        self.hotkey_button.configure(state='disabled' if self.busy else 'normal')
        for entry in (self.count_entry, self.interval_entry):
            entry.configure(state='disabled' if locked else 'normal')

    def toggle_hotkeys(self):
        if self.hotkeys.get():
            return self._enable_keys()
        if self.keyboard:
            self.keyboard.stop()
            self.keyboard = None
        return True

    def _enable_keys(self) -> bool:
        if self.keyboard and self.keyboard.is_alive():
            return True
        try:
            from pynput import keyboard
            down = set()
            def press(key):
                if key not in {keyboard.Key.f8, keyboard.Key.f9, keyboard.Key.esc} or key in down:
                    return
                down.add(key)
                if key == keyboard.Key.esc:
                    self.controller.stop()  # Immediate thread-safe stop, independent of Tk dialogs.
                elif key == keyboard.Key.f8 and self.controller.running:
                    self.controller.stop()
                else:
                    self.controller.events.put(('hotkey', 'record' if key == keyboard.Key.f8 else 'replay'))
            def release(key):
                down.discard(key)
            self.keyboard = keyboard.Listener(on_press=press, on_release=release, suppress=False)
            self.keyboard.start()
            self.hotkeys.set(True)
            return True
        except Exception as exc:
            self.hotkeys.set(False)
            messagebox.showerror('快捷键无法启用', f'{exc}\n请按 README 安装依赖。为确保能停止，暂不开始鼠标任务。', parent=self.root)
            return False

    def _start(self, kind: str):
        if self.busy or self.chat_running():
            return
        if kind == 'record' and self.dirty and not messagebox.askyesno('覆盖未保存录制', '当前鼠标录制还未保存，确定重新录制？', parent=self.root):
            return
        if not self._enable_keys():
            return
        try:
            count, interval = self._read_replay_options()
            self.busy = True
            self.received_recording = False
            self.on_busy(True)
            self.controller.start(kind, count, interval)
            self._refresh()
        except Exception as exc:
            self.busy = False
            self.on_busy(False)
            self._refresh()
            messagebox.showerror('无法开始', str(exc), parent=self.root)

    def _read_replay_options(self) -> tuple[int, float]:
        try:
            count = int(self.replay_count.get().strip())
            interval = float(self.replay_interval.get().strip())
        except ValueError as exc:
            raise ValueError('回放次数必须是整数，间隔必须是数字') from exc
        if not 1 <= count <= 1000:
            raise ValueError('回放次数必须是 1 到 1000')
        if not 0 <= interval <= 86400:
            raise ValueError('每次间隔必须是 0 到 86400 秒')
        return count, interval

    def record(self):
        self._start('record')

    def play(self):
        self._start('replay')

    def stop(self):
        self.controller.stop()
        self.status.set('正在停止并释放鼠标按钮…')

    def save(self):
        if self.busy or self.chat_running() or not self.controller.recording:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        name = filedialog.asksaveasfilename(parent=self.root, initialdir=self.directory, defaultextension='.json',
                                           filetypes=[('鼠标录制', '*.json')], title='保存鼠标录制')
        if name:
            try:
                save_recording(Path(name), self.controller.recording)
                self.dirty = False
                self.status.set(f'已保存：{name}')
            except Exception as exc:
                messagebox.showerror('保存失败', str(exc), parent=self.root)

    def load(self):
        if self.busy or self.chat_running():
            return
        if self.dirty and not messagebox.askyesno('加载录制', '当前录制未保存，确定用其他录制替换？', parent=self.root):
            return
        name = filedialog.askopenfilename(parent=self.root, initialdir=self.directory if self.directory.exists() else self.directory.parent,
                                         filetypes=[('鼠标录制', '*.json')])
        if name:
            try:
                self.controller.recording = load_recording(Path(name))
                self.dirty = False
                self._describe()
                self.status.set('已加载，回放前请把目标窗口恢复到录制时的位置')
                self._refresh()
            except Exception as exc:
                messagebox.showerror('无法加载', str(exc), parent=self.root)

    def _describe(self):
        recording = self.controller.recording
        if recording:
            self.summary.set(f'共 {len(recording.events)} 个事件 · 时长 {recording.duration:.1f} 秒 · 屏幕 {recording.screen[2]} × {recording.screen[3]}')

    def _poll(self):
        if self.closed:
            return
        if self.timer:
            self.root.after_cancel(self.timer)
            self.timer = None
        if self.busy and (not self.keyboard or not self.keyboard.is_alive()):
            self.controller.stop()
            self.status.set('快捷键监听已失效，正在停止鼠标任务')
        for _ in range(100):
            try:
                kind, value = self.controller.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'phase':
                self.status.set(value)
            elif kind == 'recorded':
                self.received_recording = True
                self.dirty = True
                self._describe()
            elif kind == 'progress':
                if self.controller.kind == 'record':
                    self.summary.set(f'已录制 {value[0]} 个事件 · {value[1]:.1f} 秒')
                else:
                    self.status.set(f'第 {value[0]} / {value[1]} 次：回放 {value[2]} / {value[3]} · Esc 停止')
            elif kind == 'interval':
                self.status.set(f'第 {value[0]} / {value[1]} 次完成，等待下一次：{value[2]:.1f} 秒 · Esc 停止')
            elif kind == 'hotkey' and not self.busy and not self.chat_running():
                self._start(value)
            elif kind == 'finished':
                self.pending = value
        if self.pending is not None and not self.controller.running:
            result, self.pending = self.pending, None
            self.busy = False
            self.status.set(self._finished_message(result))
            self._write_status_log(result)
            self._describe()
            self._refresh()
            self.on_busy(False)
            if self.closed:
                return
        self.timer = self.root.after(100, self._poll)

    def _write_status_log(self, result: dict) -> None:
        try:
            directory = self.directory.parent/'logs'
            directory.mkdir(parents=True, exist_ok=True)
            with (directory/'mouse_recorder.log').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps({'time': datetime.now().astimezone().isoformat(), **result,
                    'available_events': len(self.controller.recording.events) if self.controller.recording else 0,
                    'message': self.status.get()}, ensure_ascii=False)+'\n')
        except OSError:
            # A diagnostic write must never prevent saving a valid recording.
            self.status.set(self.status.get()+'（状态日志未能写入）')

    def _finished_message(self, result: dict) -> str:
        error = result.get('error', '')
        if result.get('kind') == 'record':
            if self.received_recording or result.get('new_recording'):
                count = len(self.controller.recording.events) if self.controller.recording else 0
                message = f'录制已结束，已保留 {count} 个事件，可以点击“保存录制”'
            elif self.controller.recording:
                message = '本次未生成新录制，当前保留的是上一次录制，可保存上一次内容'
            else:
                message = '本次没有可保存的录制。请等待倒计时结束后移动鼠标，再按 F8 停止'
            return message + (f'；原因：{error}' if error else '')
        return error or ('回放已停止' if result.get('stopped') else '回放完成')

    def shutdown(self):
        self.closed = True
        self.controller.stop()
        if self.keyboard:
            self.keyboard.stop()
        if self.timer:
            self.root.after_cancel(self.timer)
