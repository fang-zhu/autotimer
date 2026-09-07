import asyncio
from dataclasses import dataclass
from time import monotonic
from playwright.async_api import Error as PlaywrightError
from .chatgpt import ChatGPTPage, Snapshot, digest
from .errors import PageUnavailable, ResponseTimeout, UnsafeState


@dataclass
class CompletionGate:
    stable_seconds: float
    signature: tuple | None = None
    stable_since: float | None = None

    def reset(self) -> None:
        self.signature = None
        self.stable_since = None

    def observe(self, state: Snapshot, baseline: dict, prompt: str, now: float) -> bool:
        expected_users = tuple(baseline['users']) + (digest(prompt),)
        if state.users != expected_users:
            self.reset()
            # Missing messages can be temporary during reload; extra/different turns cannot.
            if len(state.users) >= len(expected_users):
                raise UnsafeState('用户消息发生变化，不能将回答归属到当前步骤')
            return False
        previous = tuple(baseline['assistants'])
        new_response = len(state.assistants) > len(previous) and state.assistants[:len(previous)] == previous
        if not (new_response and state.last_text.strip() and state.online and state.editable
                and not state.busy and not state.error and state.done and not state.composer_text.strip()):
            self.reset()
            return False
        signature = (state.assistants, state.last_text)
        if signature != self.signature:
            self.signature, self.stable_since = signature, now
            return False
        return self.stable_since is not None and now - self.stable_since >= self.stable_seconds


async def wait_response(chat: ChatGPTPage, baseline: dict, prompt: str, timeout: float,
                        stable_seconds: float, retry_count: int, retry_interval: float,
                        on_started, on_recovery=lambda count: None, initial_recoveries: int = 0) -> Snapshot:
    gate = CompletionGate(stable_seconds)
    deadline = monotonic() + timeout
    started = False
    recoveries = initial_recoveries
    next_recovery = 0.0
    last_notice = 0.0
    while monotonic() < deadline:
        try:
            state = await chat.snapshot()
            if (state.busy or len(state.assistants) > len(baseline['assistants'])) and not started:
                on_started()
                started = True
            if state.error:
                gate.reset()
                if monotonic() >= next_recovery:
                    if recoveries >= retry_count:
                        raise UnsafeState(f'网页持续报错，恢复次数耗尽：{state.error}')
                    recoveries += 1
                    on_recovery(recoveries)
                    chat.logger.warning('页面错误，等待恢复 %s/%s：%s', recoveries, retry_count, state.error)
                    await chat.diagnostics('page_error')
                    # Observe only: reloading or clicking Retry can regenerate a response.
                    next_recovery = monotonic() + retry_interval
            if gate.observe(state, baseline, prompt, monotonic()):
                return state
            if monotonic() - last_notice >= 5:
                stable = int(monotonic() - gate.stable_since) if gate.stable_since is not None else 0
                chat.logger.info('等待回答：%s；内容稳定检测 %s/%s 秒',
                                 '离线，等待网络恢复' if not state.online else ('生成中' if state.busy else '核对完成信号'), stable, stable_seconds)
                last_notice = monotonic()
        except (PageUnavailable, PlaywrightError) as exc:
            gate.reset()
            if monotonic() - last_notice >= 15:
                chat.logger.warning('页面暂不可用，请按需手动登录/恢复网络：%s', exc)
                last_notice = monotonic()
        await asyncio.sleep(min(1, max(0, deadline - monotonic())))
    raise ResponseTimeout(f'回答等待超过 {timeout:.0f} 秒，未取得完整结束证据')
