"""Offline delivery with original bytes, selected snapshot, and scoped audit chain."""
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from . import main as core, store
from .reports import export_report

router = APIRouter(prefix='/api/v1')
MAX_ORIGINAL_BYTES = 100 * 1024 * 1024
MAX_DOCUMENTS = 1000


def file_digest(path):
    hasher = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


def audit_snapshot(c, p):
    events, issues, previous = [], [], ''
    for row in c.execute('SELECT data,hash FROM audit WHERE project_id=? AND tenant=? ORDER BY seq', (p['id'], p['tenant'])):
        try:
            event = json.loads(row['data'])
            if not isinstance(event, dict):
                raise ValueError('invalid audit event')
            if store.digest(event) != row['hash'] or event.get('previous_hash') != previous:
                issues.append('项目审计链校验失败，不能交付完整证据包')
            events.append({**event, 'hash': row['hash']})
            previous = row['hash']
        except (ValueError, TypeError):
            issues.append('项目审计事件无法读取')
    if not events:
        issues.append('项目审计链缺失')
    return {'events': events, 'event_count': len(events), 'head_hash': previous, 'captured_at': store.now()}, issues


def inspect_delivery(c, p, q):
    snapshot = q['snapshot'] if q else p
    audit, issues = audit_snapshot(c, p)
    if q and (store.digest(q['snapshot']) != q['input_hash'] or store.digest({k: v for k, v in q.items() if k != 'hash'}) != q['hash']):
        issues.append('所选报价快照哈希不一致')
    documents, files, size = [], {}, 0
    if len(snapshot['documents']) > MAX_DOCUMENTS:
        issues.append('原件超过1000份，请缩小项目交付范围')
    else:
        for doc in snapshot['documents']:
            sha = doc.get('hash', '')
            if not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{64}', sha):
                issues.append('原件哈希格式无效：'+doc['name'])
                continue
            path = store.ROOT/'originals'/sha
            if sha not in files:
                try:
                    n = path.stat().st_size
                    size += n
                    if size > MAX_ORIGINAL_BYTES:
                        issues.append('去重原件总大小超过本地证据包100MB上限')
                        break
                    if file_digest(path) != sha:
                        issues.append('原件内容哈希不一致：'+doc['name'])
                    files[sha] = {'path': 'originals/'+sha, 'size': n, 'source': path}
                except OSError:
                    issues.append('原件缺失或不可读取：'+doc['name'])
                    continue
            if doc.get('size') != files[sha]['size']:
                issues.append('原件登记大小不一致：'+doc['name'])
            documents.append({'id': doc['id'], 'name': doc['name'], 'hash': sha, 'path': files[sha]['path'],
                              'version': doc.get('version', 1), 'superseded': doc.get('superseded', False)})
    by_id = {d['id']: d for d in documents}
    for evidence in snapshot['evidence']:
        doc = by_id.get(evidence['document_id'])
        if not doc or doc['hash'] != evidence['document_hash']:
            issues.append('证据与原件版本关联不一致：'+evidence['id'])
    business = core.blockers(p)
    approved = bool(q and p['report_approval'] and p['report_approval']['quote_id'] == q['id'] and q['id'] not in p['stale_quotes'] and not business)
    return {'valid': not issues, 'issues': list(dict.fromkeys(issues)), 'document_count': len(documents),
            'unique_original_count': len(files), 'original_bytes': size, 'quote_id': q['id'] if q else None,
            'quote_version': q['version'] if q else None, 'approved': approved,
            'status': '试点范围审核成果（无签章效力）' if approved else '未完成成果草稿 · 未经完整审核',
            'business_blockers': business, 'stale_quote': bool(q and q['id'] in p['stale_quotes']),
            'audit_event_count': audit['event_count'], 'audit_head_hash': audit['head_hash'],
            'notice': '仅检查文件及快照完整性，不代表专业验收；包内包含所选版本原件，请按项目保密范围交付。'}, files, documents, audit


@router.get('/projects/{pid}/delivery-check')
def check_delivery(pid: str, quote_id: str = '', unpriced: bool = False, a=Depends(core.actor)):
    with store.transaction() as c:
        p = core.project(c, pid, a)
        if unpriced and quote_id:
            raise HTTPException(422, '不能同时选择报价版本和当前无价草稿')
        q = None if unpriced else (core.find(p, 'quotes', quote_id) if quote_id else (p['quotes'][-1] if p['quotes'] else None))
        return inspect_delivery(c, p, q)[0]


def export_bundle(c, p, q, template=None):
    check, originals, documents, audit = inspect_delivery(c, p, q)
    if not check['valid']:
        raise HTTPException(409, '交付完整性检查未通过：'+'；'.join(check['issues']))
    files = {}
    for fmt in ['json', 'xlsx', 'pdf']:
        payload, _, suffix = export_report(p, q, fmt, check['approved'], template)
        files['reports/result.'+suffix] = payload
    if q:
        files['quote.json'] = store.canonical(q).encode()
    else:
        files['unpriced-snapshot.json'] = store.canonical({k: p[k] for k in ['documents', 'evidence', 'facts', 'components', 'takeoffs', 'boq', 'rules', 'reviews', 'coverage', 'document_requests', 'pricing_bases', 'quantity_diagnoses', 'quantity_batches', 'changes', 'applied_changes']}).encode()
    files['audit.json'] = store.canonical(audit).encode()
    files['delivery-check.json'] = store.canonical(check).encode()
    files['verify_delivery.py'] = (Path(__file__).resolve().parent.parent/'scripts'/'verify_delivery.py').read_bytes()
    files['README.txt'] = ('强·报价交付证据包\n状态：'+check['status']+'\n'
        'reports/ 内为同一选定报价版本的 PDF、Excel 和结构化成果。originals/ 按SHA256保存原件，原始文件名见manifest.json的documents索引。\n'
        'audit.json包含打包前本项目完整审计链（可能包含报价生成后的撤审、修改事件），不是跨企业日志。\n'
        '先将随包的verify_delivery.py保存到本地，再使用Python 3执行：python verify_delivery.py 本证据包.zip；无须安装依赖，校验时不会解压或执行其余包内文件。\n'
        '包内清单及哈希用于一致性检查，不是可信数字签名，不能证明来源真实性、专业正确性或抵御整体重制。请另行保存下载时的整包SHA256。\n'
        '包含原始资料及审核意见，请仅向有权接收的项目人员交付。\n').encode()
    for sha, record in originals.items():
        try:
            # Read no more than the inspected file size, detecting changes during export.
            with record['source'].open('rb') as source:
                payload = source.read(record['size']+1)
        except OSError:
            raise HTTPException(409, '打包时原件不可读取，未生成证据包') from None
        if len(payload) != record['size'] or hashlib.sha256(payload).hexdigest() != sha:
            raise HTTPException(409, '打包时原件发生变化，未生成证据包')
        files[record['path']] = payload
    if sum(map(len, files.values())) > 190 * 1024 * 1024:
        raise HTTPException(422, '交付文件超过本地证据包大小上限，请缩小范围')
    manifest = {'schema': 'qiangquote-delivery-v1', 'created_at': store.now(), 'project_id': p['id'],
                'project_name': q['project_name'] if q else p['name'], 'status': check['status'],
                'quote_id': q['id'] if q else None, 'quote_version': q['version'] if q else None,
                'quote_hash': q['hash'] if q else None, 'documents': documents,
                'files': [{'path': name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()} for name, data in sorted(files.items())]}
    files['manifest.json'] = store.canonical(manifest).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            archive.writestr(name, data)
    return output.getvalue(), 'application/zip', 'zip'


@router.get('/projects/{pid}/artifacts/{aid}/verify')
def verify_artifact(pid: str, aid: str, a=Depends(core.actor)):
    with store.connect() as c:
        core.project(c, pid, a)
        row = c.execute('SELECT data FROM artifacts WHERE id=? AND project_id=? AND tenant=?', (aid, pid, a['tenant'])).fetchone()
        if not row:
            raise HTTPException(404, '成果文件不存在或无权访问')
        item = json.loads(row['data'])
    path = store.ROOT/'exports'/item['hash']
    try:
        actual = file_digest(path)
    except OSError:
        actual = None
    return {'id': aid, 'valid': actual == item['hash'], 'expected_hash': item['hash'], 'actual_hash': actual,
            'issue': '' if actual == item['hash'] else '文件缺失或内容哈希不一致，请恢复原导出件'}
