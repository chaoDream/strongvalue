#!/usr/bin/env python3
"""Verify a QiangQuote delivery ZIP without extracting or executing its contents."""
import hashlib
import json
import sys
import zipfile
from pathlib import PurePosixPath

MAX_BYTES = 200 * 1024 * 1024


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def verify(path):
    issues = []
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            names = [m.filename for m in members]
            if len(names) != len(set(names)) or len(names) > 5000:
                raise ValueError('重复文件名或文件数量超限')
            if sum(m.file_size for m in members) > MAX_BYTES:
                raise ValueError('解压后总大小超过200MB校验上限')
            if any(n.startswith('/') or '\\' in n or '..' in PurePosixPath(n).parts for n in names):
                raise ValueError('包含不安全的文件路径')
            manifest = json.loads(archive.read('manifest.json'))
            if not isinstance(manifest, dict) or manifest.get('schema') != 'qiangquote-delivery-v1':
                raise ValueError('不支持的证据包版本')
            files = manifest['files']
            if len({f['path'] for f in files}) != len(files):
                raise ValueError('清单包含重复条目')
            if set(names) != {'manifest.json'} | {f['path'] for f in files}:
                raise ValueError('压缩包文件集合与清单不一致')
            for record in files:
                data = archive.read(record['path'])
                if len(data) != record['size'] or hashlib.sha256(data).hexdigest() != record['sha256']:
                    issues.append('文件内容校验失败：'+record['path'])
            audit = json.loads(archive.read('audit.json'))
            previous = ''
            for row in audit['events']:
                event = {k: v for k, v in row.items() if k != 'hash'}
                if row['hash'] != digest(event) or event['previous_hash'] != previous:
                    issues.append('审计链校验失败：'+str(event.get('id', '')))
                previous = row['hash']
            if previous != audit['head_hash'] or audit['event_count'] != len(audit['events']):
                issues.append('审计链末端或数量不一致')
            if 'quote.json' in names:
                quote = json.loads(archive.read('quote.json'))
                if digest({k: v for k, v in quote.items() if k != 'hash'}) != quote['hash'] or digest(quote['snapshot']) != quote['input_hash']:
                    issues.append('报价快照校验失败')
                if quote['hash'] != manifest['quote_hash']:
                    issues.append('报价版本与清单不一致')
            for doc in manifest['documents']:
                if hashlib.sha256(archive.read(doc['path'])).hexdigest() != doc['hash']:
                    issues.append('原件索引与文件不一致：'+doc['id'])
            return {'valid': not issues, 'issues': issues, 'file_count': len(files),
                    'status': manifest['status'], 'quote_version': manifest['quote_version'],
                    'notice': '仅校验包内一致性；哈希清单不是数字签名，不能证明来源真实性或专业结论。'}
    except (ValueError, KeyError, TypeError, AttributeError, OSError, zipfile.BadZipFile, RuntimeError) as exc:
        return {'valid': False, 'issues': ['证据包无法校验：'+str(exc)]}


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print('用法：python verify_delivery.py 证据包.zip')
        sys.exit(2)
    result = verify(sys.argv[1])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result['valid'] else 1)
