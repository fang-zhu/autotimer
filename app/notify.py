import sys


def notify(success: bool) -> None:
    print('\n任务执行结束。' if success else '\n任务未全部成功，请查看日志并按需恢复。')
    if sys.platform == 'win32':
        import winsound
        winsound.MessageBeep(winsound.MB_OK if success else winsound.MB_ICONEXCLAMATION)
