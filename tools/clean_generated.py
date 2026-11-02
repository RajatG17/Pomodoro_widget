"""Remove only known disposable build/cache files, never the distributed app."""
import argparse
from pathlib import Path


CACHE_DIRS = ("__pycache__", "tests/__pycache__", "tools/__pycache__",
              "build/PomodoroTimer/localpycs")
BUILD_FILES = ("Analysis-00.toc", "EXE-00.toc", "PKG-00.toc", "PYZ-00.toc",
               "PYZ-00.pyz", "PomodoroTimer.pkg", "base_library.zip",
               "warn-PomodoroTimer.txt", "xref-PomodoroTimer.html")


def clean_generated(root, dry_run=False, include_spec=False):
    root = Path(root).resolve()

    def safe(path):
        # Do not follow links/junctions out of the project, even for a known name.
        return not any(parent.is_symlink() or getattr(parent, "is_junction", lambda: False)()
                       for parent in (path, *path.parents) if parent != root and root in parent.parents)

    candidates = []
    for relative in CACHE_DIRS:
        directory = root / relative
        if safe(directory) and directory.is_dir():
            candidates.extend(path for path in directory.iterdir()
                              if path.suffix in (".pyc", ".pyo"))
    candidates.extend(root / "build" / "PomodoroTimer" / name for name in BUILD_FILES)
    if include_spec:
        candidates.append(root / "PomodoroTimer.spec")
    removed = []
    for path in candidates:
        if safe(path) and path.is_file():
            removed.append(path.relative_to(root).as_posix())
            if not dry_run:
                path.unlink()
    if not dry_run:
        for relative in (*CACHE_DIRS, "build/PomodoroTimer", "build"):
            directory = root / relative
            if safe(directory) and directory.is_dir():
                try:
                    directory.rmdir()  # Only empty directories; keep unknown files.
                except OSError:
                    pass
    return removed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="List without deleting")
    parser.add_argument("--include-spec", action="store_true",
                        help="Also remove PomodoroTimer.spec; omit if you customized it")
    args = parser.parse_args()
    for path in clean_generated(Path(__file__).resolve().parent.parent,
                                dry_run=args.dry_run, include_spec=args.include_spec):
        print(path)


if __name__ == "__main__":
    main()
