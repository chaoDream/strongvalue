import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.getenv('QIANG_DATA_DIR', 'data')).resolve()


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid.uuid4().hex


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def connect():
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    if hasattr(os, 'getuid') and ROOT.stat().st_uid == os.getuid():
        ROOT.chmod(0o700)
    c = sqlite3.connect(ROOT / 'qiangquote.db', timeout=30)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    return c


def init():
    with connect() as c:
        c.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, tenant TEXT NOT NULL, username TEXT UNIQUE, password TEXT, role TEXT);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id TEXT, expires TEXT);
        CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, tenant TEXT NOT NULL, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, tenant TEXT, data TEXT, hash TEXT);
        CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY, project_id TEXT, tenant TEXT, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS report_templates(id TEXT PRIMARY KEY, tenant TEXT NOT NULL, family TEXT NOT NULL, version INTEGER NOT NULL, data TEXT NOT NULL, UNIQUE(tenant,family,version));
        CREATE TABLE IF NOT EXISTS organization_resources(id TEXT PRIMARY KEY, tenant TEXT NOT NULL, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS organization_quotas(id TEXT PRIMARY KEY, tenant TEXT NOT NULL, data TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS template_no_update BEFORE UPDATE ON report_templates BEGIN SELECT RAISE(ABORT, 'immutable template'); END;
        CREATE TRIGGER IF NOT EXISTS template_no_delete BEFORE DELETE ON report_templates BEGIN SELECT RAISE(ABORT, 'immutable template'); END;
        CREATE TRIGGER IF NOT EXISTS artifact_no_update BEFORE UPDATE ON artifacts BEGIN SELECT RAISE(ABORT, 'immutable artifact'); END;
        CREATE TRIGGER IF NOT EXISTS artifact_no_delete BEFORE DELETE ON artifacts BEGIN SELECT RAISE(ABORT, 'immutable artifact'); END;
        CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT, 'immutable audit'); END;
        CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT, 'immutable audit'); END;
        ''')
        if 'active' not in {row['name'] for row in c.execute('PRAGMA table_info(users)')}:
            c.execute('ALTER TABLE users ADD COLUMN active INTEGER NOT NULL DEFAULT 1')


@contextmanager
def transaction():
    c = connect()
    try:
        c.execute('BEGIN IMMEDIATE')
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def audit(c, p, actor, action, detail):
    prev = c.execute('SELECT hash FROM audit WHERE project_id=? ORDER BY seq DESC LIMIT 1', (p['id'],)).fetchone()
    event = {'id': uid(), 'actor': actor['username'], 'role': actor['role'], 'action': action,
             'detail': detail, 'timestamp': now(), 'previous_hash': prev['hash'] if prev else ''}
    c.execute('INSERT INTO audit(project_id,tenant,data,hash) VALUES(?,?,?,?)',
              (p['id'], p['tenant'], canonical(event), digest(event)))


def save(c, p):
    p['updated_at'] = now()
    c.execute('UPDATE projects SET data=? WHERE id=?', (canonical(p), p['id']))
