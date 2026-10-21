"""Evidence-backed project dates and explicit professional applicability decisions."""
from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from . import main as core, store
from .task_execution import original_bytes

router = APIRouter(prefix='/api/v1')
MILESTONES = {'tender':'招标', 'contract':'合同签订', 'design':'设计版次', 'construction':'施工', 'settlement':'结算'}


def iso(value):
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(422, '日期须为有效的 YYYY-MM-DD') from None
    return value


def text(body, key, limit=3000):
    value = body.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise HTTPException(422, '请填写日期选择、冲突裁决依据及设计版次说明')
    return value.strip()


def validate(p, basis):
    for eid in {basis['evidence_id'], *[m['evidence_id'] for m in basis['milestones'].values()]}:
        e = core.require_evidence(p, eid)
        doc = core.find(p, 'documents', e['document_id'])
        if e['document_hash'] != doc['hash']:
            raise HTTPException(409, '项目日期依据的原件关联失效')
        original_bytes(doc)


def issues(p):
    history = p.get('pricing_bases', [])
    if not history:
        return []  # Legacy manual-date mode remains explicit and compatible.
    basis = history[-1]
    if basis['state'] != 'approved':
        return ['项目适用日期裁决尚未审定或已失效']
    if p.get('pricing_date') != basis['pricing_date']:
        return ['计价日期与已审裁决不一致，请重新提交复核']
    try:
        validate(p, basis)
    except HTTPException:
        return ['项目日期原始依据已失效，请重新提交日期裁决']
    return []


@router.post('/projects/{pid}/pricing-bases')
def submit(pid: str, body: dict, a=Depends(core.actor)):
    core.edit(a)
    milestones = body.get('milestones')
    if not isinstance(milestones, dict) or not milestones or set(milestones)-MILESTONES.keys():
        raise HTTPException(422, '请至少登记一个有效的工程时间节点')
    records = {}
    for key, value in milestones.items():
        if not isinstance(value, dict) or not isinstance(value.get('evidence_id'), str):
            raise HTTPException(422, '每个时间节点均须提供原始依据')
        records[key] = {'date':iso(value.get('date')), 'evidence_id':value['evidence_id']}
        if key == 'design':
            records[key]['edition'] = text(value, 'edition', 200)
    selected = body.get('selected')
    if selected not in [*records, 'custom']:
        raise HTTPException(422, '请选择已登记的时间节点或合同另行约定日期')
    chosen = iso(body.get('pricing_date'))
    if selected != 'custom' and chosen != records[selected]['date']:
        raise HTTPException(422, '计价日期必须与选中的时间节点一致')
    reason = text(body, 'reason')
    if not isinstance(body.get('evidence_id'), str) or not body['evidence_id']:
        raise HTTPException(422, '请提供日期选择及冲突裁决的原始依据')
    with store.transaction() as c:
        p = core.project(c, pid, a)
        old = p['pricing_bases'][-1] if p['pricing_bases'] else None
        if body.get('previous_hash', '') != (store.digest(old) if old else ''):
            raise HTTPException(409, '日期裁决已变化，请刷新后重新提交')
        basis = {'id':store.uid(), 'name':'项目适用日期裁决', 'version':len(p['pricing_bases'])+1,
                 'milestones':records, 'selected':selected, 'pricing_date':chosen, 'reason':reason,
                 'evidence_id':body.get('evidence_id'), 'state':'draft', 'created_at':store.now(), 'created_by':a['username']}
        validate(p, basis)
        p['pricing_bases'].append(basis)
        core.invalidate(p, 'quotes')
        store.audit(c, p, a, 'PRICING_BASIS_SUBMITTED', basis)
        store.save(c, p)
    return basis
