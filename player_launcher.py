"""Reveal the application owning a media session, without shell command strings."""
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import os
from pathlib import PureWindowsPath
import re


@dataclass(frozen=True)
class PlayerWindow:
    handle: int
    executable: str
    app_id: str
    title: str
    visible: bool = True


def identity(value):
    name = PureWindowsPath(value.rsplit("!", 1)[-1]).name
    return "".join(character for character in name.casefold().removesuffix(".exe") if character.isalnum())


def choose_window(source, title, windows):
    source_key = identity(source)
    aliases = {"microsoftedge": "msedge", "microsoftzunemusic": "musicui",
               "orgmozillafirefox": "firefox"}
    source_key = aliases.get(source_key, source_key)
    matches = [window for window in windows if
               (window.app_id and window.app_id.casefold() == source.casefold())
               or (source_key and identity(window.executable) == source_key)]
    if not matches:
        return None
    # EnumWindows is in Z order; prefer a matching track title, otherwise the top app window.
    return max(matches, key=lambda window: (bool(title and title.casefold() in window.title.casefold()),
                                            window.visible))


def list_player_windows():
    user32 = ctypes.WinDLL("user32")
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                  wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetApplicationUserModelId.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.UINT), wintypes.LPWSTR]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumChildWindows.argtypes = [wintypes.HWND, callback_type, wintypes.LPARAM]
    windows, processes = [], {}

    def process_info(pid):
        if pid in processes:
            return processes[pid]
        result = ("", "")
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            try:
                path = ctypes.create_unicode_buffer(32768)
                size = wintypes.DWORD(len(path))
                if kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
                    app_id = ""
                    length = wintypes.UINT()
                    if kernel32.GetApplicationUserModelId(handle, ctypes.byref(length), None) == 122:
                        buffer = ctypes.create_unicode_buffer(length.value)
                        if kernel32.GetApplicationUserModelId(handle, ctypes.byref(length), buffer) == 0:
                            app_id = buffer.value
                    result = (PureWindowsPath(path.value).name, app_id)
            finally:
                kernel32.CloseHandle(handle)
        processes[pid] = result
        return result

    def inspect(hwnd, unused):
        visible = bool(user32.IsWindowVisible(hwnd))
        if not visible:
            # Tray players often hide their unowned main window. Do not expose
            # arbitrary hidden helper/IME windows when looking for that player.
            if user32.GetWindow(hwnd, 4) or not user32.GetWindowLongW(hwnd, -16) & 0x80000:
                return True
        title = ctypes.create_unicode_buffer(1024)
        user32.GetWindowTextW(hwnd, title, len(title))
        if not title.value:
            return True
        pids = set()

        def add_pid(child, data):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(child, ctypes.byref(pid))
            pids.add(pid.value)
            return True

        add_pid(hwnd, 0)
        class_name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, class_name, len(class_name))
        if class_name.value == "ApplicationFrameWindow":
            user32.EnumChildWindows(hwnd, callback_type(add_pid), 0)
        for pid in pids:
            executable, app_id = process_info(pid)
            if executable:
                windows.append(PlayerWindow(hwnd, executable, app_id, title.value, visible))
        return True

    user32.EnumWindows(callback_type(inspect), 0)
    return windows


def activate_window(handle):
    user32 = ctypes.WinDLL("user32")
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    if user32.IsIconic(handle) or not user32.IsWindowVisible(handle):
        user32.ShowWindow(handle, 9)  # Restore only if minimized.
    return bool(user32.SetForegroundWindow(handle))


def open_player(source, title="", *, windows=None, activate=activate_window, launch=os.startfile):
    window = choose_window(source, title, list_player_windows() if windows is None else windows)
    if window is not None:
        return activate(window.handle)
    # Only registered packaged-app IDs may be launched from session metadata.
    # Never execute a path, URL, or command supplied in a track title or source.
    if len(source) <= 256 and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*![A-Za-z0-9_.-]+", source):
        try:
            launch("shell:AppsFolder\\" + source)
            return True
        except OSError:
            pass
    return False
