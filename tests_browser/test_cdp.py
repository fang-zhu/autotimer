import asyncio
import logging
import os
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from time import monotonic
from playwright.async_api import async_playwright
from app.browser import BrowserSession
from app.config import Config, Settings
from app.browser_launcher import find_browser

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(ROOT/'.browser-cache'))


class CdpBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnect_preserves_actual_browser_process_and_target_page(self):
        (ROOT/'.download-temp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT/'.download-temp') as directory:
            root = Path(directory)
            profile = root/'remote'
            async with async_playwright() as engine:
                diagnostic = (root/'browser-stderr.txt').open('w', encoding='utf-8')
                process = subprocess.Popen([str(find_browser('chrome')), '--headless=new', '--remote-debugging-address=127.0.0.1',
                    '--remote-debugging-port=0', f'--user-data-dir={profile}', '--no-first-run', 'about:blank'],
                    stdout=subprocess.DEVNULL, stderr=diagnostic)
                remote = None
                try:
                    deadline = monotonic()+15
                    while not (profile/'DevToolsActivePort').exists() and monotonic() < deadline:
                        if process.poll() is not None:
                            break
                        await asyncio.sleep(.05)
                    if not (profile/'DevToolsActivePort').exists():
                        diagnostic.flush()
                        raise AssertionError(f'Browser exited={process.poll()}; stderr=' + (root/'browser-stderr.txt').read_text(encoding='utf-8', errors='replace')[:2000])
                    port = int((profile/'DevToolsActivePort').read_text().splitlines()[0])
                    endpoint = f'http://127.0.0.1:{port}'
                    print('CDP test: connecting', flush=True)
                    remote = await engine.chromium.connect_over_cdp(endpoint, timeout=5000)
                    context = remote.contexts[0]
                    await context.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html', body='<title>CDP fixture</title><p>keep this tab</p>'))
                    page = context.pages[0]
                    await page.goto('https://fixture.invalid/c/test', timeout=5000)
                    print('CDP test: attaching second client', flush=True)
                    config = Config('test', page.url, datetime.now().astimezone(), True,
                                    root/'task-profile', root/'logs', Settings(), [], 'cdp', endpoint)
                    async with BrowserSession(config, logging.getLogger('cdp.test')) as attached:
                        self.assertEqual(await asyncio.wait_for(attached.title(), 5), 'CDP fixture')
                    print('CDP test: checking preserved browser', flush=True)
                    self.assertIsNone(process.poll())
                    self.assertFalse(page.is_closed())
                    self.assertEqual(await page.locator('p').inner_text(timeout=5000), 'keep this tab')
                finally:
                    if remote and process.poll() is None:
                        try:
                            session = await asyncio.wait_for(remote.new_browser_cdp_session(), 2)
                            await asyncio.wait_for(session.send('Browser.close'), 2)  # Only this test-owned browser.
                        except Exception:
                            pass
                    try:
                        await asyncio.to_thread(process.wait, timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        await asyncio.to_thread(process.wait, timeout=5)
                    diagnostic.close()
