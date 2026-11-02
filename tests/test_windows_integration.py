import unittest
import os
from unittest.mock import Mock, MagicMock

from windows_integration import GlobalHotkeys, HOTKEYS


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


@unittest.skipUnless(os.environ.get("POMODORO_TEST_HOTKEYS") == "1",
                     "Opt in to brief native registration with POMODORO_TEST_HOTKEYS=1")
class NativeHotkeyTests(unittest.TestCase):
    def test_native_worker_receives_messages_and_releases_registration(self):
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
