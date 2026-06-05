"""In-memory loaded-file sessions for web plot requests."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException

_sessions: dict[str, dict[str, Any]] = {}


def create_session(path: Path, obj: dict[str, Any]) -> str:
    session_id = str(uuid.uuid4())
    _sessions[session_id] = {"path": path, "name": path.name, "obj": obj}
    return session_id


def get_session(session_id: str) -> dict[str, Any]:
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired.")
    return session
