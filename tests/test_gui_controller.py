import asyncio
import json
import queue
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch
from app.config import Config, Settings, Step
from app.errors import UnsafeState
from app.gui_controller import TaskController
from app.state_manager import StateManager, RunLock
from app.runtime import execute


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = Config('test', 'https://chatgpt.com/c/test', datetime.now().astimezone(), True,
                             self.root/'profile', self.root/'logs', Settings(), [Step('1', 'one', 'hello', 30, 5, 0)])
        self.controller = None

    def tearDown(self):
        if self.controller and self.controller.running:
            self.controller.stop()
            self.controller.thread.join(3)
        self.tmp.cleanup()

    def finish(self):
        self.controller.thread.join(3)
        self.assertFalse(self.controller.running)
        events = []
        while not self.controller.events.empty():
            events.append(self.controller.events.get_nowait())
        return events

    def test_worker_emits_completion_without_touching_ui(self):
        executor = AsyncMock(return_value=0)
        self.controller = TaskController(executor)
        self.controller.start(self.config)
        events = self.finish()
        executor.assert_awaited_once()
        self.assertEqual(events[-1], ('finished', {'code': 0, 'error': ''}))

    def test_stop_awaits_cleanup_and_blocks_duplicate_start(self):
        started, cleaned = threading.Event(), threading.Event()
        async def executor(*args):
            try:
                started.set()
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(.03)
                cleaned.set()
        self.controller = TaskController(executor)
        self.controller.start(self.config)
        self.assertTrue(started.wait(2))
        with self.assertRaises(UnsafeState):
            self.controller.start(self.config)
        self.controller.stop()
        self.controller.stop()  # A repeated stop must not cancel cleanup a second time.
        events = self.finish()
        self.assertTrue(cleaned.is_set())
        self.assertEqual(events[-1][1]['code'], 130)
        with RunLock(self.config.profile_dir/'.task.lock'):
            pass

    def test_pending_task_is_rechecked_under_lock(self):
        path = self.config.log_dir/'old'/'status.json'
        StateManager(path, self.config)
        executor = AsyncMock(return_value=0)
        self.controller = TaskController(executor)
        self.controller.start(self.config)  # Stale GUI thought there was no pending run.
        events = self.finish()
        executor.assert_not_awaited()
        self.assertIn('未完成任务已变化', events[-1][1]['error'])

    def test_resume_passes_existing_path_without_abandoning(self):
        path = self.config.log_dir/'old'/'status.json'
        StateManager(path, self.config)
        executor = AsyncMock(return_value=0)
        self.controller = TaskController(executor)
        self.controller.start(self.config, path)
        self.finish()
        self.assertEqual(executor.call_args.args[1], path)
        self.assertEqual(json.loads(path.read_text())['status'], 'WAITING')

    def test_confirmed_restart_archives_before_starting_new_task(self):
        path = self.config.log_dir/'old'/'status.json'
        StateManager(path, self.config)
        executor = AsyncMock(return_value=0)
        self.controller = TaskController(executor)
        self.controller.start(self.config, path, restart=True)
        self.finish()
        self.assertIsNone(executor.call_args.args[1])
        self.assertEqual(json.loads(path.read_text())['status'], 'ABANDONED')

    def test_failure_is_reported_and_releases_lock(self):
        self.controller = TaskController(AsyncMock(side_effect=RuntimeError('fixture error')))
        self.controller.start(self.config)
        events = self.finish()
        self.assertIn('fixture error', events[-1][1]['error'])
        with RunLock(self.config.profile_dir/'.task.lock'):
            pass


class RuntimeStopTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_saves_paused_state_and_closes_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = Config('test', 'https://chatgpt.com/c/test', datetime.now().astimezone(), True,
                            root/'profile', root/'logs', Settings(), [Step('1', 'one', 'hello', 30, 5, 0)])
            started = asyncio.Event()
            session = AsyncMock()
            chat = AsyncMock()
            async def waiting(*args):
                started.set()
                await asyncio.Event().wait()
            chat.wait_accessible.side_effect = waiting
            with patch('app.browser.BrowserSession', return_value=session), patch('app.chatgpt.ChatGPTPage', return_value=chat):
                task = asyncio.create_task(execute(config, None, lambda *_: None))
                await asyncio.wait_for(started.wait(), 2)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            session.__aexit__.assert_awaited_once()
            path = next(config.log_dir.glob('*/status.json'))
            state = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(state['status'], 'PAUSED')
            self.assertFalse(state['steps']['1']['click_intent'])
