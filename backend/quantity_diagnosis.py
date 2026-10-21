"""Human-authored variance explanations; never changes quantities or tolerances."""
import copy
from fastapi import APIRouter, Depends, HTTPException
from . import main as core, store
from .reconciliation import quantity_hash, context_hash
from .task_execution import original_bytes

router = APIRouter(prefix='/api/v1')
CATEGORIES = {'geometry':'图纸几何', 'coverage':'重复或遗漏', 'method':'施工工法', 'standard':'计量标准', 'precision':'精度与修约'}


def latest(p, rid, key):
    return next((d for d in reversed(p['quantity_diagnoses']) if d['reconciliation_id']==rid and d['target_key']==key), None)


def validate(p, d):
    r = core.find(p, 'reconciliations', d['reconciliation_id'])
    if not r.get('calculation_hash'):
        raise HTTPException(409, '历史底稿未记录完整计算口径，请重新对账后分析')
    if r['calculation_hash'] != context_hash(p) or r['input_hash'] != quantity_hash(p) or d['context_hash'] != context_hash(p):
        raise HTTPException(409, '当前工程量或计算口径已变化，请重新对账并记录诊断')
    for eid in {r['evidence_id'], d['evidence_id']}:
        e = core.require_evidence(p, eid)
        doc = core.find(p, 'documents', e['document_id'])
        if e['document_hash'] != doc['hash']:
            raise HTTPException(409, '诊断原始依据关联失效')
        original_bytes(doc)


@router.post('/projects/{pid}/quantity-diagnoses')
def submit(pid: str, body: dict, a=Depends(core.actor)):
    core.edit(a)
    if not isinstance(body.get('category'), str) or body['category'] not in CATEGORIES:
        raise HTTPException(422, '请选择几何、重复遗漏、工法、计量标准或精度诊断')
    fields = {}
    for key in ['observed', 'reference', 'reason', 'action']:
        v = body.get(key)
        if not isinstance(v, str) or not v.strip() or len(v)>3000:
            raise HTTPException(422, '请完整填写系统侧、人工基准、差异分析和处理建议（各不超过3000字）')
        fields[key] = v.strip()
    if not isinstance(body.get('evidence_id'), str) or not body['evidence_id']:
        raise HTTPException(422, '诊断须关联当前原始依据')
    with store.transaction() as c:
        p = core.project(c, pid, a)
        r = core.find(p, 'reconciliations', body.get('reconciliation_id'))
        targets = {f"row:{v['row']}":{**v, 'kind':'baseline_row'} for v in r['rows'] if not v['passed']}
        targets.update({'missing:'+tid:{'kind':'missing_takeoff', **copy.deepcopy(core.find(p, 'takeoffs', tid))}
                        for tid in r['missing_takeoffs'] if any(t['id']==tid for t in p['takeoffs'])})
        key = body.get('target_key')
        if not isinstance(key, str) or key not in targets:
            raise HTTPException(422, '请选择该底稿中的未通过行或未覆盖计算书')
        old = latest(p, r['id'], key)
        if body.get('previous_hash', '') != (store.digest(old) if old else ''):
            raise HTTPException(409, '诊断已变化，请刷新后重新提交')
        d = {'id':store.uid(), 'name':targets[key]['name']+' · 工程量差异诊断', 'version':old['version']+1 if old else 1,
             'reconciliation_id':r['id'], 'target_key':key, 'target':copy.deepcopy(targets[key]),
             'context_hash':context_hash(p), 'category':body['category'], **fields, 'evidence_id':body['evidence_id'],
             'baseline_evidence_id':r['evidence_id'], 'state':'draft', 'created_at':store.now(), 'created_by':a['username']}
        validate(p, d)
        p['quantity_diagnoses'].append(d)
        core.invalidate(p, 'quotes')
        store.audit(c, p, a, 'QUANTITY_DIAGNOSIS_SUBMITTED', d)
        store.save(c, p)
    return d
