"""Project workflows that build on the original-drawing path without bypassing its reviews."""
import copy
import hashlib
import re
import sqlite3
import json
from datetime import date
from decimal import Decimal, localcontext
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from . import main as core, store
from .engine import number, quantity_number, calculate, DomainError, GEOMETRIES, money
from .imports import preview_boq

router = APIRouter(prefix='/api/v1')


def admin(a):
    if a['role'] != 'admin': raise HTTPException(403, '仅企业管理员可以管理成员')


@router.get('/organization/members')
def members(a=Depends(core.actor)):
    admin(a)
    with store.connect() as c:
        return [dict(row) for row in c.execute('SELECT id,username,role,active FROM users WHERE tenant=? ORDER BY username', (a['tenant'],))]


@router.post('/organization/members')
def create_member(body: dict, a=Depends(core.actor)):
    admin(a)
    if not isinstance(body.get('username'), str) or not re.fullmatch(r'[A-Za-z0-9_.-]{2,60}', body['username']):
        raise HTTPException(422, '账号使用2至60位字母、数字、点、横线或下划线')
    if body.get('role') not in core.ROLES or not isinstance(body.get('password'), str) or not 12 <= len(body['password']) <= 256:
        raise HTTPException(422, '请选择有效角色并设置12至256位密码')
    user = {'id': store.uid(), 'username': body['username'], 'role': body['role'], 'active': 1}
    try:
        with store.transaction() as c:
            c.execute('INSERT INTO users(id,tenant,username,password,role) VALUES(?,?,?,?,?)', (user['id'], a['tenant'], user['username'], core.password_hash(body['password']), user['role']))
            store.audit(c, {'id':'tenant:'+a['tenant'], 'tenant':a['tenant']}, a, 'MEMBER_CREATED', user)
    except sqlite3.IntegrityError:
        raise HTTPException(409, '账号名称不可用')
    return user


@router.patch('/organization/members/{uid}')
def update_member(uid: str, body: dict, a=Depends(core.actor)):
    admin(a)
    if uid == a['id']: raise HTTPException(409, '不能在此修改自己的角色或停用自己')
    if not body.get('reason', '').strip(): raise HTTPException(422, '成员权限变更须填写原因')
    if body.get('role') not in core.ROLES or type(body.get('active')) is not bool:
        raise HTTPException(422, '角色和启停状态无效')
    with store.transaction() as c:
        row = c.execute('SELECT id,username,role,active FROM users WHERE id=? AND tenant=?', (uid, a['tenant'])).fetchone()
        if not row: raise HTTPException(404, '成员不存在或无权操作')
        c.execute('UPDATE users SET role=?,active=? WHERE id=?', (body['role'], int(body['active']), uid))
        c.execute('DELETE FROM sessions WHERE user_id=?', (uid,))
        store.audit(c, {'id':'tenant:'+a['tenant'], 'tenant':a['tenant']}, a, 'MEMBER_ACCESS_CHANGED',
                    {'before': dict(row), 'role':body['role'], 'active':body['active'], 'reason':body['reason']})
    return {'id':uid, 'username':row['username'], 'role':body['role'], 'active':body['active']}


@router.get('/organization/audit')
def organization_audit(a=Depends(core.actor)):
    admin(a)
    with store.connect() as c:
        return [{**json.loads(row['data']), 'hash':row['hash']} for row in c.execute('SELECT data,hash FROM audit WHERE project_id=? AND tenant=? ORDER BY seq DESC', ('tenant:'+a['tenant'], a['tenant']))]


def links(b):
    return b.get('quantity_links') or [{'takeoff_id': b['takeoff_id'], 'quantity': b['quantity']}]


def validate_links(p, b):
    with localcontext() as ctx:
        ctx.prec = 80
        if sum((Decimal(l['quantity']) for l in links(b)), Decimal(0)) != Decimal(b['quantity']):
            raise HTTPException(409, '清单工程量与来源贡献量不闭合')
        for link in links(b):
            t = core.find(p, 'takeoffs', link['takeoff_id'])
            if t['state'] != 'approved':
                raise HTTPException(409, '来源工程量尚未审定')
            assigned = sum((Decimal(l['quantity']) for line in p['boq'] for l in links(line) if l['takeoff_id'] == t['id']), Decimal(0))
            if assigned > Decimal(t['quantity']):
                raise HTTPException(409, '同一来源工程量被重复计入清单')


@router.get('/takeoff-types')
def takeoff_types(a=Depends(core.actor)):
    return GEOMETRIES


@router.patch('/projects/{pid}/settings')
def project_settings(pid: str, body: dict, a=Depends(core.actor)):
    core.edit(a)
    if not body.get('reason', '').strip():
        raise HTTPException(422, '修改项目适用日期或范围须填写依据')
    with store.transaction() as c:
        p = core.project(c, pid, a)
        if 'pricing_date' in body:
            try:
                date.fromisoformat(body['pricing_date'])
            except (ValueError, TypeError):
                raise HTTPException(422, '计价适用日期须为 YYYY-MM-DD')
            if p.get('pricing_bases') and body['pricing_date'] != p['pricing_bases'][-1]['pricing_date']:
                p['pricing_bases'][-1]['state'] = 'stale'
            p['pricing_date'] = body['pricing_date']
        if 'boq_standard' in body or 'boq_edition' in body:
            if any(not isinstance(body.get(k),str) or not body[k].strip() or len(body[k])>200 for k in ['boq_standard','boq_edition']):
                raise HTTPException(422, '请同时填写有依据的清单标准及版本')
            p.update(boq_standard=body['boq_standard'].strip(),boq_edition=body['boq_edition'].strip())
        if 'scope' in body:
            if not body['scope'] or any(s not in core.REQUIREMENTS for s in body['scope']):
                raise HTTPException(422, '专业范围无效')
            p['scope'] = list(dict.fromkeys(body['scope']))
        if 'require_golden_baseline' in body:
            if type(body['require_golden_baseline']) is not bool: raise HTTPException(422, '黄金底稿门禁须为启停值')
            p['require_golden_baseline'] = body['require_golden_baseline']
        core.invalidate(p, 'quotes')
        store.audit(c, p, a, 'PROJECT_SETTINGS_UPDATED', body)
        store.save(c, p)
    return core.view(p)


@router.post('/projects/{pid}/boq/import-preview')
async def import_preview(pid: str, file: UploadFile = File(...), a=Depends(core.actor)):
    core.edit(a)
    raw = await file.read(10*1024*1024+1)
    if not raw or len(raw) > 10*1024*1024:
        raise HTTPException(413, '清单导入文件须为1字节至10MB')
    try:
        parsed = preview_boq(file.filename or '', raw)
    except DomainError:
        raise
    except Exception:
        raise HTTPException(422, '清单文件结构损坏、编码无效或格式与扩展名不符，请检查原文件')
    sha = hashlib.sha256(raw).hexdigest()
    with store.transaction() as c:
        p = core.project(c, pid, a)
        folder = store.ROOT/'originals'
        folder.mkdir(exist_ok=True)
        if not (folder/sha).exists(): (folder/sha).write_bytes(raw)
        record = {**parsed, 'id': store.uid(), 'name': Path(file.filename).name, 'hash': sha, 'size': len(raw), 'created_at': store.now(), 'committed': False}
        p['imports'].append(record)
        store.audit(c, p, a, 'BOQ_IMPORT_PREVIEW', {'import_id': record['id'], 'hash': sha, 'rows': len(parsed['records']), 'errors': len(parsed['errors'])})
        store.save(c, p)
    return record


@router.post('/projects/{pid}/boq/imports/{iid}/commit')
def import_commit(pid: str, iid: str, body: dict, a=Depends(core.actor)):
    core.edit(a)
    with store.transaction() as c:
        p = core.project(c, pid, a)
        preview = core.find(p, 'imports', iid)
        if preview['committed']:
            return {'duplicate': True, 'import_id': iid}
        if preview['errors'] or (preview['warnings'] and not body.get('acknowledge_duplicates')):
            raise HTTPException(409, '请修正来源文件错误；重复编码警示须显式确认')
        doc = {'id': store.uid(), 'name': preview['name'], 'hash': preview['hash'], 'version': 1, 'size': preview['size'],
               'specialty': '合同计价', 'parse_state': 'needs_review', 'uploaded_at': store.now(), 'parsed': True,
               'message': '已有清单兼容导入；不代表原图算量', 'import_id': iid}
        p['documents'].append(doc)
        core.invalidate(p, 'boq')
        for row in preview['records']:
            e = {'id': store.uid(), 'document_id': doc['id'], 'document_hash': doc['hash'], 'page': None,
                 'sheet': row['sheet'], 'row': row['row'], 'method': 'boq_import', 'bbox': None,
                 'text': f"{row['sheet']} 第{row['row']}行：{row['code']} {row['name']} {row['quantity']} {row['unit']}；{row['features']}"}
            p['evidence'].append(e)
            t = {'id': store.uid(), 'source_kind': 'imported_boq', 'name': row['name'], 'quantity': row['quantity'], 'gross': row['quantity'],
                 'unit': row['unit'], 'formula': '来源清单原始工程量（待专业复核）', 'rule_version': 'boq-import-1.0', 'parameters': {'source_quantity': row['quantity']},
                 'measurement_basis': '原有清单导入，非系统原图算量；'+e['text'], 'evidence': {'source_quantity': e['id']}, 'state': 'draft'}
            p['takeoffs'].append(t)
            p['boq'].append({**{k: row[k] for k in ['code', 'name', 'features', 'node', 'unit', 'quantity']},
                             'id': store.uid(), 'takeoff_id': t['id'], 'origin': 'imported_boq', 'import_id': iid,
                             'reference_unit_price': row.get('reference_unit_price'), 'quotas': [], 'state': 'draft'})
        preview['committed'] = True
        p['status'] = 'BOQ_DRAFT'
        store.audit(c, p, a, 'BOQ_IMPORT_COMMITTED', {'import_id': iid, 'document_id': doc['id'], 'rows': len(preview['records'])})
        store.save(c, p)
    return {'import_id': iid, 'count': len(preview['records']), 'duplicate': False}


@router.post('/projects/{pid}/boq/merge')
def merge_boq(pid: str, body: dict, a=Depends(core.actor)):
    core.edit(a)
    ids = list(dict.fromkeys(body.get('ids', [])))
    if len(ids) < 2: raise HTTPException(422, '请选择至少两项清单')
    with store.transaction() as c, localcontext() as ctx:
        ctx.prec = 80
        p = core.project(c, pid, a)
        items = [core.find(p, 'boq', bid) for bid in ids]
        first = items[0]
        if any(any(b.get(k) != first.get(k) for k in ['code', 'unit', 'features', 'node', 'quotas']) for b in items):
            raise HTTPException(409, '仅可合并编码、特征、单位、工程层级和定额组成一致的清单')
        merged = {**copy.deepcopy(first), 'id': store.uid(), 'name': body.get('name') or first['name'],
                  'quantity': str(sum((Decimal(b['quantity']) for b in items), Decimal(0))),
                  'quantity_links': [l for b in items for l in links(b)], 'state': 'draft', 'merged_from': ids}
        p.setdefault('boq_history', []).extend(copy.deepcopy(items))
        p['boq'] = [b for b in p['boq'] if b['id'] not in ids]+[merged]
        core.invalidate(p, 'quotes')
        store.audit(c, p, a, 'BOQ_MERGED', {'from': ids, 'to': merged['id']})
        store.save(c, p)
    return merged


@router.post('/projects/{pid}/boq/{bid}/split')
def split_boq(pid: str, bid: str, body: dict, a=Depends(core.actor)):
    core.edit(a)
    parts = body.get('parts', [])
    if not isinstance(parts, list) or not 2 <= len(parts) <= 30:
        raise HTTPException(422, '拆分须为2至30项')
    with store.transaction() as c, localcontext() as ctx:
        ctx.prec = 80
        p = core.project(c, pid, a)
        b = core.find(p, 'boq', bid)
        if len(links(b)) != 1:
            raise HTTPException(409, '多来源合并清单请先按来源重编；不能自动猜测各构件分摊')
        values = [number(part.get('quantity'), True) for part in parts]
        if sum(values) != Decimal(b['quantity']):
            raise HTTPException(422, '拆分工程量之和必须等于原清单工程量')
        created = [{**copy.deepcopy(b), 'id': store.uid(), 'name': part.get('name') or b['name'],
                    'quantity': str(q), 'quantity_links': [{'takeoff_id': links(b)[0]['takeoff_id'], 'quantity': str(q)}],
                    'split_from': bid, 'state': 'draft'} for part, q in zip(parts, values)]
        p.setdefault('boq_history', []).append(copy.deepcopy(b))
        p['boq'] = [x for x in p['boq'] if x['id'] != bid]+created
        core.invalidate(p, 'quotes')
        store.audit(c, p, a, 'BOQ_SPLIT', {'from': bid, 'to': [x['id'] for x in created]})
        store.save(c, p)
    return created


@router.post('/projects/{pid}/changes')
def create_change(pid: str, body: dict, a=Depends(core.actor)):
    core.edit(a)
    with store.transaction() as c:
        p = core.project(c, pid, a)
        b = core.find(p, 'boq', body.get('boq_id'))
        revoked = b.get('change_id') in p['applied_changes'] and core.find(p, 'changes', b['change_id'])['state'] == 'rejected' if b.get('change_id') else False
        if b['state'] != 'approved' and not (b['state'] == 'stale' and revoked):
            raise HTTPException(409, '先审定原清单再建立变更基线；已撤销签证可通过新签证修正')
        core.require_evidence(p, body.get('evidence_id'))
        if not body.get('reason', '').strip(): raise HTTPException(422, '变更原因和计量依据必填')
        q = number(body.get('quantity'))
        change = {'id': store.uid(), 'boq_id': b['id'], 'name': b['name'], 'unit': b['unit'], 'base_hash': store.digest(b),
                  'base_snapshot': copy.deepcopy(b), 'before_quantity': b['quantity'], 'quantity': str(q),
                  'delta': str(q-Decimal(b['quantity'])), 'evidence_id': body['evidence_id'], 'reason': body['reason'],
                  'state': 'draft', 'created_at': store.now()}
        p['changes'].append(change)
        core.invalidate(p, 'quotes')
        store.audit(c, p, a, 'CHANGE_REQUESTED', change)
        store.save(c, p)
    return change


@router.post('/projects/{pid}/changes/{cid}/apply')
def apply_change(pid: str, cid: str, a=Depends(core.actor)):
    core.edit(a)
    with store.transaction() as c:
        p = core.project(c, pid, a)
        change = core.find(p, 'changes', cid)
        if cid in p['applied_changes']: return {'duplicate': True}
        b = core.find(p, 'boq', change['boq_id'])
        if change['state'] != 'approved' or change['base_hash'] != store.digest(b):
            raise HTTPException(409, '变更未审定或原清单已变化，请重新建立变更')
        core.require_evidence(p, change['evidence_id'])
        p.setdefault('boq_history', []).append(copy.deepcopy(b))
        t = {'id': store.uid(), 'name': b['name']+'（变更后）', 'source_kind': 'change_order', 'change_id': cid,
             'quantity': change['quantity'], 'gross': change['quantity'], 'unit': b['unit'], 'formula': '原工程量 + 审定变更差量',
             'parameters': {'before': change['before_quantity'], 'delta': change['delta']}, 'rule_version': 'change-1.0',
             'measurement_basis': change['reason'], 'evidence': {'change': change['evidence_id']}, 'state': 'approved'}
        p['takeoffs'].append(t)
        old_takeoff_ids = {l['takeoff_id'] for l in links(b)}
        b.update(quantity=change['quantity'], takeoff_id=t['id'], quantity_links=[{'takeoff_id': t['id'], 'quantity': change['quantity']}],
                 change_id=cid, state='draft')
        active_ids = {l['takeoff_id'] for line in p['boq'] for l in links(line)}
        superseded = [x for x in p['takeoffs'] if x['id'] in old_takeoff_ids and x['id'] not in active_ids]
        p.setdefault('takeoff_history', []).extend(copy.deepcopy(superseded))
        p['takeoffs'] = [x for x in p['takeoffs'] if x['id'] not in {t['id'] for t in superseded}]
        p['applied_changes'].append(cid)
        core.invalidate(p, 'quotes')
        store.audit(c, p, a, 'CHANGE_APPLIED', {'change_id': cid, 'boq_id': b['id'], 'takeoff_id': t['id']})
        store.save(c, p)
    return b


@router.get('/projects/{pid}/quotes/compare')
def compare(pid: str, left: str, right: str, a=Depends(core.actor)):
    with store.connect() as c:
        p = core.project(c, pid, a)
    from .quote_comparison import compare_quotes
    return compare_quotes(p, left, right)


@router.post('/projects/{pid}/scenarios')
def scenario(pid: str, body: dict, a=Depends(core.actor)):
    core.edit(a)
    factors = {k: number(body.get(k, '1')) for k in ['quantity_factor', 'material_factor', 'labor_factor', 'machinery_factor']}
    if any(v > 10 for v in factors.values()): raise HTTPException(422, '内部测算系数范围为0至10')
    with store.transaction() as c, localcontext() as ctx:
        ctx.prec = 80
        p = core.project(c, pid, a)
        q = core.find(p, 'quotes', body.get('quote_id'))
        items = copy.deepcopy(q['snapshot']['boq'])
        for b in items:
            b['quantity'] = str(quantity_number(b['quantity'])*factors['quantity_factor'])
            for quota in b['quotas']:
                for r in quota['resources']:
                    key = {'材料': 'material_factor', '人工': 'labor_factor', '机械': 'machinery_factor'}.get(r.get('type'))
                    if key: r['price'] = str(number(r['price'])*factors[key])
        result = calculate(items, q['snapshot']['rules'][0])
        record = {'id': store.uid(), 'name': str(body.get('name') or '内部敏感性测算'), 'quote_id': q['id'], 'base_version': q['version'],
                  'base_total': q['total'], 'total': result['total'], 'delta': money(Decimal(result['total'])-Decimal(q['total'])),
                  'factors': {k: str(v) for k, v in factors.items()}, 'result': result, 'created_at': store.now(),
                  'notice': '假设性内部测算，不更改报价、价格快照或审批，不可作为审核成果'}
        p['scenarios'].append(record)
        store.audit(c, p, a, 'SCENARIO_CALCULATED', {k: v for k, v in record.items() if k != 'result'})
        store.save(c, p)
    return record


@router.post('/projects/{pid}/catalog')
def save_catalog(pid: str, body: dict, a=Depends(core.actor)):
    core.edit(a)
    with store.transaction() as c:
        p = core.project(c, pid, a)
        b = core.find(p, 'boq', body.get('boq_id'))
        if b['state'] != 'approved' or not b['quotas'] or core.price_blockers({**p, 'boq': [b]}):
            raise HTTPException(409, '只能将来源完整的已审组价保存为授权定额候选')
        item = {'id': store.uid(), 'name': b['name'], 'features': b['features'], 'code': b['code'], 'unit': b['unit'],
                'region': p['region'], 'edition': p['rules'][0]['version'] if p['rules'] else '', 'quotas': copy.deepcopy(b['quotas']),
                'state': 'draft', 'created_at': store.now(), 'boq_id': b['id'], 'evidence_id': b['quotas'][0]['evidence_id']}
        p['catalog'].append(item)
        store.audit(c, p, a, 'CATALOG_CREATED', {'id': item['id'], 'boq_id': b['id']})
        store.save(c, p)
    return item


@router.get('/projects/{pid}/boq/{bid}/quota-candidates')
def candidates(pid: str, bid: str, a=Depends(core.actor)):
    with store.connect() as c: p = core.project(c, pid, a)
    b = core.find(p, 'boq', bid)
    result = []
    for item in p['catalog']:
        if item['state'] != 'approved' or item['region'] != p['region'] or item['unit'] != b['unit']:
            continue
        if item['edition'] != (p['rules'][0]['version'] if p['rules'] else ''):
            continue
        if core.price_blockers({**p, 'boq': [item]}): continue
        score = (100 if item['code'] == b['code'] else 0)+(30 if item['name'] == b['name'] else 0)
        score += sum(1 for ch in set(b['name']+b['features']) if ch in item['name']+item['features'])
        if score:
            result.append({**item, 'score': score, 'match_reason': '同项目已授权且已审候选；地区/单位/规则版本过滤；编码与名称特征文字匹配，需专业确认'})
    return sorted(result, key=lambda x: x['score'], reverse=True)[:5]
