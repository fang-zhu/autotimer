import logging
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from app.browser import BrowserSession
from app.config import Config, Settings, Step
from app.errors import PageUnavailable


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_context_and_engine_close_when_task_raises(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = Config('test', 'https://chatgpt.com/c/test', datetime.now().astimezone(), True,
                            root/'profile', root/'logs', Settings(), [Step('1', 'one', 'hello', 30, 5, 0)])
            page = AsyncMock()
            context = AsyncMock()
            context.pages = [page]
            context.set_default_timeout = Mock()
            context.set_default_navigation_timeout = Mock()
            engine = AsyncMock()
            engine.chromium.launch_persistent_context.return_value = context
            manager = SimpleNamespace(start=AsyncMock(return_value=engine))
            with patch('app.browser.async_playwright', return_value=manager):
                with self.assertRaises(PageUnavailable):
                    async with BrowserSession(config, logging.getLogger('test')):
                        raise PageUnavailable('interrupted task')
            context.close.assert_awaited_once()
            engine.stop.assert_awaited_once()

    async def test_engine_stops_when_browser_launch_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = Config('test', 'https://chatgpt.com/c/test', datetime.now().astimezone(), True,
                            root/'profile', root/'logs', Settings(), [])
            engine = AsyncMock()
            engine.chromium.launch_persistent_context.side_effect = RuntimeError('launch failed')
            with patch('app.browser.async_playwright', return_value=SimpleNamespace(start=AsyncMock(return_value=engine))):
                with self.assertRaises(RuntimeError):
                    async with BrowserSession(config, logging.getLogger('test')):
                        self.fail('must not enter')
            engine.stop.assert_awaited_once()
