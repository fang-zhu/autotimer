import tempfile
import unittest
from pathlib import Path
import yaml
from app.gui_config import PromptDraft, read_draft, save_draft


class GuiConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root/'config.yaml'
        (self.root/'prompt.txt').write_text('original 中文', encoding='utf-8')
        self.raw = {'task_name': 'test', 'chat_url': 'https://chatgpt.com/c/test',
                    'start_time': '2026-09-08 08:30:00', 'steps': [{'id': 7, 'name': 'first', 'prompt_file': 'prompt.txt'}]}
        self.path.write_text(yaml.safe_dump(self.raw), encoding='utf-8')

    def tearDown(self):
        self.tmp.cleanup()

    def test_unchanged_external_prompt_keeps_file_reference(self):
        raw, config, steps = read_draft(self.path)
        raw['steps'] = [steps[0].to_mapping()]
        saved = save_draft(self.path, raw)
        self.assertEqual(saved.fingerprint, config.fingerprint)
        self.assertEqual(raw['steps'][0]['prompt_file'], 'prompt.txt')

    def test_edited_external_prompt_is_inline_and_source_preserved(self):
        raw, _, steps = read_draft(self.path)
        steps[0].prompt = 'edited 中文'
        raw['steps'] = [steps[0].to_mapping()]
        config = save_draft(self.path, raw)
        self.assertEqual(config.steps[0].prompt, 'edited 中文')
        self.assertEqual((self.root/'prompt.txt').read_text(encoding='utf-8'), 'original 中文')
        self.assertNotIn('prompt_file', raw['steps'][0])

    def test_invalid_draft_does_not_replace_original(self):
        old = self.path.read_bytes()
        self.raw['chat_url'] = 'bad'
        with self.assertRaises(ValueError):
            save_draft(self.path, self.raw)
        self.assertEqual(self.path.read_bytes(), old)
        self.assertFalse(self.path.with_suffix('.yaml.bak').exists())

    def test_save_keeps_previous_configuration_backup(self):
        old = self.path.read_bytes()
        self.raw['start_time'] = '2026-09-09 09:30:00'
        save_draft(self.path, self.raw)
        self.assertEqual(self.path.with_suffix('.yaml.bak').read_bytes(), old)

    def test_reordering_preserves_stable_step_ids(self):
        a, b = PromptDraft('7', 'a', 'hello'), PromptDraft('2', 'b', 'world')
        self.raw['steps'] = [b.to_mapping(), a.to_mapping()]
        config = save_draft(self.path, self.raw)
        self.assertEqual([s.id for s in config.steps], ['2', '7'])

    def test_empty_override_uses_global_value(self):
        self.raw['settings'] = {'response_timeout_seconds': 123}
        self.raw['steps'] = [PromptDraft('1', 'a', 'hello').to_mapping()]
        self.assertEqual(save_draft(self.path, self.raw).steps[0].timeout_seconds, 123)

    def test_non_numeric_override_is_rejected(self):
        with self.assertRaises(ValueError):
            PromptDraft('1', 'a', 'hello', timeout='oops').to_mapping()
