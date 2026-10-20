import sqlite3
import tarfile
import pytest
from scripts import package_transfer, restore_transfer


def test_snapshot_removes_authentication_but_preserves_source_and_validates_restore(tmp_path, monkeypatch):
    root = tmp_path / 'source'
    (root / 'data/exports').mkdir(parents=True)
    (root / 'data/exports/report').write_bytes(b'test-export')
    db = root / 'data/qiangquote.db'
    with sqlite3.connect(db) as c:
        c.executescript("CREATE TABLE users(id TEXT, password TEXT); CREATE TABLE sessions(token TEXT);"
                        "INSERT INTO users VALUES('test-user','private-password-hash');"
                        "INSERT INTO sessions VALUES('private-session');")
    (root / '.venv/lib').mkdir(parents=True)
    (root / '.venv/lib/package.txt').write_text('test-dependency')
    (root / '.venv/python').symlink_to('/usr/bin/python3')
    monkeypatch.setattr(package_transfer, 'ROOT', root)
    snapshot = tmp_path / 'snapshot'
    package_transfer.package(snapshot)
    with sqlite3.connect(db) as c:
        assert c.execute('SELECT password FROM users').fetchone()[0] == 'private-password-hash'
        assert c.execute('SELECT count(*) FROM sessions').fetchone()[0] == 1
    with sqlite3.connect(snapshot / 'data/qiangquote.db') as c:
        assert c.execute('SELECT password FROM users').fetchone()[0] == '!reset-required'
        assert c.execute('SELECT count(*) FROM sessions').fetchone()[0] == 0
    assert b'private-password-hash' not in (snapshot / 'data/qiangquote.db').read_bytes()
    target = tmp_path / 'restored'
    archive = tmp_path / 'deps.tar.gz'
    restore_transfer.restore(snapshot, target, archive)
    assert (target / 'exports/report').read_bytes() == b'test-export'
    with tarfile.open(archive) as tar:
        assert '.venv/lib/package.txt' in tar.getnames()
        assert '.venv/python' not in tar.getnames()
    with pytest.raises(ValueError, match='already exists'):
        restore_transfer.restore(snapshot, target)
    (snapshot / 'data/exports/report').write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='checksum'):
        restore_transfer.restore(snapshot, tmp_path / 'never-created')
    assert not (tmp_path / 'never-created').exists()
