import asyncio
import logging
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from time import monotonic
from playwright.async_api import async_playwright
from app.chatgpt import ChatGPTPage, match_sent
from app.config import Config, Settings, Step
from app.errors import ResponseTimeout, UnsafeState
from app.response_monitor import wait_response
from app.state_manager import StateManager
from app.task_runner import TaskRunner

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(ROOT/'.browser-cache'))


class BrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        (ROOT/'.download-temp').mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT/'.download-temp')
        self.root = Path(self.tmp.name)
        (self.root/'screenshots').mkdir()
        self.engine = await async_playwright().start()
        self.context = await self.engine.chromium.launch_persistent_context(str(self.root/'profile'), headless=True)
        self.context.set_default_timeout(1000)
        html = (ROOT/'tests_browser/fixture.html').read_text(encoding='utf-8')
        await self.context.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html', body=html))
        self.page = self.context.pages[0]
        await self.page.goto('https://fixture.invalid/c/test')
        self.logger = logging.getLogger('browser.test')
        self.logger.handlers = [logging.NullHandler()]
        self.logger.propagate = False
        self.chat = ChatGPTPage(self.page, self.page.url, self.logger, self.root)

    async def asyncTearDown(self):
        await self.context.close()
        await self.engine.stop()
        self.tmp.cleanup()

    async def send_one(self, prompt='line one\n  line two'):
        state = await self.chat.snapshot()
        baseline = {'users': list(state.users), 'assistants': list(state.assistants)}
        await self.chat.prepare(prompt)
        await self.chat.click_send()
        verified = await self.chat.verify_sent(baseline, prompt, 2)
        self.assertTrue(match_sent(verified, baseline, prompt))
        return baseline

    async def monitor(self, baseline, prompt='line one\n  line two', timeout=8):
        return await wait_response(self.chat, baseline, prompt, timeout, .2, 1, .1, lambda: None)

    async def test_single_prompt_streaming_completion(self):
        baseline = await self.send_one()
        result = await self.monitor(baseline)
        self.assertIn('Final answer', result.last_text)
        self.assertEqual(await self.page.evaluate('window.clicks'), 1)

    async def test_pause_without_final_controls_does_not_complete(self):
        await self.page.evaluate('window.mode="pause"')
        start = monotonic()
        baseline = await self.send_one()
        result = await self.monitor(baseline)
        self.assertGreater(monotonic()-start, 2.2)
        self.assertIn('Final answer', result.last_text)

    async def test_fallback_selectors_and_replaced_composer(self):
        await self.page.evaluate('window.useFallbacks()')
        # A real DOM replacement simulates a rerender; no cached ElementHandle survives.
        await self.page.evaluate('const e=document.querySelector("[contenteditable]"); e.replaceWith(e.cloneNode(true));')
        baseline = await self.send_one('fallback')
        result = await self.monitor(baseline, 'fallback')
        self.assertIn('fallback', result.last_text)

    async def test_reload_after_send_does_not_require_resend(self):
        baseline = await self.send_one('reload')
        await self.monitor(baseline, 'reload')
        await self.page.reload()
        result = await self.chat.verify_sent(baseline, 'reload', 2)
        self.assertTrue(match_sent(result, baseline, 'reload'))
        await self.monitor(baseline, 'reload')
        self.assertEqual(await self.page.evaluate('window.clicks'), 1)

    async def test_timeout_creates_screenshot_without_second_send(self):
        await self.page.evaluate('window.mode="never"')
        baseline = await self.send_one('never')
        with self.assertRaises(ResponseTimeout):
            await self.monitor(baseline, 'never', timeout=.3)
        await self.chat.diagnostics('timeout')
        self.assertEqual(len(list((self.root/'screenshots').glob('*.png'))), 1)
        self.assertEqual(await self.page.evaluate('window.clicks'), 1)

    async def test_full_runner_two_identical_prompts(self):
        settings = Settings(stable_wait_seconds=.1, login_timeout_seconds=5, send_verification_timeout_seconds=2)
        steps = [Step(str(i), str(i), 'same prompt', 10, .1, 0) for i in (1, 2)]
        config = Config('fixture', self.page.url, datetime.now().astimezone(), True, self.root/'profile', self.root, settings, steps)
        state = StateManager(self.root/'status.json', config)
        self.assertTrue(await TaskRunner(config, self.chat, state, self.logger).run())
        self.assertEqual(state.data['completed_steps'], ['1', '2'])
        self.assertEqual(await self.page.evaluate('window.clicks'), 2)

    async def test_other_draft_is_preserved(self):
        await self.page.locator('#prompt-textarea').fill('my unsent draft')
        with self.assertRaises(UnsafeState):
            await self.chat.prepare('automation prompt')
        self.assertEqual(await self.page.locator('#prompt-textarea').inner_text(), 'my unsent draft')
        self.assertEqual(await self.page.evaluate('window.clicks'), 0)

    async def test_offline_resets_completion_until_network_returns(self):
        baseline = await self.send_one('offline')
        await self.context.set_offline(True)
        pending = asyncio.create_task(self.monitor(baseline, 'offline'))
        try:
            await asyncio.sleep(1.2)
            self.assertFalse(pending.done())
            await self.context.set_offline(False)
            self.assertIn('Final answer', (await pending).last_text)
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)

    async def test_persistent_context_reopen_keeps_history_without_resend(self):
        baseline = await self.send_one('persistent')
        await self.monitor(baseline, 'persistent')
        await self.context.close()
        self.context = await self.engine.chromium.launch_persistent_context(str(self.root/'profile'), headless=True)
        html = (ROOT/'tests_browser/fixture.html').read_text(encoding='utf-8')
        await self.context.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html', body=html))
        self.page = self.context.pages[0]
        await self.page.goto('https://fixture.invalid/c/test')
        self.chat = ChatGPTPage(self.page, self.page.url, self.logger, self.root)
        result = await self.chat.verify_sent(baseline, 'persistent', 2)
        self.assertTrue(match_sent(result, baseline, 'persistent'))
        self.assertEqual(await self.page.evaluate('window.clicks'), 1)
