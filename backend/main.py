import hashlib
import hmac
import io
import json
import os
import secrets
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import FastAPI, BackgroundTasks, Depends, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import store
from .engine import DomainError, takeoff, calculate, number, GEOMETRIES, validate_rules
from .parsers import validate_file, extract, fact_candidates, dimension_candidates, pdf_text_anchors, dxf_objects

app = FastAPI(title='QiangQuote 工程智能计价', version='0.4.0', description='原图证据、受控算量、清单组价、变更对账；模型服务可配置，专业软件接入待授权验证。')
store.init()
ROLES = {'manager', 'estimator', 'reviewer', 'admin'}
REQUIREMENTS = {'建筑': '建筑平面、剖面及做法', '结构': '结构配筋、基础及节点', '工艺设备': '工艺流程、设备表及安装要求',
                '给排水': '管线平纵断面、材质及埋深', '电气': '配电系统及敷设方式', '合同计价': '合同、计量与取费依据'}
REVIEW_COLLECTIONS = {'facts', 'components', 'takeoffs', 'boq', 'rules', 'coverage', 'quotes', 'changes', 'catalog', 'dimension_candidates', 'reconciliations', 'price_changes', 'boq_mappings'}


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    return salt+':'+hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 180000).hex()


def bootstrap():
    with store.transaction() as c:
        if not c.execute('SELECT 1 FROM users LIMIT 1').fetchone():
            password = os.getenv('QIANG_BOOTSTRAP_PASSWORD') or secrets.token_urlsafe(14)
            for username, role, tenant in [('admin', 'admin', 'local'), ('estimator', 'estimator', 'local'),
                                           ('reviewer', 'reviewer', 'local'), ('other', 'admin', 'other')]:
                c.execute('INSERT INTO users(id,tenant,username,password,role) VALUES(?,?,?,?,?)', (store.uid(), tenant, username, password_hash(password), role))
            if not os.getenv('QIANG_BOOTSTRAP_PASSWORD'):
                print('\n强·报价 本地初始化密码（请妥善保存）: '+password+'\n', flush=True)


bootstrap()
from .recovery import recover_interrupted_jobs
recover_interrupted_jobs()


@app.exception_handler(DomainError)
async def domain_error(request, exc):
    return JSONResponse(status_code=422, content={'detail': str(exc)})


def actor(request: Request):
    token = request.cookies.get('qiang_session', '')
    with store.connect() as c:
        user = c.execute('SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id WHERE s.token=? AND s.expires>? AND u.active=1',
                         (hashlib.sha256(token.encode()).hexdigest(), store.now())).fetchone()
    if not user:
        raise HTTPException(401, '请先登录')
    return {key:user[key] for key in ['id', 'tenant', 'username', 'role', 'active']}


def edit(a):
    if a['role'] not in ('admin', 'manager', 'estimator'):
        raise HTTPException(403, '当前角色只能复核，不能修改编制数据')


def project(c, pid, a):
    row = c.execute('SELECT data FROM projects WHERE id=? AND tenant=?', (pid, a['tenant'])).fetchone()
    if not row:
        raise HTTPException(404, '项目不存在或无权访问')
    return normalize(json.loads(row['data']))


def normalize(p):
    for key in ['imports', 'changes', 'applied_changes', 'scenarios', 'catalog', 'dimension_candidates', 'analyses', 'reconciliations', 'price_changes', 'applied_price_changes', 'boq_mappings', 'library_snapshots']:
        p.setdefault(key, [])
    return p


def effective_date(p):
    from zoneinfo import ZoneInfo
    return date.fromisoformat(p['pricing_date']) if p.get('pricing_date') else datetime.now(ZoneInfo('Asia/Shanghai')).date()


def find(p, collection, oid):
    result = next((x for x in p[collection] if x['id'] == oid), None)
    if not result:
        raise HTTPException(404, '对象不存在')
    return result


def invalidate(p, stage):
    stages = ['facts', 'components', 'takeoffs', 'boq', 'rules', 'quotes']
    start = stages.index(stage)
    for collection in stages[start:]:
        if collection == 'rules':
            continue
        for obj in p[collection]:
            if collection == 'quotes':
                # Quote payloads are immutable. Validity is separately recorded.
                p['stale_quotes'] = list(set(p['stale_quotes']+[obj['id']]))
            elif obj.get('state') == 'approved':
                obj['state'] = 'stale'
    p['report_approval'] = None
    p['status'] = 'FACTS_REVIEW' if stage == 'facts' else 'TAKEOFF_DRAFT'


def gaps(p):
    result = []
    for specialty, requirement in REQUIREMENTS.items():
        docs = [d for d in p['documents'] if d['specialty'] == specialty and not d.get('superseded')]
        coverage = p['coverage'].get(specialty)
        approved = coverage and coverage.get('state') == 'approved'
        result.append({'specialty': specialty, 'required_doc': requirement, 'files': len(docs),
                       'status': '充分' if approved else '部分' if docs else '缺失',
                       'blocking': specialty in p['scope'] and not approved})
    return result


def blockers(p):
    from .workflows import allocation_diagnostics
    from .boq_mapping import mapping_issues
    out = [x['specialty']+'资料尚未复核完整' for x in gaps(p) if x['blocking']]
    out.extend(x['message'] for x in allocation_diagnostics(p)['issues'] if x['blocking'])
    out.extend(mapping_issues(p))
    if any(x['state'] not in ('approved', 'rejected', 'superseded') for x in p['facts']):
        out.append('存在未审核或冲突的工程事实')
    if any(x['state'] == 'conflict' for x in p.get('dimension_candidates', [])):
        out.append('存在跨图尺寸冲突，须逐项专业裁决')
    for key, label in [('components', '尺寸'), ('takeoffs', '工程量'), ('boq', '清单')]:
        if key == 'components' and not p[key] and p['takeoffs'] and all(t.get('source_kind') == 'imported_boq' for t in p['takeoffs']):
            continue
        if not p[key] or any(x['state'] != 'approved' for x in p[key]):
            out.append(label+'未全部审定')
    if not p['rules'] or p['rules'][0]['state'] != 'approved':
        out.append('计价规则包未审定')
    elif p['rules'][0]['region'] != p['region'] or not date.fromisoformat(p['rules'][0]['valid_from']) <= effective_date(p) <= date.fromisoformat(p['rules'][0]['valid_until']):
        out.append('计价规则包地区或有效期不适用')
    elif not any(e['id'] == p['rules'][0].get('evidence_id') and any(d['id'] == e['document_id'] and not d.get('superseded') for d in p['documents']) for e in p['evidence']):
        out.append('计价规则依据已替代或缺失')
    if any(x['state'] != 'rejected' and x['id'] not in p.get('applied_changes', []) for x in p.get('changes', [])):
        out.append('存在待审或已审未应用的变更签证')
    if any(x['state'] != 'rejected' and x['id'] not in p.get('applied_price_changes', []) for x in p.get('price_changes', [])):
        out.append('存在待审或已审未应用的资源调价')
    if p.get('require_golden_baseline'):
        from .reconciliation import quantity_hash
        if not any(r['state'] == 'approved' and r['passed'] and r['input_hash'] == quantity_hash(p) for r in p.get('reconciliations', [])):
            out.append('缺少覆盖当前工程量的已审专业黄金底稿对照')
    out.extend(price_blockers(p))
    return list(dict.fromkeys(out))


def price_blockers(p):
    from .resource_library import reference_errors
    out = []
    tax_modes = set()
    for item in p['boq']:
        if not item.get('quotas'):
            out.append(item['name']+'缺少定额及资源价格')
        for q in item.get('quotas', []):
            if not q.get('license') or not q.get('edition') or not q.get('source') or not q.get('resources'):
                out.append(item['name']+'缺少定额版本、授权、来源或消耗')
            for r in q.get('resources', []):
                if r.get('price_change_id'):
                    change = next((x for x in p.get('price_changes', []) if x['id'] == r['price_change_id']), None)
                    if not change or change['state'] != 'approved' or change['id'] not in p.get('applied_price_changes', []):
                        out.append(r.get('name', '资源')+'调价审批已失效，须建立并审定新的价格修正')
                tax_modes.add(r.get('tax_mode'))
                if r.get('region') != p['region']:
                    out.append(r.get('name', '资源')+'价格地区与项目不一致')
                if r.get('tax_mode') not in ('含税', '不含税'):
                    out.append('资源税制口径无效')
                if not all(r.get(k) for k in ['source', 'license', 'period', 'valid_until', 'region', 'tax_mode']):
                    out.append(r.get('name', '资源')+'缺少价格来源/许可/期次/地区/税制')
                try:
                    if date.fromisoformat(r.get('valid_until', '')) < effective_date(p):
                        out.append(r['name']+'价格过期')
                    if r.get('valid_from') and date.fromisoformat(r['valid_from']) > effective_date(p):
                        out.append(r['name']+'价格尚未生效')
                except ValueError:
                    out.append('价格有效期格式无效')
            # Every reference must still point to a current original document.
            for priced in [q]+q.get('resources', []):
                e = next((e for e in p['evidence'] if e['id'] == priced.get('evidence_id')), None)
                if not e or next((d for d in p['documents'] if d['id'] == e['document_id']), {}).get('superseded'):
                    out.append('定额/资源价格缺少当前原始证据')
    if len(tax_modes) > 1:
        out.append('资源含税与不含税价格混用；请按计价规则统一口径')
    out.extend(reference_errors(p))
    return out


def view(p):
    normalize(p)
    return {**p, 'gaps': gaps(p), 'blockers': blockers(p), 'capabilities': capabilities()}


class Login(BaseModel):
    username: str
    password: str


@app.post('/api/v1/auth/login')
def login(body: Login):
    with store.transaction() as c:
        u = c.execute('SELECT * FROM users WHERE username=?', (body.username,)).fetchone()
        salt = u['password'].split(':')[0] if u else 'invalid'
        check = password_hash(body.password, salt)
        if not u or not hmac.compare_digest(check, u['password']) or not u['active']:
            raise HTTPException(401, '账号或密码错误')
        token = secrets.token_urlsafe(32)
        expires = (datetime.now(timezone.utc)+timedelta(hours=8)).isoformat()
        c.execute('INSERT INTO sessions VALUES(?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), u['id'], expires))
        response = JSONResponse({'username': u['username'], 'role': u['role'], 'tenant': u['tenant']})
        response.set_cookie('qiang_session', token, httponly=True, samesite='strict', secure=os.getenv('QIANG_SECURE_COOKIE') == '1', max_age=28800)
        return response


@app.get('/api/v1/auth/me')
def me(a=Depends(actor)):
    return {k: a[k] for k in ['username', 'role', 'tenant']}


@app.post('/api/v1/auth/logout')
def logout(request: Request, a=Depends(actor)):
    with store.transaction() as c:
        c.execute('DELETE FROM sessions WHERE token=?', (hashlib.sha256(request.cookies.get('qiang_session', '').encode()).hexdigest(),))
    r = JSONResponse({'ok': True})
    r.delete_cookie('qiang_session')
    return r


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    region: str = '河南省'
    purpose: str = '招标控制价草稿'
    scope: list[str] = ['建筑', '结构', '合同计价']
    require_golden_baseline: bool = True


@app.post('/api/v1/projects')
def create_project(body: ProjectIn, a=Depends(actor)):
    edit(a)
    if not body.scope or any(s not in REQUIREMENTS for s in body.scope):
        raise HTTPException(422, '请选择有效的试点专业范围')
    p = {**body.model_dump(), 'id': store.uid(), 'tenant': a['tenant'], 'status': 'CREATED', 'created_at': store.now(),
         **{k: [] for k in ['documents', 'evidence', 'facts', 'components', 'takeoffs', 'boq', 'rules', 'quotes', 'reviews', 'jobs', 'stale_quotes']},
         'coverage': {}, 'report_approval': None}
    with store.transaction() as c:
        c.execute('INSERT INTO projects VALUES(?,?,?)', (p['id'], a['tenant'], store.canonical(p)))
        store.audit(c, p, a, 'PROJECT_CREATED', body.model_dump())
    return view(p)


@app.get('/api/v1/projects')
def projects(a=Depends(actor)):
    with store.connect() as c:
        rows = c.execute('SELECT data FROM projects WHERE tenant=?', (a['tenant'],)).fetchall()
    return [view(json.loads(r['data'])) for r in rows][::-1]


@app.get('/api/v1/projects/{pid}')
def get_project(pid: str, a=Depends(actor)):
    with store.connect() as c:
        return view(project(c, pid, a))


def parse_job(pid, did, jid, a):
    try:
        with store.transaction() as c:
            p = project(c, pid, a)
            d = find(p, 'documents', did)
            if d.get('parsed'):
                find(p, 'jobs', jid)['state'] = 'completed'
                store.save(c, p)
                return
            find(p, 'jobs', jid)['state'] = 'running'
            store.save(c, p)
        pages = extract(Path(d['name']).suffix.lower(), (store.ROOT/'originals'/d['hash']).read_bytes())
        raw = (store.ROOT/'originals'/d['hash']).read_bytes()
        vector_objects = pdf_text_anchors(raw) if d['name'].lower().endswith('.pdf') else dxf_objects(raw)[0] if d['name'].lower().endswith('.dxf') else []
        with store.transaction() as c:
            p = project(c, pid, a)
            d = find(p, 'documents', did)
            # Retry is idempotent: a completed document is not parsed twice.
            if d.get('parsed'):
                return
            for page, text in pages:
                if not text.strip():
                    continue
                e = {'id': store.uid(), 'document_id': did, 'document_hash': d['hash'], 'page': page, 'text': text,
                     'method': 'local_text', 'bbox': None}
                p['evidence'].append(e)
                for candidate in dimension_candidates(text):
                    candidate.update(id=store.uid(), document_id=d['id'], evidence_id=e['id'], state='draft', method='explicit_text_dimension')
                    group = lambda x: (x['component_name'] or x.get('document_id'), x['parameter'])
                    conflicts = [x for x in p['dimension_candidates'] if candidate['component_name'] and group(x) == group(candidate) and Decimal(x['value']) != Decimal(candidate['value']) and x['state'] not in ('rejected', 'superseded')]
                    if conflicts:
                        candidate['state'] = 'conflict'
                        for x in conflicts: x['state'] = 'conflict'
                    p['dimension_candidates'].append(candidate)
                for fact in fact_candidates(text):
                    conflict = any(f['key'] == fact['key'] and f['value'] != fact['value'] and f['state'] not in ('rejected','superseded') for f in p['facts'])
                    if conflict:
                        for f in p['facts']:
                            if f['key'] == fact['key'] and f['state'] not in ('rejected','superseded'):
                                f['state'] = 'conflict'
                    p['facts'].append({**fact, 'id': store.uid(), 'evidence_id': e['id'], 'state': 'conflict' if conflict else 'draft'})
            d['parsed'] = True
            d['vector_objects'] = vector_objects
            d['pages'] = len(pages)
            d['parse_state'] = 'needs_review'
            d['message'] = '文字已提取；尺寸及事实须人工复核' if any(t.strip() for _, t in pages) else '未提取到可用文字；扫描图需 OCR/视觉适配器，或手工标注证据'
            find(p, 'jobs', jid).update(state='completed', message=d['message'])
            p['status'] = 'FACTS_REVIEW'
            store.audit(c, p, a, 'DOCUMENT_PARSED', {'document_id': did, 'pages': len(pages)})
            store.save(c, p)
    except Exception as exc:
        with store.transaction() as c:
            p = project(c, pid, a)
            find(p, 'documents', did)['parse_state'] = 'failed'
            find(p, 'jobs', jid).update(state='failed', message=str(exc)[:300])
            store.audit(c, p, a, 'PARSE_FAILED', {'document_id': did, 'error': str(exc)[:300]})
            store.save(c, p)


@app.post('/api/v1/projects/{pid}/documents')
async def upload(pid: str, tasks: BackgroundTasks, specialty: str = Form(...), file: UploadFile = File(...), a=Depends(actor)):
    edit(a)
    if specialty not in REQUIREMENTS:
        raise HTTPException(422, '专业分类无效')
    raw = await file.read(30*1024*1024+1)
    if not raw or len(raw) > 30*1024*1024:
        raise HTTPException(413, '单文件大小须为 1 字节至 30 MB')
    name = Path(file.filename or 'document').name
    try:
        validate_file(Path(name).suffix.lower(), raw)
    except Exception as e:
        raise HTTPException(422, str(e))
    sha = hashlib.sha256(raw).hexdigest()
    with store.transaction() as c:
        p = project(c, pid, a)
        existing = next((d for d in p['documents'] if d['hash'] == sha and not d.get('superseded')), None)
        if existing:
            return {'document': existing, 'duplicate': True}
        previous = [d for d in p['documents'] if d['name'] == name]
        for d in previous:
            d['superseded'] = True
        superseded_ids = {d['id'] for d in previous}
        old_evidence = {e['id'] for e in p['evidence'] if e['document_id'] in superseded_ids}
        for record in p['facts']+p['dimension_candidates']:
            if record['evidence_id'] in old_evidence:
                record['state'] = 'superseded'
        d = {'id': store.uid(), 'name': name, 'hash': sha, 'size': len(raw), 'specialty': specialty,
             'version': len(previous)+1, 'parse_state': 'queued', 'uploaded_at': store.now(), 'supersedes': [d['id'] for d in previous]}
        job = {'id': store.uid(), 'document_id': d['id'], 'state': 'queued', 'message': '等待解析'}
        p['documents'].append(d)
        p['jobs'].append(job)
        p['coverage'].pop(specialty, None)
        invalidate(p, 'facts')
        p['status'] = 'FILES_INGESTED'
        folder = store.ROOT/'originals'
        folder.mkdir(exist_ok=True)
        target = folder/sha
        if not target.exists():
            target.write_bytes(raw)
        store.audit(c, p, a, 'DOCUMENT_UPLOADED', d)
        store.save(c, p)
    tasks.add_task(parse_job, pid, d['id'], job['id'], a)
    return {'document': d, 'job': job, 'duplicate': False}


@app.post('/api/v1/projects/{pid}/jobs/{jid}/retry')
def retry(pid: str, jid: str, tasks: BackgroundTasks, a=Depends(actor)):
    edit(a)
    with store.transaction() as c:
        p = project(c, pid, a)
        j = find(p, 'jobs', jid)
        if j['state'] != 'failed':
            raise HTTPException(409, '任务仍在队列/执行中或已完成，无需重复提交；中断任务在服务恢复后可重试')
        if j.get('kind') == 'model_analysis':
            from . import gateway
            config = gateway.configuration()
            if not config['configured'] or config['destination'] != j['destination'] or config['model'] != j['model']:
                raise HTTPException(409, '模型配置已变化，请重新确认接收方后发起解析')
        j['state'] = 'queued'
        store.save(c, p)
    if j.get('kind') == 'model_analysis':
        from .analysis_routes import analyze_job
        tasks.add_task(analyze_job, pid, j['document_id'], jid, a)
    else:
        tasks.add_task(parse_job, pid, j['document_id'], jid, a)
    return j


@app.get('/api/v1/projects/{pid}/documents/{did}/file')
def original(pid: str, did: str, a=Depends(actor)):
    with store.connect() as c:
        d = find(project(c, pid, a), 'documents', did)
    ext = Path(d['name']).suffix.lower()
    return FileResponse(store.ROOT/'originals'/d['hash'], filename=d['name'],
                        media_type='application/pdf' if ext == '.pdf' else 'application/octet-stream',
                        content_disposition_type='inline' if ext == '.pdf' else 'attachment',
                        headers={'X-Content-Type-Options': 'nosniff'})


@app.post('/api/v1/projects/{pid}/evidence')
def add_evidence(pid: str, body: dict, a=Depends(actor)):
    edit(a)
    with store.transaction() as c:
        p = project(c, pid, a)
        d = find(p, 'documents', body.get('document_id'))
        if d.get('superseded'):
            raise HTTPException(409, '不能引用已被替代的图纸')
        text = str(body.get('text', '')).strip()
        if not text:
            raise HTTPException(422, '请填写原文或标注内容')
        page = body.get('page')
        if not isinstance(page, int) or page < 1:
            raise HTTPException(422, '页号须为正整数')
        e = {'id': store.uid(), 'document_id': d['id'], 'document_hash': d['hash'], 'page': page,
             'text': text[:100000], 'bbox': body.get('bbox'), 'method': 'manual', 'drawing_no': body.get('drawing_no', '')}
        p['evidence'].append(e)
        store.audit(c, p, a, 'EVIDENCE_CREATED', e)
        store.save(c, p)
    return e


def require_evidence(p, eid):
    e = find(p, 'evidence', eid)
    if find(p, 'documents', e['document_id']).get('superseded'):
        raise HTTPException(409, '证据已过期，请引用当前图纸')
    return e


@app.post('/api/v1/projects/{pid}/facts')
def add_fact(pid: str, body: dict, a=Depends(actor)):
    edit(a)
    with store.transaction() as c:
        p = project(c, pid, a)
        require_evidence(p, body.get('evidence_id'))
        if not all(body.get(k) for k in ['key', 'label', 'value']):
            raise HTTPException(422, '事实字段不完整')
        f = {k: str(body.get(k, '')) for k in ['key', 'label', 'value', 'unit', 'evidence_id']}
        f.update(id=store.uid(), state='draft')
        if any(x['key'] == f['key'] and x['value'] != f['value'] and x['state'] not in ('rejected','superseded') for x in p['facts']):
            f['state'] = 'conflict'
            for x in p['facts']:
                if x['key'] == f['key'] and x['state'] not in ('rejected','superseded'):
                    x['state'] = 'conflict'
        p['facts'].append(f)
        invalidate(p, 'facts')
        store.audit(c, p, a, 'FACT_CREATED', f)
        store.save(c, p)
    return f


@app.put('/api/v1/projects/{pid}/components/{cid}')
@app.post('/api/v1/projects/{pid}/components')
def component(pid: str, body: dict, cid: str = '', a=Depends(actor)):
    edit(a)
    with store.transaction() as c:
        p = project(c, pid, a)
        result = takeoff(body.get('kind'), body.get('parameters', {}))
        required_parameters = set(GEOMETRIES[body['kind']]['fields']) | {'deduction'}
        if set(body['parameters']) != required_parameters:
            raise HTTPException(422, '参数需完整且仅包含当前几何类型字段；无扣减也应显式填写零并关联依据')
        refs = body.get('evidence', {})
        for key in body['parameters']:
            require_evidence(p, refs.get(key))
        if body['kind'] == 'rebar_schedule':
            if any(find(p, 'documents', require_evidence(p, eid)['document_id'])['specialty'] != '结构' for eid in refs.values()):
                raise HTTPException(409, '钢筋表汇总的每项参数须引用结构配筋详图或钢筋表，不能以含钢率替代')
        if not body.get('name') or not body.get('measurement_basis'):
            raise HTTPException(422, '构件名称和计量/扣减依据必填')
        invalidate(p, 'components')
        old = find(p, 'components', cid) if cid else None
        comp = {k: body[k] for k in ['name', 'kind', 'parameters', 'evidence', 'measurement_basis']}
        comp.update(id=cid or store.uid(), revision=old['revision']+1 if old else 1, state='draft', preview=result)
        if old:
            p.setdefault('component_history', []).append(dict(old))
            p['components'][p['components'].index(old)] = comp
            for t in p['takeoffs']:
                if t['component_id'] == cid:
                    t['state'] = 'stale'
        else:
            p['components'].append(comp)
        store.audit(c, p, a, 'COMPONENT_UPDATED' if old else 'COMPONENT_CREATED', comp)
        store.save(c, p)
    return comp


@app.post('/api/v1/projects/{pid}/takeoffs/calculate')
def calculate_takeoff(pid: str, a=Depends(actor)):
    edit(a)
    with store.transaction() as c:
        p = project(c, pid, a)
        if p['applied_changes']:
            raise HTTPException(409, '当前清单已应用签证；重新生成原设计算量前需专业人员建立新的变更基线，避免把变更工程量覆盖回原设计')
        if not p['components'] or any(x['state'] != 'approved' for x in p['components']):
            raise HTTPException(409, '所有构件尺寸需先经专业复核')
        invalidate(p, 'boq')
        # Retain old calculation books; current version is the only BOQ input.
        p.setdefault('takeoff_history', []).extend(p['takeoffs'])
        external = [t for t in p['takeoffs'] if t.get('source_kind') in ('imported_boq', 'change_order')]
        p['takeoffs'] = external + [{**takeoff(x['kind'], x['parameters']), 'id': store.uid(), 'name': x['name'],
                          'component_id': x['id'], 'component_hash': store.digest(x), 'evidence': x['evidence'],
                          'measurement_basis': x['measurement_basis'], 'state': 'draft'} for x in p['components']]
        p['status'] = 'TAKEOFF_DRAFT'
        store.audit(c, p, a, 'TAKEOFF_CALCULATED', {'ids': [x['id'] for x in p['takeoffs']]})
        store.save(c, p)
    return p['takeoffs']


@app.post('/api/v1/projects/{pid}/boq/generate')
def generate_boq(pid: str, a=Depends(actor)):
    edit(a)
    with store.transaction() as c:
        p = project(c, pid, a)
        if p['applied_changes']:
            raise HTTPException(409, '当前清单已应用签证，请继续编制当前变更版本，不能再次加入原设计数量')
        if not p['takeoffs'] or any(x['state'] != 'approved' for x in p['takeoffs']):
            raise HTTPException(409, '工程量未全部审核')
        from .extensions import links
        existing = {l['takeoff_id'] for x in p['boq'] for l in links(x)}
        for t in p['takeoffs']:
            if t['id'] not in existing:
                p['boq'].append({'id': store.uid(), 'takeoff_id': t['id'], 'code': '', 'name': t['name'], 'features': '',
                                 'node': '单项工程 / 单位工程', 'quantity': t['quantity'], 'unit': t['unit'], 'state': 'draft', 'quotas': []})
        # Obsolete BOQ lines stay in history, never in current totals.
        current = {t['id'] for t in p['takeoffs']}
        obsolete = [x for x in p['boq'] if any(l['takeoff_id'] not in current for l in links(x))]
        p.setdefault('boq_history', []).extend(obsolete)
        p['boq'] = [x for x in p['boq'] if all(l['takeoff_id'] in current for l in links(x))]
        invalidate(p, 'boq')
        p['status'] = 'BOQ_DRAFT'
        store.audit(c, p, a, 'BOQ_GENERATED', {'items': len(p['boq'])})
        store.save(c, p)
    return p['boq']


@app.put('/api/v1/projects/{pid}/boq/{bid}')
def update_boq(pid: str, bid: str, body: dict, a=Depends(actor)):
    edit(a)
    with store.transaction() as c:
        p = project(c, pid, a)
        b = find(p, 'boq', bid)
        if b.get('mapping_ref') and 'code' in body and body['code'] != b['code']:
            raise HTTPException(409, '此编码来自已审映射；更换编码请修订并采用新的有据映射')
        if 'quotas' in body:
            if not isinstance(body['quotas'], list):
                raise HTTPException(422, '定额须为数组')
            try:
                calculate([{**b, 'quotas': body['quotas']}], {'fees': []})
                for q in body['quotas']:
                    require_evidence(p, q.get('evidence_id'))
                    for resource in q['resources']:
                        require_evidence(p, resource.get('evidence_id'))
            except (KeyError, TypeError) as e:
                raise HTTPException(422, '定额资源字段不完整：'+str(e))
        invalidate(p, 'boq')
        b.update({k: body[k] for k in ['code', 'name', 'features', 'node', 'quotas'] if k in body})
        b['state'] = 'draft'
        store.audit(c, p, a, 'BOQ_UPDATED', b)
        store.save(c, p)
    return b


@app.put('/api/v1/projects/{pid}/rules')
def rules(pid: str, body: dict, a=Depends(actor)):
    edit(a)
    required = ['name', 'version', 'region', 'standard', 'license', 'source', 'valid_from', 'valid_until', 'fees', 'evidence_id']
    if not all(k in body and body[k] != '' for k in required):
        raise HTTPException(422, '规则包需名称、版本、地区、标准、授权、来源、有效期和费用数组')
    if not isinstance(body['fees'], list) or any(not isinstance(f, dict) or not all(k in f and f[k] != '' for k in ['name', 'basis', 'rate', 'source']) for f in body['fees']):
        raise HTTPException(422, '每项费用需名称、基数、费率和取费依据')
    try:
        start, end = date.fromisoformat(body['valid_from']), date.fromisoformat(body['valid_until'])
    except ValueError:
        raise HTTPException(422, '规则生效日期格式无效')
    if start > end:
        raise HTTPException(422, '规则有效期不正确')
    validate_rules(body)
    with store.transaction() as c:
        p = project(c, pid, a)
        require_evidence(p, body['evidence_id'])
        if p['rules']:
            p.setdefault('rule_history', []).extend(p['rules'])
        p['rules'] = [{**{k: body[k] for k in required}, 'rounding_config': body.get('rounding_config', {}), 'id': store.uid(), 'state': 'draft'}]
        invalidate(p, 'quotes')
        store.audit(c, p, a, 'RULESET_UPDATED', p['rules'][0])
        store.save(c, p)
    return p['rules'][0]


def review_target(p, collection, oid):
    if collection == 'coverage':
        return p['coverage'].get(oid)
    return find(p, collection, oid)


@app.get('/api/v1/projects/{pid}/review-target')
def read_review_target(pid: str, collection: str, object_id: str, a=Depends(actor)):
    if collection not in REVIEW_COLLECTIONS:
        raise HTTPException(422, '审核对象类型无效')
    with store.connect() as c:
        p = project(c, pid, a)
        target = review_target(p, collection, object_id)
        if target is None: raise HTTPException(404, '审核对象不存在')
        return {'object':target, 'object_hash':store.digest(target)}


@app.post('/api/v1/projects/{pid}/coverage')
def coverage(pid: str, body: dict, a=Depends(actor)):
    edit(a)
    with store.transaction() as c:
        p = project(c, pid, a)
        s = body.get('specialty')
        if s not in REQUIREMENTS or not body.get('reason'):
            raise HTTPException(422, '请选择专业并填写完整性判断依据')
        e = require_evidence(p, body.get('evidence_id'))
        if find(p, 'documents', e['document_id'])['specialty'] != s:
            raise HTTPException(422, '完整性依据须引用本专业文件')
        p['coverage'][s] = {'id': s, 'specialty': s, 'reason': body['reason'], 'evidence_id': body['evidence_id'], 'state': 'draft'}
        invalidate(p, 'quotes')
        store.audit(c, p, a, 'COVERAGE_SUBMITTED', p['coverage'][s])
        store.save(c, p)
    return p['coverage'][s]


def apply_review(c, p, body, a, hash_verified=False):
    if a['role'] != 'reviewer':
        raise HTTPException(403, '仅独立复核账号可以签署审核')
    collection = body.get('collection')
    if collection not in REVIEW_COLLECTIONS:
        raise HTTPException(422, '审核对象类型无效')
    if body.get('decision') not in ['approve', 'reject', 'request_more_info'] or not isinstance(body.get('reason'), str) or not body['reason'].strip() or len(body['reason']) > 3000:
        raise HTTPException(422, '审核决定和依据必填')
    target = review_target(p, collection, body.get('object_id'))
    if not target:
        raise HTTPException(404, '审核对象不存在')
    if not hash_verified and body.get('object_hash') != store.digest(target):
        raise HTTPException(409, '对象已变更，请刷新后重新复核')
    approve = body['decision'] == 'approve'
    if approve:
        if collection == 'facts':
            if any(f['key'] == target['key'] and f['value'] != target['value'] and f['state'] not in ('rejected','superseded') for f in p['facts']):
                raise HTTPException(409, '先拒绝冲突候选，再核准选定值')
            require_evidence(p, target['evidence_id'])
        if collection == 'dimension_candidates':
            require_evidence(p, target['evidence_id'])
            if any(x['component_name'] and x['component_name'] == target['component_name'] and x['parameter'] == target['parameter'] and Decimal(x['value']) != Decimal(target['value']) and x['state'] not in ('rejected','superseded') for x in p['dimension_candidates']):
                raise HTTPException(409, '同构件同参数存在冲突值，请先拒绝不采纳候选')
        if collection == 'components':
            for eid in target['evidence'].values():
                require_evidence(p, eid)
        if collection == 'takeoffs':
            if target.get('source_kind') in ('imported_boq', 'change_order'):
                for eid in target['evidence'].values(): require_evidence(p, eid)
                if target.get('change_id') and (target['change_id'] not in p['applied_changes'] or find(p, 'changes', target['change_id'])['state'] != 'approved'):
                    raise HTTPException(409, '变更尚未审定应用或审批已被撤回')
            else:
                comp = find(p, 'components', target['component_id'])
                if comp['state'] != 'approved' or target['component_hash'] != store.digest(comp):
                    raise HTTPException(409, '构件已变化，请重新算量')
        if collection == 'boq':
            if not target.get('code') or not target.get('features'):
                raise HTTPException(409, '请补充有依据的清单编码和项目特征')
            from .extensions import validate_links
            validate_links(p, target)
            from .boq_mapping import mapping_issues
            issues = mapping_issues({**p, 'boq': [target]})
            if issues:
                raise HTTPException(409, '；'.join(issues))
        if collection == 'boq_mappings':
            from .boq_mapping import validate_mapping, invalidate_dependents
            validate_mapping(p, target)
            obsolete = [r for r in p['boq_mappings'] if r['family'] == target['family'] and r['version'] < target['version'] and r['state'] == 'approved']
            if any(r['family'] == target['family'] and r['version'] > target['version'] and r['state'] == 'approved' for r in p['boq_mappings']):
                raise HTTPException(409, '此规则已有已审新版本，不能重新启用旧版本')
            for rule in obsolete:
                rule['state'] = 'superseded'
            invalidate_dependents(p, {r['id'] for r in obsolete})
        if collection == 'rules':
            require_evidence(p, target['evidence_id'])
            if target['region'] != p['region'] or not date.fromisoformat(target['valid_from']) <= effective_date(p) <= date.fromisoformat(target['valid_until']):
                raise HTTPException(409, '规则包地区或有效期不适用')
        if collection == 'changes':
            require_evidence(p, target['evidence_id'])
            if target['base_hash'] != store.digest(find(p, 'boq', target['boq_id'])):
                raise HTTPException(409, '变更基线已变化，请重新建立变更')
        if collection == 'catalog':
            if price_blockers({**p, 'boq': [target]}):
                raise HTTPException(409, '定额候选来源、许可、价格或适用期间不完整')
        if collection == 'reconciliations':
            from .reconciliation import quantity_hash
            require_evidence(p, target['evidence_id'])
            if not target['passed'] or target['input_hash'] != quantity_hash(p):
                raise HTTPException(409, '底稿对照不通过或当前工程量已变化；请解决差异后重新对账')
        if collection == 'price_changes':
            from .workflows import validate_price_change
            validate_price_change(p, target)
        if collection == 'quotes':
            issues = blockers(p)
            if issues or target['id'] in p['stale_quotes']:
                raise HTTPException(409, '报告审批阻断：'+'；'.join(issues or ['输入已变更']))
    record = {'id': store.uid(), 'collection': collection, 'object_id': target['id'], 'decision': body['decision'],
              'reason': body['reason'], 'object_hash': body['object_hash'], 'reviewer': a['username'], 'timestamp': store.now()}
    p['reviews'].append(record)
    if collection == 'quotes':
        p['report_approval'] = {'quote_id': target['id'], 'review_id': record['id']} if approve else None
        p['status'] = 'REPORT_APPROVED' if approve else 'REPORT_DRAFT'
    else:
        target['state'] = 'approved' if approve else 'rejected' if body['decision'] == 'reject' else 'needs_info'
        if collection == 'changes' and not approve and target['id'] in p['applied_changes']:
            for b in p['boq']:
                if b.get('change_id') == target['id']:
                    b['state'] = 'stale'
                    find(p, 'takeoffs', b['takeoff_id'])['state'] = 'stale'
        if not approve:
            if collection == 'boq_mappings':
                from .boq_mapping import invalidate_dependents
                invalidate_dependents(p, {target['id']})
            if collection == 'price_changes':
                for b in p['boq']:
                    if any(r.get('price_change_id') == target['id'] for q in b['quotas'] for r in q['resources']):
                        b['state'] = 'stale'
            if collection in ('coverage', 'changes', 'catalog', 'dimension_candidates', 'reconciliations', 'price_changes', 'boq_mappings'):
                invalidate(p, 'quotes')
            else:
                invalidate(p, collection)
        elif collection in ('takeoffs', 'boq') and all(x['state'] == 'approved' for x in p[collection]):
            p['status'] = 'TAKEOFF_APPROVED' if collection == 'takeoffs' else 'BOQ_APPROVED'
    store.audit(c, p, a, 'REVIEW_DECISION', record)
    return record


@app.post('/api/v1/projects/{pid}/reviews')
def review(pid: str, body: dict, a=Depends(actor)):
    with store.transaction() as c:
        p = project(c, pid, a)
        record = apply_review(c, p, body, a)
        store.save(c, p)
    return record

@app.get('/api/v1/projects/{pid}/hashes')
def object_hashes(pid: str, a=Depends(actor)):
    with store.connect() as c:
        p = project(c, pid, a)
    return {key: {x['id']: store.digest(x) for x in (list(p[key].values()) if key == 'coverage' else p[key])}
            for key in REVIEW_COLLECTIONS}


@app.post('/api/v1/projects/{pid}/quotes/calculate')
def quote(pid: str, a=Depends(actor)):
    edit(a)
    with store.transaction() as c:
        p = project(c, pid, a)
        if not p['boq'] or any(x['state'] != 'approved' for x in p['boq']):
            raise HTTPException(409, '请先审定清单')
        issues = price_blockers(p)
        if issues:
            raise HTTPException(409, 'PRICE_UNAVAILABLE：'+'；'.join(issues))
        if not p['rules'] or p['rules'][0]['state'] != 'approved':
            raise HTTPException(409, 'RULESET_UNVERIFIED：规则包未审定')
        ruleset = p['rules'][0]
        require_evidence(p, ruleset.get('evidence_id'))
        if ruleset['region'] != p['region'] or not date.fromisoformat(ruleset['valid_from']) <= effective_date(p) <= date.fromisoformat(ruleset['valid_until']):
            raise HTTPException(409, 'RULESET_UNVERIFIED：规则包地区或有效期不适用')
        snapshot = {'boq': p['boq'], 'rules': p['rules'], 'takeoffs': p['takeoffs'], 'evidence': p['evidence'],
                    'documents': p['documents'], 'components': p['components'], 'facts': p['facts'], 'reviews': p['reviews'], 'coverage': p['coverage'],
                    'pricing_date': effective_date(p).isoformat(), 'changes': p['changes'], 'applied_changes': p['applied_changes'],
                    'reconciliations': p['reconciliations'], 'require_golden_baseline': p.get('require_golden_baseline', False)}
        snapshot.update(price_changes=p['price_changes'], applied_price_changes=p['applied_price_changes'])
        snapshot.update(boq_mappings=p['boq_mappings'], boq_standard=p.get('boq_standard',''), boq_edition=p.get('boq_edition',''))
        snapshot['library_snapshots']=p['library_snapshots']
        result = calculate(p['boq'], ruleset)
        q = {'id': store.uid(), 'version': len(p['quotes'])+1, 'created_at': store.now(), 'state': 'draft',
             'input_hash': store.digest(snapshot), 'snapshot': json.loads(store.canonical(snapshot)), **result,
             'warnings': blockers(p), 'project_name': p['name'], 'scope': p['scope']}
        q['source_mode'] = 'imported_or_mixed' if any(t.get('source_kind') == 'imported_boq' for t in p['takeoffs']) else 'original_documents'
        if q['source_mode'] == 'imported_or_mixed':
            q['warnings'].append('包含已有清单导入数量；兼容流程不等于原始图纸自动算量验收')
        if not p.get('require_golden_baseline'):
            q['warnings'].append('未启用同构件独立底稿门禁；本报告不代表黄金算例验收通过')
        q['hash'] = store.digest(q)
        p['quotes'].append(q)
        p['report_approval'] = None
        p['status'] = 'REPORT_DRAFT'
        store.audit(c, p, a, 'QUOTE_RECALCULATED', {'quote_id': q['id'], 'total': q['total'], 'input_hash': q['input_hash']})
        store.save(c, p)
    return q


@app.get('/api/v1/projects/{pid}/audit')
def audit_log(pid: str, a=Depends(actor)):
    with store.connect() as c:
        project(c, pid, a)
        return [{**json.loads(r['data']), 'hash': r['hash']} for r in c.execute('SELECT data,hash FROM audit WHERE project_id=? AND tenant=? ORDER BY seq DESC', (pid, a['tenant']))]


@app.get('/api/v1/integrations/capabilities')
def capabilities():
    return [{'provider': '广联达', 'product': x, 'version': '未配置', 'status': '未验证', 'can_import': False,
             'can_export': False, 'reason': '需厂商授权、指定版本及往返样本验证'} for x in ['GTJ', 'GQI', 'GCCP']]


@app.get('/api/v1/health')
def health():
    return {'status': 'ok', 'version': '0.4.0', 'storage': 'sqlite-local', 'ai': 'not_configured'}


@app.get('/api/v1/projects/{pid}/exports')
def export(pid: str, format: str = 'json', quote_id: str = '', template_id: str = '', a=Depends(actor)):
    from .reports import export_report
    with store.transaction() as c:
        p = project(c, pid, a)
        q = find(p, 'quotes', quote_id) if quote_id else (p['quotes'][-1] if p['quotes'] else None)
        approved = bool(q and p['report_approval'] and p['report_approval']['quote_id'] == q['id'] and q['id'] not in p['stale_quotes'] and not blockers(p))
        from .templates import find_template
        template = find_template(c, template_id, a['tenant']) if template_id else None
        payload, media, suffix = export_report(p, q, format, approved, template)
        sha = hashlib.sha256(payload).hexdigest()
        directory = store.ROOT/'exports'
        directory.mkdir(exist_ok=True)
        path = directory/sha
        if not path.exists():
            path.write_bytes(payload)
        artifact = {'id': store.uid(), 'format': format, 'quote_id': q['id'] if q else None, 'quote_version': q['version'] if q else None,
                    'hash': sha, 'approved': approved, 'timestamp': store.now(), 'actor': a['username'], 'media': media, 'suffix': suffix}
        artifact.update(template=template, template_hash=store.digest(template) if template else None)
        c.execute('INSERT INTO artifacts VALUES(?,?,?,?)', (artifact['id'], pid, a['tenant'], store.canonical(artifact)))
        store.audit(c, p, a, 'EXPORT_COMPLETED', artifact)
    return StreamingResponse(io.BytesIO(payload), media_type=media,
                             headers={'Content-Disposition': 'attachment; filename="QiangQuote-'+(q['id'][:8] if q else 'unpriced')+'.'+suffix+'"', 'X-Artifact-Hash': sha})


@app.get('/api/v1/projects/{pid}/artifacts')
def artifacts(pid: str, a=Depends(actor)):
    with store.connect() as c:
        project(c, pid, a)
        return [json.loads(r['data']) for r in c.execute('SELECT data FROM artifacts WHERE project_id=? AND tenant=? ORDER BY rowid DESC', (pid, a['tenant']))]


@app.get('/api/v1/projects/{pid}/artifacts/{aid}/file')
def artifact_file(pid: str, aid: str, inline: bool = False, a=Depends(actor)):
    with store.connect() as c:
        project(c, pid, a)
        row = c.execute('SELECT data FROM artifacts WHERE id=? AND project_id=? AND tenant=?', (aid, pid, a['tenant'])).fetchone()
        if not row:
            raise HTTPException(404, '成果文件不存在或无权访问')
        artifact = json.loads(row['data'])
    return FileResponse(store.ROOT/'exports'/artifact['hash'], media_type=artifact['media'],
                        filename='QiangQuote-'+aid[:8]+'.'+artifact['suffix'],
                        content_disposition_type='inline' if inline and artifact['format'] == 'pdf' else 'attachment',
                        headers={'X-Artifact-Hash': artifact['hash']})


@app.middleware('http')
async def security_headers(request: Request, call_next):
    # Same-origin mutations only. API identity is never accepted from client tenant headers.
    origin = request.headers.get('origin')
    if request.method not in ('GET', 'HEAD', 'OPTIONS') and origin:
        from urllib.parse import urlsplit
        if urlsplit(origin).netloc != request.headers.get('host'):
            return JSONResponse(status_code=403, content={'detail': '拒绝跨站写入'})
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    if request.url.path.startswith('/api'):
        response.headers['Cache-Control'] = 'no-store'
    return response


from .extensions import router as extension_router
app.include_router(extension_router)
from .analysis_routes import router as analysis_router
app.include_router(analysis_router)
from .reconciliation import router as reconciliation_router
app.include_router(reconciliation_router)
from .workflows import router as workflow_router
app.include_router(workflow_router)
from .templates import router as template_router
app.include_router(template_router)
from .boq_mapping import router as boq_mapping_router
app.include_router(boq_mapping_router)
from .resource_library import router as resource_library_router
app.include_router(resource_library_router)

dist = Path(__file__).parent.parent/'frontend'/'dist'
if dist.exists():
    app.mount('/', StaticFiles(directory=dist, html=True), name='frontend')
