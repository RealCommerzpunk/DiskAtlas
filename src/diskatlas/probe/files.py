"""Rekursiver Dateiindex eines eingehängten Volumes."""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Callable, Iterable, Iterator

from diskatlas.probe.types import FileRecord

ErrorCallback = Callable[[str, OSError], None]


def iter_files(
    root: str,
    exclude_dirs: Iterable[str] = (),
    one_filesystem: bool = True,
    on_error: ErrorCallback | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> Iterator[FileRecord]:
    """Liefert alle regulären Dateien unterhalb von `root`.

    Symlinks und Junctions werden nicht verfolgt; mit `one_filesystem` werden
    (unter POSIX) keine anderen Dateisysteme betreten, die unterhalb eingehängt sind.
    """
    root_path = os.path.abspath(root)
    if not os.path.isdir(root_path):
        raise FileNotFoundError(f"Verzeichnis nicht gefunden: {root_path}")
    prefix = root_path if root_path.endswith(os.sep) else root_path + os.sep
    patterns = [p.lower() for p in exclude_dirs]
    check_dev = one_filesystem and os.name != "nt"
    root_dev = os.stat(root_path).st_dev if check_dev else None

    stack = [root_path]
    while stack:
        if should_stop and should_stop():
            return
        current = stack.pop()
        try:
            iterator = os.scandir(current)
        except OSError as exc:
            if on_error:
                on_error(current, exc)
            continue
        with iterator:
            for entry in iterator:
                try:
                    if entry.is_symlink() or _is_junction(entry):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if _excluded(entry.name, patterns):
                            continue
                        if check_dev and entry.stat(follow_symlinks=False).st_dev != root_dev:
                            continue
                        stack.append(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        st = entry.stat(follow_symlinks=False)
                        rel = entry.path[len(prefix) :]
                        if os.sep != "/":
                            rel = rel.replace(os.sep, "/")
                        yield FileRecord(rel, st.st_size, st.st_mtime)
                except OSError as exc:
                    if on_error:
                        on_error(entry.path, exc)


def _excluded(name: str, patterns: list[str]) -> bool:
    lowered = name.lower()
    return any(fnmatch.fnmatchcase(lowered, p) for p in patterns)


def _is_junction(entry: os.DirEntry) -> bool:
    is_junction = getattr(entry, "is_junction", None)
    return bool(is_junction and is_junction())
