"""Small Windows adapters; worker threads never call tkinter directly."""
import ctypes
from ctypes import wintypes
import logging
import queue
import threading


HOTKEYS = (
    ("Start/Pause", 0x20, "toggle"),
    ("Reset", 0x52, "reset"),
    ("Add 10m", 0xBB, "add"),
    ("Remove 10m", 0xBD, "subtract"),
    ("Numpad add 10m", 0x6B, "add"),
    ("Numpad remove 10m", 0x6D, "subtract"),
    ("Show widget", 0x50, "show"),
)


class GlobalHotkeys:
    def __init__(self, root, actions, user32=None, kernel32=None):
        self.root = root
        self.actions = actions
        self.user32 = user32 or ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = kernel32 or ctypes.WinDLL("kernel32", use_last_error=True)
        self.user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                           wintypes.UINT, wintypes.UINT, wintypes.UINT]
        self.user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                          wintypes.UINT, wintypes.UINT]
        self.user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int,
                                              wintypes.UINT, wintypes.UINT]
        self.user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT,
                                                 wintypes.WPARAM, wintypes.LPARAM]
        self.pending = queue.SimpleQueue()
        self.ready = threading.Event()
        self.stopping = threading.Event()
        self.thread_id = None
        self.thread = None
        self.job = None
        self.failed_keys = []
        self.error = None

    def start(self):
        self.thread = threading.Thread(target=self._listen, daemon=True, name="PomodoroHotkeys")
        self.thread.start()
        if not self.ready.wait(3) or self.error:
            self.close()
            raise RuntimeError("Could not start Windows global shortcuts") from self.error
        self.job = self.root.after(50, self._dispatch)

    def _listen(self):
        registered = {}
        try:
            self.thread_id = self.kernel32.GetCurrentThreadId()
            message = wintypes.MSG()
            # Create this thread's message queue before another thread can stop it.
            self.user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
            for key_id, (label, vk, action) in enumerate(HOTKEYS, 1):
                if self.user32.RegisterHotKey(None, key_id, 0x4003, vk):  # Ctrl+Alt, no repeat
                    registered[key_id] = action
                else:
                    self.failed_keys.append(label)
            self.ready.set()
            while not self.stopping.is_set():
                result = self.user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result == 0:
                    break
                if result == -1:
                    raise OSError("Windows hotkey message loop failed")
                if message.message == 0x0312 and message.wParam in registered:  # WM_HOTKEY
                    self.pending.put(registered[message.wParam])
        except Exception as error:
            self.error = error
            logging.getLogger(__name__).warning("Windows global shortcuts unavailable", exc_info=True)
        finally:
            self.ready.set()
            for key_id in registered:
                self.user32.UnregisterHotKey(None, key_id)

    def _dispatch(self):
        self.job = None
        if self.stopping.is_set():
            return
        while not self.pending.empty():
            action = self.pending.get_nowait()
            self.actions[action]()
        self.job = self.root.after(50, self._dispatch)

    def close(self):
        self.stopping.set()
        if self.job is not None:
            self.root.after_cancel(self.job)
            self.job = None
        if self.thread_id is not None:
            self.user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)  # WM_QUIT
        if self.thread is not None:
            self.thread.join(timeout=3)
