import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.paths import project_root


class RuntimePathTests(unittest.TestCase):
    def test_frozen_runtime_uses_executable_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / 'ChatGPTWebAutomation.exe'
            with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', str(executable)):
                self.assertEqual(project_root(), Path(directory).resolve())

    def test_source_runtime_uses_project_directory(self):
        with patch.object(sys, 'frozen', False, create=True):
            self.assertEqual(project_root(), Path(__file__).resolve().parents[1])
