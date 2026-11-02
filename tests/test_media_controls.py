import asyncio
import gc
import os
from pathlib import Path
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from media_controls import (MediaItem, MediaSnapshot, MediaService, WinRTMediaBackend,
                            choose_session, clean_text, player_name)
from player_launcher import PlayerWindow, choose_window, open_player


def session(source, title="Track", status="PLAYING", **overrides):
    flags = dict(is_play_pause_toggle_enabled=True, is_play_enabled=True, is_pause_enabled=True,
                 is_next_enabled=True, is_previous_enabled=True, is_stop_enabled=True)
    flags.update(overrides)
    return SimpleNamespace(source_app_user_model_id=source,
        get_playback_info=lambda: SimpleNamespace(controls=SimpleNamespace(**flags),
                                                 playback_status=SimpleNamespace(name=status)),
        try_get_media_properties_async=AsyncMock(return_value=SimpleNamespace(title=title, artist="Artist")),
        try_toggle_play_pause_async=AsyncMock(return_value=True), try_pause_async=AsyncMock(return_value=True),
        try_play_async=AsyncMock(return_value=True), try_skip_next_async=AsyncMock(return_value=True),
        try_skip_previous_async=AsyncMock(return_value=True), try_stop_async=AsyncMock(return_value=True))


class MediaSelectionTests(unittest.TestCase):
    def test_auto_prefers_playing_media_over_current_paused_session(self):
        browser = MediaItem("browser", "Chrome", status="Paused")
        music = MediaItem("music", "Spotify.exe", status="Playing")
        snapshot = MediaSnapshot(True, (browser, music), "browser", "")
        self.assertEqual(choose_session(snapshot), music)
        self.assertEqual(choose_session(snapshot, preferred="browser"), browser)
        self.assertIsNone(choose_session(MediaSnapshot()))

    def test_missing_selection_recovers_to_available_player(self):
        item = MediaItem("new", "firefox.exe", status="Playing")
        self.assertEqual(choose_session(MediaSnapshot(True, (item,)), "closed"), item)

    def test_names_and_untrusted_metadata_are_display_only(self):
        self.assertEqual(player_name("SpotifyAB.SpotifyMusic_hash!Spotify"), "Spotify")
        self.assertEqual(player_name("msedge.exe"), "Microsoft Edge")
        self.assertEqual(clean_text("a\n\tb"), "a b")
        self.assertEqual(len(clean_text("x" * 1000)), 512)


class NativeBackendUnitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.browser, self.music = session("Chrome", "Video"), session("Spotify.exe", "Song")
        self.backend = WinRTMediaBackend()
        self.backend.manager = SimpleNamespace(get_sessions=lambda: [self.browser, self.music],
                                                get_current_session=lambda: self.browser)

    async def test_browser_and_music_metadata_and_controls_are_discovered(self):
        snapshot = await self.backend.snapshot()
        self.assertTrue(snapshot.available)
        self.assertEqual([item.title for item in snapshot.sessions], ["Video", "Song"])
        self.assertEqual(snapshot.current_id, snapshot.sessions[0].id)
        self.assertIn("next", snapshot.sessions[1].controls)

    async def test_commands_target_exact_session_not_another_player(self):
        snapshot = await self.backend.snapshot()
        self.assertTrue(await self.backend.command(snapshot.sessions[1].id, "next"))
        self.music.try_skip_next_async.assert_awaited_once()
        self.browser.try_skip_next_async.assert_not_called()
        self.assertFalse(await self.backend.command("closed-session", "toggle"))

    async def test_session_identifiers_survive_enumeration_reordering(self):
        self.music.source_app_user_model_id = "Chrome"
        first = await self.backend.snapshot()
        music_id = first.sessions[1].id
        self.backend.manager.get_sessions = lambda: [self.music, self.browser]
        second = await self.backend.snapshot()
        self.assertEqual(second.sessions[0].id, music_id)
        self.assertTrue(await self.backend.command(music_id, "next"))
        self.music.try_skip_next_async.assert_awaited_once()

    async def test_unsupported_actions_are_not_sent(self):
        self.music = session("Spotify.exe", is_next_enabled=False)
        snapshot = await self.backend.snapshot()
        self.assertNotIn("next", snapshot.sessions[1].controls)
        self.assertFalse(await self.backend.command(snapshot.sessions[1].id, "next"))
        self.music.try_skip_next_async.assert_not_called()

    async def test_toggle_uses_pause_when_only_separate_controls_exist(self):
        self.music = session("Spotify.exe", is_play_pause_toggle_enabled=False)
        snapshot = await self.backend.snapshot()
        self.assertTrue(await self.backend.command(snapshot.sessions[1].id, "toggle"))
        self.music.try_pause_async.assert_awaited_once()

    async def test_metadata_failure_keeps_transport_controls_working(self):
        self.music.try_get_media_properties_async.side_effect = OSError("player changed")
        snapshot = await self.backend.snapshot()
        self.assertEqual(snapshot.sessions[1].title, "")
        self.assertIn("toggle", snapshot.sessions[1].controls)


class MediaServiceTests(unittest.TestCase):
    def test_worker_delivers_snapshots_commands_and_stops_without_tk(self):
        backend = SimpleNamespace(connect=AsyncMock(), snapshot=AsyncMock(return_value=MediaSnapshot(True)),
                                  command=AsyncMock(return_value=True), close=Mock())
        service = MediaService(lambda: backend)
        try:
            service.start()
            service.command("player", "toggle")
            deadline = time.monotonic() + 3
            update = None
            while time.monotonic() < deadline:
                update = service.get_update()
                if update is not None and backend.command.await_count:
                    break
                time.sleep(0.02)
            self.assertTrue(update.available)
            backend.command.assert_awaited_once_with("player", "toggle")
        finally:
            service.close()
        self.assertFalse(service.thread.is_alive())
        backend.close.assert_called_once()

    def test_missing_packages_do_not_break_timer_or_send_playback_commands(self):
        backend = SimpleNamespace(connect=AsyncMock(side_effect=ImportError("optional winrt")), close=Mock())
        service = MediaService(lambda: backend)
        service._worker()
        self.assertFalse(service.get_update().available)
        backend.close.assert_called_once()

    def test_command_queue_is_bounded(self):
        service = MediaService()
        for _ in range(16):
            self.assertTrue(service.command("player", "next"))
        self.assertFalse(service.command("player", "next"))
        with self.assertRaises(ValueError):
            service.command("player", "not-a-control")


class PlayerLauncherTests(unittest.TestCase):
    def test_browser_window_with_track_title_is_preferred(self):
        windows = [PlayerWindow(1, "chrome.exe", "", "Other tab"),
                   PlayerWindow(2, "chrome.exe", "", "Current song - Chrome"),
                   PlayerWindow(3, "spotify.exe", "", "Current song")]
        self.assertEqual(choose_window("Chrome", "Current song", windows).handle, 2)

    def test_packaged_app_identity_is_supported(self):
        app_id = "Example.Music_hash!Player"
        self.assertEqual(choose_window(app_id, "", [PlayerWindow(7, "ApplicationFrameHost.exe", app_id, "Music")]).handle, 7)

    def test_existing_player_is_revealed_without_launching_duplicate(self):
        activate, launch = Mock(return_value=True), Mock()
        self.assertTrue(open_player("Spotify.exe", windows=[PlayerWindow(9, "spotify.exe", "", "Music")],
                                    activate=activate, launch=launch))
        activate.assert_called_once_with(9)
        launch.assert_not_called()

    def test_tray_player_main_window_can_be_revealed_without_running_an_executable(self):
        activate, launch = Mock(return_value=True), Mock()
        self.assertTrue(open_player("MusicBee.exe", windows=[PlayerWindow(10, "MusicBee.exe", "", "Song", False)],
                                    activate=activate, launch=launch))
        activate.assert_called_once_with(10)
        launch.assert_not_called()

    def test_only_registered_packaged_app_identifiers_can_be_launched(self):
        launch = Mock()
        self.assertTrue(open_player("Example.Music_hash!Player", windows=[], launch=launch))
        launch.assert_called_once_with("shell:AppsFolder\\Example.Music_hash!Player")
        for source in ("https://evil.example", "C:\\arbitrary.exe", "cmd.exe /c start", "spotify:"):
            self.assertFalse(open_player(source, windows=[], launch=launch))
        self.assertEqual(launch.call_count, 1)


@unittest.skipUnless(os.environ.get("POMODORO_TEST_MEDIA") == "1", "Opt in to read-only native media discovery")
class NativeMediaTests(unittest.TestCase):
    def test_native_session_discovery_without_playback_or_launch(self):
        service = MediaService()
        try:
            service.start()
            deadline = time.monotonic() + 7
            snapshot = None
            while time.monotonic() < deadline:
                snapshot = service.get_update()
                if snapshot is not None:
                    break
                time.sleep(0.05)
            self.assertIsNotNone(snapshot)
            self.assertTrue(snapshot.available, snapshot.message)
            self.assertIsInstance(snapshot.sessions, tuple)
        finally:
            service.close()
        self.assertFalse(service.thread.is_alive())

    def test_live_widget_discovers_media_without_blocking_tk_or_controlling_playback(self):
        import tkinter as tk
        import pomodoro
        gc.collect()
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(pomodoro, "STATE_FILE", str(Path(directory) / "state.json")):
            root = tk.Tk()
            root.withdraw()
            app = pomodoro.PomodoroApp(root)
            heartbeat = []
            try:
                app.enable_media()
                app.notebook.select(app.ui.pages["Music"])
                root.deiconify()
                root.after(50, lambda: heartbeat.append(True))
                deadline = time.monotonic() + 7
                while time.monotonic() < deadline:
                    root.update()
                    if app.media_snapshot.available and heartbeat:
                        break
                    time.sleep(0.02)
                self.assertTrue(heartbeat, "Tk event loop was blocked")
                self.assertTrue(app.media_snapshot.available, app.media_snapshot.message)
                self.assertFalse(app.running)
                worker = app._media_service.thread
            finally:
                app._on_close()
            self.assertFalse(worker.is_alive())
        gc.collect()


if __name__ == "__main__":
    unittest.main()
