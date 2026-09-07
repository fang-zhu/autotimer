import asyncio
import logging
from pathlib import Path
from playwright.async_api import async_playwright, BrowserContext, Page, Error as PlaywrightError
from .config import Config
from .errors import PageUnavailable


class BrowserSession:
    def __init__(self, config: Config, logger: logging.Logger):
        self.config, self.logger = config, logger
        self.engine = None
        self.context: BrowserContext | None = None
        self.attached = False

    async def __aenter__(self) -> Page:
        self.config.profile_dir.mkdir(parents=True, exist_ok=True)
        self.engine = await async_playwright().start()
        try:
            if self.config.browser_mode == 'cdp':
                self.attached = True
                try:
                    browser = await self.engine.chromium.connect_over_cdp(self.config.cdp_url, timeout=10000)
                except PlaywrightError as exc:
                    raise PageUnavailable('无法连接浏览器。请先点击“打开可连接浏览器”，手动登录并打开目标对话后再启动。普通方式打开的窗口没有调试端口。') from exc
                if not browser.contexts:
                    raise PageUnavailable('已连接浏览器但没有默认会话，请手动打开一个标签页。')
                self.context = browser.contexts[0]
                self.context.set_default_timeout(3000)
                self.context.set_default_navigation_timeout(30000)
                target = self.config.chat_url.rstrip('/')
                matches = [p for p in self.context.pages if p.url.split('?')[0].split('#')[0].rstrip('/') == target]
                if len(matches) > 1:
                    raise PageUnavailable('有多个相同目标对话标签，请只保留一个后恢复。')
                if not matches:
                    raise PageUnavailable('浏览器已连接，但没有找到配置的对话。请在该浏览器中手动打开对话链接，再恢复任务。')
                page = matches[0]
                await page.bring_to_front()
                self.logger.info('已连接现有浏览器和目标标签；停止任务时仅断开连接，不关闭窗口。')
                return page
            self.context = await self.engine.chromium.launch_persistent_context(
                user_data_dir=str(self.config.profile_dir), headless=False,
                viewport={'width': 1280, 'height': 900}, accept_downloads=False)
            self.context.set_default_timeout(3000)
            self.context.set_default_navigation_timeout(30000)
            page = self.context.pages[0] if self.context.pages else await self.context.new_page()
            for attempt in range(self.config.settings.retry_count + 1):
                try:
                    await page.goto(self.config.chat_url, wait_until='domcontentloaded')
                    break
                except PlaywrightError as exc:
                    self.logger.warning('打开对话失败 %s：%s', attempt + 1, exc)
                    if attempt == self.config.settings.retry_count:
                        # Leave the window available for manual network/login recovery.
                        self.logger.warning('自动导航重试耗尽；请在窗口中手动打开配置 URL。')
                        break
                    await asyncio.sleep(self.config.settings.retry_interval_seconds)
            self.logger.info('浏览器已打开；需要时请在窗口中手动登录或完成验证。')
            return page
        except BaseException:
            await self.__aexit__(None, None, None)
            raise

    async def __aexit__(self, *_):
        try:
            if self.context and not self.attached:
                await self.context.close()
        finally:
            if self.engine:
                await self.engine.stop()
