import asyncio
import logging
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from time import monotonic
from urllib.parse import urlparse
from playwright.async_api import Page, Locator, Error as PlaywrightError
from . import selectors as S
from .errors import PageUnavailable, UnsafeState


def normalize(text: str) -> str:
    # Preserve internal whitespace so code indentation is not silently discarded.
    return text.replace('\r\n', '\n').replace('\r', '\n').replace('\u00a0', ' ').strip()


def digest(text: str) -> str:
    return sha256(normalize(text).encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class Snapshot:
    users: tuple[str, ...]
    assistants: tuple[str, ...]
    last_text: str
    composer_text: str
    editable: bool
    busy: bool
    done: bool
    online: bool
    error: str = ''


def match_sent(snapshot: Snapshot, baseline: dict, prompt: str) -> bool:
    before = tuple(baseline['users'])
    return (snapshot.users == before + (digest(prompt),)
            and not normalize(snapshot.composer_text))


class ChatGPTPage:
    def __init__(self, page: Page, chat_url: str, logger: logging.Logger, run_dir: Path):
        self.page, self.chat_url, self.logger, self.run_dir = page, chat_url, logger, run_dir

    async def first_visible(self, choices: tuple[str, ...], scope=None) -> Locator | None:
        scope = scope if scope is not None else self.page
        for selector in choices:
            items = scope.locator(selector)
            for index in range(await items.count()):
                locator = items.nth(index)
                if await locator.is_visible():
                    return locator
        return None

    async def messages(self, choices: tuple[str, ...]) -> list[dict]:
        for selector in choices:
            locator = self.page.locator(selector)
            if await locator.count():
                # One synchronous JS read avoids mixing DOM generations within a list.
                return await locator.evaluate_all('(nodes) => nodes.map(n => ({text:n.innerText}))')
        return []

    async def composer(self) -> Locator:
        editor = await self.first_visible(S.COMPOSER)
        if editor is None:
            raise PageUnavailable('找不到输入框：可能需要登录、完成验证或更新 selectors.py')
        return editor

    async def snapshot(self) -> Snapshot:
        if urlparse(self.page.url)._replace(query='', fragment='') != urlparse(self.chat_url)._replace(query='', fragment=''):
            raise PageUnavailable('当前页面不是配置的对话，请手动打开正确对话')
        if await self.first_visible(S.LOGIN) is not None:
            raise PageUnavailable('请手动登录或完成安全验证')
        try:
            editor = await self.composer()
            editable = await editor.is_editable()
            text = await editor.evaluate('(e) => e.value ?? e.innerText ?? ""')
            users = await self.messages(S.USER)
            assistants = await self.messages(S.ASSISTANT)
            busy = await self.first_visible(S.STOP + S.BUSY) is not None
            done = False
            if assistants:
                # Prefer action buttons belonging to this assistant's own turn.
                last = None
                for selector in S.ASSISTANT:
                    if await self.page.locator(selector).count():
                        last = self.page.locator(selector).last
                        break
                if last is not None:
                    turn = await last.evaluate_handle('(e, selector) => e.closest(selector) || e', S.TURN)
                    try:
                        done = await turn.evaluate('(e, selectors) => selectors.some(s => [...e.querySelectorAll(s)].some(b => b.getClientRects().length && !b.disabled))', S.DONE)
                    finally:
                        await turn.dispose()
            alerts = []
            for selector in S.ERROR:
                items = self.page.locator(selector)
                for index in range(await items.count()):
                    if await items.nth(index).is_visible():
                        alerts.append(await items.nth(index).inner_text())
            error = next((t for t in alerts if any(word in t.lower() for word in S.ERROR_TEXT)), '')
            retry = await self.first_visible(S.RETRY)
            if retry is not None:
                error = error or ('页面要求重试：' + await retry.inner_text())
            online = bool(await self.page.evaluate('navigator.onLine'))
            return Snapshot(tuple(digest(x['text']) for x in users), tuple(digest(x['text']) for x in assistants),
                            assistants[-1]['text'] if assistants else '', text, editable, busy, done, online, error)
        except PlaywrightError as exc:
            raise PageUnavailable(str(exc)) from exc

    async def wait_ready(self, timeout: float, stable_seconds: float) -> Snapshot:
        deadline = monotonic() + timeout
        stable_since = None
        previous = None
        last_notice = 0.0
        while monotonic() < deadline:
            try:
                state = await self.snapshot()
                if state.online and state.editable and not state.busy and not state.error and (not state.assistants or state.done):
                    signature = (state.users, state.assistants, state.composer_text)
                    if signature != previous:
                        stable_since, previous = monotonic(), signature
                    elif stable_since is not None and monotonic() - stable_since >= stable_seconds:
                        return state
                else:
                    stable_since, previous = None, None
            except (PageUnavailable, PlaywrightError) as exc:
                stable_since, previous = None, None
                if monotonic() - last_notice > 15:
                    self.logger.warning('等待页面恢复/手动登录：%s', exc)
                    last_notice = monotonic()
            await asyncio.sleep(1)
        raise PageUnavailable('等待可操作且上一轮已结束的页面超时')

    async def wait_accessible(self, timeout: float) -> None:
        """Login readiness must not wait for an in-flight response during recovery."""
        deadline = monotonic() + timeout
        last_notice = 0.0
        while monotonic() < deadline:
            try:
                if (await self.snapshot()).online:
                    return
            except (PageUnavailable, PlaywrightError) as exc:
                if monotonic() - last_notice > 15:
                    self.logger.warning('请手动登录并打开配置的对话；等待页面恢复：%s', exc)
                    last_notice = monotonic()
            await asyncio.sleep(1)
        raise PageUnavailable('手动登录/网络恢复等待超时')

    async def prepare(self, prompt: str) -> None:
        editor = await self.composer()
        current = await editor.evaluate('(e) => e.value ?? e.innerText ?? ""')
        if normalize(current) and normalize(current) != normalize(prompt):
            raise UnsafeState('输入框存在其他草稿，拒绝覆盖；请手动清空后恢复')
        await editor.fill(prompt)
        current = await editor.evaluate('(e) => e.value ?? e.innerText ?? ""')
        if normalize(current) != normalize(prompt):
            raise PageUnavailable('输入框内容未完整写入')
        button = await self.first_visible(S.SEND)
        if button is None or not await button.is_enabled():
            raise PageUnavailable('发送按钮不可用')

    async def click_send(self) -> None:
        button = await self.first_visible(S.SEND)
        if button is None:
            raise PageUnavailable('发送按钮消失，发送结果待核对')
        # Exactly one click invocation after durable intent. No Enter fallback.
        await button.click(timeout=3000, no_wait_after=True)

    async def verify_sent(self, baseline: dict, prompt: str, timeout: float) -> Snapshot:
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            try:
                state = await self.snapshot()
                if match_sent(state, baseline, prompt):
                    return state
                if len(state.users) > len(baseline['users']) and state.users != tuple(baseline['users']) + (digest(prompt),):
                    raise UnsafeState('对话出现意外用户消息，禁止继续自动发送')
            except (PageUnavailable, PlaywrightError):
                pass
            await asyncio.sleep(1)
        raise UnsafeState('无法确认发送成功；为避免重复，不重发。请核对网页后从断点恢复')

    async def diagnostics(self, label: str) -> None:
        from datetime import datetime
        prefix = datetime.now().strftime('%H%M%S_%f') + '_' + re.sub(r'[^a-zA-Z0-9_-]', '_', label)
        try:
            await self.page.screenshot(path=str(self.run_dir / 'screenshots' / f'{prefix}.png'), full_page=True, timeout=5000)
            counts = {key: [await self.page.locator(s).count() for s in getattr(S, key)]
                      for key in ('COMPOSER', 'SEND', 'STOP', 'USER', 'ASSISTANT')}
            self.logger.error('页面诊断 URL=%s selectors=%s', self.page.url, counts)
        except Exception as exc:
            self.logger.error('截图/诊断失败（浏览器可能关闭）：%s', exc)

    async def model_name(self) -> str:
        element = await self.first_visible(S.MODEL)
        return (await element.inner_text()).strip() if element is not None else '未识别（不自动切换）'
