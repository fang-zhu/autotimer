import json
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
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
