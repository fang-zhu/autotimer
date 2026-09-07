import unittest
from dataclasses import replace
from app.chatgpt import Snapshot, digest, match_sent
from app.errors import UnsafeState
from app.response_monitor import CompletionGate


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.baseline = {'users': [digest('old')], 'assistants': [digest('old answer')]}
        self.state = Snapshot((digest('old'), digest('new')), (digest('old answer'), digest('answer')),
                              'answer', '', True, False, True, True)

    def test_requires_full_stability_period(self):
        gate = CompletionGate(5)
        self.assertFalse(gate.observe(self.state, self.baseline, 'new', 0))
        self.assertFalse(gate.observe(self.state, self.baseline, 'new', 4.9))
        self.assertTrue(gate.observe(self.state, self.baseline, 'new', 5))

    def test_stream_pause_cannot_finish_while_stop_visible(self):
        gate = CompletionGate(5)
        state = replace(self.state, busy=True)
        self.assertFalse(gate.observe(state, self.baseline, 'new', 0))
        self.assertFalse(gate.observe(state, self.baseline, 'new', 100))

    def test_no_end_action_no_completion(self):
        gate = CompletionGate(5)
        state = replace(self.state, done=False)
        self.assertFalse(gate.observe(state, self.baseline, 'new', 0))
        self.assertFalse(gate.observe(state, self.baseline, 'new', 100))

    def test_old_answer_never_satisfies_new_turn(self):
        gate = CompletionGate(5)
        state = replace(self.state, assistants=tuple(self.baseline['assistants']), last_text='old answer')
        self.assertFalse(gate.observe(state, self.baseline, 'new', 100))

    def test_content_change_resets_stability(self):
        gate = CompletionGate(5)
        gate.observe(self.state, self.baseline, 'new', 0)
        state = replace(self.state, assistants=(digest('old answer'), digest('longer answer')), last_text='longer answer')
        self.assertFalse(gate.observe(state, self.baseline, 'new', 5))
        self.assertFalse(gate.observe(state, self.baseline, 'new', 9))
        self.assertTrue(gate.observe(state, self.baseline, 'new', 10))

    def test_offline_disabled_error_draft_reset(self):
        for change in ({'online': False}, {'editable': False}, {'error': 'Network error'}, {'composer_text': 'draft'}, {'busy': True}):
            with self.subTest(change=change):
                gate = CompletionGate(5)
                gate.observe(self.state, self.baseline, 'new', 0)
                self.assertFalse(gate.observe(replace(self.state, **change), self.baseline, 'new', 4))
                self.assertFalse(gate.observe(self.state, self.baseline, 'new', 6))
                self.assertTrue(gate.observe(self.state, self.baseline, 'new', 11))

    def test_reload_empty_history_resets(self):
        gate = CompletionGate(5)
        gate.observe(self.state, self.baseline, 'new', 0)
        self.assertFalse(gate.observe(replace(self.state, users=(), assistants=()), self.baseline, 'new', 4))
        self.assertFalse(gate.observe(self.state, self.baseline, 'new', 10))

    def test_unexpected_manual_message_stops(self):
        with self.assertRaises(UnsafeState):
            CompletionGate(5).observe(replace(self.state, users=self.state.users+(digest('manual'),)), self.baseline, 'new', 0)

    def test_old_identical_prompt_is_not_new_send(self):
        baseline = {'users': list(self.state.users), 'assistants': []}
        self.assertFalse(match_sent(self.state, baseline, 'new'))
        self.assertTrue(match_sent(replace(self.state, users=self.state.users+(digest('new'),)), baseline, 'new'))

    def test_verification_requires_cleared_editor_and_matching_prompt(self):
        self.assertTrue(match_sent(self.state, self.baseline, 'new'))
        self.assertFalse(match_sent(replace(self.state, composer_text='new'), self.baseline, 'new'))
        self.assertFalse(match_sent(self.state, self.baseline, 'different'))

    def test_indentation_is_not_discarded(self):
        self.assertNotEqual(digest('a\n  b'), digest('a\nb'))
