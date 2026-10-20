import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['QIANG_DATA_DIR'] = '/tmp/qiangquote-test-bootstrap'
os.environ['QIANG_BOOTSTRAP_PASSWORD'] = 'Test-Only-Password-2026'

import pytest
from fastapi.testclient import TestClient
from backend import store
from backend.main import app, bootstrap


@pytest.fixture
def clients(tmp_path, monkeypatch):
    for key in ['QIANG_AI_GATEWAY_URL', 'QIANG_AI_GATEWAY_TOKEN', 'QIANG_AI_MODEL', 'QIANG_AI_PROTOCOL']:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(store, 'ROOT', tmp_path)
    store.init()
    bootstrap()
    users = {}
    for name in ['admin', 'estimator', 'reviewer', 'other']:
        c = TestClient(app)
        assert c.post('/api/v1/auth/login', json={'username': name, 'password': 'Test-Only-Password-2026'}).status_code == 200
        users[name] = c
    yield users
    for c in users.values():
        c.close()
