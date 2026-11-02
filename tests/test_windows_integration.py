import unittest
import os
import ctypes
from unittest.mock import Mock, MagicMock

from windows_integration import GlobalHotkeys, HOTKEYS, SingleInstance, enable_taskbar, window_is_foreground
from unittest.mock import patch


class GlobalHotkeyTests(unittest.TestCase):
    def setUp(self):
        self.root = Mock()
        self.root.after.return_value = "poll-job"
        self.user32, self.kernel32 = MagicMock(), MagicMock()
        self.kernel32.GetCurrentThreadId.return_value = 123
        self.user32.RegisterHotKey.return_value = True
        self.actions = {key: Mock() for key in ("toggle", "reset", "add", "subtract", "show")}
        self.hotkeys = GlobalHotkeys(self.root, self.actions, self.user32, self.kernel32)

    def _messages(self, ids):
        ids = iter(ids)
        def get_message(pointer, hwnd, minimum, maximum):
            key_id = next(ids, None)
            if key_id is None:
                return 0
            pointer._obj.message = 0x0312
            pointer._obj.wParam = key_id
            return 1
        self.user32.GetMessageW.side_effect = get_message

    def test_worker_registers_modified_keys_and_queues_actions_without_tk_calls(self):
        self._messages([1, 3, 4, 7])
        self.hotkeys._listen()
        self.assertEqual([self.hotkeys.pending.get_nowait() for _ in range(4)],
                         ["toggle", "add", "subtract", "show"])
        self.root.after.assert_not_called()
        self.assertTrue(self.hotkeys.ready.is_set())
        self.assertEqual(self.user32.UnregisterHotKey.call_count, len(HOTKEYS))
        for call in self.user32.RegisterHotKey.call_args_list:
            self.assertEqual(call.args[2], 0x4003)

    def test_conflicting_registration_is_reported_and_not_dispatched(self):
        self.user32.RegisterHotKey.side_effect = [False] + [True] * (len(HOTKEYS) - 1)
        self._messages([1, 3])
        self.hotkeys._listen()
        self.assertEqual(self.hotkeys.failed_keys, ["Start/Pause"])
        self.assertEqual(self.hotkeys.pending.get_nowait(), "add")
        self.assertTrue(self.hotkeys.pending.empty())
        self.assertEqual(self.user32.UnregisterHotKey.call_count, len(HOTKEYS) - 1)

    def test_ui_dispatches_and_close_cancels_polling(self):
        self.hotkeys.pending.put("add")
        self.hotkeys._dispatch()
        self.actions["add"].assert_called_once()
        self.hotkeys.close()
        self.root.after_cancel.assert_called_once_with("poll-job")
        self.hotkeys.pending.put("reset")
        self.hotkeys._dispatch()
        self.actions["reset"].assert_not_called()

    def test_message_loop_failure_still_unregisters_keys(self):
        self.user32.GetMessageW.return_value = -1
        with self.assertLogs("windows_integration", level="WARNING"):
            self.hotkeys._listen()
        self.assertIsInstance(self.hotkeys.error, OSError)
        self.assertEqual(self.user32.UnregisterHotKey.call_count, len(HOTKEYS))


class InstanceAndTaskbarTests(unittest.TestCase):
    def test_windows_foreground_identifies_native_tk_wrapper_and_children(self):
        root, user32 = Mock(), MagicMock()
        root.winfo_id.return_value = 100
        user32.GetAncestor.side_effect = lambda hwnd, flag: 42 if hwnd in (100, 101, 42) else 99
        for foreground, expected in ((42, True), (101, True), (200, False), (0, False)):
            with self.subTest(foreground=foreground):
                user32.GetForegroundWindow.return_value = foreground
                self.assertEqual(window_is_foreground(root, user32), expected)
        root.focus_displayof.assert_not_called()

    def _instance(self, already_exists=False):
        kernel = MagicMock()
        kernel.CreateMutexW.return_value = 11
        kernel.CreateEventW.return_value = 12
        with patch("windows_integration.ctypes.get_last_error", return_value=183 if already_exists else 0):
            instance = SingleInstance("C:\\test-profile\\state.json", kernel)
        return instance, kernel

    def test_secondary_notifies_existing_instance_and_releases_handles_once(self):
        instance, kernel = self._instance(already_exists=True)
        self.assertFalse(instance.primary)
        instance.notify()
        kernel.SetEvent.assert_called_once_with(12)
        instance.close()
        instance.close()
        self.assertEqual(kernel.CloseHandle.call_count, 2)

    def test_primary_dispatches_show_request_on_ui_thread(self):
        instance, kernel = self._instance()
        self.assertTrue(instance.primary)
        root, callback = Mock(), Mock()
        root.after.return_value = "show-poll"
        instance.attach(root, callback)
        kernel.WaitForSingleObject.return_value = 0
        instance._poll()
        callback.assert_called_once()
        instance.close()
        root.after_cancel.assert_called_once_with("show-poll")

    def test_different_settings_paths_have_independent_instance_names(self):
        instance, kernel = self._instance()
        with patch("windows_integration.ctypes.get_last_error", return_value=0):
            other = SingleInstance("C:\\another-profile\\state.json", kernel)
        self.assertNotEqual(instance.name, other.name)
        instance.close()
        other.close()

    def test_failed_event_creation_releases_mutex(self):
        kernel = MagicMock()
        kernel.CreateMutexW.return_value = 11
        kernel.CreateEventW.return_value = 0
        with patch("windows_integration.ctypes.get_last_error", return_value=0):
            with self.assertRaises(OSError):
                SingleInstance("C:\\test-profile\\state.json", kernel)
        kernel.CloseHandle.assert_called_once_with(11)

    def test_taskbar_style_is_applied_to_tk_outer_window(self):
        root, user32 = Mock(), MagicMock()
        user32.GetAncestor.return_value = 42
        suffix = "Ptr" if ctypes.sizeof(ctypes.c_void_p) == 8 else ""
        get_style = getattr(user32, f"GetWindowLong{suffix}W")
        set_style = getattr(user32, f"SetWindowLong{suffix}W")
        get_style.return_value = 0x80
        self.assertEqual(enable_taskbar(root, user32), 42)
        set_style.assert_called_once_with(42, -20, 0x40000)


@unittest.skipUnless(os.environ.get("POMODORO_TEST_HOTKEYS") == "1",
                     "Opt in to brief native registration with POMODORO_TEST_HOTKEYS=1")
class NativeHotkeyTests(unittest.TestCase):
    def test_native_worker_receives_messages_and_releases_registration(self):
        import gc
        gc.collect()  # Any prior test Tk interpreter must be finalized on this thread.
        root, action = Mock(), Mock()
        hotkeys = GlobalHotkeys(root, {name: action for _, _, name in HOTKEYS})
        try:
            hotkeys.start()
            available = [index for index, (label, _, _) in enumerate(HOTKEYS, 1)
                         if label not in hotkeys.failed_keys]
            self.assertTrue(available, "All test shortcut combinations are already in use")
            self.assertTrue(hotkeys.user32.PostThreadMessageW(hotkeys.thread_id, 0x0312,
                                                             available[0], 0))
            hotkeys.user32.PostThreadMessageW(hotkeys.thread_id, 0x0012, 0, 0)
            hotkeys.thread.join(timeout=3)
            self.assertFalse(hotkeys.thread.is_alive())
            hotkeys._dispatch()
            action.assert_called_once()
        finally:
            hotkeys.close()


if __name__ == "__main__":
    unittest.main()
