import json
import ctypes
import tempfile
import unittest
import tkinter as tk
from types import SimpleNamespace
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
        sound_patch = patch.object(pomodoro.PomodoroApp, "_play_sequence")
        sound_patch.start()
        self.addCleanup(sound_patch.stop)
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

    def test_local_shortcuts_do_not_intercept_spotify_url_typing(self):
        with patch.object(self.app, "reset") as reset:
            result = self.app._local_shortcut(SimpleNamespace(widget=self.app.uri_entry, state=0),
                                               self.app.reset)
        reset.assert_not_called()
        self.assertIsNone(result)

    def test_local_shortcuts_leave_modified_keys_alone(self):
        with patch.object(self.app, "toggle") as toggle:
            self.app._local_shortcut(SimpleNamespace(widget=self.root, state=0x4), self.app.toggle)
        toggle.assert_not_called()

    def test_space_on_start_button_toggles_only_once(self):
        self.root.deiconify()
        self.root.update()
        self.app.start_btn.focus_force()
        self.root.update()
        self.app.start_btn.event_generate("<KeyPress-space>")
        self.app.start_btn.event_generate("<KeyRelease-space>")
        self.root.update()
        self.assertTrue(self.app.running)

    def test_global_shortcut_failure_is_nonfatal_and_visible(self):
        with patch.object(pomodoro, "GlobalHotkeys") as hotkeys:
            hotkeys.return_value.start.side_effect = RuntimeError("unavailable")
            self.app.start_global_hotkeys()
        self.assertEqual(self.app.status_label.cget("text"), "Global shortcuts unavailable")
        self.assertIsNone(self.app._hotkeys)

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

    def test_removing_excess_time_preserves_elapsed_time(self):
        self.app.total, self.app.remaining = 100, 10
        with patch.object(self.app, "complete"):
            self.app.add_time(-600)
        self.assertEqual(self.app.total, 90)
        self.assertEqual(self.app.remaining, 0)

    def test_adjustment_to_zero_completes_once_and_cancels_tick(self):
        self.app.total, self.app.remaining = 100, 10
        self.app.toggle()
        job = self.app.after_id
        with patch.object(self.app, "complete") as complete:
            self.app.add_time(-600)
            self.app.add_time(-600)
            self.app.tick()
        complete.assert_called_once()
        self.assertFalse(self.app.running)
        self.assertTrue(self.app.completed)
        self.assertIsNone(self.app.after_id)
        self.assertNotIn(job, self.root.tk.call("after", "info"))

    def test_extending_done_timer_clears_completed_flag(self):
        self.app.total, self.app.remaining, self.app.completed = 100, 0, True
        self.app.add_time(600)
        self.assertEqual(self.app.remaining, 600)
        self.assertFalse(self.app.completed)

    def test_restarting_done_timer_clears_completed_flag(self):
        self.app.remaining, self.app.completed = 0, True
        self.app.toggle()
        self.assertFalse(self.app.completed)
        self.assertTrue(self.app.running)

    def test_extension_is_bounded_to_persistable_duration(self):
        self.app.total = pomodoro.MAX_SAVED_DURATION - 10
        self.app.remaining = 100
        self.app.add_time(600)
        self.assertEqual(self.app.total, pomodoro.MAX_SAVED_DURATION)
        self.assertEqual(self.app.remaining, 110)

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

    def test_duration_feedback_survives_display_refresh(self):
        self.app.set_duration(300)
        self.app._update_display()
        self.assertEqual(self.app.status_label.cget("text"), "Set 5m")

    def test_time_adjustment_feedback_survives_display_refresh(self):
        self.app.add_time(600)
        self.app._update_display()
        self.assertEqual(self.app.status_label.cget("text"), "+10m")

    def test_done_feedback_survives_display_refresh(self):
        with patch.object(pomodoro.threading, "Thread"), patch.object(self.app, "flash"):
            self.app.complete()
        self.app._update_display()
        self.assertEqual(self.app.status_label.cget("text"), "Done!")

    def test_pausing_before_first_tick_still_says_paused(self):
        self.app.toggle()
        self.app.toggle()
        self.assertEqual(self.app.status_label.cget("text"), "Paused")

    def test_restored_partial_timer_says_paused(self):
        app = self._app_from_state({"total": 1500, "remaining": 600})
        self.assertEqual(app.status_label.cget("text"), "Paused")

    def test_volume_mute(self):
        self.app._on_volume(50)
        self.assertFalse(self.app.muted)
        self.assertEqual(self.app.volume, 50)
        self.app.mute_var.set(True)
        self.app._on_mute()
        self.assertTrue(self.app.muted)
        self.assertEqual(self.app.volume, 0)

    def test_volume_drag_is_debounced_and_saves_latest_value_once(self):
        with patch.object(self.root, "after", return_value="pending-save") as after, \
                patch.object(self.root, "after_cancel"), \
                patch.object(self.app, "_save_state", wraps=self.app._save_state) as save:
            for volume in range(10, 80, 10):
                self.app._on_volume(volume)
            save.assert_not_called()
            callback = after.call_args.args[1]
            callback()
            save.assert_called_once()
        saved = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["volume"], 70)

    def test_close_flushes_pending_volume_save(self):
        self.app._on_volume(37)
        with patch.object(self.root, "destroy"):
            self.app._on_close()
        self.assertIsNone(self.app._state_after)
        self.assertEqual(json.loads(self.state_file.read_text(encoding="utf-8"))["volume"], 37)

    def test_failed_atomic_replace_preserves_original_and_cleans_temp_file(self):
        original = b'{"volume": 83}'
        self.state_file.write_bytes(original)
        with patch.object(pomodoro.os, "replace", side_effect=PermissionError("locked")), \
                self.assertLogs("pomodoro", level="WARNING"):
            self.assertFalse(self.app._save_state())
        self.assertEqual(self.state_file.read_bytes(), original)
        self.assertEqual(list(self.state_file.parent.iterdir()), [self.state_file])

    def test_serialization_failure_does_not_truncate_settings(self):
        original = b'{"volume": 83}'
        self.state_file.write_bytes(original)
        with patch.object(pomodoro.json, "dumps", side_effect=TypeError("bad state")), \
                self.assertLogs("pomodoro", level="WARNING"):
            self.assertFalse(self.app._save_state())
        self.assertEqual(self.state_file.read_bytes(), original)

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
        with patch.object(self.app, "_has_focus", return_value=False):
            self.app._apply_alpha()
        self.assertAlmostEqual(float(self.app.root.attributes("-alpha")), 0.2, places=2)

    def test_inactive_unpinned_window_is_also_translucent(self):
        self.app.root.attributes("-topmost", False)
        with patch.object(self.app, "_has_focus", return_value=False):
            self.app._apply_alpha()
        self.assertAlmostEqual(float(self.app.root.attributes("-alpha")), 0.2, places=2)

    def test_focus_loss_fades_at_idle_without_waiting_two_seconds(self):
        self.app._on_click()
        with patch.object(self.app, "_has_focus", return_value=False):
            self.app._schedule_fade()
            self.root.update_idletasks()
        self.assertAlmostEqual(float(self.root.attributes("-alpha")), 0.2, places=2)

    def test_moving_focus_between_child_controls_does_not_fade(self):
        self.app._on_click()
        with patch.object(self.app, "_has_focus", return_value=True):
            self.app._schedule_fade()
            self.root.update_idletasks()
        self.assertAlmostEqual(float(self.root.attributes("-alpha")), 1.0, places=2)

    def test_unmapped_initialization_defaults_to_opaque(self):
        self.assertAlmostEqual(float(self.root.attributes("-alpha")), 1.0, places=2)

    def test_mapping_inactive_window_raises_it_and_starts_focus_monitor(self):
        with patch.object(self.root, "lift") as lift, \
                patch.object(self.app, "_has_focus", return_value=False):
            self.root.deiconify()
            self.root.update()
        lift.assert_called()
        self.assertAlmostEqual(float(self.root.attributes("-alpha")), 0.2, places=2)
        self.assertIsNotNone(self.app._focus_after)

    def test_opacity_timeout_does_not_fade_focused_window(self):
        self.app._on_click()
        with patch.object(self.app, "_has_focus", return_value=True):
            self.app._revert_alpha()
        self.assertAlmostEqual(float(self.root.attributes("-alpha")), 1.0, places=2)

    def test_opacity_timeout_fades_inactive_pinned_window(self):
        self.app._on_click()
        with patch.object(self.app, "_has_focus", return_value=False):
            self.app._revert_alpha()
        self.assertAlmostEqual(float(self.root.attributes("-alpha")), 0.2, places=2)

    def test_clicking_child_widget_makes_window_opaque(self):
        self.root.deiconify()
        self.root.update()
        self.root.attributes("-alpha", 0.2)
        self.app.time_label.event_generate("<Button-1>")
        self.assertAlmostEqual(float(self.root.attributes("-alpha")), 1.0, places=2)

    def test_focus_in_makes_window_opaque(self):
        self.root.attributes("-alpha", 0.2)
        self.root.event_generate("<FocusIn>")
        self.assertAlmostEqual(float(self.root.attributes("-alpha")), 1.0, places=2)

    def test_unpin_cancels_pending_fade(self):
        self.app._schedule_fade()
        job = self.app._opaque_after
        self.app.toggle_topmost()
        self.assertIsNone(self.app._opaque_after)
        self.assertNotIn(job, self.root.tk.call("after", "info"))
        self.assertAlmostEqual(float(self.root.attributes("-alpha")), 1.0, places=2)

    def test_focus_out_replaces_pending_fade_timer(self):
        self.app._schedule_fade()
        original_job = self.app._opaque_after
        self.root.event_generate("<FocusOut>")
        self.assertIsNotNone(self.app._opaque_after)
        self.assertNotEqual(original_job, self.app._opaque_after)
        self.assertNotIn(original_job, self.root.tk.call("after", "info"))

    def test_stale_tk_focus_does_not_override_windows_foreground_state(self):
        with patch.object(self.root, "focus_displayof", return_value=self.app.start_btn), \
                patch.object(pomodoro, "window_is_foreground", return_value=False):
            self.app._apply_alpha()
        self.assertAlmostEqual(float(self.root.attributes("-alpha")), 0.2, places=2)

    def test_poll_repairs_missed_focus_out_without_rewriting_unchanged_alpha(self):
        self.root.deiconify()
        self.root.update()
        self.root.after_cancel(self.app._focus_after)
        self.app._on_click()
        with patch.object(self.app, "_has_focus", return_value=False):
            self.app._poll_focus()
        self.assertAlmostEqual(float(self.root.attributes("-alpha")), 0.2, places=2)
        with patch.object(self.app, "_has_focus", return_value=False), \
                patch.object(self.app, "_set_alpha") as set_alpha:
            self.app._apply_alpha()
        set_alpha.assert_not_called()

    def test_close_cancels_focus_monitor(self):
        self.root.deiconify()
        self.root.update()
        job = self.app._focus_after
        with patch.object(self.root, "destroy"):
            self.app._on_close()
        self.assertIsNone(self.app._focus_after)
        self.assertNotIn(job, self.root.tk.call("after", "info"))

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
            if isinstance(widget, (tk.Menu, tk.Toplevel)):
                return  # Preset menus and lazy tooltips are intentionally hidden.
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

    def test_default_layout_is_compact_at_standard_display_scaling(self):
        app = self._app_from_state({}, scaling=96 / 72)
        self.assertLessEqual(app._minimum_width, 300)
        self.assertLessEqual(app._minimum_height, 340)

    def test_old_layout_compacts_once_without_losing_position_or_timer(self):
        app = self._app_from_state({"geometry": "510x600+140+140", "remaining": 123,
                                    "volume": 47, "muted": True,
                                    "session_plan": {"phase": "short_break", "completed_sessions": 2}})
        app.root.deiconify()
        app.root.update_idletasks()
        self.assertEqual((app.root.winfo_width(), app.root.winfo_height()),
                         (app._minimum_width, app._minimum_height))
        self.assertEqual((app.root.winfo_x(), app.root.winfo_y()), (140, 140))
        self.assertEqual(app.remaining, 123)
        self.assertEqual(app.volume, 47)
        self.assertTrue(app.muted)
        self.assertEqual(app.sessions.completed_sessions, 2)
        self.assertEqual(app.sessions.phase, "short_break")
        app._save_state()
        saved = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["ui_layout_version"], pomodoro.UI_LAYOUT_VERSION)

    def test_current_layout_keeps_valid_saved_dimensions(self):
        app = self._app_from_state({"geometry": "510x600+140+140",
                                    "ui_layout_version": pomodoro.UI_LAYOUT_VERSION})
        app.root.deiconify()
        app.root.update_idletasks()
        self.assertEqual(app.root.geometry(), "510x600+140+140")

    def test_segmented_navigation_keeps_running_timer_unchanged(self):
        self.app.toggle()
        remaining, job = self.app.remaining, self.app.after_id
        self.app.ui.navigation["Music"].invoke()
        self.root.update_idletasks()
        self.assertEqual(self.app.notebook.select(), str(self.app.ui.pages["Music"]))
        self.assertEqual(self.app.remaining, remaining)
        self.assertEqual(self.app.after_id, job)
        self.assertTrue(self.app.running)

    def test_phase_selection_updates_accent_and_thin_progress(self):
        self.app.select_phase("short_break")
        self.assertEqual(self.app.start_btn.cget("bg"), pomodoro.COLORS["break"])
        self.assertEqual(self.app.progress.itemcget(self.app.progress.bar, "fill"), pomodoro.COLORS["break"])
        self.app.total, self.app.remaining = 100, 25
        self.app._update_display()
        self.assertEqual(self.app.progress["value"], 75)
        self.assertLessEqual(self.app.progress.winfo_reqheight(), self.app.ui.dp(4))

    def test_presets_still_work_from_compact_menu(self):
        self.app.preset_menu.invoke(1)
        self.assertEqual(self.app.remaining, 900)
        self.app.toggle()
        self.assertEqual(self.app.preset_btn.cget("state"), "disabled")

    def test_preset_menu_keeps_native_keyboard_handling(self):
        with patch.object(self.app, "reset") as reset:
            result = self.app._local_shortcut(SimpleNamespace(widget=self.app.preset_menu, state=0), reset)
        self.assertIsNone(result)
        reset.assert_not_called()

    def test_modern_button_hover_and_enter_activation(self):
        self.root.deiconify()
        self.root.update()
        button = self.app.start_btn
        normal = button.cget("bg")
        button._hover(True)
        self.assertNotEqual(button.cget("bg"), normal)
        button._hover(False)
        self.assertEqual(button.cget("bg"), normal)
        button.focus_force()
        self.root.update()
        button.event_generate("<Return>")
        self.assertTrue(self.app.running)

    def test_tooltips_are_lazy_and_cancel_pending_callbacks(self):
        tooltip = next(tip for tip in self.app.ui.tooltips if tip.widget is self.app.start_btn)
        tooltip.schedule()
        job = tooltip.job
        self.assertIsNone(tooltip.window)
        tooltip.hide()
        self.assertNotIn(job, self.root.tk.call("after", "info"))
        self.root.deiconify()
        self.root.update()
        tooltip.show()
        self.assertIsNotNone(tooltip.window)
        tooltip.hide()
        self.assertIsNone(tooltip.window)

    def test_session_validation_message_fits_compact_layout(self):
        self.app.session_vars["short_break_minutes"].set("0")
        self.assertFalse(self.app.apply_session_settings())
        self._assert_controls_fit(self.app)

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
            width, height = self._usable_test_size()
            self.assertEqual(self.app._geometry_parts(fitted), (width, height, 800 - width, 600 - height))
            self.assertIsNotNone(self.app._validated_geometry(fitted))

    def test_negative_monitor_coordinates_roundtrip_without_jumping(self):
        width, height = self._usable_test_size()
        geometry = self.app._format_geometry(width, height, -1000, 120)
        with patch.object(self.app, "_screen_rects", return_value=[(-1280, 0, 0, 1024)]):
            fitted = self.app._fit_geometry(geometry)
            self.assertEqual(self.app._geometry_parts(fitted), (width, height, -1000, 120))
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
        self.root.attributes("-alpha", 0.2)
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
        expected = app._last_geometry
        app._save_state()
        saved = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["geometry"], expected)

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
        expected = self.root.geometry()
        self.app._save_state()
        saved = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["geometry"], expected)

    def _usable_test_size(self):
        return max(310, self.app._minimum_width), max(340, self.app._minimum_height)

    def test_geometry_on_connected_secondary_monitor_is_preserved(self):
        monitors = [(0, 0, 1920, 1080), (1920, 0, 3840, 1080)]
        width, height = self._usable_test_size()
        geometry = f"{width}x{height}+2000+120"
        with patch.object(self.app, "_screen_rects", return_value=monitors):
            self.assertEqual(self.app._validated_geometry(geometry), geometry)

    def test_geometry_on_disconnected_monitor_is_rejected(self):
        with patch.object(self.app, "_screen_rects", return_value=[(0, 0, 1920, 1080)]):
            self.assertIsNone(self.app._validated_geometry("290x300+2000+120"))

    def test_right_bottom_relative_geometry_remains_supported(self):
        width, height = self._usable_test_size()
        geometry = f"{width}x{height}-80-80"
        self.assertEqual(self.app._validated_geometry(geometry), geometry)

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

    def test_work_completion_counts_once_and_waits_for_manual_break_start(self):
        self.app.remaining = 1
        self.app.toggle()
        self.app.tick()
        self.assertEqual(self.app.sessions.phase, "work")
        self.assertEqual(self.app.sessions.completed_sessions, 1)
        self.assertFalse(self.app.running)
        self.assertEqual(self.app.remaining, 0)
        self.assertIn("Next: Short break", self.app.phase_label.cget("text"))
        self.app.toggle()
        self.assertEqual(self.app.sessions.phase, "short_break")
        self.assertEqual(self.app.remaining, 300)
        self.assertTrue(self.app.running)

    def test_break_completion_does_not_count_as_work_and_returns_to_work(self):
        self.app.sessions.completed_sessions = 1
        self.app.select_phase("short_break")
        self.app.remaining = 1
        self.app.toggle()
        self.app.tick()
        self.assertEqual(self.app.sessions.completed_sessions, 1)
        self.app.next_phase()
        self.assertEqual(self.app.sessions.phase, "work")
        self.assertEqual(self.app.remaining, 1500)
        self.assertFalse(self.app.running)

    def test_fourth_work_session_offers_long_break(self):
        self.app.sessions.completed_sessions = 3
        self.app.complete()
        self.assertEqual(self.app.sessions.completed_sessions, 4)
        self.app.next_phase()
        self.assertEqual(self.app.sessions.phase, "long_break")
        self.assertEqual(self.app.remaining, 900)
        self.app.complete()
        self.app.next_phase()
        self.assertIn("Session 1/4", self.app.phase_label.cget("text"))

    def test_skipping_phase_does_not_increment_session_count(self):
        self.app.next_phase()
        self.assertEqual(self.app.sessions.phase, "short_break")
        self.assertEqual(self.app.sessions.completed_sessions, 0)
        self.assertFalse(self.app.running)

    def test_phase_change_requires_pause(self):
        self.app.toggle()
        remaining = self.app.remaining
        self.assertFalse(self.app.select_phase("long_break"))
        self.assertEqual(self.app.sessions.phase, "work")
        self.assertEqual(self.app.remaining, remaining)

    def test_session_plan_changes_do_not_interrupt_current_timer(self):
        self.app.toggle()
        remaining = self.app.remaining
        self.app.session_vars["work_minutes"].set("45")
        self.app.session_vars["short_break_minutes"].set("10")
        self.assertTrue(self.app.apply_session_settings())
        self.assertEqual(self.app.remaining, remaining)
        self.assertTrue(self.app.running)
        self.app.toggle()
        self.app.next_phase()
        self.assertEqual(self.app.remaining, 600)

    def test_invalid_session_plan_is_rejected_without_partial_updates(self):
        original = self.app.sessions.to_state()
        for invalid in ("0", "bad", "10000"):
            with self.subTest(invalid=invalid):
                self.app.session_vars["work_minutes"].set("50")
                self.app.session_vars["short_break_minutes"].set(invalid)
                self.assertFalse(self.app.apply_session_settings())
                self.assertEqual(self.app.sessions.to_state(), original)

    def test_session_state_restores_phase_count_and_paused_remaining_time(self):
        self.app.sessions.completed_sessions = 3
        self.app.select_phase("short_break")
        self.app.sessions.configure(work_minutes=40, long_break_every=3)
        self.app.remaining = 123
        self.app._save_state()
        saved = json.loads(self.state_file.read_text(encoding="utf-8"))
        app = self._app_from_state(saved)
        self.assertEqual(app.sessions.phase, "short_break")
        self.assertEqual(app.sessions.completed_sessions, 3)
        self.assertEqual(app.sessions.work_minutes, 40)
        self.assertEqual(app.remaining, 123)
        self.assertFalse(app.running)

    def test_completed_session_is_not_counted_again_after_reopening(self):
        self.app.complete()
        saved = json.loads(self.state_file.read_text(encoding="utf-8"))
        app = self._app_from_state(saved)
        app.complete()
        self.assertEqual(app.sessions.completed_sessions, 1)

    def test_extending_completed_work_does_not_double_count_same_session(self):
        self.app.complete()
        self.app.add_time(60)
        self.assertFalse(self.app.completed)
        self.app.complete()
        self.assertEqual(self.app.sessions.completed_sessions, 1)

    def test_repeated_completion_notifies_only_once(self):
        with patch.object(pomodoro.threading, "Thread") as thread, patch.object(self.app, "flash"):
            self.app.complete()
            self.app.complete()
        self.assertEqual(self.app.sessions.completed_sessions, 1)
        thread.assert_called_once()

    def test_phase_change_cancels_previous_completion_flash(self):
        self.app.complete()
        job = self.app._flash_after
        self.app.next_phase()
        self.assertIsNone(self.app._flash_after)
        self.assertNotIn(job, self.root.tk.call("after", "info"))
        self.assertEqual(self.app.time_label.cget("bg"), pomodoro.COLORS["bg"])

    def test_reset_session_count_does_not_interrupt_countdown(self):
        self.app.sessions.completed_sessions = 3
        self.app.toggle()
        remaining, job = self.app.remaining, self.app.after_id
        self.app.reset_session_count()
        self.assertEqual(self.app.sessions.completed_sessions, 0)
        self.assertEqual(self.app.remaining, remaining)
        self.assertEqual(self.app.after_id, job)
        self.assertTrue(self.app.running)

    def test_malformed_session_state_is_backward_compatible(self):
        app = self._app_from_state({"total": 1800, "remaining": 123, "session_plan": []})
        self.assertEqual(app.remaining, 123)
        self.assertEqual(app.sessions.phase, "work")
        self.assertEqual(app.sessions.completed_sessions, 0)

    def test_completed_phase_and_sessions_controls_fit(self):
        self.app.complete()
        self._assert_controls_fit(self.app)

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

    def test_relaunch_notifies_primary_without_resetting_state_or_creating_window(self):
        with patch.object(pomodoro, "SingleInstance") as guard, \
                patch.object(pomodoro, "backup_saved_state") as backup, \
                patch.object(pomodoro.tk, "Tk") as root:
            guard.return_value.primary = False
            pomodoro.main(["--reset-state"])
        guard.return_value.notify.assert_called_once()
        guard.return_value.close.assert_called_once()
        backup.assert_not_called()
        root.assert_not_called()

    def test_show_existing_window_preserves_timer_and_reveals_widget(self):
        self.app.remaining = 123
        with patch.object(self.root, "focus_force") as focus, \
                patch.object(self.app, "_has_focus", return_value=True):
            self.app.show_window()
            self.root.update()
        self.assertTrue(self.root.winfo_ismapped())
        self.assertEqual(self.app.remaining, 123)
        focus.assert_called_once()
        self.assertAlmostEqual(float(self.root.attributes("-alpha")), 1.0, places=2)

    def test_taskbar_entry_survives_mapping_fading_and_pin_changes(self):
        self.app.enable_desktop_presence()
        self.root.deiconify()
        self.root.update()
        user32 = ctypes.WinDLL("user32")
        user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        user32.GetAncestor.restype = ctypes.c_void_p
        user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]

        def check_style():
            self.root.update()
            hwnd = user32.GetAncestor(self.root.winfo_id(), 2)
            style = user32.GetWindowLongW(hwnd, -20)
            self.assertTrue(style & 0x40000)
            self.assertFalse(style & 0x80)

        check_style()
        with patch.object(self.app, "_has_focus", return_value=False):
            self.app._apply_alpha()
        check_style()
        self.app.toggle_topmost()
        check_style()


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
