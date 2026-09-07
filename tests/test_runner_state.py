import json
import logging
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch
from app.chatgpt import Snapshot, digest
from app.config import Config, Settings, Step
from app.errors import UnsafeState, PageUnavailable, ResponseTimeout
from app.state_manager import StateManager, RunLock, unfinished
from app.task_runner import TaskRunner


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        settings = Settings(stable_wait_seconds=.01, retry_interval_seconds=.01, login_timeout_seconds=.1)
        self.step = Step('1', 'test', 'prompt', 30, .01, 0)
        self.config = Config('test', 'https://chatgpt.com/c/test', datetime.now().astimezone(), True,
                             self.root/'profile', self.root/'logs', settings, [self.step])
        run = self.config.log_dir/'run'
        run.mkdir(parents=True)
        self.state = StateManager(run/'status.json', self.config)
        self.logger = logging.getLogger('test.quiet')
        self.logger.handlers = [logging.NullHandler()]
        self.logger.propagate = False
        self.chat = AsyncMock()
        self.chat.logger = self.logger
        self.before = Snapshot((), (), '', '', True, False, False, True)
        self.chat.wait_ready.return_value = self.before
        self.chat.snapshot.return_value = self.before
        self.runner = TaskRunner(self.config, self.chat, self.state, self.logger)

    def tearDown(self):
        self.tmp.cleanup()

    async def test_click_intent_is_durable_before_click(self):
        async def click():
            data = json.loads(self.state.path.read_text(encoding='utf-8'))
            self.assertTrue(data['steps']['1']['click_intent'])
            self.assertEqual(data['steps']['1']['status'], 'SENDING')
        self.chat.click_send.side_effect = click
        await self.runner.send(self.step)
        self.chat.click_send.assert_awaited_once()

    async def test_uncertain_click_is_never_retried(self):
        self.chat.click_send.side_effect = PageUnavailable('timeout after click')
        self.chat.verify_sent.side_effect = UnsafeState('not confirmed')
        with self.assertRaises(UnsafeState):
            await self.runner.send(self.step)
        self.chat.click_send.assert_awaited_once()
        # Recreate the process state from disk and try recovery.
        resumed = StateManager(self.state.path, self.config, True)
        runner = TaskRunner(self.config, self.chat, resumed, self.logger)
        with self.assertRaises(UnsafeState):
            await runner.send(self.step)
        self.chat.click_send.assert_awaited_once()

    async def test_recovery_of_confirmed_send_never_clicks(self):
        await self.runner.send(self.step)
        self.chat.reset_mock()
        await self.runner.send(self.step)
        self.chat.click_send.assert_not_awaited()
        self.chat.prepare.assert_not_awaited()
        self.chat.verify_sent.assert_awaited_once()

    async def test_pre_click_preparation_can_retry(self):
        self.chat.prepare.side_effect = [PageUnavailable('disabled'), None]
        await self.runner.send(self.step)
        self.assertEqual(self.chat.prepare.await_count, 2)
        self.chat.click_send.assert_awaited_once()

    async def test_changed_conversation_prevents_click(self):
        self.chat.snapshot.return_value = replace(self.before, users=(digest('manual'),))
        with self.assertRaises(UnsafeState):
            await self.runner.send(self.step)
        self.chat.click_send.assert_not_awaited()

    async def test_all_steps_are_serial(self):
        second = replace(self.step, id='2')
        config = replace(self.config, steps=[self.step, second])
        state = StateManager(self.state.path, config)
        runner = TaskRunner(config, self.chat, state, self.logger)
        events = []
        async def send(step):
            events.append('send'+step.id)
            state.update(step.id, 'WAITING_RESPONSE', sent_at=datetime.now().astimezone().isoformat())
            return {}
        async def monitor(step, baseline, timeout):
            events.append('done'+step.id)
        runner.send = send
        runner.monitor = monitor
        self.assertTrue(await runner.run())
        self.assertEqual(events, ['send1', 'done1', 'send2', 'done2'])

    async def test_timeout_does_not_send_next_step_even_with_continue(self):
        config = replace(self.config, settings=replace(self.config.settings, continue_on_error=True),
                         steps=[self.step, replace(self.step, id='2')])
        state = StateManager(self.state.path, config)
        runner = TaskRunner(config, self.chat, state, self.logger)
        runner.monitor = AsyncMock(side_effect=ResponseTimeout('still running'))
        self.assertFalse(await runner.run())
        self.chat.click_send.assert_awaited_once()
        self.assertEqual(state.data['steps']['2']['status'], 'WAITING')
        self.assertEqual(state.data['status'], 'PAUSED')

    async def test_completed_step_is_skipped(self):
        self.state.update('1', 'COMPLETED')
        self.assertTrue(await self.runner.run())
        self.chat.click_send.assert_not_awaited()

    def test_config_change_rejects_recovery(self):
        with self.assertRaises(UnsafeState):
            StateManager(self.state.path, replace(self.config, steps=[replace(self.step, prompt='changed')]), True)

    def test_unfinished_finds_changed_config_same_chat(self):
        changed = replace(self.config, steps=[replace(self.step, prompt='changed')])
        self.assertEqual(unfinished(changed), [self.state.path])

    def test_invalid_state_file_blocks_new_run(self):
        self.state.path.write_text('{broken', encoding='utf-8')
        with self.assertRaises(UnsafeState):
            unfinished(self.config)

    def test_changing_log_directory_still_finds_active_run(self):
        changed = replace(self.config, log_dir=self.root/'other_logs')
        self.assertEqual(unfinished(changed), [self.state.path])

    def test_missing_indexed_status_blocks_new_run(self):
        self.state.path.unlink()
        with self.assertRaises(UnsafeState):
            unfinished(self.config)

    def test_profile_lock_excludes_second_process_and_releases(self):
        path = self.root/'lock'
        with RunLock(path):
            with self.assertRaises(UnsafeState):
                with RunLock(path):
                    pass
        with RunLock(path):
            pass
