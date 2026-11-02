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

    def test_startup_is_opaque_even_when_pinned(self):
        self.assertAlmostEqual(float(self.root.attributes("-alpha")), 1.0, places=2)

    def test_mapping_window_raises_it_and_starts_visibility_grace_period(self):
        with patch.object(self.root, "lift") as lift:
            self.root.deiconify()
            self.root.update()
        lift.assert_called()
        self.assertAlmostEqual(float(self.root.attributes("-alpha")), 1.0, places=2)
        self.assertIsNotNone(self.app._opaque_after)

    def test_opacity_timeout_does_not_fade_focused_window(self):
        self.app._on_click()
        with patch.object(self.root, "focus_displayof", return_value=self.app.start_btn):
            self.app._revert_alpha()
        self.assertAlmostEqual(float(self.root.attributes("-alpha")), 1.0, places=2)

    def test_opacity_timeout_fades_inactive_pinned_window(self):
        self.app._on_click()
        with patch.object(self.root, "focus_displayof", return_value=None):
            self.app._revert_alpha()
        self.assertAlmostEqual(float(self.root.attributes("-alpha")), 0.8, places=2)

    def test_clicking_child_widget_makes_window_opaque(self):
        self.root.deiconify()
        self.root.update()
        self.root.attributes("-alpha", 0.8)
        self.app.time_label.event_generate("<Button-1>")
        self.assertAlmostEqual(float(self.root.attributes("-alpha")), 1.0, places=2)

    def test_focus_in_makes_window_opaque(self):
        self.root.attributes("-alpha", 0.8)
        self.root.event_generate("<FocusIn>")
        self.assertAlmostEqual(float(self.root.attributes("-alpha")), 1.0, places=2)

    def test_unpin_cancels_pending_fade(self):
        self.app._on_click()
        job = self.app._opaque_after
        self.app.toggle_topmost()
        self.assertIsNone(self.app._opaque_after)
        self.assertNotIn(job, self.root.tk.call("after", "info"))
        self.assertAlmostEqual(float(self.root.attributes("-alpha")), 1.0, places=2)

    def test_focus_out_replaces_pending_fade_timer(self):
        self.app._on_click()
        original_job = self.app._opaque_after
        self.root.event_generate("<FocusOut>")
        self.assertIsNotNone(self.app._opaque_after)
        self.assertNotEqual(original_job, self.app._opaque_after)
        self.assertNotIn(original_job, self.root.tk.call("after", "info"))

    def _app_from_state(self, state, scaling=None):
        self.state_file.write_text(json.dumps(state), encoding="utf-8")
        root = tk.Tk()
        self.addCleanup(self._destroy_root, root)
        root.withdraw()
        if scaling is not None:
            original = root.tk.call("tk", "scaling")
            root.tk.call("tk", "scaling", scaling)
            self.addCleanup(root.tk.call, "tk", "scaling", original)
        return pomodoro.PomodoroApp(root)

    def _assert_controls_fit(self, app):
        def check(widget):
            children = widget.winfo_children()
            if not children:
                self.assertTrue(widget.winfo_ismapped(), f"Hidden control: {widget}")
                x = widget.winfo_rootx() - app.root.winfo_rootx()
                y = widget.winfo_rooty() - app.root.winfo_rooty()
                self.assertGreaterEqual(x, 0, str(widget))
                self.assertGreaterEqual(y, 0, str(widget))
                self.assertLessEqual(x + widget.winfo_width(), app.root.winfo_width(), str(widget))
                self.assertLessEqual(y + widget.winfo_height(), app.root.winfo_height(), str(widget))
                if isinstance(widget, (tk.Button, tk.Checkbutton, tk.Scale, tk.Label)):
                    self.assertGreaterEqual(widget.winfo_width(), widget.winfo_reqwidth(), str(widget))
                    self.assertGreaterEqual(widget.winfo_height(), widget.winfo_reqheight(), str(widget))
            for child in children:
                check(child)

        app.root.deiconify()
        app.root.update()
        for child in app.root.winfo_children():
            if child is not app.notebook:
                check(child)
        for tab in app.notebook.tabs():
            app.notebook.select(tab)
            app.root.update()
            check(app.root.nametowidget(tab))

    def test_all_timer_and_music_controls_fit_default_window(self):
        self._assert_controls_fit(self.app)

    def test_all_controls_fit_at_larger_display_scaling(self):
        app = self._app_from_state({"geometry": "290x300+120+120"}, scaling=2.0)
        self._assert_controls_fit(app)

    def test_minimum_size_prevents_clipping_after_forced_resize(self):
        self.root.deiconify()
        self.root.geometry("1x1+120+120")
        self.root.update()
        self.assertGreaterEqual(self.root.winfo_width(), self.app._minimum_width)
        self.assertGreaterEqual(self.root.winfo_height(), self.app._minimum_height)
        self._assert_controls_fit(self.app)

    def test_monitor_bounds_exclude_taskbar_and_docked_toolbars(self):
        def monitor_info(monitor, pointer):
            info = pointer._obj
            info.rcWork.left, info.rcWork.top = 40, 20
            info.rcWork.right, info.rcWork.bottom = 1880, 1040
            return True

        with patch.object(pomodoro.ctypes.windll.user32, "GetMonitorInfoW",
                          side_effect=monitor_info):
            areas = self.app._screen_rects()
        self.assertTrue(areas)
        self.assertTrue(all(area == (40, 20, 1880, 1040) for area in areas))

    def test_window_is_fitted_inside_usable_work_area(self):
        with patch.object(self.app, "_screen_rects", return_value=[(40, 20, 800, 600)]):
            fitted = self.app._fit_geometry("310x340+750+550")
            self.assertEqual(self.app._geometry_parts(fitted), (310, 340, 490, 260))
            self.assertIsNotNone(self.app._validated_geometry(fitted))

    def test_negative_monitor_coordinates_roundtrip_without_jumping(self):
        geometry = self.app._format_geometry(310, 340, -1000, 120)
        with patch.object(self.app, "_screen_rects", return_value=[(-1280, 0, 0, 1024)]):
            fitted = self.app._fit_geometry(geometry)
            self.assertEqual(self.app._geometry_parts(fitted), (310, 340, -1000, 120))
            self.assertEqual(self.app._validated_geometry(fitted), fitted)

    def test_small_work_area_keeps_window_header_reachable(self):
        with patch.object(self.app, "_screen_rects", return_value=[(0, 0, 280, 250)]):
            fitted = self.app._fit_geometry("310x340+120+120")
            self.app._set_window_geometry(fitted)
            self.root.deiconify()
            self.root.update()
            self._assert_window_in_work_area(self.app)
            header = self.root.winfo_children()[0]
            for control in header.winfo_children():
                self.assertTrue(control.winfo_ismapped())

    def test_dragging_cannot_lose_window_offscreen(self):
        self.root.deiconify()
        self.root.update()
        event = type("DragEvent", (), {"x": 100000, "y": 100000})()
        self.app.do_drag(event)
        self.root.update_idletasks()
        self._assert_window_in_work_area(self.app)

    def test_escape_recovers_window_without_resetting_timer(self):
        self.root.deiconify()
        self.root.update()
        self.root.focus_force()
        self.root.update()
        self.app.remaining = 123
        self.root.geometry("+99999+99999")
        self.root.attributes("-alpha", 0.8)
        self.root.event_generate("<Escape>")
        self.root.update()
        self._assert_window_in_work_area(self.app)
        self.assertEqual(self.app.remaining, 123)
        self.assertAlmostEqual(float(self.root.attributes("-alpha")), 1.0, places=2)

    def test_tiny_saved_geometry_recovers_visible_window(self):
        app = self._app_from_state({"geometry": "1x1+120+120"})
        app.root.deiconify()
        app.root.update_idletasks()
        self.assertGreaterEqual(app.root.winfo_width(), 290)
        self.assertGreaterEqual(app.root.winfo_height(), 300)

    def test_offscreen_saved_geometry_is_repositioned(self):
        app = self._app_from_state({"geometry": "290x300+999999+999999"})
        app.root.deiconify()
        app.root.update_idletasks()
        self._assert_window_in_work_area(app)

    def _assert_window_in_work_area(self, app):
        x, y = app.root.winfo_x(), app.root.winfo_y()
        width, height = app.root.winfo_width(), app.root.winfo_height()
        self.assertTrue(any(left <= x and top <= y
                            and x + width <= right and y + height <= bottom
                            for left, top, right, bottom in app._screen_rects()),
                        f"Window outside monitor work areas: {(x, y, width, height)}")

    def test_hidden_window_save_keeps_last_valid_geometry(self):
        app = self._app_from_state({"geometry": "310x340+140+140"})
        app._save_state()
        saved = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["geometry"], "310x340+140+140")

    def test_malformed_geometry_uses_visible_default(self):
        for geometry in (None, {}, 42, "garbage", "999999x999999+0+0",
                         "9" * 5000 + "x300+0+0"):
            with self.subTest(geometry=geometry):
                app = self._app_from_state({"geometry": geometry})
                app.root.deiconify()
                app.root.update_idletasks()
                self.assertGreaterEqual(app.root.winfo_width(), 290)
                self.assertLessEqual(app.root.winfo_width(), app.root.winfo_screenwidth())

    def test_mapped_window_position_is_saved(self):
        self.root.deiconify()
        self.root.geometry("310x340+140+140")
        self.root.update_idletasks()
        self.app._save_state()
        saved = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["geometry"], "310x340+140+140")

    def test_geometry_on_connected_secondary_monitor_is_preserved(self):
        monitors = [(0, 0, 1920, 1080), (1920, 0, 3840, 1080)]
        with patch.object(self.app, "_screen_rects", return_value=monitors):
            self.assertEqual(self.app._validated_geometry("310x340+2000+120"),
                             "310x340+2000+120")

    def test_geometry_on_disconnected_monitor_is_rejected(self):
        with patch.object(self.app, "_screen_rects", return_value=[(0, 0, 1920, 1080)]):
            self.assertIsNone(self.app._validated_geometry("290x300+2000+120"))

    def test_right_bottom_relative_geometry_remains_supported(self):
        self.assertEqual(self.app._validated_geometry("310x340-80-80"),
                         "310x340-80-80")

    def test_nonobject_saved_state_uses_defaults(self):
        for state in ([], None, "settings", 42):
            with self.subTest(state=state):
                app = self._app_from_state(state)
                self.assertEqual(app.total, 1500)
                self.assertEqual(app.remaining, 1500)

    def test_invalid_saved_field_types_use_defaults(self):
        app = self._app_from_state({"total": "bad", "remaining": {},
                                    "volume": "loud", "muted": [], "topmost": {}})
        app.root.update_idletasks()
        self.assertEqual(app.total, 1500)
        self.assertEqual(app.remaining, 1500)
        self.assertEqual(app.volume, 70)
        self.assertFalse(app.muted)
        self.assertTrue(app.root.attributes("-topmost"))

    def test_saved_numeric_values_are_bounded(self):
        app = self._app_from_state({"total": 300, "remaining": 900, "volume": 999})
        self.assertEqual(app.remaining, 300)
        self.assertEqual(app.volume, 100)
        for total in (-1, 0, True, 10 ** 100):
            with self.subTest(total=total):
                app = self._app_from_state({"total": total})
                self.assertEqual(app.total, 1500)

    def test_geometry_recovery_preserves_valid_settings_after_mapping(self):
        app = self._app_from_state({"total": 1800, "remaining": 600,
                                    "volume": 47, "muted": True,
                                    "geometry": "1x1+120+120"})
        app.root.deiconify()
        app.root.update_idletasks()
        self.assertEqual(app.total, 1800)
        self.assertEqual(app.remaining, 600)
        self.assertEqual(app.volume, 47)
        self.assertTrue(app.muted)

    def test_truncated_settings_are_ignored(self):
        self.state_file.write_text('{"total":', encoding="utf-8")
        self.assertEqual(pomodoro.PomodoroApp._load_state(), {})

    def test_invalid_text_encoding_is_ignored(self):
        self.state_file.write_bytes(b'\xff\xfe{broken}')
        self.assertEqual(pomodoro.PomodoroApp._load_state(), {})

    def test_completed_saved_timer_is_not_restarted(self):
        app = self._app_from_state({"total": 300, "remaining": 0})
        self.assertEqual(app.remaining, 0)
        self.assertTrue(app.completed)
        self.assertFalse(app.running)
        self.assertEqual(app.status_label.cget("text"), "Done!")

    def test_reset_saved_state_keeps_exact_backup(self):
        original = b'{"geometry":"1x1+120+120", "volume":83}'
        self.state_file.write_bytes(original)
        backup = pomodoro.backup_saved_state()
        self.assertFalse(self.state_file.exists())
        self.assertEqual(backup.parent, self.state_file.parent)
        self.assertEqual(backup.read_bytes(), original)

    def test_reset_without_settings_needs_no_backup(self):
        self.assertIsNone(pomodoro.backup_saved_state())

    def test_backup_failure_keeps_original_settings(self):
        original = b'{"volume":83}'
        self.state_file.write_bytes(original)
        with patch.object(Path, "replace", side_effect=PermissionError("access denied")):
            with self.assertRaises(PermissionError):
                pomodoro.backup_saved_state()
        self.assertEqual(self.state_file.read_bytes(), original)

    def test_reset_flag_backs_up_state_before_starting_app(self):
        self.state_file.write_text('{"total": 300}', encoding="utf-8")
        with patch.object(pomodoro.tk, "Tk") as root, \
                patch.object(pomodoro, "PomodoroApp") as app_class:
            pomodoro.main(["--reset-state"])
        self.assertFalse(self.state_file.exists())
        self.assertEqual(len(list(self.state_file.parent.glob("*.backup-*"))), 1)
        app_class.assert_called_once_with(root.return_value)
        root.return_value.mainloop.assert_called_once()

    def test_reset_flag_does_not_start_app_if_backup_fails(self):
        with patch.object(pomodoro, "backup_saved_state", side_effect=PermissionError("locked")), \
                patch.object(pomodoro.tk, "Tk") as root, \
                patch("sys.stderr"):
            with self.assertRaises(SystemExit) as raised:
                pomodoro.main(["--reset-state"])
        self.assertEqual(raised.exception.code, 1)
        root.assert_not_called()


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
