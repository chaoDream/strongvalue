"""Verify a published snapshot, restore data to a new directory, optionally join dependencies."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path


def verified_files(snapshot):
    snapshot = Path(snapshot).resolve()
    manifest = json.loads((snapshot / 'manifest.json').read_text())
    entries = manifest['data_files'] + manifest['dependency_volumes']
    checked = {}
    for entry in entries:
        path = (snapshot / entry['path']).resolve()
        if not path.is_relative_to(snapshot) or path == snapshot:
            raise ValueError('Invalid snapshot path')
        if path.stat().st_size != entry['size'] or hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
            raise ValueError('Snapshot checksum mismatch: ' + entry['path'])
        checked[entry['path']] = path
    return manifest, checked


def restore(snapshot, data_dir, archive=None):
    manifest, checked = verified_files(snapshot)
    target = Path(data_dir)
    if target.exists() or (archive and Path(archive).exists()):
        raise ValueError('Destination already exists; no files overwritten')
    for entry in manifest['data_files']:
        relative = Path(entry['path']).relative_to('data')
        if '..' in relative.parts:
            raise ValueError('Invalid data path')
    target.mkdir(parents=True, mode=0o700)
    for entry in manifest['data_files']:
        destination = target / Path(entry['path']).relative_to('data')
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(checked[entry['path']], destination)
        destination.chmod(0o600)
    if archive:
        with Path(archive).open('xb') as output:
            for entry in manifest['dependency_volumes']:
                with checked[entry['path']].open('rb') as part:
                    shutil.copyfileobj(part, output)
    print('迁移文件校验通过；数据已恢复。登录前请重置成员密码。')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('snapshot')
    parser.add_argument('--data-dir', required=True, help='Must be a new directory')
    parser.add_argument('--dependencies-archive', help='Optional new archive path to combine verified volumes')
    args = parser.parse_args()
    restore(args.snapshot, args.data_dir, args.dependencies_archive)
