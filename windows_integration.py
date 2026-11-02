"""Small Windows adapters; worker threads never call tkinter directly."""
import ctypes
from ctypes import wintypes
import logging
import hashlib
import os
import queue
import threading
import uuid


HOTKEYS = (
    ("Start/Pause", 0x20, "toggle"),
    ("Reset", 0x52, "reset"),
    ("Add 10m", 0xBB, "add"),
    ("Remove 10m", 0xBD, "subtract"),
    ("Numpad add 10m", 0x6B, "add"),
    ("Numpad remove 10m", 0x6D, "subtract"),
    ("Show widget", 0x50, "show"),
)


class SingleInstance:
    """Per-session, per-settings-file guard with an auto-reset 'show' event."""
    def __init__(self, state_file, kernel32=None):
        self.kernel32 = kernel32 or ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel32.CreateMutexW.restype = wintypes.HANDLE
        self.kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL,
                                              wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel32.CreateEventW.restype = wintypes.HANDLE
        self.kernel32.SetEvent.argtypes = [wintypes.HANDLE]
        self.kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel32.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        identity = os.path.normcase(os.path.abspath(state_file)).encode("utf-8")
        self.name = "Local\\PomodoroWidget-" + hashlib.sha256(identity).hexdigest()[:24]
        self.root, self.job, self.callback = None, None, None
        self.mutex = self.kernel32.CreateMutexW(None, False, self.name)
        if not self.mutex:
            raise ctypes.WinError(ctypes.get_last_error())
        self.primary = ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS
        self.event = self.kernel32.CreateEventW(None, False, False, self.name + "-show")
        if not self.event:
            error = ctypes.get_last_error()
            self.kernel32.CloseHandle(self.mutex)
            self.mutex = None
            raise ctypes.WinError(error)

    def notify(self):
        if not self.kernel32.SetEvent(self.event):
            raise ctypes.WinError(ctypes.get_last_error())

    def attach(self, root, callback):
        self.root, self.callback = root, callback
        self.job = root.after(100, self._poll)

    def _poll(self):
        self.job = None
        if self.event is None:
            return
        result = self.kernel32.WaitForSingleObject(self.event, 0)
        if result == 0:
            self.callback()
        elif result == 0xFFFFFFFF:
            logging.getLogger(__name__).warning("Could not read Pomodoro show event")
        self.job = self.root.after(100, self._poll)

    def close(self):
        if self.job is not None:
            self.root.after_cancel(self.job)
            self.job = None
        for attribute in ("event", "mutex"):
            handle = getattr(self, attribute)
            if handle is not None:
                self.kernel32.CloseHandle(handle)
                setattr(self, attribute, None)


def _add_taskbar_tab(hwnd):
    """Notify the shell explicitly; changing styles alone may not refresh its list."""
    class GUID(ctypes.Structure):
        _fields_ = [("data", ctypes.c_byte * 16)]

    clsid = GUID.from_buffer_copy(uuid.UUID("56FDF344-FD6D-11d0-958A-006097C9A090").bytes_le)
    iid = GUID.from_buffer_copy(uuid.UUID("56FDF342-FD6D-11d0-958A-006097C9A090").bytes_le)
    ole32 = ctypes.WinDLL("ole32")
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    initialized = ole32.CoInitializeEx(None, 2) in (0, 1)
    ole32.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD,
                                      ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    interface = ctypes.c_void_p()
    try:
        result = ole32.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid),
                                        ctypes.byref(interface))
        if result < 0:
            raise OSError(f"Taskbar interface unavailable: HRESULT {result:#x}")
        table = ctypes.cast(interface, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        initialize = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(table[3])
        add_tab = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.HWND)(table[4])
        if initialize(interface) < 0 or add_tab(interface, hwnd) < 0:
            raise OSError("Could not register Pomodoro with the Windows taskbar")
    finally:
        if interface.value:
            table = ctypes.cast(interface, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)(table[2])(interface)
        if initialized:
            ole32.CoUninitialize()


def enable_taskbar(root, user32=None):
    """Expose Tk's borderless outer window to the taskbar and Alt+Tab."""
    native = user32 is None
    user32 = user32 or ctypes.WinDLL("user32", use_last_error=True)
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    pointer_suffix = "Ptr" if ctypes.sizeof(ctypes.c_void_p) == 8 else ""
    get_style = getattr(user32, f"GetWindowLong{pointer_suffix}W")
    set_style = getattr(user32, f"SetWindowLong{pointer_suffix}W")
    get_style.argtypes = [wintypes.HWND, ctypes.c_int]
    get_style.restype = ctypes.c_ssize_t
    set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    set_style.restype = ctypes.c_ssize_t
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    hwnd = user32.GetAncestor(root.winfo_id(), 2)  # GA_ROOT: Tk's outer wrapper
    if not hwnd:
        raise ctypes.WinError(ctypes.get_last_error())
    old_style = get_style(hwnd, -20)
    style = (old_style | 0x40000) & ~0x80  # APPWINDOW, not TOOLWINDOW
    if style == old_style:
        return hwnd
    ctypes.set_last_error(0)
    if not set_style(hwnd, -20, style) and ctypes.get_last_error():
        raise ctypes.WinError(ctypes.get_last_error())
    if not user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0037):
        raise ctypes.WinError(ctypes.get_last_error())
    if native:
        _add_taskbar_tab(hwnd)
    return hwnd


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
