"""Projects (one folder per device under study) and session files (JSON Lines)."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .model import Chunk, Marker

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}$")


def default_base() -> Path:
    return Path.home() / "Tapline"


def _check_name(name: str) -> str:
    if not isinstance(name, str) or not _NAME.match(name) or ".." in name:
        raise ValueError("Use letters, numbers, spaces, dot, dash or underscore (max 64 characters)")
    return name


class SessionWriter:
    def __init__(self, path: Path, meta: dict):
        self.path = path
        self.name = path.name
        self._f = open(path, "a", encoding="utf-8")
        self._line({"type": "meta", "created": time.time(), **meta})

    def _line(self, d: dict):
        if self._f.closed:
            return
        self._f.write(json.dumps(d, ensure_ascii=False) + "\n")
        self._f.flush()

    def write_chunk(self, c: Chunk):
        self._line({"type": "chunk", **c.to_json()})

    def write_marker(self, m: Marker):
        self._line({"type": "marker", **m.to_json()})

    def write_meta(self, d: dict):
        self._line({"type": "meta", **d})

    def close(self):
        if not self._f.closed:
            self._f.close()


class Project:
    def __init__(self, root: Path):
        self.root = Path(root)
        (self.root / "sessions").mkdir(parents=True, exist_ok=True)

    @property
    def name(self) -> str:
        return self.root.name

    @classmethod
    def open_or_create(cls, name: str, base: Path | None = None) -> "Project":
        return cls(Path(base or default_base()) / _check_name(name))

    @staticmethod
    def list_projects(base: Path | None = None) -> list[str]:
        b = Path(base or default_base())
        return sorted(p.name for p in b.iterdir() if p.is_dir()) if b.exists() else []

    @property
    def protocol_path(self) -> Path:
        return self.root / "protocol.json"

    def new_session(self, meta: dict) -> SessionWriter:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = self.root / "sessions" / f"{stamp}.jsonl"
        i = 1
        while path.exists():
            path = self.root / "sessions" / f"{stamp}-{i}.jsonl"
            i += 1
        return SessionWriter(path, meta)

    def list_sessions(self) -> list[str]:
        return sorted(p.name for p in (self.root / "sessions").glob("*.jsonl"))

    def load_session(self, name: str) -> tuple[list[Chunk], list[Marker], dict]:
        if "/" in name or "\\" in name or ".." in name or not name.endswith(".jsonl"):
            raise ValueError("Bad session name")
        chunks, markers, meta = [], [], {}
        with open(self.root / "sessions" / name, encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                    kind = d.pop("type")
                    if kind == "chunk":
                        chunks.append(Chunk.from_json(d))
                    elif kind == "marker":
                        markers.append(Marker.from_json(d))
                    elif kind == "meta":
                        meta.update(d)
                except (ValueError, KeyError, TypeError):
                    continue  # e.g. last line cut off by a crash
        return chunks, markers, meta
