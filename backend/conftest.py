"""Shared test fixtures. Both live here so either test file can use either:
`database` for direct db access, `client` for the JSON routes."""

import db
import pytest
from fastapi.testclient import TestClient
from main import app


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "inventory.db"
    db.init(path)
    return path


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db.init(tmp_path / "inventory.db")
    return TestClient(app)
