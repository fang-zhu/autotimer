import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
import yaml
from app.config import load_config
from app.scheduler import remaining_seconds, wait_for_start
from unittest.mock import patch


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = dict(task_name='中文', chat_url='https://chatgpt.com/c/test',
                         start_time='2026-09-08 08:30:00', steps=[{'prompt': '你好'}])

    def tearDown(self):
        self.tmp.cleanup()

    def load(self):
        path = self.root / 'config.yaml'
        path.write_text(yaml.safe_dump(self.data, allow_unicode=True), encoding='utf-8')
        return load_config(path)

    def test_defaults_and_chinese(self):
        config = self.load()
        self.assertEqual(config.steps[0].prompt, '你好')
        self.assertEqual(config.settings.retry_count, 3)
        self.assertIsNotNone(config.start_time.tzinfo)

    def test_prompt_file_resolves_relative_to_config(self):
        (self.root / '提示词.txt').write_text('中文\n第二行', encoding='utf-8-sig')
        self.data['steps'] = [{'prompt_file': '提示词.txt', 'timeout_seconds': 3600}]
        config = self.load()
        self.assertEqual(config.steps[0].prompt, '中文\n第二行')
        self.assertEqual(config.steps[0].timeout_seconds, 3600)

    def test_rejects_ambiguous_prompt(self):
        self.data['steps'][0]['prompt_file'] = 'x.txt'
        with self.assertRaises(ValueError):
            self.load()

    def test_rejects_duplicate_ids(self):
        self.data['steps'] = [{'id': 1, 'prompt': 'a'}, {'id': 1, 'prompt': 'b'}]
        with self.assertRaises(ValueError):
            self.load()

    def test_rejects_bad_settings(self):
        for setting in ({'retry_count': -1}, {'stable_wait_seconds': 0},
                        {'response_timeout_seconds': float('nan')}, {'continue_on_error': 'false'},
                        {'retry_count': True}, {'response_timeout_second': 3}):
            with self.subTest(setting=setting):
                self.data['settings'] = setting
                with self.assertRaises(ValueError):
                    self.load()

    def test_rejects_external_url_and_missing_conversation(self):
        for url in ('https://evil.example/c/test', 'https://chatgpt.com/', 'http://chatgpt.com/c/x', 'https://chatgpt.com.evil.com/c/x'):
            self.data['chat_url'] = url
            with self.assertRaises(ValueError):
                self.load()

    def test_fingerprint_tracks_content_not_schedule(self):
        first = self.load().fingerprint
        self.data['start_time'] = '2027-01-01 08:30:00'
        self.assertEqual(first, self.load().fingerprint)
        self.data['steps'][0]['prompt'] = '不同内容'
        self.assertNotEqual(first, self.load().fingerprint)

    def test_absolute_windows_paths(self):
        self.data['log_dir'] = str(self.root / '带空格 logs')
        self.assertEqual(self.load().log_dir, self.root / '带空格 logs')

    def test_many_steps_and_per_step_rules(self):
        self.data['steps'] = [{'prompt': str(i), 'stable_wait_seconds': 8, 'delay_after_response_seconds': 0} for i in range(50)]
        self.assertEqual(len(self.load().steps), 50)
        self.assertEqual(self.load().steps[-1].stable_wait_seconds, 8)


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    def test_cross_midnight(self):
        now = datetime(2026, 9, 7, 23, 59, 50, tzinfo=timezone(timedelta(hours=8)))
        self.assertEqual(remaining_seconds(now + timedelta(seconds=20), now), 20)

    def test_past_target(self):
        now = datetime.now().astimezone()
        self.assertEqual(remaining_seconds(now - timedelta(days=1), now), 0)

    async def test_immediate_ignores_future_start(self):
        from types import SimpleNamespace
        config = SimpleNamespace(start_time=datetime.now().astimezone()+timedelta(days=2), start_immediately=True)
        with patch('app.scheduler.asyncio.sleep') as sleep:
            await wait_for_start(config, lambda _: None)
            sleep.assert_not_called()
