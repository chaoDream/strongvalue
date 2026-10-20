"""Create a consistent sanitized data snapshot and offline dependency volumes."""
import hashlib
import json
import os
import platform
import shutil
import sqlite3
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def package(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    data = destination / 'data'
    data.mkdir()
    source = sqlite3.connect(f'file:{ROOT / "data/qiangquote.db"}?mode=ro', uri=True)
    target = sqlite3.connect(data / 'qiangquote.db')
    try:
        source.backup(target)
        target.execute('DELETE FROM sessions')
        target.execute("UPDATE users SET password='!reset-required'")
        target.commit()
        target.execute('PRAGMA journal_mode=DELETE')
        target.execute('VACUUM')
        assert target.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert target.execute('SELECT count(*) FROM sessions').fetchone()[0] == 0
        assert not target.execute("SELECT 1 FROM users WHERE password != '!reset-required'").fetchone()
    finally:
        source.close()
        target.close()
    for directory in ['originals', 'exports']:
        src = ROOT / 'data' / directory
        if src.exists():
            shutil.copytree(src, data / directory)
    archive = destination / 'dependencies.tar.gz'
    skipped = []
    def include(info):
        if '__pycache__' in Path(info.name).parts or info.name.endswith(('.pyc', '.DS_Store')):
            return None
        if info.issym() and os.path.isabs(info.linkname):
            skipped.append(info.name)
            return None
        return info
    with tarfile.open(archive, 'w:gz', compresslevel=6) as tar:
        for name in ['.venv', 'frontend/node_modules', '.pnpm-store']:
            if (ROOT / name).exists():
                tar.add(ROOT / name, arcname=name, filter=include)
    volumes = []
    with archive.open('rb') as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            name = f'dependencies.tar.gz.part{len(volumes):03d}'
            (destination / name).write_bytes(chunk)
            volumes.append({'path': name, 'size': len(chunk), 'sha256': hashlib.sha256(chunk).hexdigest()})
    archive.unlink()
    files = [{'path': str(p.relative_to(destination)), 'size': p.stat().st_size,
              'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in data.rglob('*') if p.is_file()]
    manifest = {'platform': platform.platform(), 'machine': platform.machine(), 'python': platform.python_version(),
                'data_files': files, 'dependency_volumes': volumes, 'excluded_absolute_symlinks': skipped,
                'credentials': 'Sessions removed; all user password hashes replaced. Reset passwords before login.',
                'portability': 'Dependency archive is a local platform snapshot. Install Python separately and recreate venv; use lock files on other platforms.'}
    (destination / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'files': len(files), 'volumes': len(volumes), 'compressed_bytes': sum(v['size'] for v in volumes)}))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('destination', help='New output directory; never overwrites an existing snapshot')
    package(parser.parse_args().destination)
