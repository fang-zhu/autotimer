import logging
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from app.browser import BrowserSession
from app.config import Config, Settings, parse_config
from app.errors import PageUnavailable


class ConnectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = Config('test', 'https://chatgpt.com/c/test', datetime.now().astimezone(), True,
                             self.root/'profile', self.root/'logs', Settings(), [], 'cdp')
        self.page = AsyncMock()
        self.page.url = self.config.chat_url
        self.context = AsyncMock()
        self.context.pages = [self.page]
        self.context.set_default_timeout = Mock()
        self.context.set_default_navigation_timeout = Mock()
        self.browser = AsyncMock()
        self.browser.contexts = [self.context]
        self.engine = AsyncMock()
        self.engine.chromium.connect_over_cdp.return_value = self.browser

    def tearDown(self):
        self.tmp.cleanup()

    async def test_attach_reuses_target_without_navigation_and_does_not_close_browser(self):
        with patch('app.browser.async_playwright', return_value=SimpleNamespace(start=AsyncMock(return_value=self.engine))):
            async with BrowserSession(self.config, logging.getLogger('test')) as page:
                self.assertIs(page, self.page)
        self.page.goto.assert_not_awaited()
        self.context.close.assert_not_awaited()
        self.browser.close.assert_not_awaited()
        self.engine.stop.assert_awaited_once()

    async def test_missing_target_preserves_all_existing_tabs(self):
        self.page.url = 'https://chatgpt.com/'
        with patch('app.browser.async_playwright', return_value=SimpleNamespace(start=AsyncMock(return_value=self.engine))):
            with self.assertRaises(PageUnavailable):
                async with BrowserSession(self.config, logging.getLogger('test')):
                    self.fail('must reject missing target')
        self.context.new_page.assert_not_awaited()
        self.page.goto.assert_not_awaited()
        self.context.close.assert_not_awaited()

    async def test_duplicate_target_tabs_are_rejected(self):
        self.context.pages = [self.page, self.page]
        with patch('app.browser.async_playwright', return_value=SimpleNamespace(start=AsyncMock(return_value=self.engine))):
            with self.assertRaises(PageUnavailable):
                async with BrowserSession(self.config, logging.getLogger('test')):
                    self.fail('must reject duplicate target')

    def test_only_local_debug_endpoint_is_accepted(self):
        for endpoint in ('http://0.0.0.0:9222', 'http://example.com:9222', 'http://user@127.0.0.1:9222', 'http://127.0.0.1:9222/path'):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(ValueError):
                    parse_config({'chat_url': self.config.chat_url, 'start_immediately': True,
                                  'browser': {'mode': 'cdp', 'cdp_url': endpoint}, 'steps': [{'prompt': 'hello'}]}, self.root/'config.yaml')

    def test_connection_mode_keeps_existing_task_fingerprint(self):
        self.assertEqual(replace(self.config, browser_mode='persistent').fingerprint, self.config.fingerprint)
