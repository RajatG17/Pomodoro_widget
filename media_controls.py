"""Windows media-session discovery and targeted playback, off the Tk thread."""
import asyncio
from dataclasses import dataclass, replace
import logging
import queue
import threading
import time


ACTIONS = {"toggle", "previous", "next", "stop"}


def clean_text(value, limit=512):
    return " ".join(str(value or "").split())[:limit]


def player_name(source):
    simple = source.rsplit("!", 1)[-1].rsplit("\\", 1)[-1]
    lower = simple.casefold().removesuffix(".exe")
    names = {"chrome": "Chrome", "msedge": "Microsoft Edge", "microsoftedge": "Microsoft Edge",
             "firefox": "Firefox", "brave": "Brave", "opera": "Opera", "vivaldi": "Vivaldi",
             "spotify": "Spotify", "microsoft.zunemusic": "Media Player", "music.ui": "Media Player"}
    return names.get(lower, clean_text(simple.removesuffix(".exe"), 80) or "Media player")


@dataclass(frozen=True)
class MediaItem:
    id: str
    source: str
    title: str = ""
    artist: str = ""
    status: str = "Paused"
    controls: tuple = ()

    @property
    def player(self):
        return player_name(self.source)


@dataclass(frozen=True)
class MediaSnapshot:
    available: bool = False
    sessions: tuple = ()
    current_id: str = ""
    message: str = "Open Music to detect players."


def choose_session(snapshot, preferred=None, previous=None):
    sessions = {item.id: item for item in snapshot.sessions}
    if preferred in sessions:
        return sessions[preferred]
    for key in (snapshot.current_id, previous):
        if key in sessions and sessions[key].status == "Playing":
            return sessions[key]
    return (next((item for item in snapshot.sessions if item.status == "Playing"), None)
            or sessions.get(snapshot.current_id) or sessions.get(previous)
            or next(iter(snapshot.sessions), None))


def media_diagnostics():
    """Read-only packaging/support check; never include titles or issue controls."""
    service = MediaService()
    try:
        service.start()
        deadline = time.monotonic() + 7
        while time.monotonic() < deadline:
            snapshot = service.get_update()
            if snapshot is not None:
                return {"available": snapshot.available, "session_count": len(snapshot.sessions),
                        "message": snapshot.message}
            time.sleep(0.05)
        return {"available": False, "session_count": 0, "message": "Media discovery timed out."}
    finally:
        service.close()


class WinRTMediaBackend:
    def __init__(self):
        self.manager = self.runtime = None
        self.sessions = {}
        self._next_id = 1

    async def connect(self):
        # Lazy imports keep timer-only use working without optional media packages.
        import winrt.runtime as runtime
        import winrt.windows.foundation
        import winrt.windows.foundation.collections
        import winrt.windows.media
        import winrt.windows.storage.streams
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager
        runtime.init_apartment(runtime.ApartmentType.MULTI_THREADED)
        self.runtime = runtime
        self.manager = await asyncio.wait_for(GlobalSystemMediaTransportControlsSessionManager.request_async(), 3)

    async def snapshot(self):
        native = list(self.manager.get_sessions())[:32]
        current = self.manager.get_current_session()
        previous = self.sessions
        self.sessions = {}
        current_id = ""

        async def describe(session, key, source):
            try:
                playback = session.get_playback_info()
                flags = playback.controls
                controls = []
                if (flags.is_play_pause_toggle_enabled or flags.is_play_enabled or flags.is_pause_enabled):
                    controls.append("toggle")
                for action, enabled in (("next", flags.is_next_enabled),
                                        ("previous", flags.is_previous_enabled), ("stop", flags.is_stop_enabled)):
                    if enabled:
                        controls.append(action)
                title = artist = ""
                try:
                    properties = await asyncio.wait_for(session.try_get_media_properties_async(), 1.5)
                    if properties:
                        title, artist = clean_text(properties.title), clean_text(properties.artist)
                except (OSError, asyncio.TimeoutError):
                    pass  # A slow metadata provider must not disable its transport controls.
                status = playback.playback_status.name.replace("_", " ").title()
                return MediaItem(key, source, title, artist, status, tuple(controls))
            except OSError:
                return None  # The session can disappear between enumeration and inspection.

        requests = []
        for session in native:
            try:
                source = session.source_app_user_model_id
            except OSError:
                continue
            key = next((key for key, old in previous.items() if old == session), None)
            if key is None:
                key = f"{source}\x1f{self._next_id}"
                self._next_id += 1
            self.sessions[key] = session
            if session == current:
                current_id = key
            requests.append(describe(session, key, source))
        items = tuple(item for item in await asyncio.gather(*requests) if item is not None)
        return MediaSnapshot(True, items, current_id, "")

    async def command(self, key, action):
        session = self.sessions.get(key)
        if session is None:
            return False
        playback = session.get_playback_info()
        flags = playback.controls
        if action == "toggle":
            if flags.is_play_pause_toggle_enabled:
                operation = session.try_toggle_play_pause_async()
            elif playback.playback_status.name == "PLAYING" and flags.is_pause_enabled:
                operation = session.try_pause_async()
            elif flags.is_play_enabled:
                operation = session.try_play_async()
            else:
                return False
        else:
            method, supported = {"next": ("try_skip_next_async", flags.is_next_enabled),
                                 "previous": ("try_skip_previous_async", flags.is_previous_enabled),
                                 "stop": ("try_stop_async", flags.is_stop_enabled)}[action]
            if not supported:
                return False
            operation = getattr(session, method)()
        return bool(await asyncio.wait_for(operation, 2))

    def close(self):
        self.sessions.clear()
        self.manager = None
        if self.runtime is not None:
            self.runtime.uninit_apartment()
            self.runtime = None


class MediaService:
    """Own WinRT on one worker; communicate using immutable snapshots and queues."""
    def __init__(self, backend_factory=WinRTMediaBackend):
        self.backend_factory = backend_factory
        self.updates = queue.Queue(maxsize=1)
        self.commands = queue.Queue(maxsize=16)
        self.stopping = threading.Event()
        self.refresh_requested = threading.Event()
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._worker, name="PomodoroMedia", daemon=True)
        self.thread.start()

    def _publish(self, snapshot):
        try:
            self.updates.get_nowait()
        except queue.Empty:
            pass
        self.updates.put_nowait(snapshot)

    def get_update(self):
        try:
            return self.updates.get_nowait()
        except queue.Empty:
            return None

    def command(self, key, action):
        if action not in ACTIONS:
            raise ValueError("Unknown media command")
        try:
            self.commands.put_nowait((key, action))
            return True
        except queue.Full:
            return False

    def refresh(self):
        self.refresh_requested.set()

    def _worker(self):
        backend = None
        try:
            backend = self.backend_factory()
            asyncio.run(self._run(backend))
        except ImportError:
            self._publish(MediaSnapshot(message="Install media support for track detection."))
        except Exception:
            logging.getLogger(__name__).warning("Windows media sessions unavailable", exc_info=True)
            self._publish(MediaSnapshot(message="Windows media unavailable; media keys still work."))
        finally:
            if backend is not None:
                backend.close()

    async def _run(self, backend):
        await backend.connect()
        next_poll, message_until, message = 0, 0, ""
        while not self.stopping.is_set():
            changed = False
            for _ in range(16):
                if self.stopping.is_set():
                    break
                try:
                    key, action = self.commands.get_nowait()
                except queue.Empty:
                    break
                changed = True
                try:
                    success = await backend.command(key, action)
                except (OSError, asyncio.TimeoutError):
                    success = False
                message = "" if success else "Player closed or does not support this action."
                message_until = time.monotonic() + 4
            now = time.monotonic()
            if now >= next_poll or changed or self.refresh_requested.is_set():
                self.refresh_requested.clear()
                try:
                    snapshot = await backend.snapshot()
                    if message and now < message_until:
                        snapshot = replace(snapshot, message=message)
                    self._publish(snapshot)
                except OSError:
                    self._publish(MediaSnapshot(message="Media session changed; retrying…"))
                next_poll = time.monotonic() + 1
            await asyncio.sleep(0.1)

    def close(self):
        self.stopping.set()
        if self.thread is not None:
            self.thread.join(timeout=2)
