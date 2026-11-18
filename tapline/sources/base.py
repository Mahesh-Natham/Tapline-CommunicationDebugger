"""Common interface for anything that produces raw bytes."""
from __future__ import annotations

from typing import AsyncIterator

from ..model import Chunk


class Source:
    name = "source"
    error: str | None = None

    async def start(self) -> None:
        raise NotImplementedError

    async def stop(self) -> None:
        raise NotImplementedError

    def chunks(self) -> AsyncIterator[Chunk]:
        raise NotImplementedError

    async def write(self, data: bytes) -> None:
        raise NotImplementedError(f"{self.name} cannot send data")


class SourceError(Exception):
    """A source could not start; the message is written for the user."""
