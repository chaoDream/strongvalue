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
from .compound_takeoff import component_quantity, structured_validator, STRUCTURED_KINDS
from .parsers import validate_file, extract, fact_candidates, dimension_candidates, pdf_text_anchors, dxf_objects
from . import facts as project_facts

app = FastAPI(title='QiangQuote 工程智能计价', version='0.45.0', description='原图证据、受控算量、清单组价、变更对账；模型服务可配置，专业软件接入待授权验证。')
store.init()
ROLES = {'manager', 'estimator', 'reviewer', 'admin'}
REQUIREMENTS = {'建筑': '建筑平面、剖面及做法', '结构': '结构配筋、基础及节点', '工艺设备': '工艺流程、设备表及安装要求',
                '给排水': '管线平纵断面、材质及埋深', '电气': '配电系统及敷设方式', '合同计价': '合同、计量与取费依据'}
REVIEW_COLLECTIONS = {'facts', 'components', 'takeoffs', 'boq', 'rules', 'coverage', 'quotes', 'changes', 'catalog', 'dimension_candidates', 'reconciliations', 'price_changes', 'boq_mappings', 'document_requests', 'pricing_bases', 'quantity_diagnoses', 'resource_substitutions', 'engineering_structures', 'engineering_settlements', 'integration_trials', 'drawing_sheets', 'drawing_references'}


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
    a={key:user[key] for key in ['id', 'tenant', 'username', 'role', 'active']}
    from .project_access import denial_context
    scope=denial_context.get()
    if scope is not None: scope['actor']=a
    return a


def edit(a):
    if a['role'] not in ('admin', 'manager', 'estimator'):
        raise HTTPException(403, '当前角色只能复核，不能修改编制数据')


def task_project(c, pid, a):
    """Tenant-bound service work; request handlers must use project()."""
    row = c.execute('SELECT data FROM projects WHERE id=? AND tenant=?', (pid, a['tenant'])).fetchone()
    if not row:
        raise HTTPException(404, '项目不存在或无权访问')
    return normalize(json.loads(row['data']))


def project(c, pid, a):
    from .project_access import allowed, denied
    p = task_project(c,pid,a)
    if not allowed(p,a):
        denied(pid)
        raise HTTPException(404, '项目不存在或无权访问')
    return p


def normalize(p):
    for key in ['imports', 'changes', 'applied_changes', 'scenarios', 'catalog', 'dimension_candidates', 'analyses', 'reconciliations', 'price_changes', 'applied_price_changes', 'boq_mappings', 'library_snapshots', 'document_requests', 'pricing_bases', 'quantity_diagnoses', 'quantity_batches', 'resource_substitutions', 'applied_resource_substitutions', 'engineering_structures', 'quota_adoptions', 'enterprise_quota_adoptions', 'engineering_settlements', 'integration_trials', 'drawing_sheets', 'drawing_references']:
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



def invalidate_component_dependents(p, cid):
    changed = {cid}
    while True:
        before = len(changed)
        for target in p['components']:
            if target['id'] in changed:
                continue
            refs = {target.get('source_component_id'), target.get('excavation_component_id')}
            refs.update(d.get('component_id') for d in target.get('occupations', []))
            if refs & changed:
                changed.add(target['id'])
                if target.get('state') == 'approved':
                    target['state'] = 'stale'
        if len(changed) == before:
            break


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
    from .drawing_register import issues as drawing_issues
    out.extend(drawing_issues(p))
    from .document_requests import task_issues
    out.extend(task_issues(p))
    from .pricing_basis import issues as date_issues
    out.extend(date_issues(p))
    from .engineering_structure import issues as structure_issues
    out.extend(structure_issues(p))
    from .scoped_rules import issues as scoped_issues
    out.extend(scoped_issues(p))
    if any(x['state'] not in ('approved', 'rejected', 'superseded') for x in p['facts']):
        out.append('存在未审核或冲突的工程事实')
    for fact in p['facts']:
        if fact['state']=='approved':
            try: project_facts.validate(p,fact)
            except (HTTPException,DomainError) as exc:
                out.append(fact['label']+'：'+str(getattr(exc,'detail',exc)))
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
    if any(x['state'] not in ('rejected','cancelled') and x['id'] not in p.get('applied_changes', []) for x in p.get('changes', [])):
        out.append('存在待审或已审未应用的变更签证')
    if any(x['state'] != 'rejected' and x['id'] not in p.get('applied_price_changes', []) for x in p.get('price_changes', [])):
        out.append('存在待审或已审未应用的资源调价')
    if p.get('require_golden_baseline'):
        from .reconciliation import matches_context
        if not any(r['state'] == 'approved' and r['passed'] and matches_context(p,r) for r in p.get('reconciliations', [])):
            out.append('缺少覆盖当前工程量的已审专业黄金底稿对照')
    for comp in p['components']:
        if comp['kind'] in STRUCTURED_KINDS:
            try:
                structured_validator(comp['kind'])(p, comp, saved=True)
            except (HTTPException, DomainError) as exc:
                out.append(comp['name']+'：'+str(getattr(exc, 'detail', exc)))
    out.extend(price_blockers(p))
    return list(dict.fromkeys(out))


def price_blockers(p, catalog_context=None):
    from .resource_library import reference_errors
    out = []
    tax_modes = set()
    for item in p['boq']:
        if not item.get('quotas'):
            out.append(item['name']+'缺少定额及资源价格')
        for q in item.get('quotas', []):
            if not q.get('license') or not q.get('edition') or not q.get('source') or not q.get('resources'):
                out.append(item['name']+'缺少定额版本、授权、来源或消耗')
            if 'conversion' in q:
                from .engine import quota_conversion
                try:
                    quota_conversion(q)
                except (DomainError, KeyError, TypeError) as error:
                    out.append('定额换算无效：'+str(error))
            for r in q.get('resources', []):
                from .engine import resource_pricing_state
                try:
                    pending = resource_pricing_state(r) == 'unpriced'
                    if not pending:
                        number(r.get('price'))
                except (DomainError, KeyError, TypeError) as error:
                    out.append(r.get('name', '资源')+'价格无效：'+str(error))
                    continue
                if pending:
                    out.append(r.get('name', '资源')+'待询价，不能以零价参与组价')
                    continue
                if r.get('source_type') == '历史审核价':
                    out.append(r.get('name', '资源')+'历史审核价仅供参考，须补充当前适用且获授权的价格')
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
            for priced in [q]+q.get('resources', [])+([q['conversion']] if isinstance(q.get('conversion'), dict) else []):
                e = next((e for e in p['evidence'] if e['id'] == priced.get('evidence_id')), None)
                if not e or next((d for d in p['documents'] if d['id'] == e['document_id']), {}).get('superseded'):
                    out.append('定额/资源价格缺少当前原始证据')
    if len(tax_modes) > 1:
        out.append('资源含税与不含税价格混用；请按计价规则统一口径')
    out.extend(reference_errors(p))
    from .quota_catalog import reference_errors as catalog_errors
    out.extend(catalog_errors(catalog_context or p,p['boq']))
    from .enterprise_quotas import reference_errors as enterprise_errors
    out.extend(enterprise_errors(catalog_context or p,p['boq']))
    from .resource_substitutions import reference_errors as substitution_errors
    out.extend(substitution_errors(p))
    return out


def view(p):
    normalize(p)
    from .document_requests import source_issues
    from .pricing_basis import issues as date_issues
    from .reconciliation import context_hash
    from .pricing_tasks import tasks
    structure = p['engineering_structures'][-1] if p['engineering_structures'] else None
    from .drawing_register import projection as drawing_projection
    return {**p, 'drawing_register':drawing_projection(p), 'fact_conflicts':project_facts.conflict_groups(p), 'rule_hash':store.digest(p['rules'][0]) if p['rules'] else '', 'engineering_structure_hash': store.digest(structure) if structure else '', 'pricing_tasks': tasks(p), 'gaps': gaps(p), 'blockers': blockers(p), 'capabilities': capabilities(), 'document_request_source_issues': source_issues(p), 'pricing_basis_issues': date_issues(p), 'quantity_context_hash': context_hash(p)}


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
    from .project_access import allowed
    return [view(p) for p in (json.loads(r['data']) for r in reversed(rows)) if allowed(p,a)]


@app.get('/api/v1/projects/{pid}')
def get_project(pid: str, a=Depends(actor)):
    with store.connect() as c:
        return view(project(c, pid, a))


def parse_job(pid, did, jid, a):
    from .task_execution import claim,current,validate_source,original_bytes,fail
    token=claim(pid,did,jid,a)
    if token is None:return
    try:
        with store.transaction() as c:
            p = task_project(c, pid, a)
            d = find(p, 'documents', did)
            job=find(p,'jobs',jid)
            if not current(job,token):return
            validate_source(d,job)
            original_bytes(d)
            if d.get('parsed'):
                job.update(state='completed',finished_at=store.now(),message='原件已有完整解析结果，未重复生成')
                store.save(c, p)
                return
            find(p, 'jobs', jid)['state'] = 'running'
            store.save(c, p)
        raw=original_bytes(d)
        pages = extract(Path(d['name']).suffix.lower(), raw)
        vector_objects = pdf_text_anchors(raw) if d['name'].lower().endswith('.pdf') else dxf_objects(raw)[0] if d['name'].lower().endswith('.dxf') else []
        with store.transaction() as c:
            p = task_project(c, pid, a)
            d = find(p, 'documents', did)
            job=find(p,'jobs',jid)
            if not current(job,token):return
            validate_source(d,job)
            original_bytes(d)
            # Retry is idempotent: a completed document is not parsed twice.
            if d.get('parsed'):
                job.update(state='completed',finished_at=store.now(),message='已有解析结果，未重复写入')
                store.save(c,p)
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
                    project_facts.append(p, project_facts.bind(fact,e,d,'explicit_text',fact['raw_text']))
            d['parsed'] = True
            d['vector_objects'] = vector_objects
            d['pages'] = len(pages)
            d['parse_state'] = 'needs_review'
            d['message'] = '文字已提取；尺寸及事实须人工复核' if any(t.strip() for _, t in pages) else '未提取到可用文字；扫描图需 OCR/视觉适配器，或手工标注证据'
            job.update(state='completed', message=d['message'],finished_at=store.now())
            p['status'] = 'FACTS_REVIEW'
            store.audit(c, p, a, 'DOCUMENT_PARSED', {'document_id': did, 'pages': len(pages)})
            store.save(c, p)
    except Exception as exc:
        fail(pid,did,jid,a,token,exc)


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
        job = {'id': store.uid(), 'document_id': d['id'], 'state': 'queued', 'message': '等待解析','kind':'local_parse','source_hash':sha,'attempts':0}
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
        from .task_execution import validate_source,validate_model
        doc=find(p,'documents',j['document_id'])
        validate_source(doc,j)
        if j.get('kind') == 'model_analysis':validate_model(p,doc,j)
        if j.get('kind') == 'local_ocr':
            from .ocr import validate_job
            validate_job(p,doc,j)
        j.update(state='queued',message='已申请重试，等待执行',finished_at=None)
        store.audit(c,p,a,'JOB_RETRY_REQUESTED',{'job_id':jid,'previous_attempts':j.get('attempts',0)})
        store.save(c, p)
    if j.get('kind') == 'model_analysis':
        from .analysis_routes import analyze_job
        tasks.add_task(analyze_job, pid, j['document_id'], jid, a)
    elif j.get('kind') == 'local_ocr':
        from .ocr import ocr_job
        tasks.add_task(ocr_job, pid, j['document_id'], jid, a)
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
        text = body.get('text', '')
        if not isinstance(text,str) or not text.strip():
            raise HTTPException(422, '请填写原文或标注内容')
        text = text.strip()
        page = body.get('page')
        from .evidence_regions import page_region
        region = page_region(d, body)
        e = {'id': store.uid(), 'document_id': d['id'], 'document_hash': d['hash'], 'page': page,
             'text': text[:100000], **region, 'method': 'manual', 'drawing_no': str(body.get('drawing_no', ''))[:200]}
        p['evidence'].append(e)
        store.audit(c, p, a, 'EVIDENCE_CREATED', e)
        store.save(c, p)
    return e


def require_evidence(p, eid):
    e = find(p, 'evidence', eid)
    if find(p, 'documents', e['document_id']).get('superseded'):
        raise HTTPException(409, '证据已过期，请引用当前图纸')
    if 'ocr_transcription' in e:
        from .ocr_transcriptions import validate_transcription
        validate_transcription(p,e)
    return e


@app.get('/api/v1/fact-types')
def fact_types(a=Depends(actor)):
    return project_facts.catalog()


@app.post('/api/v1/projects/{pid}/facts')
def add_fact(pid: str, body: dict, a=Depends(actor)):
    edit(a)
    with store.transaction() as c:
        p = project(c, pid, a)
        evidence = require_evidence(p, body.get('evidence_id'))
        if not all(body.get(k) for k in ['key', 'label', 'value']):
            raise HTTPException(422, '事实字段不完整')
        doc = find(p,'documents',evidence['document_id'])
        from .task_execution import original_bytes
        original_bytes(doc)
        f = project_facts.normalize(body['key'],body['value'],body.get('unit',''))
        if body['key'] not in project_facts.CATALOG:
            if not isinstance(body['label'],str) or len(body['label'])>100: raise DomainError('事实名称无效')
            f['label']=body['label']
        f = project_facts.bind(f,evidence,doc,'manual',body.get('raw_text') or evidence['text'][:2000])
        project_facts.append(p,f)
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
        old = find(p, 'components', cid) if cid else None
        compound = body.get('kind') in STRUCTURED_KINDS
        if old and (compound or old['kind'] in STRUCTURED_KINDS):
            if body.get('object_hash') != store.digest(old):
                raise HTTPException(409, '构件已变化，请刷新后再修订')
            if not compound or old['kind'] in STRUCTURED_KINDS and body['kind'] != old['kind']:
                raise HTTPException(409, '结构化构件须通过原编辑器修订，不能静默改类型或丢弃扣减明细')
        if compound:
            result, stamp = structured_validator(body['kind'])(p, body)
            fields = ['name', 'kind', 'parameters', 'measurement_basis', 'basis_evidence_id']
            if body['kind'] == 'chamber_plate':
                fields += ['quantity_type','plate_role','source_component_id','source_component_hash','plate_sections','scope_confirmed','material','material_evidence_id','construction_basis','construction_evidence_id','rounding_digits','rounding_evidence_id']
                if 'opening_mode' in body: fields += ['opening_mode']
            elif body['kind'] == 'circular_chamber':
                fields += ['quantity_type','purpose','source_component_id','source_component_hash','chamber_wells','locations_confirmed','material','material_evidence_id','construction_basis','construction_evidence_id','rounding_digits','rounding_evidence_id']
            elif body['kind'] == 'route_bedding':
                fields += ['quantity_type','source_component_id','source_component_hash','bedding_sections','scope_confirmed','length_basis','material','material_evidence_id','construction_basis','construction_evidence_id','rounding_digits','rounding_evidence_id']
            elif body['kind'] == 'pipe_envelope':
                fields += ['quantity_type','source_component_id','source_component_hash','pipe_sections','outside_confirmed','rounding_digits','rounding_evidence_id']
            elif body['kind'] == 'volume_balance':
                fields += ['quantity_type','excavation_component_id','excavation_component_hash','occupations','boundaries_confirmed','rounding_digits','rounding_evidence_id']
            elif body['kind'] == 'station_trench':
                fields += ['quantity_type','source_component_id','source_component_hash','stations','method','geology_basis','geology_evidence_id','construction_basis','construction_evidence_id','rounding_digits','rounding_evidence_id']
            elif body['kind'] == 'design_route':
                fields += ['route_points','quantity_type','coordinate_basis','coordinate_evidence_id','multiplicity','multiplicity_evidence_id','rounding_digits','rounding_evidence_id']
            elif body['kind'] == 'surface_faces':
                fields += ['quantity_type','purpose','source_component_id','source_component_hash','face_decisions']
                if 'face_subdivisions' in body: fields += ['face_subdivisions']
            else:
                fields += ['measurement_parts'] if body['kind']=='compound' else ['regions','quantity_type','coordinate_basis','coordinate_evidence_id','multiplicity','multiplicity_evidence_id']
            if body['kind']=='planar_regions' and body['quantity_type']=='volume': fields += ['thickness','thickness_unit','thickness_evidence_id']
            comp = {k: body[k] for k in fields}
            comp.update(evidence=result['source_evidence'], source_stamp=stamp)
            if body['kind'] in ('surface_faces','station_trench','volume_balance','pipe_envelope','route_bedding','circular_chamber','chamber_plate'): comp['source_geometry'] = result['source_geometry']
        else:
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
            comp = {k: body[k] for k in ['name', 'kind', 'parameters', 'evidence', 'measurement_basis']}
        invalidate(p, 'takeoffs' if body['kind'] in ('surface_faces','station_trench','volume_balance','pipe_envelope','route_bedding','circular_chamber','chamber_plate') else 'components')
        if old: invalidate_component_dependents(p, cid)
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


@app.get('/api/v1/projects/{pid}/chamber-plate-candidates')
def chamber_plate_candidates(pid: str, component_id: str, a=Depends(actor)):
    from .chamber_plate import candidates
    with store.connect() as c:
        return candidates(project(c, pid, a), component_id)


@app.get('/api/v1/projects/{pid}/chamber-location-candidates')
def chamber_location_candidates(pid: str, component_id: str, a=Depends(actor)):
    from .chamber_takeoff import candidates
    with store.connect() as c:
        return candidates(project(c, pid, a), component_id)


@app.get('/api/v1/projects/{pid}/bedding-section-candidates')
def bedding_section_candidates(pid: str, component_id: str, a=Depends(actor)):
    from .bedding_takeoff import candidates
    with store.connect() as c:
        return candidates(project(c, pid, a), component_id)


@app.get('/api/v1/projects/{pid}/pipe-envelope-candidates')
def pipe_envelope_candidates(pid: str, component_id: str, a=Depends(actor)):
    from .pipe_envelope import candidates
    with store.connect() as c:
        return candidates(project(c, pid, a), component_id)


@app.post('/api/v1/projects/{pid}/volume-balance-candidates')
def volume_balance_candidates(pid: str, body: dict, a=Depends(actor)):
    from .volume_balance import candidates
    with store.connect() as c:
        return candidates(project(c, pid, a), body)


@app.get('/api/v1/projects/{pid}/trench-station-candidates')
def trench_station_candidates(pid: str, component_id: str, a=Depends(actor)):
    from .trench_takeoff import candidates
    with store.connect() as c:
        return candidates(project(c, pid, a), component_id)


@app.get('/api/v1/projects/{pid}/surface-face-candidates')
def surface_face_candidates(pid: str, component_id: str, a=Depends(actor)):
    from .surface_takeoff import candidates
    with store.connect() as c:
        return candidates(project(c, pid, a), component_id)


@app.post('/api/v1/projects/{pid}/surface-face-candidates/partition')
def partition_surface_faces(pid: str, body: dict, a=Depends(actor)):
    edit(a)
    from .surface_takeoff import partition_candidates
    with store.connect() as c:
        return partition_candidates(project(c, pid, a), body)


@app.post('/api/v1/projects/{pid}/components/preview')
def preview_component(pid: str, body: dict, a=Depends(actor)):
    edit(a)
    with store.connect() as c:
        p = project(c, pid, a)
        if body.get('kind') not in STRUCTURED_KINDS:
            raise HTTPException(422, '此预览用于分项、区域、表面或设计管线算量')
        result, stamp = structured_validator(body['kind'])(p, body)
    return {**result, 'source_stamp': stamp}


@app.post('/api/v1/projects/{pid}/takeoffs/calculate')
def calculate_takeoff(pid: str, a=Depends(actor)):
    edit(a)
    with store.transaction() as c:
        p = project(c, pid, a)
        if p['applied_changes']:
            raise HTTPException(409, '当前清单已应用签证；重新生成原设计算量前需专业人员建立新的变更基线，避免把变更工程量覆盖回原设计')
        if not p['components'] or any(x['state'] != 'approved' for x in p['components']):
            raise HTTPException(409, '所有构件尺寸需先经专业复核')
        for comp in p['components']:
            if comp['kind'] in STRUCTURED_KINDS: structured_validator(comp['kind'])(p, comp, saved=True)
        invalidate(p, 'boq')
        # Retain old calculation books; current version is the only BOQ input.
        p.setdefault('takeoff_history', []).extend(p['takeoffs'])
        external = [t for t in p['takeoffs'] if t.get('source_kind') in ('imported_boq', 'change_order')]
        p['takeoffs'] = external + [{**component_quantity(x), 'id': store.uid(), 'name': x['name'],
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


@app.get('/api/v1/projects/{pid}/pricing-tasks')
def pricing_tasks(pid: str, a=Depends(actor)):
    from .pricing_tasks import tasks
    with store.connect() as c:
        return tasks(project(c, pid, a))


@app.put('/api/v1/projects/{pid}/boq/{bid}')
def update_boq(pid: str, bid: str, body: dict, a=Depends(actor)):
    edit(a)
    with store.transaction() as c:
        p = project(c, pid, a)
        b = find(p, 'boq', bid)
        if 'object_hash' in body and body['object_hash'] != store.digest(b):
            raise HTTPException(409, '清单或资源已变化，请刷新后重新核对')
        if b.get('mapping_ref') and 'code' in body and body['code'] != b['code']:
            raise HTTPException(409, '此编码来自已审映射；更换编码请修订并采用新的有据映射')
        if 'quotas' in body:
            if not isinstance(body['quotas'], list):
                raise HTTPException(422, '定额须为数组')
            try:
                from .engine import validate_pricing_inputs
                validate_pricing_inputs([{**b, 'quotas': body['quotas']}])
                for q in body['quotas']:
                    require_evidence(p, q.get('evidence_id'))
                    if 'conversion' in q:
                        require_evidence(p, q['conversion']['evidence_id'])
                    for resource in q['resources']:
                        require_evidence(p, resource.get('evidence_id'))
            except (KeyError, TypeError) as e:
                raise HTTPException(422, '定额资源字段不完整：'+str(e))
        if body.get('detach_catalog') is True and (not isinstance(body.get('reason'),str) or not body['reason'].strip() or len(body['reason'])>3000):
            raise HTTPException(422,'转为人工编制须填写原因')
        if body.get('detach_catalog') is True or 'quotas' in body and body['quotas'] != b['quotas']:
            previous_ref=b.pop('catalog_ref',None)
            if previous_ref:store.audit(c,p,a,'QUOTA_CANDIDATE_DETACHED',{'boq_id':bid,'previous_ref':previous_ref,'reason':body.get('reason') or '人工修改定额与资源，保留历史采用记录'})
        if b.get('enterprise_quota_ref'):
            from .enterprise_quotas import composition_hash
            if body.get('detach_catalog') is True or 'quotas' in body and composition_hash(body['quotas'])!=composition_hash(b['quotas']):
                previous_ref=b.pop('enterprise_quota_ref')
                store.audit(c,p,a,'ENTERPRISE_QUOTA_DETACHED',{'boq_id':bid,'previous_ref':previous_ref,'reason':body.get('reason') or '人工修改定额消耗组成，保留历史采用记录'})
        invalidate(p, 'boq')
        b.update({k: body[k] for k in ['code', 'name', 'features', 'node', 'quotas'] if k in body})
        b['state'] = 'draft'
        p['status'] = 'BOQ_DRAFT'
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
        if 'object_hash' in body and body['object_hash'] != (store.digest(p['rules'][0]) if p['rules'] else ''):
            raise HTTPException(409, '计价规则已变化，请刷新后核对再提交')
        require_evidence(p, body['evidence_id'])
        from .scoped_rules import validate as validate_scoped
        validate_scoped(p, body)
        if p['rules']:
            p.setdefault('rule_history', []).extend(p['rules'])
        p['rules'] = [{**{k: body[k] for k in required}, 'rounding_config': body.get('rounding_config', {}), 'node_fees':body.get('node_fees', []), 'structure_hash':body.get('structure_hash','') if body.get('node_fees') else '', 'id': store.uid(), 'state': 'draft'}]
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
    if collection in ('drawing_sheets','drawing_references'):
        from .drawing_register import assert_current
        assert_current(p,collection,target)
    if collection == 'changes' and target['state'] == 'cancelled':
        raise HTTPException(409, '已取消调量子项不能签署复核，请重新建立调量单')
    if collection == 'document_requests' and target['state'] == 'cancelled':
        raise HTTPException(409, '已取消任务须先重新开启，不能直接复核')
    if collection == 'pricing_bases' and target['id'] != p['pricing_bases'][-1]['id']:
        raise HTTPException(409, '历史日期裁决只供查阅，请复核最新版本')
    if collection == 'engineering_structures' and target['id'] != p['engineering_structures'][-1]['id']:
        raise HTTPException(409, '历史工程结构仅供查阅，请复核最新版本')
    if collection == 'quantity_diagnoses':
        from .quantity_diagnosis import latest
        if latest(p, target['reconciliation_id'], target['target_key'])['id'] != target['id']:
            raise HTTPException(409, '历史诊断只供查阅，请复核最新诊断版本')
    approve = body['decision'] == 'approve'
    if approve:
        if collection == 'integration_trials':
            from .integration_trials import validate
            validate(p, target)
        if collection == 'engineering_settlements':
            from .engineering_settlements import validate
            validate(p,target)
        if collection == 'engineering_structures':
            from .engineering_structure import validate
            validate(p, target)
        if collection == 'resource_substitutions':
            from .resource_substitutions import validate
            validate(p, target)
        if collection == 'quantity_diagnoses':
            from .quantity_diagnosis import validate
            validate(p, target)
        if collection == 'pricing_bases':
            from .pricing_basis import validate
            validate(p, target)
        if collection == 'document_requests':
            from .document_requests import validate_submission
            if target['state'] not in ('submitted', 'approved'):
                raise HTTPException(409, '补件任务须先提交补充依据再复核关闭')
            validate_submission(p, target)
        if collection in ('drawing_sheets','drawing_references'):
            from .drawing_register import validate_sheet,validate_reference
            (validate_sheet if collection=='drawing_sheets' else validate_reference)(p,target)
        if collection == 'facts':
            if project_facts.conflicts(p,target):
                raise HTTPException(409, '先拒绝冲突候选，再核准选定值')
            project_facts.validate(p,target)
        if collection == 'dimension_candidates':
            require_evidence(p, target['evidence_id'])
            if any(x['component_name'] and x['component_name'] == target['component_name'] and x['parameter'] == target['parameter'] and Decimal(x['value']) != Decimal(target['value']) and x['state'] not in ('rejected','superseded') for x in p['dimension_candidates']):
                raise HTTPException(409, '同构件同参数存在冲突值，请先拒绝不采纳候选')
        if collection == 'components':
            if target['kind'] in STRUCTURED_KINDS:
                structured_validator(target['kind'])(p, target, saved=True)
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
                if comp['kind'] in STRUCTURED_KINDS:
                    result, _ = structured_validator(comp['kind'])(p, comp, saved=True)
                    if any(target.get(k) != value for k, value in result.items()):
                        raise HTTPException(409, '计算书与构件明细不一致，请重新算量')
        if collection == 'boq':
            if not target.get('code') or not target.get('features'):
                raise HTTPException(409, '请补充有依据的清单编码和项目特征')
            from .engine import quota_conversion
            for quota in target.get('quotas', []):
                if 'conversion' in quota:
                    quota_conversion(quota)
                    require_evidence(p, quota['conversion']['evidence_id'])
            from .resource_substitutions import reference_errors as substitution_errors
            substitution_issues = substitution_errors({**p, 'boq': [target]})
            if substitution_issues:
                raise HTTPException(409, '；'.join(substitution_issues))
            from .quota_catalog import reference_errors as catalog_errors
            from .enterprise_quotas import reference_errors as enterprise_errors
            catalog_issues=catalog_errors(p,[target],True)+enterprise_errors(p,[target],True)
            if catalog_issues:raise HTTPException(409,'；'.join(catalog_issues))
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
            from .scoped_rules import validate as validate_scoped
            validate_scoped(p, target)
            require_evidence(p, target['evidence_id'])
            if target['region'] != p['region'] or not date.fromisoformat(target['valid_from']) <= effective_date(p) <= date.fromisoformat(target['valid_until']):
                raise HTTPException(409, '规则包地区或有效期不适用')
        if collection == 'changes':
            require_evidence(p, target['evidence_id'])
            if target['base_hash'] != store.digest(find(p, 'boq', target['boq_id'])):
                raise HTTPException(409, '变更基线已变化，请重新建立变更')
        if collection == 'catalog':
            if target.get('schema')=='quota-catalog-v2':
                from .quota_catalog import validate as validate_catalog
                validate_catalog(p,target,True)
            if price_blockers({**p, 'boq': [target]},p):
                raise HTTPException(409, '定额候选来源、许可、价格或适用期间不完整')
        if collection == 'reconciliations':
            from .reconciliation import matches_context
            require_evidence(p, target['evidence_id'])
            if not target['passed'] or not matches_context(p,target):
                raise HTTPException(409, '底稿对照不通过或当前工程量已变化；请解决差异后重新对账')
        if collection == 'price_changes':
            from .workflows import validate_price_change
            validate_price_change(p, target)
        if collection == 'quotes':
            from .quota_catalog import reference_errors as catalog_errors
            from .enterprise_quotas import reference_errors as enterprise_errors
            catalog_issues=catalog_errors(p,verify_bytes=True)+enterprise_errors(p,verify_bytes=True)
            if catalog_issues:raise HTTPException(409,'；'.join(catalog_issues))
            from .scoped_rules import validate as validate_scoped
            if p['rules']:validate_scoped(p,p['rules'][0])
            from .engineering_structure import latest as current_structure, validate as validate_structure
            if current_structure(p):
                validate_structure(p, current_structure(p))
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
        if not approve and collection in ('drawing_sheets','drawing_references'):
            invalidate(p,'components' if collection=='drawing_sheets' else 'quotes')
        elif not approve and collection not in ('engineering_settlements', 'integration_trials'):
            if collection == 'boq_mappings':
                from .boq_mapping import invalidate_dependents
                invalidate_dependents(p, {target['id']})
            if collection == 'resource_substitutions':
                for b in p['boq']:
                    if any(r.get('substitution_id') == target['id'] for q in b['quotas'] for r in q['resources']):
                        b['state'] = 'stale'
            if collection == 'price_changes':
                for b in p['boq']:
                    if any(r.get('price_change_id') == target['id'] for q in b['quotas'] for r in q['resources']):
                        b['state'] = 'stale'
            if collection in ('coverage', 'changes', 'catalog', 'dimension_candidates', 'reconciliations', 'price_changes', 'boq_mappings', 'document_requests', 'pricing_bases', 'quantity_diagnoses', 'resource_substitutions', 'engineering_structures'):
                invalidate(p, 'quotes')
            else:
                invalidate(p, collection)
        elif collection in ('takeoffs', 'boq') and all(x['state'] == 'approved' for x in p[collection]):
            p['status'] = 'TAKEOFF_APPROVED' if collection == 'takeoffs' else 'BOQ_APPROVED'
    if collection == 'engineering_structures':
        invalidate(p, 'quotes')
    if collection in ('drawing_sheets','drawing_references'):
        # A new review record is an input change even when the row stays approved.
        invalidate(p, 'quotes')
    if collection == 'document_requests':
        target.update(version=target['version']+1, updated_at=store.now())
        invalidate(p, 'quotes')
    if collection == 'quantity_diagnoses':
        invalidate(p, 'quotes')
    if collection == 'pricing_bases':
        if approve:
            p['pricing_date'] = target['pricing_date']
        invalidate(p, 'quotes')
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
        from .drawing_register import issues as drawing_issues
        drawing_errors=drawing_issues(p)
        if drawing_errors:raise HTTPException(409,'图纸校核尚未完成：'+'；'.join(drawing_errors))
        if not p['boq'] or any(x['state'] != 'approved' for x in p['boq']):
            raise HTTPException(409, '请先审定清单')
        from .engineering_structure import latest as current_structure, issues as structure_issues, validate as validate_structure, rollup
        structure_errors = structure_issues(p)
        if structure_errors:
            raise HTTPException(409, '；'.join(structure_errors))
        if current_structure(p):
            validate_structure(p, current_structure(p))
        from .pricing_basis import issues as date_issues
        date_errors = date_issues(p)
        if date_errors:
            raise HTTPException(409, '计价日期尚未核准：'+'；'.join(date_errors))
        from .quota_catalog import reference_errors as catalog_errors
        from .enterprise_quotas import reference_errors as enterprise_errors
        issues = price_blockers(p)+catalog_errors(p,verify_bytes=True)+enterprise_errors(p,verify_bytes=True)
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
        snapshot['document_requests']=p['document_requests']
        snapshot['pricing_bases']=p['pricing_bases']
        snapshot['quantity_diagnoses']=p['quantity_diagnoses']
        snapshot['quantity_batches']=p['quantity_batches']
        snapshot['resource_substitutions']=p['resource_substitutions']
        snapshot['applied_resource_substitutions']=p['applied_resource_substitutions']
        snapshot['engineering_structures']=p['engineering_structures']
        snapshot['catalog']=p['catalog']
        snapshot['quota_adoptions']=p.get('quota_adoptions',[])
        snapshot['enterprise_quota_adoptions']=p['enterprise_quota_adoptions']
        snapshot['drawing_sheets']=p['drawing_sheets']
        snapshot['drawing_references']=p['drawing_references']
        from .scoped_rules import validate as validate_scoped
        validate_scoped(p, ruleset)
        result = calculate(p['boq'], ruleset, current_structure(p))
        engineering_summary = rollup(p, result)
        if engineering_summary:
            result['engineering_summary'] = engineering_summary
        q = {'id': store.uid(), 'version': len(p['quotes'])+1, 'created_at': store.now(), 'state': 'draft',
             'input_hash': store.digest(snapshot), 'snapshot': json.loads(store.canonical(snapshot)), **result,
             'warnings': blockers(p), 'project_name': p['name'], 'scope': p['scope']}
        q['source_mode'] = 'imported_or_mixed' if any(t.get('source_kind') == 'imported_boq' for t in p['takeoffs']) else 'original_documents'
        if q['source_mode'] == 'imported_or_mixed':
            q['warnings'].append('包含已有清单导入数量；兼容流程不等于原始图纸自动算量验收')
        if not p['pricing_bases']:
            q['warnings'].append('尚未登记多时间节点专业裁决，计价日期按项目手工设置或当前日期检查')
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
    return {'status': 'ok', 'version': '0.45.0', 'storage': 'sqlite-local', 'ai': 'not_configured'}


def persist_export(c, p, a, format='pdf', quote_id='', template_id='', unpriced=False):
    from .reports import export_report
    pid = p['id']
    if unpriced and quote_id:
        raise HTTPException(422, '不能同时选择报价版本和当前无价草稿')
    q = None if unpriced else (find(p, 'quotes', quote_id) if quote_id else (p['quotes'][-1] if p['quotes'] else None))
    if q and (store.digest(q['snapshot']) != q['input_hash'] or store.digest({k:v for k,v in q.items() if k != 'hash'}) != q['hash']):
        raise HTTPException(409, '所选报价快照哈希不一致，不能生成成果文件')
    approved = bool(q and p['report_approval'] and p['report_approval']['quote_id'] == q['id'] and q['id'] not in p['stale_quotes'] and not blockers(p))
    from .templates import find_template
    template = find_template(c, template_id, a['tenant']) if template_id else None
    pagination = {}
    if format == 'zip':
        from .delivery import export_bundle
        payload, media, suffix = export_bundle(c, p, q, template)
    else:
        payload, media, suffix = export_report(p, q, format, approved, template, pagination=pagination)
    sha = hashlib.sha256(payload).hexdigest()
    directory = store.ROOT/'exports'
    directory.mkdir(exist_ok=True)
    path = directory/sha
    if not path.exists():
        path.write_bytes(payload)
    artifact = {'id': store.uid(), 'format': format, 'quote_id': q['id'] if q else None, 'quote_version': q['version'] if q else None,
                'hash': sha, 'approved': approved, 'timestamp': store.now(), 'actor': a['username'], 'media': media, 'suffix': suffix}
    if pagination: artifact['pdf_manifest'] = pagination
    artifact['report_context'] = {'project_name':q['project_name'] if q else p['name'], 'scope':q['scope'] if q else p['scope'], 'quote_hash':q['hash'] if q else None}
    artifact.update(template=template, template_hash=store.digest(template) if template else None)
    c.execute('INSERT INTO artifacts VALUES(?,?,?,?)', (artifact['id'], pid, a['tenant'], store.canonical(artifact)))
    store.audit(c, p, a, 'EXPORT_COMPLETED', artifact)
    return artifact, payload


@app.get('/api/v1/projects/{pid}/exports')
def export(pid: str, format: str = 'json', quote_id: str = '', template_id: str = '', unpriced: bool = False, a=Depends(actor)):
    with store.transaction() as c:
        artifact, payload = persist_export(c, project(c, pid, a), a, format, quote_id, template_id, unpriced)
    return StreamingResponse(io.BytesIO(payload), media_type=artifact['media'],
                             headers={'Content-Disposition': 'attachment; filename="QiangQuote-'+(artifact['quote_id'][:8] if artifact['quote_id'] else 'unpriced')+'.'+artifact['suffix']+'"', 'X-Artifact-Hash': artifact['hash'], 'X-Artifact-ID': artifact['id']})


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
    from .delivery import file_digest
    path = store.ROOT/'exports'/artifact['hash']
    try:
        intact = file_digest(path) == artifact['hash']
    except OSError:
        intact = False
    if not intact:
        raise HTTPException(409, '历史导出件缺失或内容哈希不一致，请恢复原件后下载')
    return FileResponse(path, media_type=artifact['media'],
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
    from .project_access import denial_context,audit_denials
    scope={'actor':None,'projects':set()}
    token=denial_context.set(scope)
    try:
        response = await call_next(request)
    finally:
        denial_context.reset(token)
    audit_denials(scope,request.method,request.url.path)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    if request.url.path.startswith('/api'):
        response.headers['Cache-Control'] = 'no-store'
    return response


from .engineering_structure import router as engineering_structure_router
app.include_router(engineering_structure_router)
from .resource_substitutions import router as resource_substitution_router
app.include_router(resource_substitution_router)
from .project_access import router as project_access_router
app.include_router(project_access_router)
from .quantity_batches import router as quantity_batch_router
app.include_router(quantity_batch_router)
from .quantity_diagnosis import router as quantity_diagnosis_router
app.include_router(quantity_diagnosis_router)
from .pricing_basis import router as pricing_basis_router
app.include_router(pricing_basis_router)
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
from .document_requests import router as document_requests_router
app.include_router(document_requests_router)
from .quote_comparison import router as quote_comparison_router
app.include_router(quote_comparison_router)
from .quota_catalog import router as quota_catalog_router
app.include_router(quota_catalog_router)
from .enterprise_quotas import router as enterprise_quota_router
app.include_router(enterprise_quota_router)
from .integration_trials import router as integration_trial_router
app.include_router(integration_trial_router)
from .engineering_settlements import router as engineering_settlement_router
app.include_router(engineering_settlement_router)
from .delivery import router as delivery_router
app.include_router(delivery_router)

from .drawing_candidates import router as drawing_candidates_router
app.include_router(drawing_candidates_router)
from .drawing_register import router as drawing_register_router
app.include_router(drawing_register_router)
from .report_preview import router as report_preview_router
app.include_router(report_preview_router)

from .ocr import router as ocr_router
app.include_router(ocr_router)

from .ocr_transcriptions import router as ocr_transcription_router
app.include_router(ocr_transcription_router)

dist = Path(__file__).parent.parent/'frontend'/'dist'
if dist.exists():
    app.mount('/', StaticFiles(directory=dist, html=True), name='frontend')
