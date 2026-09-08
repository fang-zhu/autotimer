import json
import queue
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock, patch
from app.mouse_recording import MouseEvent, MouseRecording, save_recording, load_recording, validate
from app.mouse_controller import replay, MouseController

SCREEN = (0, 0, 1920, 1080, 96)


def example():
    return MouseRecording(SCREEN, (MouseEvent(0, 'move', 10, 20), MouseEvent(0, 'click', 10, 20, 'left', True),
                                  MouseEvent(0, 'move', 30, 40), MouseEvent(0, 'click', 30, 40, 'left', False),
                                  MouseEvent(0, 'scroll', 30, 40, dx=0, dy=-2)), 0)


class RecordingTests(unittest.TestCase):
    def test_round_trip_preserves_all_events(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'鼠标.json'
            save_recording(path, example())
            self.assertEqual(load_recording(path), example())

    def test_invalid_write_keeps_existing_recording(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'mouse.json'
            save_recording(path, example())
            original = path.read_bytes()
            with self.assertRaises(ValueError):
                save_recording(path, replace(example(), events=()))
            self.assertEqual(path.read_bytes(), original)

    def test_file_cannot_contain_executable_or_unknown_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'bad.json'
            path.write_text(json.dumps({'version': 1, 'screen': SCREEN, 'duration': 0, 'events': [{'at': 0, 'kind': 'move', 'x': 1, 'y': 1, 'command': 'run'}]}))
            with self.assertRaises(ValueError):
                load_recording(path)

    def test_unbalanced_button_is_rejected(self):
        with self.assertRaises(ValueError):
            validate(MouseRecording(SCREEN, (MouseEvent(0,'click',1,1,'left',True),), 1))

    def test_out_of_bounds_and_non_finite_times_are_rejected(self):
        for event in (MouseEvent(0,'move',3000,1), MouseEvent(float('nan'),'move',1,1), MouseEvent(-1,'move',1,1)):
            with self.subTest(event=event), self.assertRaises(ValueError):
                validate(MouseRecording(SCREEN, (event,), 1))

    def test_reversed_timing_and_unsupported_keys_are_rejected(self):
        for events in ((MouseEvent(1,'move',1,1),MouseEvent(0,'move',2,2)), (MouseEvent(0,'keyboard',1,1),)):
            with self.assertRaises(ValueError):
                validate(MouseRecording(SCREEN, events, 1))

    def test_negative_virtual_screen_coordinates_are_supported(self):
        validate(MouseRecording((-1920,0,3840,1080,96), (MouseEvent(0,'move',-100,10),), 0))


class ReplayTests(unittest.TestCase):
    def test_replays_click_drag_and_wheel(self):
        controller = Mock()
        replay(example(), controller, {'left':'L'}, threading.Event(), screen=lambda: SCREEN)
        controller.press.assert_called_once_with('L')
        controller.release.assert_called_once_with('L')
        controller.scroll.assert_called_once_with(0,-2)
        self.assertEqual(controller.position, (30,40))

    def test_screen_mismatch_causes_no_input(self):
        controller = Mock()
        with self.assertRaises(ValueError):
            replay(example(), controller, {'left':'L'}, threading.Event(), screen=lambda:(0,0,800,600,96))
        self.assertEqual(controller.mock_calls, [])

    def test_stop_releases_held_button_and_skips_remaining_events(self):
        controller = Mock()
        cancel = threading.Event()
        controller.press.side_effect = lambda _: cancel.set()
        replay(example(), controller, {'left':'L'}, cancel, screen=lambda:SCREEN)
        controller.release.assert_called_once_with('L')
        controller.scroll.assert_not_called()

    def test_error_releases_button(self):
        controller = Mock()
        controller.press.side_effect = RuntimeError('input error')
        with self.assertRaises(RuntimeError):
            replay(example(), controller, {'left':'L'}, threading.Event(), screen=lambda:SCREEN)
        controller.release.assert_called_once_with('L')

    def test_cancel_interrupts_long_wait_without_input(self):
        controller = Mock()
        cancel = threading.Event()
        cancel.set()
        recording = MouseRecording(SCREEN, (MouseEvent(100,'move',1,1),),100)
        replay(recording, controller, {}, cancel, screen=lambda:SCREEN)
        self.assertEqual(controller.mock_calls, [])

    def test_cancel_during_countdown_preserves_previous_recording(self):
        controller = MouseController()
        controller.recording = example()
        controller.start('record')
        controller.stop()
        controller.thread.join(3)
        self.assertFalse(controller.running)
        self.assertEqual(controller.recording, example())

    def test_empty_replay_is_rejected(self):
        with self.assertRaises(ValueError):
            MouseController().start('replay')

    def test_replay_options_reject_invalid_values(self):
        controller = MouseController()
        controller.recording = example()
        for count, interval in ((0, 0), (1001, 0), (1, -1), (1, 86401), (True, 0)):
            with self.subTest(count=count, interval=interval), self.assertRaises(ValueError):
                controller.start('replay', count, interval)

    def test_replay_runs_requested_cycles_and_interval(self):
        controller = MouseController()
        controller.recording = example()
        fake_mouse = ModuleType('pynput.mouse')
        fake_mouse.Controller = lambda: Mock()
        fake_mouse.Button = type('Button', (), {'left': 'L', 'right': 'R', 'middle': 'M'})
        fake_pynput = ModuleType('pynput')
        fake_pynput.mouse = fake_mouse
        waits = []
        controller.cancel.wait = lambda timeout: waits.append(timeout) or False
        with patch.dict(sys.modules, {'pynput': fake_pynput, 'pynput.mouse': fake_mouse}), \
                patch('app.mouse_controller.replay') as one_replay:
            # Exercise the cycle configuration through the worker without using
            # a real mouse device or sleeping for the configured interval.
            controller._run('replay', 3, 0.4)
        self.assertEqual(one_replay.call_count, 3)
        interval_events = []
        while True:
            try:
                kind, value = controller.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'interval':
                interval_events.append(value)
        self.assertEqual([round(item[2], 1) for item in interval_events], [0.4, 0.2, 0.4, 0.2])
        self.assertEqual(waits, [1, 1, 1, 0.2, 0.2, 0.2, 0.2])

    def test_cancel_during_between_cycle_interval_skips_remaining_cycles(self):
        controller = MouseController()
        controller.recording = example()
        fake_mouse = ModuleType('pynput.mouse')
        fake_mouse.Controller = lambda: Mock()
        fake_mouse.Button = type('Button', (), {'left': 'L', 'right': 'R', 'middle': 'M'})
        fake_pynput = ModuleType('pynput')
        fake_pynput.mouse = fake_mouse
        calls = []
        def cancel_after_first(*args, **kwargs):
            calls.append(True)
            controller.cancel.set()
        with patch.dict(sys.modules, {'pynput': fake_pynput, 'pynput.mouse': fake_mouse}), \
                patch('app.mouse_controller.replay', side_effect=cancel_after_first):
            controller._run('replay', 3, 10)
        self.assertEqual(len(calls), 1)

    def test_long_interval_does_not_flood_progress_queue(self):
        controller = MouseController()
        controller.recording = example()
        fake_mouse = ModuleType('pynput.mouse')
        fake_mouse.Controller = lambda: Mock()
        fake_mouse.Button = type('Button', (), {'left': 'L', 'right': 'R', 'middle': 'M'})
        fake_pynput = ModuleType('pynput')
        fake_pynput.mouse = fake_mouse
        controller.cancel.wait = lambda timeout: False
        with patch.dict(sys.modules, {'pynput': fake_pynput, 'pynput.mouse': fake_mouse}), \
                patch('app.mouse_controller.replay'):
            controller._run('replay', 2, 12)
        interval_events = []
        while True:
            try:
                kind, value = controller.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'interval':
                interval_events.append(value)
        self.assertEqual([item[2] for item in interval_events], list(range(12, 0, -1)))

    def test_recording_drag_stopped_mid_press_releases_at_last_position(self):
        controller = MouseController()
        from types import SimpleNamespace
        class FakeListener:
            def __init__(self, **callbacks):
                self.callbacks = callbacks
            def start(self):
                self.callbacks['on_click'](10,20,SimpleNamespace(name='left'),True)
                self.callbacks['on_move'](100,200)
                controller.stop()
            def stop(self):
                pass
            def join(self, timeout):
                pass
        with patch('app.mouse_controller.screen_signature', return_value=SCREEN):
            controller._record(SimpleNamespace(Listener=FakeListener))
        validate(controller.recording)
        last = controller.recording.events[-1]
        self.assertEqual((last.kind, last.pressed, last.x, last.y), ('click',False,100,200))

    def test_listener_join_error_preserves_already_recorded_events(self):
        from types import SimpleNamespace
        controller = MouseController()
        class Listener:
            def __init__(self, **callbacks):
                self.callbacks = callbacks
            def start(self):
                self.callbacks['on_move'](20,30)
                controller.stop()
            def stop(self):
                pass
            def join(self, timeout):
                raise RuntimeError('callback failed during shutdown')
        with patch('app.mouse_controller.screen_signature', return_value=SCREEN):
            with self.assertRaisesRegex(RuntimeError, 'callback failed'):
                controller._record(SimpleNamespace(Listener=Listener))
        validate(controller.recording)
        self.assertEqual(len(controller.recording.events), 1)
        self.assertEqual(controller.events.get_nowait()[0], 'phase')
        self.assertEqual(controller.events.get_nowait()[0], 'recorded')

    def test_listener_stop_error_still_joins_and_preserves_events(self):
        from types import SimpleNamespace
        controller = MouseController()
        joined = []
        class Listener:
            def __init__(self, **callbacks):
                self.callbacks = callbacks
            def start(self):
                self.callbacks['on_move'](20,30)
                controller.stop()
            def stop(self):
                raise RuntimeError('stop failed')
            def join(self, timeout):
                joined.append(True)
        with patch('app.mouse_controller.screen_signature', return_value=SCREEN):
            with self.assertRaisesRegex(RuntimeError, 'stop failed'):
                controller._record(SimpleNamespace(Listener=Listener))
        self.assertTrue(joined)
        validate(controller.recording)

    def test_changed_screen_does_not_publish_unsafe_recording(self):
        from types import SimpleNamespace
        controller = MouseController()
        old = example()
        controller.recording = old
        class Listener:
            def __init__(self, **callbacks):
                self.callbacks = callbacks
            def start(self):
                self.callbacks['on_move'](20,30)
                controller.stop()
            def stop(self):
                pass
            def join(self, timeout):
                pass
        with patch('app.mouse_controller.screen_signature', side_effect=[SCREEN,(0,0,800,600,96)]):
            with self.assertRaisesRegex(ValueError, '屏幕设置变化'):
                controller._record(SimpleNamespace(Listener=Listener))
        self.assertIs(controller.recording, old)
