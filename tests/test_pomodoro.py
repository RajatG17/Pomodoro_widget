import json
import tempfile
import unittest
import tkinter as tk
from pathlib import Path
from unittest.mock import patch

import pomodoro


class PomodoroTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state_file = Path(self.directory.name) / "state.json"
        state_patch = patch.object(pomodoro, "STATE_FILE", str(self.state_file))
        state_patch.start()
        self.addCleanup(state_patch.stop)
        self.root = tk.Tk()
        self.addCleanup(self._destroy_root, self.root)
        self.root.withdraw()
        self.app = pomodoro.PomodoroApp(self.root)

    @staticmethod
    def _destroy_root(root):
        # Cancel pending Tk callbacks before destroying their Tcl commands.
        for job in root.tk.call("after", "info"):
            root.after_cancel(job)
        root.destroy()

    def test_fmt_mm_ss(self):
        self.assertEqual(self.app.fmt(0), "00:00")
        self.assertEqual(self.app.fmt(65), "01:05")
        self.assertEqual(self.app.fmt(3600), "1:00:00")

    def test_add_time_positive(self):
        self.app.total = 100
        self.app.remaining = 40
        self.app.add_time(60)
        self.assertEqual(self.app.remaining, 100)
        self.assertEqual(self.app.total, 160)

    def test_add_time_negative_clamps(self):
        self.app.total = 100
        self.app.remaining = 10
        self.app.add_time(-600)
        self.assertEqual(self.app.remaining, 0)
        self.assertGreaterEqual(self.app.total, self.app.remaining)

    def test_set_duration_when_stopped(self):
        self.app.running = False
        self.app.set_duration(300)
        self.assertEqual(self.app.total, 300)
        self.assertEqual(self.app.remaining, 300)

    def test_set_duration_when_running_ignored(self):
        self.app.running = True
        self.app.total = 100
        self.app.remaining = 50
        self.app.set_duration(300)
        self.assertEqual(self.app.total, 100)

    def test_volume_mute(self):
        self.app._on_volume(50)
        self.assertFalse(self.app.muted)
        self.assertEqual(self.app.volume, 50)
        self.app.mute_var.set(True)
        self.app._on_mute()
        self.assertTrue(self.app.muted)
        self.assertEqual(self.app.volume, 0)

    def test_tone_bytes_respects_mute(self):
        self.app.muted = True
        self.assertIsNone(self.app._tone_bytes(440, 100))
        self.app.muted = False
        self.app.volume = 50
        data = self.app._tone_bytes(440, 100)
        self.assertIsNotNone(data)
        self.assertGreater(len(data), 44)

    def test_persistence_roundtrip(self):
        self.app.total = 1234
        self.app.remaining = 567
        self.app.volume = 42
        self.app.muted = True
        self.app.vol_scale.set(42)
        self.app.mute_var.set(True)
        self.app._save_state()
        root2 = tk.Tk()
        self.addCleanup(self._destroy_root, root2)
        root2.withdraw()
        app2 = pomodoro.PomodoroApp(root2)
        self.assertEqual(app2.total, 1234)
        self.assertEqual(app2.remaining, 567)
        self.assertEqual(app2.volume, 42)
        self.assertTrue(app2.muted)

    def test_translucent_when_pinned(self):
        self.app.root.attributes("-topmost", True)
        self.app._apply_alpha()
        self.assertAlmostEqual(float(self.app.root.attributes("-alpha")), 0.8, places=2)

    def test_opaque_when_unpinned(self):
        self.app.root.attributes("-topmost", False)
        self.app._apply_alpha()
        self.assertAlmostEqual(float(self.app.root.attributes("-alpha")), 1.0, places=2)


class TestIsolationTests(unittest.TestCase):
    def test_state_writing_tests_leave_existing_settings_untouched(self):
        # A sentinel stands in for user settings; never touch the real file,
        # even while reproducing the original test-isolation bug.
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "user-state.json"
            original = json.dumps({"total": 2100, "remaining": 900,
                                   "volume": 83, "geometry": "290x300+120+120"})
            state_file.write_text(original, encoding="utf-8")
            suite = unittest.TestSuite(PomodoroTests(name) for name in (
                "test_add_time_positive", "test_volume_mute",
                "test_set_duration_when_stopped", "test_persistence_roundtrip",
            ))
            result = unittest.TestResult()
            with patch.object(pomodoro, "STATE_FILE", str(state_file)):
                suite.run(result)
            self.assertTrue(result.wasSuccessful(), result.errors + result.failures)
            self.assertEqual(state_file.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
