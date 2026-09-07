"""Native Windows configuration and monitoring window (Tk main thread only)."""
import json
import os
import queue
import tkinter as tk
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from tkinter import ttk, messagebox, filedialog
from tkinter.scrolledtext import ScrolledText
from .config import Config, parse_config
from .gui_config import PromptDraft, read_draft, save_draft
from .gui_controller import TaskController
from .state_manager import unfinished
from .prompt_import import split_prompts
from .browser_launcher import open_connectable_browser
from .mouse_panel import MousePanel

STATUS_NAMES = {'WAITING': '等待', 'SENDING': '发送 / 核对', 'WAITING_RESPONSE': '等待回答',
                'COMPLETED': '已完成', 'FAILED': '失败', 'PAUSED': '已暂停',
                'FINISHED': '全部完成', 'FINISHED_WITH_ERRORS': '完成（有失败）'}
SETTING_LABELS = {
    'response_timeout_seconds': '每轮回答超时（秒）', 'stable_wait_seconds': '内容稳定时长（秒）',
    'retry_count': '额外重试次数', 'retry_interval_seconds': '重试间隔（秒）',
    'send_verification_timeout_seconds': '发送核对超时（秒）', 'login_timeout_seconds': '登录 / 页面等待（秒）',
    'delay_after_response_seconds': '步骤间隔（秒）'}


class AutomationWindow:
    def __init__(self, root: tk.Tk, config_path: Path, controller: TaskController | None = None):
        self.root, self.path = root, config_path.resolve()
        self.controller = controller or TaskController()
        self.raw, self.config, self.steps = read_draft(self.path)
        self.selected = -1
        self.loading = True
        self.dirty = False
        self.active = False
        self.closing = False
        self.last_run_dir: Path | None = None
        self.pending_finish = None
        self.state_data: dict = {}
        self._timers: set[str] = set()
        self._closed = False
        self.lockable: list = []
        self.root.title('ChatGPT 网页自动任务')
        width = min(1120, root.winfo_screenwidth() - 60)
        height = min(880, root.winfo_screenheight() - 100)
        root.geometry(f'{width}x{height}')
        root.minsize(920, 660)
        self._style()
        self.vars = {
            'task_name': tk.StringVar(value=self.config.task_name),
            'chat_url': tk.StringVar(value='' if 'REPLACE_WITH_' in self.config.chat_url else self.config.chat_url),
            'start_time': tk.StringVar(value=str(self.raw.get('start_time', self.config.start_time.isoformat()))),
            'start_immediately': tk.BooleanVar(value=self.config.start_immediately),
            'log_dir': tk.StringVar(value=str(self.raw.get('log_dir', 'logs'))),
            'profile_dir': tk.StringVar(value=str(self.raw.get('browser', {}).get('profile_dir', 'browser_data'))),
            'continue_on_error': tk.BooleanVar(value=self.config.settings.continue_on_error),
            'browser_mode': tk.StringVar(value='连接已打开的浏览器' if self.config.browser_mode == 'cdp' else '程序打开专用窗口'),
            'browser_channel': tk.StringVar(value='Edge' if self.config.browser_channel == 'msedge' else 'Chrome'),
            'cdp_url': tk.StringVar(value=self.config.cdp_url),
        }
        for key in SETTING_LABELS:
            self.vars[key] = tk.StringVar(value=str(getattr(self.config.settings, key)))
        for variable in self.vars.values():
            variable.trace_add('write', self._mark_dirty)
        self.phase = tk.StringVar(value='就绪 · 填写对话链接后，保存并启动')
        self.clock = tk.StringVar()
        self.countdown = tk.StringVar(value='尚未启动')
        self.detail = tk.StringVar(value='登录由你手动完成；任务只会在点击“启动任务”后运行。')
        self.summary = tk.StringVar(value=f'共 {len(self.steps)} 步')
        self.step_name = tk.StringVar()
        self.step_timeout = tk.StringVar()
        self.step_stable = tk.StringVar()
        self.step_delay = tk.StringVar()
        self.source = tk.StringVar()
        for variable in (self.step_name, self.step_timeout, self.step_stable, self.step_delay):
            variable.trace_add('write', self._mark_dirty)
        self._build()
        self._refresh_steps(0)
        self.loading = False
        self.dirty = False
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.root.bind('<Destroy>', self._on_destroy, add='+')
        self._schedule(100, self._drain)
        self._schedule(200, self._tick)

    def _schedule(self, delay: int, callback) -> None:
        if self._closed:
            return
        def invoke():
            self._timers.discard(token)
            if not self._closed:
                callback()
        token = self.root.after(delay, invoke)
        self._timers.add(token)

    def _on_destroy(self, event) -> None:
        if event.widget is self.root:
            self.mouse_panel.shutdown()
            self._closed = True
            for token in self._timers:
                self.root.after_cancel(token)
            self._timers.clear()

    def _style(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use('clam')
        self.root.option_add('*Font', ('Microsoft YaHei UI', 10))
        style.configure('.', font=('Microsoft YaHei UI', 10))
        style.configure('TFrame', background='#f4f6fa')
        style.configure('TLabel', background='#f4f6fa', foreground='#253047')
        style.configure('Title.TLabel', font=('Microsoft YaHei UI', 19, 'bold'))
        style.configure('Muted.TLabel', foreground='#65728a')
        style.configure('TButton', padding=(10, 6))
        style.configure('Accent.TButton', background='#235dd0', foreground='white')
        style.map('Accent.TButton', background=[('active', '#164aaf'), ('disabled', '#a8b6d0')])
        style.configure('Treeview', rowheight=30, font=('Microsoft YaHei UI', 10))
        style.configure('Treeview.Heading', font=('Microsoft YaHei UI', 10, 'bold'))
        style.configure('TNotebook.Tab', padding=(16, 7))

    def _entry(self, parent, variable, **grid) -> ttk.Entry:
        widget = ttk.Entry(parent, textvariable=variable)
        widget.grid(sticky='ew', padx=6, pady=5, **grid)
        self.lockable.append(widget)
        return widget

    def _build(self) -> None:
        shell = ttk.Frame(self.root, padding=18)
        shell.pack(fill='both', expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(3, weight=1)
        header = ttk.Frame(shell)
        header.grid(row=0, column=0, sticky='ew')
        ttk.Label(header, text='ChatGPT 网页自动任务', style='Title.TLabel').pack(side='left')
        ttk.Label(header, textvariable=self.clock, style='Muted.TLabel').pack(side='right')
        ttk.Label(shell, text='定时开始  ·  提示词依次执行  ·  回答结束后继续', style='Muted.TLabel').grid(row=1, column=0, sticky='w', pady=(3, 10))
        form = ttk.Frame(shell)
        form.grid(row=2, column=0, sticky='ew', pady=(0, 10))
        form.columnconfigure(1, weight=1)
        form.columnconfigure(3, weight=1)
        ttk.Label(form, text='任务名称').grid(row=0, column=0, sticky='w')
        self._entry(form, self.vars['task_name'], row=0, column=1)
        ttk.Label(form, text='开始时间').grid(row=0, column=2, sticky='w', padx=(14, 0))
        self._entry(form, self.vars['start_time'], row=0, column=3)
        ttk.Label(form, text='对话链接').grid(row=1, column=0, sticky='w')
        self.url_entry = self._entry(form, self.vars['chat_url'], row=1, column=1, columnspan=3)
        ttk.Label(form, text='粘贴 https://chatgpt.com/c/… 链接', style='Muted.TLabel').grid(row=2, column=1, sticky='w', padx=6)
        immediate = ttk.Checkbutton(form, text='立即执行（忽略开始时间）', variable=self.vars['start_immediately'])
        immediate.grid(row=2, column=3, sticky='w', padx=6)
        self.lockable.append(immediate)
        notebook = ttk.Notebook(shell)
        notebook.grid(row=3, column=0, sticky='nsew')
        self.notebook = notebook
        prompts_tab, settings_tab, browser_tab = ttk.Frame(notebook, padding=12), ttk.Frame(notebook, padding=14), ttk.Frame(notebook, padding=14)
        notebook.add(prompts_tab, text='提示词步骤')
        notebook.add(browser_tab, text='浏览器与登录')
        notebook.add(settings_tab, text='等待与异常设置')
        self._build_prompts(prompts_tab)
        self._build_browser(browser_tab)
        self._build_settings(settings_tab)
        self.mouse_panel = MousePanel(notebook, self.root, self.path.parent/'recordings', self._mouse_busy, lambda: self.active)
        notebook.add(self.mouse_panel, text='鼠标录制')
        status = ttk.Frame(shell)
        status.grid(row=4, column=0, sticky='ew', pady=(12, 5))
        status.columnconfigure(0, weight=1)
        ttk.Label(status, textvariable=self.phase, font=('Microsoft YaHei UI', 11, 'bold')).grid(row=0, column=0, sticky='w')
        ttk.Label(status, textvariable=self.countdown).grid(row=0, column=1, sticky='e')
        self.progress = ttk.Progressbar(status, maximum=len(self.steps))
        self.progress.grid(row=1, column=0, sticky='ew', pady=6, padx=(0, 12))
        ttk.Label(status, textvariable=self.summary).grid(row=1, column=1)
        ttk.Label(status, textvariable=self.detail, style='Muted.TLabel').grid(row=2, column=0, columnspan=2, sticky='w')
        self.log_text = ScrolledText(shell, height=6, wrap='word', state='disabled', font=('Microsoft YaHei UI', 9), relief='flat', background='#182235', foreground='#e6edf7')
        self.log_text.grid(row=5, column=0, sticky='ew', pady=(4, 10))
        actions = ttk.Frame(shell)
        actions.grid(row=6, column=0, sticky='ew')
        self.save_button = ttk.Button(actions, text='保存配置', command=self.save)
        self.save_button.pack(side='left')
        self.reload_button = ttk.Button(actions, text='重新读取配置', command=self.reload)
        self.reload_button.pack(side='left', padx=7)
        ttk.Button(actions, text='打开日志', command=self.open_logs).pack(side='left')
        self.stop_button = ttk.Button(actions, text='停止任务', command=self.stop, state='disabled')
        self.stop_button.pack(side='right')
        self.start_button = ttk.Button(actions, text='启动任务', style='Accent.TButton', command=self.start)
        self.start_button.pack(side='right', padx=9)

    def _build_prompts(self, frame) -> None:
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(0, weight=1)
        left = ttk.Frame(frame)
        left.grid(row=0, column=0, sticky='nsew', padx=(0, 14))
        left.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(left, columns=('name', 'status'), show='headings', height=6, selectmode='browse')
        self.tree.heading('name', text='执行顺序 / 步骤')
        self.tree.heading('status', text='状态')
        self.tree.column('name', width=180, minwidth=110)
        self.tree.column('status', width=90, minwidth=70)
        self.tree.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(left, orient='vertical', command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind('<<TreeviewSelect>>', self._select)
        controls = ttk.Frame(left)
        controls.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(8, 0))
        for label, command in [('添加', self.add_step), ('删除', self.delete_step), ('上移', lambda: self.move_step(-1)), ('下移', lambda: self.move_step(1))]:
            button = ttk.Button(controls, text=label, command=command, width=5)
            button.pack(side='left', padx=2)
            self.lockable.append(button)
        split_button = ttk.Button(left, text='TXT 自动分步导入…', command=self.import_steps)
        split_button.grid(row=2, column=0, columnspan=2, sticky='ew', pady=(7, 0))
        self.lockable.append(split_button)
        right = ttk.Frame(frame)
        right.grid(row=0, column=1, sticky='nsew')
        right.columnconfigure(1, weight=1)
        right.rowconfigure(2, weight=1)
        ttk.Label(right, text='步骤名称').grid(row=0, column=0, sticky='w')
        self._entry(right, self.step_name, row=0, column=1)
        file_button = ttk.Button(right, text='导入 TXT', command=self.import_prompt)
        file_button.grid(row=0, column=2, padx=(6, 0))
        self.lockable.append(file_button)
        ttk.Label(right, text='提示词内容').grid(row=1, column=0, columnspan=3, sticky='w', pady=(5, 5))
        self.prompt_text = ScrolledText(right, height=7, wrap='word', undo=True, font=('Microsoft YaHei UI', 10), relief='solid', borderwidth=1)
        self.prompt_text.grid(row=2, column=0, columnspan=3, sticky='nsew')
        self.prompt_text.bind('<<Modified>>', self._text_modified)
        ttk.Label(right, textvariable=self.source, style='Muted.TLabel', wraplength=570).grid(row=3, column=0, columnspan=3, sticky='w', pady=5)
        overrides = ttk.Frame(right)
        overrides.grid(row=4, column=0, columnspan=3, sticky='ew')
        for column, (label, variable) in enumerate([('本步超时', self.step_timeout), ('稳定时长', self.step_stable), ('结束后间隔', self.step_delay)]):
            overrides.columnconfigure(column * 2 + 1, weight=1)
            ttk.Label(overrides, text=label).grid(row=0, column=column*2)
            entry = self._entry(overrides, variable, row=0, column=column*2+1)
            entry.configure(width=7)
        ttk.Label(right, text='单位：秒。留空时使用全局设置。', style='Muted.TLabel').grid(row=5, column=0, columnspan=3, sticky='w')

    def _build_settings(self, frame) -> None:
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(3, weight=1)
        for i, (key, label) in enumerate(SETTING_LABELS.items()):
            row, column = i // 2, (i % 2) * 2
            ttk.Label(frame, text=label).grid(row=row, column=column, sticky='w', padx=(0, 8))
            self._entry(frame, self.vars[key], row=row, column=column+1)
        check = ttk.Checkbutton(frame, text='异常后在确认安全时继续（未完成的回答仍会阻止下一步）', variable=self.vars['continue_on_error'])
        check.grid(row=4, column=0, columnspan=4, sticky='w', pady=10)
        self.lockable.append(check)
        for row, (key, label) in enumerate([('log_dir', '日志目录'), ('profile_dir', '浏览器登录目录')], 5):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky='w')
            self._entry(frame, self.vars[key], row=row, column=1, columnspan=2)
            button = ttk.Button(frame, text='选择目录', command=lambda k=key: self.browse_directory(k))
            button.grid(row=row, column=3, sticky='e')
            self.lockable.append(button)
        ttk.Label(frame, text='重试不会盲目重复点击发送。停止任务会保存断点，恢复时先核对网页。\n登录目录会保留网站登录状态，请使用项目默认目录，并妥善保管。', style='Muted.TLabel', wraplength=850).grid(row=7, column=0, columnspan=4, sticky='w', pady=12)

    def _build_browser(self, frame) -> None:
        frame.columnconfigure(1, weight=1)
        for row, (label, key, values) in enumerate([
                ('使用方式', 'browser_mode', ['程序打开专用窗口', '连接已打开的浏览器']),
                ('手动登录浏览器', 'browser_channel', ['Chrome', 'Edge'])]):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky='w', pady=7)
            box = ttk.Combobox(frame, textvariable=self.vars[key], values=values, state='readonly')
            box.grid(row=row, column=1, sticky='ew', padx=10, pady=7)
            self.lockable.append(box)
        ttk.Label(frame, text='本机连接地址').grid(row=2, column=0, sticky='w')
        self._entry(frame, self.vars['cdp_url'], row=2, column=1)
        button = ttk.Button(frame, text='打开可连接浏览器（先手动登录）', command=self.open_browser)
        button.grid(row=3, column=0, columnspan=2, sticky='w', pady=14)
        self.lockable.append(button)
        ttk.Label(frame, text='连接模式使用方法：\n1. 填写上方对话链接，选择“连接已打开的浏览器”。\n2. 点击此处按钮打开 Chrome / Edge，在其中手动登录并打开该对话。\n3. 回到本窗口点击“启动任务”。停止任务时保留浏览器窗口。\n\n普通方式打开的现有标签页不能直接接管。此处使用独立登录目录，首次仍可能需要登录。\n本机调试端口允许本机程序控制这个浏览器，请勿将端口开放到网络。\n程序不能跳过人机验证；手动验证仍反复失败时，请暂停任务，先解决浏览器访问问题。',
                  style='Muted.TLabel', wraplength=860, justify='left').grid(row=4, column=0, columnspan=2, sticky='nw')

    def open_browser(self) -> None:
        if self.active or self.mouse_panel.busy:
            return
        try:
            config = parse_config(self.draft_mapping(), self.path)
            if config.browser_mode != 'cdp':
                raise ValueError('请先将“使用方式”切换为“连接已打开的浏览器”。')
            pid = open_connectable_browser(config)
            self._log(f'已请求打开手动登录浏览器（进程 {pid}）。请在该窗口正常登录并打开配置的对话，再启动任务。')
            self.phase.set('等待你在可连接浏览器中手动登录')
        except Exception as exc:
            messagebox.showerror('无法打开浏览器', str(exc), parent=self.root)

    def _mark_dirty(self, *_) -> None:
        if not self.loading and not self.active:
            self.dirty = True
            self.root.title('ChatGPT 网页自动任务 · 未保存')

    def _text_modified(self, *_) -> None:
        if self.prompt_text.edit_modified():
            self._mark_dirty()
            self.prompt_text.edit_modified(False)

    def _commit_editor(self) -> None:
        if 0 <= self.selected < len(self.steps) and not self.active:
            step = self.steps[self.selected]
            step.name, step.prompt = self.step_name.get(), self.prompt_text.get('1.0', 'end-1c')
            step.timeout, step.stable, step.delay = self.step_timeout.get(), self.step_stable.get(), self.step_delay.get()

    def _refresh_steps(self, index: int) -> None:
        self.loading = True
        self.tree.delete(*self.tree.get_children())
        for i, step in enumerate(self.steps):
            status = self.state_data.get('steps', {}).get(step.id, {}).get('status', 'WAITING')
            self.tree.insert('', 'end', iid=str(i), values=(f'{i+1}. {step.name}', STATUS_NAMES.get(status, status)))
        self.selected = -1
        index = min(max(index, 0), len(self.steps)-1)
        if self.steps:
            self.tree.selection_set(str(index))
            self._show_step(index)
        self.loading = False
        self.summary.set(f'共 {len(self.steps)} 步')

    def _show_step(self, index: int) -> None:
        old_loading = self.loading
        self.loading = True
        self.selected = index
        step = self.steps[index]
        self.step_name.set(step.name)
        self.step_timeout.set(step.timeout)
        self.step_stable.set(step.stable)
        self.step_delay.set(step.delay)
        self.prompt_text.configure(state='normal')
        self.prompt_text.delete('1.0', 'end')
        self.prompt_text.insert('1.0', step.prompt)
        self.prompt_text.edit_modified(False)
        if self.active:
            self.prompt_text.configure(state='disabled')
        self.source.set(f'来源：{step.source_file}；编辑后将内容保存在配置中，原 TXT 不变。' if step.source_file else '内容保存在配置文件中。')
        self.loading = old_loading

    def _select(self, *_) -> None:
        selected = self.tree.selection()
        if not selected or self.loading:
            return
        index = int(selected[0])
        if index == self.selected:
            return
        self._commit_editor()
        if self.selected >= 0:
            previous = self.steps[self.selected]
            values = self.tree.item(str(self.selected), 'values')
            self.tree.item(str(self.selected), values=(f'{self.selected+1}. {previous.name}', values[1]))
        self._show_step(index)

    def add_step(self) -> None:
        if self.active:
            return
        self._commit_editor()
        used = {s.id for s in self.steps}
        sid = 1
        while str(sid) in used:
            sid += 1
        self.steps.append(PromptDraft(str(sid), f'步骤 {len(self.steps)+1}', ''))
        self._refresh_steps(len(self.steps)-1)
        self._mark_dirty()

    def delete_step(self) -> None:
        if self.active or self.selected < 0:
            return
        if len(self.steps) <= 1:
            messagebox.showinfo('保留一个步骤', '任务至少需要一个提示词步骤。', parent=self.root)
            return
        if not messagebox.askyesno('删除步骤', '确定删除选中的步骤？保存配置后生效。', parent=self.root):
            return
        index = self.selected
        self.steps.pop(index)
        self._refresh_steps(index)
        self._mark_dirty()

    def move_step(self, direction: int) -> None:
        if self.active:
            return
        self._commit_editor()
        target = self.selected + direction
        if 0 <= target < len(self.steps):
            self.steps[self.selected], self.steps[target] = self.steps[target], self.steps[self.selected]
            self._refresh_steps(target)
            self._mark_dirty()

    def import_prompt(self) -> None:
        if self.active:
            return
        path = filedialog.askopenfilename(parent=self.root, title='选择 UTF-8 提示词文件', filetypes=[('文本文件', '*.txt'), ('所有文件', '*.*')])
        if not path:
            return
        try:
            text = Path(path).read_text(encoding='utf-8-sig')
            self._commit_editor()
            step = self.steps[self.selected]
            step.prompt = step.source_prompt = text.strip()
            step.source_file = str(Path(path).resolve())
            self._show_step(self.selected)
            self._mark_dirty()
        except (OSError, UnicodeError) as exc:
            messagebox.showerror('无法导入', str(exc), parent=self.root)

    def browse_directory(self, key: str) -> None:
        if self.active:
            return
        path = filedialog.askdirectory(parent=self.root, title='选择目录')
        if path:
            self.vars[key].set(path)

    def import_steps(self) -> None:
        if self.active:
            return
        filename = filedialog.askopenfilename(parent=self.root, title='导入按第一、第二分步的 TXT', filetypes=[('文本文件', '*.txt'), ('所有文件', '*.*')])
        if not filename:
            return
        try:
            parts = split_prompts(Path(filename).read_text(encoding='utf-8-sig'))
        except (OSError, UnicodeError, ValueError) as exc:
            messagebox.showerror('无法分步导入', str(exc), parent=self.root)
            return
        dialog = tk.Toplevel(self.root)
        dialog.title(f'TXT 分步预览 · 共 {len(parts)} 步')
        dialog.geometry('850x580')
        dialog.minsize(640, 420)
        dialog.transient(self.root)
        dialog.grab_set()
        outer = ttk.Frame(dialog, padding=16)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer, text=f'识别出 {len(parts)} 步，请检查内容和顺序。', font=('Microsoft YaHei UI', 12, 'bold')).pack(anchor='w')
        ttk.Label(outer, text='按单独起行的“第一 / 第一步 / 第二…”拆分；原始标题与正文都会保留。', style='Muted.TLabel').pack(anchor='w', pady=(5, 12))
        body = ttk.Frame(outer)
        body.pack(fill='both', expand=True)
        choices = tk.Listbox(body, width=27, exportselection=False)
        choices.pack(side='left', fill='y', padx=(0, 12))
        preview = ScrolledText(body, wrap='word', state='disabled', font=('Microsoft YaHei UI', 10))
        preview.pack(side='left', fill='both', expand=True)
        for index, part in enumerate(parts, 1):
            choices.insert('end', f'{index}. {part.name}')
        def show(*_):
            selection = choices.curselection()
            if selection:
                preview.configure(state='normal')
                preview.delete('1.0', 'end')
                preview.insert('1.0', parts[selection[0]].prompt)
                preview.configure(state='disabled')
        choices.bind('<<ListboxSelect>>', show)
        choices.selection_set(0)
        show()
        actions = ttk.Frame(outer)
        actions.pack(fill='x', pady=(14, 0))
        def accept(replace: bool):
            self._commit_editor()
            if replace:
                self.steps = []
            used = {step.id for step in self.steps}
            first_new = len(self.steps)
            for part in parts:
                sid = 1
                while str(sid) in used:
                    sid += 1
                used.add(str(sid))
                self.steps.append(PromptDraft(str(sid), part.name, part.prompt))
            self.state_data = {}
            self._refresh_steps(first_new)
            self._mark_dirty()
            self._log(f'已从 TXT 导入 {len(parts)} 步，尚未保存或发送。原 TXT 文件未修改。')
            dialog.destroy()
        ttk.Button(actions, text='取消', command=dialog.destroy).pack(side='left')
        ttk.Button(actions, text='替换现有步骤', command=lambda: accept(True)).pack(side='right')
        ttk.Button(actions, text='追加到现有步骤', style='Accent.TButton', command=lambda: accept(False)).pack(side='right', padx=8)

    def draft_mapping(self) -> dict:
        self._commit_editor()
        data = deepcopy(self.raw)
        for key in ('task_name', 'chat_url', 'start_time', 'log_dir'):
            data[key] = self.vars[key].get().strip()
        if not data['chat_url'] or 'REPLACE_WITH_' in data['chat_url']:
            raise ValueError('请先填写真实对话链接，例如 https://chatgpt.com/c/你的对话ID')
        if not data['task_name']:
            raise ValueError('请填写任务名称')
        data['start_immediately'] = self.vars['start_immediately'].get()
        if not data['start_time'] and data['start_immediately']:
            data.pop('start_time')
        data['browser'] = {'headless': False, 'use_persistent_profile': True,
                           'profile_dir': self.vars['profile_dir'].get().strip(),
                           'mode': 'cdp' if self.vars['browser_mode'].get() == '连接已打开的浏览器' else 'persistent',
                           'channel': 'msedge' if self.vars['browser_channel'].get() == 'Edge' else 'chrome',
                           'cdp_url': self.vars['cdp_url'].get().strip()}
        data['settings'] = {}
        for key, label in SETTING_LABELS.items():
            try:
                data['settings'][key] = int(self.vars[key].get()) if key == 'retry_count' else float(self.vars[key].get())
            except ValueError as exc:
                raise ValueError(f'「{label}」请填写有效数字') from exc
        data['settings']['continue_on_error'] = self.vars['continue_on_error'].get()
        data['steps'] = [step.to_mapping() for step in self.steps]
        return data

    def save(self, silent: bool = False) -> Config | None:
        if self.active:
            return None
        try:
            data = self.draft_mapping()
            preview = parse_config(data, self.path)
            if any(actual.prompt != draft.prompt.strip() for actual, draft in zip(preview.steps, self.steps)):
                raise ValueError('外置 TXT 文件在窗口打开后发生变化，请点击“重新读取配置”核对提示词后再保存。')
            self.config = save_draft(self.path, data)
            self.raw = data
            self.dirty = False
            self.root.title('ChatGPT 网页自动任务')
            self._log(f'配置已保存：{self.path}；上一版保存在 {self.path.name}.bak')
            if not silent:
                self.phase.set('配置已保存 · 点击启动任务开始')
            return self.config
        except Exception as exc:
            messagebox.showerror('配置无法保存', str(exc), parent=self.root)
            return None

    def reload(self) -> None:
        if self.active:
            return
        if self.dirty and not messagebox.askyesno('重新读取', '放弃窗口中尚未保存的修改？', parent=self.root):
            return
        try:
            raw, config, steps = read_draft(self.path)
            self.loading = True
            self.raw, self.config, self.steps = raw, config, steps
            for key in ('task_name', 'chat_url', 'start_time', 'log_dir'):
                self.vars[key].set(str(raw.get(key, getattr(config, key))))
            self.vars['profile_dir'].set(str(raw.get('browser', {}).get('profile_dir', 'browser_data')))
            self.vars['browser_mode'].set('连接已打开的浏览器' if config.browser_mode == 'cdp' else '程序打开专用窗口')
            self.vars['browser_channel'].set('Edge' if config.browser_channel == 'msedge' else 'Chrome')
            self.vars['cdp_url'].set(config.cdp_url)
            self.vars['start_immediately'].set(config.start_immediately)
            for key, value in asdict(config.settings).items():
                self.vars[key].set(value)
            self.state_data = {}
            self._refresh_steps(0)
            self.loading = False
            self.dirty = False
            self.root.title('ChatGPT 网页自动任务')
            self.phase.set('已重新读取配置')
        except Exception as exc:
            self.loading = False
            messagebox.showerror('读取失败', str(exc), parent=self.root)

    def start(self) -> None:
        if self.active or self.mouse_panel.busy:
            return
        config = self.save(silent=True)
        if config is None:
            return
        try:
            pending = unfinished(config)
            if len(pending) > 1:
                raise ValueError('发现多个未完成任务，请先检查日志目录。')
            resume = pending[0] if pending else None
            restart = False
            if resume:
                data = json.loads(resume.read_text(encoding='utf-8'))
                lines = '\n'.join(f"{r['name']}：{STATUS_NAMES.get(r['status'], r['status'])}" for r in data['steps'].values())
                answer = messagebox.askyesnocancel('发现未完成任务', lines + '\n\n是：从断点恢复，不重发\n否：从头重新执行，可能重复\n取消：暂不启动', parent=self.root)
                if answer is None:
                    return
                if answer:
                    if data['fingerprint'] != config.fingerprint:
                        raise ValueError('提示词或任务配置已变化，不能套用旧断点。请还原配置后恢复，或明确选择从头执行。')
                else:
                    if not messagebox.askyesno('确认从头执行', '会重新发送全部提示词，包含已完成的步骤。\n确定从头重新执行吗？', parent=self.root):
                        return
                    restart = True
            self.state_data = {}
            self.last_run_dir = None
            self._refresh_steps(max(self.selected, 0))
            self._set_active(True)
            self.phase.set('正在启动…')
            self.controller.start(config, resume, restart)
        except Exception as exc:
            self._set_active(False)
            messagebox.showerror('无法启动', str(exc), parent=self.root)

    def _set_active(self, active: bool) -> None:
        self.active = active
        for widget in self.lockable:
            widget.configure(state='disabled' if active else ('readonly' if isinstance(widget, ttk.Combobox) else 'normal'))
        self.prompt_text.configure(state='disabled' if active else 'normal')
        for widget in (self.save_button, self.reload_button, self.start_button):
            widget.configure(state='disabled' if active else 'normal')
        self.stop_button.configure(state='normal' if active else 'disabled')
        self.mouse_panel._refresh()

    def _mouse_busy(self, busy: bool) -> None:
        for widget in (self.start_button, self.save_button, self.reload_button):
            widget.configure(state='disabled' if busy else 'normal')
        if self.closing and not busy and not self.active:
            self.close()

    def stop(self) -> None:
        if not self.active:
            return
        self.stop_button.configure(state='disabled')
        self.phase.set('正在停止并关闭浏览器，请稍候…')
        self.controller.stop()

    def _log(self, text: str) -> None:
        self.log_text.configure(state='normal')
        self.log_text.insert('end', text + '\n')
        if int(self.log_text.index('end-1c').split('.')[0]) > 2000:
            self.log_text.delete('1.0', '301.0')
        self.log_text.see('end')
        self.log_text.configure(state='disabled')

    def _drain(self) -> None:
        for _ in range(200):
            try:
                kind, data = self.controller.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'log':
                self._log(str(data))
                self.detail.set(str(data).splitlines()[0][:115])
            elif kind == 'phase':
                self.phase.set(str(data))
            elif kind == 'run_dir':
                self.last_run_dir = Path(data)
            elif kind == 'state':
                self.state_data = data
                for i, step in enumerate(self.steps):
                    status = data['steps'].get(step.id, {}).get('status', 'WAITING')
                    self.tree.set(str(i), 'status', STATUS_NAMES.get(status, status))
                complete = sum(r['status'] == 'COMPLETED' for r in data['steps'].values())
                failed = sum(r['status'] == 'FAILED' for r in data['steps'].values())
                self.progress.configure(maximum=len(self.steps), value=complete+failed)
                self.summary.set(f'成功 {complete} · 失败 {failed} · 共 {len(self.steps)} 步')
                current = data.get('current_step')
                if current is not None:
                    record = data['steps'][current]
                    self.phase.set(f"{record['name']} · {STATUS_NAMES.get(record['status'], record['status'])}")
            elif kind == 'finished':
                self.pending_finish = data
        if self.pending_finish is not None and not self.controller.running:
            result, self.pending_finish = self.pending_finish, None
            self._set_active(False)
            self.phase.set({0: '全部步骤已完成', 130: '已停止 · 再次启动可核对断点并恢复'}.get(result['code'], '任务未全部成功 · 请查看日志后恢复'))
            if result['error']:
                self.detail.set(result['error'][:115])
                if not self.closing:
                    messagebox.showerror('任务停止', result['error'], parent=self.root)
            if self.closing:
                self.close()
                if self._closed:
                    return
            if os.name == 'nt':
                import winsound
                winsound.MessageBeep(winsound.MB_OK if result['code'] == 0 else winsound.MB_ICONEXCLAMATION)
        self._schedule(100, self._drain)

    def _tick(self) -> None:
        now = datetime.now().astimezone()
        self.clock.set(now.strftime('%Y-%m-%d  %H:%M:%S'))
        if self.active:
            if self.config.start_immediately:
                self.countdown.set('立即执行')
            else:
                seconds = max(0, int(self.config.start_time.timestamp()-now.timestamp()))
                self.countdown.set(f'距离计划 {seconds//3600:02d}:{seconds%3600//60:02d}:{seconds%60:02d}' if seconds else '已到计划时间')
        else:
            self.countdown.set('尚未运行' if not self.state_data else '任务已结束 / 暂停')
        self._schedule(1000, self._tick)

    def open_logs(self) -> None:
        try:
            path = self.last_run_dir or (self.path.parent / self.vars['log_dir'].get()).resolve()
            path.mkdir(parents=True, exist_ok=True)
            os.startfile(str(path))
        except Exception as exc:
            messagebox.showerror('无法打开日志目录', str(exc), parent=self.root)

    def close(self) -> None:
        if self.mouse_panel.busy:
            if not messagebox.askyesno('停止鼠标任务', '先停止鼠标录制 / 回放，再退出窗口？', parent=self.root):
                return
            self.closing = True
            self.mouse_panel.stop()
            return
        if self.mouse_panel.dirty and not self.active:
            answer = messagebox.askyesnocancel('鼠标录制尚未保存', '退出前保存鼠标录制？', parent=self.root)
            if answer is None:
                self.closing = False
                return
            if answer:
                self.mouse_panel.save()
                if self.mouse_panel.dirty:
                    self.closing = False
                    return
            self.mouse_panel.dirty = False
        if self.active:
            if not messagebox.askyesno('停止并退出', '任务正在运行。停止自动操作、保存断点并关闭窗口？\n这不等同于停止 ChatGPT 服务器生成。', parent=self.root):
                return
            self.closing = True
            self.stop()
        else:
            if self.dirty:
                answer = messagebox.askyesnocancel('未保存修改', '退出前保存配置？', parent=self.root)
                if answer is None or (answer and self.save(silent=True) is None):
                    self.closing = False
                    return
            self.root.destroy()
