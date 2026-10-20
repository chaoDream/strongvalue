"""Tenant-private immutable presentation templates. Never modify calculation rules."""
import json
from fastapi import APIRouter, Depends, HTTPException
from . import main as core, store

router = APIRouter(prefix='/api/v1')
ACCENTS = {'173B36', '244B78', '6A4732', '584274'}


def find_template(c, template_id, tenant):
    row = c.execute('SELECT data FROM report_templates WHERE id=? AND tenant=?', (template_id, tenant)).fetchone()
    if not row:
        raise HTTPException(404, '报表模板不存在或无权访问')
    return json.loads(row['data'])


@router.get('/organization/report-templates')
def list_templates(a=Depends(core.actor)):
    with store.connect() as c:
        return [json.loads(row['data']) for row in c.execute('SELECT data FROM report_templates WHERE tenant=? ORDER BY rowid DESC', (a['tenant'],))]


@router.post('/organization/report-templates')
def create_template(body: dict, a=Depends(core.actor)):
    if a['role'] not in ('admin', 'manager'):
        raise HTTPException(403, '仅企业管理员或项目负责人可以维护企业模板')
    for field, limit, required in [('name', 80, True), ('organization', 120, False), ('cover_note', 1000, False), ('footer', 40, False)]:
        value = body.get(field, '')
        if not isinstance(value, str) or len(value) > limit or (required and not value.strip()) or any(ord(ch) < 32 and not (field == 'cover_note' and ch == '\n') for ch in value):
            raise HTTPException(422, '模板名称或说明格式无效；页脚最多40字')
    if not isinstance(body.get('accent'), str) or body['accent'] not in ACCENTS:
        raise HTTPException(422, '请选择支持的报表配色')
    with store.transaction() as c:
        previous = find_template(c, body['previous_id'], a['tenant']) if body.get('previous_id') else None
        family = previous['family'] if previous else store.uid()
        if previous:
            latest = c.execute('SELECT id FROM report_templates WHERE tenant=? AND family=? ORDER BY version DESC LIMIT 1', (a['tenant'], family)).fetchone()
            if latest['id'] != previous['id']:
                raise HTTPException(409, '该模板已有新版本，请从最新版本修订')
        template = {k: body.get(k, '') for k in ['name', 'organization', 'cover_note', 'footer', 'accent']}
        template.update(id=store.uid(), family=family, version=previous['version']+1 if previous else 1,
                        created_at=store.now(), author=a['username'], purpose='内部报表样式，不代表官方表式合规')
        c.execute('INSERT INTO report_templates VALUES(?,?,?,?,?)', (template['id'], a['tenant'], family, template['version'], store.canonical(template)))
        store.audit(c, {'id': 'tenant:'+a['tenant'], 'tenant': a['tenant']}, a, 'REPORT_TEMPLATE_VERSION_CREATED', template)
    return template
