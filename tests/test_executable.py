"""Opt-in smoke test of the actual packaged Windows window, not just its process."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import tkinter as tk
import unittest


@unittest.skipUnless(os.name == "nt" and os.environ.get("POMODORO_TEST_EXE"),
                     "Set POMODORO_TEST_EXE to the rebuilt executable to test packaging")
class ExecutableTests(unittest.TestCase):
    def test_packaged_windows_media_dependencies_and_read_only_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "PomodoroTimer.exe"
            shutil.copy2(Path(os.environ["POMODORO_TEST_EXE"]).resolve(), executable)
            report_path = Path(directory) / "media-check.json"
            state_file = Path(directory) / ".pomodoro_state.json"
            state_file.write_bytes(b'{"volume":37}')
            result = subprocess.run([str(executable), "--media-check", str(report_path)],
                                    env=dict(os.environ, USERPROFILE=directory), timeout=20)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(result.returncode, 0, report)
            self.assertTrue(report["available"], report)
            self.assertEqual(set(report), {"available", "session_count", "message"})
            self.assertEqual(state_file.read_bytes(), b'{"volume":37}')

    def _find_window(self, executable):
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                      wintypes.WPARAM, wintypes.LPARAM]
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        found = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def inspect(hwnd, data):
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, title, len(title))
            if title.value != "Pomodoro" or not user32.IsWindowVisible(hwnd):
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            process = kernel32.OpenProcess(0x1000, False, pid.value)
            if process:
                try:
                    path = ctypes.create_unicode_buffer(32768)
                    length = wintypes.DWORD(len(path))
                    if kernel32.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(length)):
                        if os.path.normcase(path.value) == os.path.normcase(str(executable)):
                            found.append(hwnd)
                finally:
                    kernel32.CloseHandle(process)
            return True

        user32.EnumWindows(callback_type(inspect), 0)
        return found[0] if found else None

    def test_packaged_app_recovers_tiny_geometry(self):
        self._check_window({"geometry": "1x1+120+120", "total": 1500, "remaining": 900})

    def test_packaged_app_recovers_nonobject_state(self):
        self._check_window([], expected_remaining=1500)

    def test_packaged_app_recovers_invalid_field_types(self):
        self._check_window({"total": "invalid", "remaining": {}, "volume": [],
                            "topmost": {}}, expected_remaining=1500)

    def test_packaged_reset_flag_preserves_backup_and_opens_defaults(self):
        self._check_window({"total": 300, "remaining": 120}, args=["--reset-state"],
                           expected_remaining=1500, expect_backup=True)

    def test_packaged_focus_drives_twenty_percent_and_full_opacity(self):
        self._check_window({"total": 1500, "remaining": 900, "topmost": True},
                           check_focus=True)

    def test_packaged_inactive_window_uses_twenty_percent_opacity(self):
        for pinned in (True, False):
            with self.subTest(pinned=pinned):
                self._check_window({"total": 1500, "remaining": 900, "topmost": pinned},
                                   check_inactive=True)

    def test_packaged_offscreen_window_fits_monitor_work_area(self):
        self._check_window({"geometry": "310x340+999999+999999",
                            "total": 1500, "remaining": 900})

    def test_packaged_oversized_window_fits_monitor_work_area(self):
        self._check_window({"geometry": "99999x99999+0+0",
                            "total": 1500, "remaining": 900})

    def test_relaunch_reuses_existing_timer(self):
        self._check_window({"total": 1500, "remaining": 321}, expected_remaining=321,
                           check_relaunch=True)

    def test_packaged_restores_break_and_session_settings(self):
        plan = {"phase": "short_break", "completed_sessions": 7, "work_minutes": 45,
                "short_break_minutes": 10, "long_break_minutes": 20, "long_break_every": 3,
                "phase_recorded": False}
        self._check_window({"total": 600, "remaining": 123, "session_plan": plan},
                           expected_remaining=123, expected_session_plan=plan)

    def test_packaged_legacy_settings_get_default_session_plan(self):
        self._check_window({"total": 1500, "remaining": 900},
                           expected_session_plan={"phase": "work", "completed_sessions": 0,
                                                  "work_minutes": 25, "short_break_minutes": 5})

    def test_packaged_completed_session_is_not_recounted_on_open(self):
        plan = {"phase": "work", "completed_sessions": 4, "phase_recorded": True}
        self._check_window({"total": 1500, "remaining": 0, "session_plan": plan},
                           expected_remaining=0, expected_session_plan=plan)

    def _assert_inside_monitor_work_area(self, hwnd, rect):
        class MonitorInfo(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

        user32 = ctypes.windll.user32
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.MonitorFromWindow.restype = wintypes.HANDLE
        user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
        monitor = user32.MonitorFromWindow(hwnd, 2)
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        self.assertTrue(user32.GetMonitorInfoW(monitor, ctypes.byref(info)))
        self.assertGreaterEqual(rect.left, info.rcWork.left)
        self.assertGreaterEqual(rect.top, info.rcWork.top)
        self.assertLessEqual(rect.right, info.rcWork.right)
        self.assertLessEqual(rect.bottom, info.rcWork.bottom)

    def _check_focus_opacity(self, hwnd):
        user32 = ctypes.windll.user32
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.GetLayeredWindowAttributes.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.BYTE), ctypes.POINTER(wintypes.DWORD),
        ]
        background = tk.Tk()
        try:
            background.title("Pomodoro opacity test background")
            background.geometry("400x300+120+120")
            background.update()
            background_hwnd = user32.GetAncestor(background.winfo_id(), 2)

            def expect_foreground_and_alpha(target, expected_alpha):
                if not user32.SetForegroundWindow(target) and user32.GetForegroundWindow() != target:
                    self.skipTest("Windows foreground policy blocks automated activation in this session")
                deadline = time.monotonic() + 1.5  # Must not wait for the old 2s fade.
                while time.monotonic() < deadline:
                    background.update()
                    color, alpha, flags = wintypes.DWORD(), wintypes.BYTE(), wintypes.DWORD()
                    layered = user32.GetLayeredWindowAttributes(
                        hwnd, ctypes.byref(color), ctypes.byref(alpha), ctypes.byref(flags))
                    # A non-layered window is fully opaque.
                    actual = alpha.value if layered else 255
                    style = user32.GetWindowLongW(hwnd, -20)
                    if (user32.GetForegroundWindow() == target and actual == expected_alpha
                            and style & 0x40000 and not style & 0x80):
                        return
                    time.sleep(0.02)
                self.fail(f"Expected foreground {target} and alpha {expected_alpha}/255; "
                          f"got foreground {user32.GetForegroundWindow()}, alpha {actual}/255, "
                          f"extended style {style:#x}")

            expect_foreground_and_alpha(background_hwnd, 51)  # 20% opacity
            expect_foreground_and_alpha(hwnd, 255)
            expect_foreground_and_alpha(background_hwnd, 51)
        finally:
            background.destroy()

    def _check_window(self, state, args=(), expected_remaining=900, expect_backup=False,
                      check_focus=False, check_relaunch=False, check_inactive=False,
                      expected_session_plan=None):
        with tempfile.TemporaryDirectory() as directory:
            # A unique binary path identifies only our process, never a user's app.
            executable = Path(directory) / "PomodoroTimer.exe"
            shutil.copy2(Path(os.environ["POMODORO_TEST_EXE"]).resolve(), executable)
            state_file = Path(directory) / ".pomodoro_state.json"
            original = json.dumps(state)
            state_file.write_text(original, encoding="utf-8")
            env = dict(os.environ, USERPROFILE=directory)
            process = subprocess.Popen([str(executable), "--no-global-hotkeys", *args], env=env)
            try:
                deadline = time.monotonic() + 20
                hwnd = None
                while time.monotonic() < deadline and process.poll() is None:
                    hwnd = self._find_window(executable)
                    if hwnd:
                        rect = wintypes.RECT()
                        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
                        user32 = ctypes.windll.user32
                        user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
                        if (rect.right - rect.left >= 290 and rect.bottom - rect.top >= 300
                                and user32.GetWindowLongW(hwnd, -20) & 0x40000):
                            break
                    time.sleep(0.1)
                else:
                    self.fail("Packaged app did not create a visible, usable window within 20s")
                self.assertGreaterEqual(rect.right - rect.left, 290)
                self.assertGreaterEqual(rect.bottom - rect.top, 300)
                self._assert_inside_monitor_work_area(hwnd, rect)
                user32 = ctypes.windll.user32
                user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
                style = user32.GetWindowLongW(hwnd, -20)
                self.assertTrue(style & 0x40000, "Taskbar/Alt+Tab entry is missing")
                self.assertFalse(style & 0x80, "Window is still marked as a tool window")
                if check_relaunch:
                    secondary = subprocess.Popen([str(executable), "--no-global-hotkeys"], env=env)
                    try:
                        self.assertEqual(secondary.wait(timeout=10), 0)
                        self.assertIsNone(process.poll(), "Relaunch closed the original app")
                        self.assertEqual(self._find_window(executable), hwnd)
                    finally:
                        if secondary.poll() is None:
                            subprocess.run(["taskkill", "/PID", str(secondary.pid), "/T", "/F"],
                                           capture_output=True, check=False)
                            secondary.wait(timeout=10)
                if check_focus:
                    self._check_focus_opacity(hwnd)
                if check_inactive:
                    user32.GetForegroundWindow.restype = wintypes.HWND
                    user32.GetLayeredWindowAttributes.argtypes = [
                        wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
                        ctypes.POINTER(wintypes.BYTE), ctypes.POINTER(wintypes.DWORD),
                    ]
                    if user32.GetForegroundWindow() == hwnd:
                        self.skipTest("The widget is foreground; inactive-window check needs another window active")
                    deadline = time.monotonic() + 1.5
                    while time.monotonic() < deadline:
                        color, alpha, flags = wintypes.DWORD(), wintypes.BYTE(), wintypes.DWORD()
                        if user32.GetLayeredWindowAttributes(
                                hwnd, ctypes.byref(color), ctypes.byref(alpha), ctypes.byref(flags)):
                            if alpha.value == 51:
                                break
                        time.sleep(0.02)
                    else:
                        self.fail("Inactive widget did not reach 20% opacity (alpha 51/255)")
                ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
                self.assertEqual(process.wait(timeout=10), 0)
                saved = json.loads(state_file.read_text(encoding="utf-8"))
                self.assertFalse(saved["geometry"].startswith("1x1"))
                self.assertEqual(saved["remaining"], expected_remaining)
                if expected_session_plan is not None:
                    for field, value in expected_session_plan.items():
                        self.assertEqual(saved["session_plan"][field], value, field)
                if expect_backup:
                    backups = list(Path(directory).glob(".pomodoro_state.json.backup-*"))
                    self.assertEqual(len(backups), 1)
                    self.assertEqual(backups[0].read_text(encoding="utf-8"), original)
            finally:
                if process.poll() is None:
                    if hwnd:
                        ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            pass
                if process.poll() is None:
                    # Kill only this test's process tree, including the onefile child.
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   capture_output=True, check=False)
                    process.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
