"""Storage interface for generated certificate files."""

from typing import BinaryIO, Protocol


class StorageBackend(Protocol):
    """Minimal storage contract used by generation and download services."""

    def save(self, key: str, content: bytes) -> None:
        """Atomically persist bytes under an opaque storage key."""

    def open_file(self, key: str) -> BinaryIO:
        """Open a stored object for sequential reading."""

    def delete(self, key: str) -> None:
        """Remove a stored object if it exists."""
