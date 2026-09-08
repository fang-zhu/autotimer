from dataclasses import dataclass, asdict
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlparse
import json
import math
import yaml


@dataclass(frozen=True)
class Settings:
    response_timeout_seconds: float = 1800
    stable_wait_seconds: float = 5
    retry_count: int = 3
    retry_interval_seconds: float = 10
    continue_on_error: bool = False
    send_verification_timeout_seconds: float = 30
    login_timeout_seconds: float = 900
    delay_after_response_seconds: float = 0


@dataclass(frozen=True)
class Step:
    id: str
    name: str
    prompt: str
    timeout_seconds: float
    stable_wait_seconds: float
    delay_after_response_seconds: float


@dataclass(frozen=True)
class MouseSettings:
    replay_count: int = 1
    replay_interval_seconds: float = 0


@dataclass(frozen=True)
class Config:
    task_name: str
    chat_url: str
    start_time: datetime
    start_immediately: bool
    profile_dir: Path
    log_dir: Path
    settings: Settings
    steps: list[Step]
    browser_mode: str = 'persistent'
    cdp_url: str = 'http://127.0.0.1:9222'
    browser_channel: str = 'chrome'
    mouse: MouseSettings = MouseSettings()

    @property
    def fingerprint(self) -> str:
        # Scheduling and operational timeout edits do not invalidate recovery.
        data = [self.task_name, self.chat_url, str(self.profile_dir),
                [(s.id, s.prompt) for s in self.steps]]
        return sha256(json.dumps(data, ensure_ascii=False).encode()).hexdigest()


def positive(value: object, name: str, zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{name} 必须是数字')
    if not math.isfinite(value) or value < 0 or (value == 0 and not zero):
        raise ValueError(f'{name} 数值无效')
    return float(value)


def boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f'{name} 必须为 true 或 false')
    return value


def load_config(path: Path) -> Config:
    path = path.resolve()
    data = yaml.safe_load(path.read_text(encoding='utf-8-sig'))
    return parse_config(data, path)


def parse_config(data: object, path: Path) -> Config:
    """Validate an in-memory GUI draft before replacing its configuration file."""
    path = path.resolve()
    if not isinstance(data, dict):
        raise ValueError('配置必须是 YAML 对象')
    allowed = {'task_name', 'chat_url', 'start_time', 'start_immediately', 'browser', 'settings', 'steps', 'log_dir', 'mouse'}
    if set(data) - allowed:
        raise ValueError(f'未知配置字段: {set(data) - allowed}')
    url = str(data.get('chat_url', '')).strip()
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.netloc != 'chatgpt.com' or not parsed.path.startswith('/c/') or len(parsed.path) <= 3:
        raise ValueError('chat_url 必须为 https://chatgpt.com/c/真实对话ID；请先手动创建对话')
    immediate = boolean(data.get('start_immediately', False), 'start_immediately')
    raw_time = data.get('start_time')
    start = datetime.fromisoformat(str(raw_time)) if raw_time is not None else None
    if start is None:
        if not immediate:
            raise ValueError('缺少 start_time')
        start = datetime.now().astimezone()
    # Naive times are interpreted using Windows local timezone at that date.
    start = start.astimezone()
    values = data.get('settings', {})
    defaults = asdict(Settings())
    if not isinstance(values, dict) or set(values) - set(defaults):
        raise ValueError('settings 存在未知字段或格式错误')
    defaults.update(values)
    for key, value in defaults.items():
        if key == 'continue_on_error':
            boolean(value, key)
        elif key == 'retry_count':
            if type(value) is not int or value < 0:
                raise ValueError('retry_count 必须是非负整数')
        else:
            positive(value, key, key == 'delay_after_response_seconds')
    settings = Settings(**defaults)
    mouse_values = data.get('mouse', {})
    if not isinstance(mouse_values, dict) or set(mouse_values) - {'replay_count', 'replay_interval_seconds'}:
        raise ValueError('mouse 字段错误')
    replay_count = mouse_values.get('replay_count', 1)
    if type(replay_count) is not int or not 1 <= replay_count <= 1000:
        raise ValueError('mouse.replay_count 必须是 1 到 1000 的整数')
    replay_interval = positive(mouse_values.get('replay_interval_seconds', 0), 'mouse.replay_interval_seconds', True)
    if replay_interval > 86400:
        raise ValueError('mouse.replay_interval_seconds 不能超过 86400 秒')
    mouse = MouseSettings(replay_count, replay_interval)
    browser = data.get('browser', {})
    if not isinstance(browser, dict) or set(browser) - {'headless', 'use_persistent_profile', 'profile_dir', 'mode', 'cdp_url', 'channel'}:
        raise ValueError('browser 字段错误')
    if boolean(browser.get('headless', False), 'headless'):
        raise ValueError('第一版要求 headless: false，以便手动登录/验证')
    if not boolean(browser.get('use_persistent_profile', True), 'use_persistent_profile'):
        raise ValueError('第一版要求 use_persistent_profile: true')
    mode = browser.get('mode', 'persistent')
    if mode not in {'persistent', 'cdp'}:
        raise ValueError('browser.mode 必须是 persistent 或 cdp')
    channel = browser.get('channel', 'chrome')
    if channel not in {'chrome', 'msedge'}:
        raise ValueError('browser.channel 必须是 chrome 或 msedge')
    endpoint = str(browser.get('cdp_url', 'http://127.0.0.1:9222')).rstrip('/')
    cdp = urlparse(endpoint)
    if (cdp.scheme != 'http' or cdp.hostname not in {'127.0.0.1', 'localhost'} or not cdp.port
            or cdp.username or cdp.password or cdp.path or cdp.query or cdp.fragment):
        raise ValueError('cdp_url 必须为本机地址，例如 http://127.0.0.1:9222')
    def resolve(value: str) -> Path:
        return (path.parent / value).resolve()
    steps = []
    ids = set()
    raw_steps = data.get('steps')
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError('steps 必须是非空列表')
    for index, item in enumerate(raw_steps, 1):
        fields = {'id', 'name', 'prompt', 'prompt_file', 'timeout_seconds', 'stable_wait_seconds', 'delay_after_response_seconds'}
        if not isinstance(item, dict) or set(item) - fields:
            raise ValueError(f'Step {index} 字段错误')
        if ('prompt' in item) == ('prompt_file' in item):
            raise ValueError(f'Step {index}: prompt 和 prompt_file 必须且只能设置一个')
        prompt = resolve(item['prompt_file']).read_text(encoding='utf-8-sig') if 'prompt_file' in item else item['prompt']
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError(f'Step {index}: 提示词不能为空')
        sid = str(item.get('id', index))
        if sid in ids:
            raise ValueError(f'重复步骤 ID: {sid}')
        ids.add(sid)
        steps.append(Step(sid, str(item.get('name', f'步骤 {index}')), prompt.strip(),
                          positive(item.get('timeout_seconds', settings.response_timeout_seconds), 'timeout_seconds'),
                          positive(item.get('stable_wait_seconds', settings.stable_wait_seconds), 'stable_wait_seconds'),
                          positive(item.get('delay_after_response_seconds', settings.delay_after_response_seconds), 'delay', True)))
    return Config(str(data.get('task_name', 'GPT自动任务')), url, start, immediate,
                  resolve(browser.get('profile_dir', 'browser_data')),
                  resolve(data.get('log_dir', 'logs')), settings, steps, mode, endpoint, channel, mouse)
