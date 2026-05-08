from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "history.db"
    monkeypatch.setenv("APPROVE_WATCH_DB", str(db))
    from approve_watch.db import init_db

    init_db(db)
    return db
