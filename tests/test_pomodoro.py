import os
import tempfile
import unittest
import tkinter as tk

import pomodoro


class PomodoroTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = pomodoro.PomodoroApp(self.root)

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass

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
        self.app._on_mute_toggle_via_var(True)
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
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        tmp.close()
        old = pomodoro.STATE_FILE
        pomodoro.STATE_FILE = tmp.name
        try:
            self.app.total = 1234
            self.app.remaining = 567
            self.app.volume = 42
            self.app.muted = True
            self.app.vol_scale.set(42)
            self.app.mute_var.set(True)
            self.app._save_state()
            # new app
            root2 = tk.Tk()
            root2.withdraw()
            app2 = pomodoro.PomodoroApp(root2)
            self.assertEqual(app2.total, 1234)
            self.assertEqual(app2.remaining, 567)
            self.assertEqual(app2.volume, 42)
            self.assertTrue(app2.muted)
            root2.destroy()
        finally:
            pomodoro.STATE_FILE = old
            os.remove(tmp.name)

    def test_translucent_when_pinned(self):
        self.app.root.attributes("-topmost", True)
        self.app._apply_alpha()
        self.assertAlmostEqual(float(self.app.root.attributes("-alpha")), 0.8, places=2)

    def test_opaque_when_unpinned(self):
        self.app.root.attributes("-topmost", False)
        self.app._apply_alpha()
        self.assertAlmostEqual(float(self.app.root.attributes("-alpha")), 1.0, places=2)


# helper to toggle mute via var (mirrors checkbutton)
PomodoroApp = pomodoro.PomodoroApp
def _on_mute_toggle_via_var(self, val):
    self.mute_var.set(val)
    self._on_mute()
PomodoroApp._on_mute_toggle_via_var = _on_mute_toggle_via_var


if __name__ == "__main__":
    unittest.main()