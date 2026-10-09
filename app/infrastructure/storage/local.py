"""Safe local-disk implementation of StorageBackend."""

import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from app.infrastructure.storage.base import StorageBackend


class LocalDiskStorage(StorageBackend):
    """Store generated files below one configured directory."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        """Resolve a relative key and reject traversal or absolute paths."""
        relative = PurePosixPath(key)
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise ValueError("Storage key must be a safe relative path")
        destination = (self._root / Path(*relative.parts)).resolve()
        if not destination.is_relative_to(self._root):
            raise ValueError("Storage key escapes the configured storage directory")
        return destination

    def save(self, key: str, content: bytes) -> None:
        """Write bytes through a same-directory temporary file and atomic replace."""
        destination = self._resolve(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=destination.parent,
                prefix=".certificate-",
                delete=False,
            ) as temporary:
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_name = temporary.name
            os.replace(temporary_name, destination)
        finally:
            if temporary_name and os.path.exists(temporary_name):
                os.unlink(temporary_name)

    def open_file(self, key: str) -> BinaryIO:
        """Open a stored object without exposing its absolute path."""
        return self._resolve(key).open("rb")

    def delete(self, key: str) -> None:
        """Delete a stored object when present."""
        self._resolve(key).unlink(missing_ok=True)
