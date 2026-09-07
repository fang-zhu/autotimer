"""Desktop entry point. Launching this window never starts a task automatically."""
import argparse
import ctypes
import sys
from pathlib import Path
import tkinter as tk
from tkinter import messagebox


def main() -> int:
    parser = argparse.ArgumentParser(description='ChatGPT 网页自动任务桌面窗口')
    parser.add_argument('--config', type=Path, default=Path(__file__).resolve().parent/'config.yaml')
    args = parser.parse_args()
    if sys.platform == 'win32':
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    root.withdraw()
    try:
        from app.gui import AutomationWindow
        AutomationWindow(root, args.config)
    except Exception as exc:
        messagebox.showerror('窗口无法启动', f'{exc}\n\n请检查配置文件或按 README 安装依赖。', parent=root)
        root.destroy()
        return 1
    root.deiconify()
    root.mainloop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
