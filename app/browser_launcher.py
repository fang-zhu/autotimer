"""Launch a standard installed browser for manual login, with a local CDP port."""
import os
import socket
import subprocess
from pathlib import Path
from urllib.parse import urlparse
from .config import Config
from .errors import PageUnavailable
from .state_manager import RunLock


def find_browser(channel: str) -> Path:
    executable = 'msedge.exe' if channel == 'msedge' else 'chrome.exe'
    if os.name == 'nt':
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, rf'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{executable}') as key:
                    value = Path(str(winreg.QueryValue(key, None)).strip('"'))
                    if value.is_file():
                        return value
            except OSError:
                pass
    relative = ('Microsoft/Edge/Application/msedge.exe', 'Edge/App/msedge.exe') if channel == 'msedge' else ('Google/Chrome/Application/chrome.exe',)
    for base in (os.environ.get('PROGRAMFILES', ''), os.environ.get('PROGRAMFILES(X86)', ''), os.environ.get('LOCALAPPDATA', '')):
        if base:
            for suffix in relative:
                candidate = Path(base)/suffix
                if candidate.is_file():
                    return candidate
    raise PageUnavailable('找不到所选浏览器，请安装 Chrome 或 Edge，或在界面切换浏览器类型。')


def open_connectable_browser(config: Config) -> int:
    endpoint = urlparse(config.cdp_url)
    port = endpoint.port
    with RunLock(config.profile_dir/'.task.lock'):
        # Do not send browsing URLs to an unknown program already owning the port.
        with socket.socket() as probe:
            probe.settimeout(.3)
            if probe.connect_ex(('127.0.0.1', port)) == 0:
                raise PageUnavailable('调试端口已被使用。若可连接浏览器已经打开，请在其中登录并直接启动任务；否则更换端口。')
        profile = config.profile_dir/'connected'/config.browser_channel
        profile.mkdir(parents=True, exist_ok=True)
        args = [str(find_browser(config.browser_channel)), '--remote-debugging-address=127.0.0.1',
                f'--remote-debugging-port={port}', f'--user-data-dir={profile}', '--no-first-run', config.chat_url]
        # The user explicitly launches an interactive browser, which outlives the task.
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   close_fds=True, creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS) if os.name == 'nt' else 0)
        return process.pid
