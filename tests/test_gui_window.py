import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch
import yaml
from app.gui import AutomationWindow
from app.gui_controller import TaskController


class WindowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'config.yaml'
        self.path.write_text(yaml.safe_dump({'task_name': 'window test', 'chat_url': 'https://chatgpt.com/c/test',
            'start_immediately': True, 'steps': [{'id': 1, 'name': 'one', 'prompt': 'first'},
                                               {'id': 2, 'name': 'two', 'prompt': 'second'}]}), encoding='utf-8')
        self.root = tk.Tk()
        self.root.withdraw()
        self.window = AutomationWindow(self.root, self.path)
        self.root.update()

    def tearDown(self):
        self.root.destroy()
        self.tmp.cleanup()

    def test_switching_steps_keeps_pending_edits(self):
        self.window.prompt_text.delete('1.0', 'end')
        self.window.prompt_text.insert('1.0', 'edited first')
        self.window.tree.selection_set('1')
        self.root.update()
        self.assertEqual(self.window.steps[0].prompt, 'edited first')
        self.assertEqual(self.window.prompt_text.get('1.0', 'end-1c'), 'second')

    def test_reorder_and_save_keep_ids_and_content(self):
        self.window.move_step(1)
        config = self.window.save(silent=True)
        self.assertEqual([s.id for s in config.steps], ['2', '1'])
        self.assertEqual([s.prompt for s in config.steps], ['second', 'first'])

    def test_add_delete_and_selection(self):
        self.window.add_step()
        self.assertEqual(len(self.window.steps), 3)
        self.assertEqual(self.window.selected, 2)
        with patch('app.gui.messagebox.askyesno', return_value=True):
            self.window.delete_step()
        self.assertEqual(len(self.window.steps), 2)
        self.assertEqual(self.window.selected, 1)

    def test_running_locks_form_and_allows_stop(self):
        self.window._set_active(True)
        self.assertEqual(str(self.window.start_button['state']), 'disabled')
        self.assertEqual(str(self.window.stop_button['state']), 'normal')
        self.assertEqual(str(self.window.prompt_text['state']), 'disabled')
        self.window._set_active(False)
        self.assertEqual(str(self.window.start_button['state']), 'normal')

    def test_invalid_url_does_not_launch_worker_or_overwrite_config(self):
        original = self.path.read_bytes()
        self.window.vars['chat_url'].set('')
        with patch('app.gui.messagebox.showerror') as error, patch.object(self.window.controller, 'start') as start:
            self.window.start()
        error.assert_called_once()
        start.assert_not_called()
        self.assertEqual(self.path.read_bytes(), original)

    def test_live_state_queue_updates_steps_and_counts(self):
        self.window.controller.events.put(('state', {'steps': {'1': {'name': 'one', 'status': 'COMPLETED'},
            '2': {'name': 'two', 'status': 'WAITING_RESPONSE'}}, 'current_step': '2'}))
        self.window._drain()
        self.assertEqual(self.window.tree.set('0', 'status'), '已完成')
        self.assertIn('等待回答', self.window.phase.get())
        self.assertIn('成功 1', self.window.summary.get())

    def test_changed_external_file_requires_reload_before_sending(self):
        prompt_file = self.path.parent/'prompt.txt'
        prompt_file.write_text('first', encoding='utf-8')
        draft = self.window.steps[0]
        draft.source_file, draft.source_prompt = str(prompt_file), 'first'
        prompt_file.write_text('unexpected external edit', encoding='utf-8')
        with patch('app.gui.messagebox.showerror') as error:
            self.assertIsNone(self.window.save(silent=True))
        self.assertIn('发生变化', error.call_args.args[1])
