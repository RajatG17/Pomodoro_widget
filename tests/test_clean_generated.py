from pathlib import Path
import tempfile
import unittest

from tools.clean_generated import clean_generated


class CleanupTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.disposable = ("__pycache__/pomodoro.pyc", "tests/__pycache__/test.pyc",
                           "build/PomodoroTimer/Analysis-00.toc",
                           "build/PomodoroTimer/localpycs/struct.pyc")
        self.preserved = ("pomodoro.py", "dist/PomodoroTimer.exe", ".pomodoro_state.json",
                          "__pycache__/notes.txt", "build/PomodoroTimer/custom.txt",
                          "PomodoroTimer.spec")
        for relative in (*self.disposable, *self.preserved):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("keep or clean", encoding="utf-8")

    def test_cleanup_removes_only_known_generated_files(self):
        self.assertEqual(set(clean_generated(self.root)), set(self.disposable))
        for relative in self.preserved:
            self.assertTrue((self.root / relative).exists(), relative)

    def test_dry_run_does_not_change_files(self):
        self.assertEqual(set(clean_generated(self.root, dry_run=True)), set(self.disposable))
        for relative in (*self.disposable, *self.preserved):
            self.assertTrue((self.root / relative).exists(), relative)

    def test_spec_deletion_requires_explicit_option(self):
        clean_generated(self.root, include_spec=True)
        self.assertFalse((self.root / "PomodoroTimer.spec").exists())
        self.assertTrue((self.root / "dist/PomodoroTimer.exe").exists())

    def test_cleanup_does_not_follow_cache_directory_links(self):
        target = self.root / "valuable"
        target.mkdir()
        (target / "keep.pyc").write_bytes(b"valuable")
        link = self.root / "tools" / "__pycache__"
        link.parent.mkdir()
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"Directory symlinks unavailable: {error}")
        clean_generated(self.root)
        self.assertEqual((target / "keep.pyc").read_bytes(), b"valuable")


if __name__ == "__main__":
    unittest.main()
