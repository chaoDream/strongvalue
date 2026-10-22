"""Preview exact stored PDF bytes, with generated section page ranges."""
import hashlib
import io
import json
from fastapi import APIRouter, Depends, HTTPException
from pypdf import PdfReader
from . import main as core, store
from .engine import DomainError

router = APIRouter(prefix='/api/v1')


def current_status(p, artifact):
    if artifact.get('purpose'):
        return 'history'
    if not artifact.get('quote_id'):
        return 'unpriced'
    q = next((q for q in p['quotes'] if q['id'] == artifact['quote_id']), None)
    if not q:
        return 'unavailable'
    if q['id'] in p['stale_quotes']:
        return 'stale'
    if store.digest(q['snapshot']) != q['input_hash'] or store.digest({k:v for k,v in q.items() if k != 'hash'}) != q['hash']:
        return 'unverified'
    try:
        return 'approved' if p['report_approval'] and p['report_approval']['quote_id'] == q['id'] and not core.blockers(p) else 'unapproved'
    except (HTTPException, DomainError):
        return 'unverified'


def metadata(p, artifact, payload):
    if artifact['format'] != 'pdf':
        raise HTTPException(422, '分页预览仅支持已留档PDF，请下载其他格式审阅')
    if hashlib.sha256(payload).hexdigest() != artifact['hash']:
        raise HTTPException(409, '历史PDF缺失或内容哈希不一致，请恢复原导出件')
    try:
        reader = PdfReader(io.BytesIO(payload))
        if reader.is_encrypted or not reader.pages:
            raise ValueError('unreadable pdf')
        count = len(reader.pages)
        manifest = artifact.get('pdf_manifest')
        sections = []
        if manifest is not None:
            if manifest.get('schema') != 'report-pagination-v1' or manifest.get('page_count') != count:
                raise ValueError('pagination mismatch')
            sections = manifest['sections']
            if not isinstance(sections, list) or not sections:
                raise ValueError('empty sections')
            previous = 0
            for s in sections:
                if set(s) != {'title', 'page', 'end_page'} or not isinstance(s['title'], str) or not s['title'].strip() or type(s['page']) is not int or type(s['end_page']) is not int or s['page'] != previous+1 or not s['page'] <= s['end_page'] <= count:
                    raise ValueError('invalid page range')
                previous = s['end_page']
            if previous != count:
                raise ValueError('missing pages')
            actual = [{'title': d.title, 'page': reader.get_destination_page_number(d)+1} for d in reader.outline]
            if actual != [{'title': s['title'], 'page': s['page']} for s in sections]:
                raise ValueError('outline mismatch')
    except Exception:
        raise HTTPException(409, 'PDF或分页索引无法核验，请恢复原文件与记录') from None
    return {'artifact': artifact, 'page_count': count, 'sections': sections, 'manifest_available': manifest is not None,
            'current_status': current_status(p, artifact), 'file_status': 'verified',
            'file_url': '/api/v1/projects/'+p['id']+'/artifacts/'+artifact['id']+'/file?inline=true',
            'notice': '所见为该留档PDF的实际字节。文件生成时审核状态固定，当前适用性另行显示；预览不签署审批，不重写历史文件。'}


@router.post('/projects/{pid}/report-previews')
def create_preview(pid: str, body: dict, a=Depends(core.actor)):
    if set(body)-{'quote_id', 'unpriced', 'template_id'} or not isinstance(body.get('quote_id', ''), str) or not isinstance(body.get('template_id', ''), str) or type(body.get('unpriced', False)) is not bool:
        raise HTTPException(422, '请选择一个报价版本或当前无价草稿，以及可选企业模板')
    unpriced = body.get('unpriced', False); quote_id = body.get('quote_id', '')
    if bool(quote_id) == unpriced:
        raise HTTPException(422, '必须明确选择一个报价版本或当前无价草稿，不能同时选择或留空')
    with store.transaction() as c:
        p = core.project(c, pid, a)
        artifact, payload = core.persist_export(c, p, a, 'pdf', quote_id, body.get('template_id', ''), unpriced)
        result = metadata(p, artifact, payload)
    return result


@router.get('/projects/{pid}/artifacts/{aid}/preview')
def read_preview(pid: str, aid: str, a=Depends(core.actor)):
    with store.connect() as c:
        p = core.project(c, pid, a)
        row = c.execute('SELECT data FROM artifacts WHERE id=? AND project_id=? AND tenant=?', (aid, pid, a['tenant'])).fetchone()
        if not row:
            raise HTTPException(404, '成果文件不存在或无权访问')
        artifact = json.loads(row['data'])
    if artifact['format'] != 'pdf':
        raise HTTPException(422, '分页预览仅支持已留档PDF，请下载其他格式审阅')
    try:
        payload = (store.ROOT/'exports'/artifact['hash']).read_bytes()
    except OSError:
        raise HTTPException(409, '历史PDF缺失，请恢复原导出件') from None
    return metadata(p, artifact, payload)
