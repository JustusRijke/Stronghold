"""Shared test fixtures. Both live here so either test file can use either:
`database` for direct db access, `client` for the JSON routes."""

import crypto
import db
import pytest
from fastapi.testclient import TestClient
from main import app


@pytest.fixture
def database(tmp_path):
    """The data file (.sql); the working .db lives in a temp dir of db's own."""
    path = tmp_path / "inventory.sql"
    crypto.init(tmp_path / "secrets.key")  # real encryption, throwaway key
    db.init(path)
    return path


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    crypto.init(tmp_path / "secrets.key")  # real encryption, throwaway key
    db.init(tmp_path / "inventory.sql")
    return TestClient(app)
