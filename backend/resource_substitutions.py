"""Evidence-backed one-to-many material substitution, reviewed before atomic application."""
import copy
import re
from decimal import Decimal, localcontext
from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from . import main as core, store
from .engine import calculate, validate_pricing_inputs, resource_pricing_state, money, DomainError
from .workflows import resources, validate_price_metadata
from .task_execution import original_bytes

router = APIRouter(prefix='/api/v1')
RESOURCE_FIELDS = {'name','type','unit','consumption','price','resource_code','specification','fee_allocation',
                   'pricing_status','source_type','source','license','period','region','tax_mode','valid_from','valid_until','evidence_id'}
IDENTITY_FIELDS = ['name','type','unit','consumption','resource_code','specification','fee_allocation']


def text(body, key, limit=2000):
    value = body.get(key)
    if not isinstance(value,str) or not value.strip() or len(value)>limit:
        raise HTTPException(422, '材料替换须完整填写名称、原因、来源、授权及材料规格和消耗依据')
    return value.strip()


def evidence(p, eid):
    ev=core.require_evidence(p,eid);doc=core.find(p,'documents',ev['document_id'])
    if ev['document_hash']!=doc['hash']:
        raise HTTPException(409,'材料替换依据的原件关联已失效')
    original_bytes(doc)


def context(p):
    return store.digest({'boq':p['boq'],'rules':p['rules'],'date':core.effective_date(p).isoformat(),'pricing_bases':p['pricing_bases'],'region':p['region'],'engineering_structures':p['engineering_structures']})


def replacement_resources(p, values):
    if not isinstance(values,list) or not 1<=len(values)<=20 or any(not isinstance(v,dict) for v in values):
        raise HTTPException(422,'须填写1至20项替换材料及各自原始消耗，不能仅填写比例')
    for r in values:
        if set(r)-RESOURCE_FIELDS or r.get('type')!='材料':
            raise HTTPException(422,'仅支持材料替换，不能夹带审批、企业库或历史调价标记')
        for key in ['name','unit','resource_code','specification','fee_allocation']:
            text(r,key)
        resource_pricing_state(r)
        evidence(p,r.get('evidence_id'))
        if r.get('pricing_status')!='unpriced':
            validate_price_metadata(p,r)
    return copy.deepcopy(values)


def changed_items(p, row, after):
    items=copy.deepcopy(p['boq']);b=next(b for b in items if b['id']==row['boq_id'])
    q=b['quotas'][row['quota_index']]
    q['resources'][row['resource_index']:row['resource_index']+1]=copy.deepcopy(after)
    return items


def impact(p, row, after):
    from .pricing_basis import issues
    from .scoped_rules import issues as scoped_issues
    from .engineering_structure import latest, issues as structure_issues
    rules=p['rules'][0] if p['rules'] else None
    proposed={**p,'boq':changed_items(p,row,after)}
    unavailable={'available':False,'notice':'当前材料缺价、规则或组价尚未完整审定；仅记录替换，不推算差额。'}
    if not rules or rules['state']!='approved' or any(b['state']!='approved' for b in p['boq']) or core.price_blockers(p) or core.price_blockers(proposed) or issues(p) or scoped_issues(p) or structure_issues(p):
        return unavailable
    if rules['region']!=p['region'] or not date.fromisoformat(rules['valid_from'])<=core.effective_date(p)<=date.fromisoformat(rules['valid_until']):
        return unavailable
    try:
        with localcontext() as ctx:
            ctx.prec=80
            before=calculate(p['boq'],rules,latest(p));updated=calculate(proposed['boq'],rules,latest(p))
            return {'available':True,'before_total':before['total'],'after_total':updated['total'],
                    'delta':money(Decimal(updated['total'])-Decimal(before['total'])),
                    'rules_snapshot':copy.deepcopy(rules),'pricing_date':core.effective_date(p).isoformat(),
                    'fees_before':before['fees'],'fees_after':updated['fees'],'rounding':updated['rounding'],
                    'notice':'依据当前全部清单、费用和修约重算的条件性试算；不验证配合比性能，应用后仍须复核清单并重新报价。'}
    except DomainError:
        return unavailable


def validate(p, record):
    if record['id'] in p['applied_resource_substitutions']:
        raise HTTPException(409,'已应用替换不能重新审批；请建立新替换单')
    b=core.find(p,'boq',record['boq_id'])
    if store.digest(b)!=record['boq_hash'] or context(p)!=record['context_hash']:
        raise HTTPException(409,'清单、计价规则或适用日期已变化，请重新建立替换单')
    row=next((r for r in resources(p) if r['key']==record['key']),None)
    if not row or row['resource_hash']!=record['resource_hash']:
        raise HTTPException(409,'待替换材料已变化，请重新建立替换单')
    evidence(p,record['evidence_id']);evidence(p,row['resource'].get('evidence_id'))
    q=b['quotas'][row['quota_index']]
    evidence(p,q.get('evidence_id'))
    if q.get('conversion'): evidence(p,q['conversion']['evidence_id'])
    after=replacement_resources(p,record['after'])
    validate_pricing_inputs(changed_items(p,row,after))
    return row


def reference_errors(p):
    errors=[];seen=set()
    for row in resources(p):
        r=row['resource'];sid=r.get('substitution_id')
        if not sid: continue
        if not isinstance(sid,str):
            errors.append('材料替换引用无效');continue
        record=next((v for v in p.get('resource_substitutions',[]) if v['id']==sid),None)
        entry=r.get('substitution_entry');key=(sid,entry) if type(entry) is int else None
        # Catalog candidates retain the server-recorded original BOQ provenance.
        source_boq=next((c['boq_id'] for c in p.get('catalog',[]) if c['id']==row['boq_id']),row['boq_id'])
        if not record or record['state']!='approved' or sid not in p.get('applied_resource_substitutions',[]):
            errors.append(r.get('name','材料')+'材料替换审批已失效，须重新建立并审定替换单')
        elif key is None or key in seen or not 0<=entry<len(record['after']) or record['boq_id']!=source_boq or any(r.get(k)!=record['after'][entry].get(k) for k in IDENTITY_FIELDS):
            errors.append(r.get('name','材料')+'替换材料与已应用记录不一致，请重新建立替换单')
        if key is not None:seen.add(key)
    return list(dict.fromkeys(errors))


@router.get('/projects/{pid}/material-substitution-targets')
def targets(pid: str, a=Depends(core.actor)):
    with store.connect() as c:
        p=core.project(c,pid,a)
        return [row for row in resources(p) if row['resource'].get('type')=='材料' and core.find(p,'boq',row['boq_id'])['state'] in ('approved','stale')]


@router.post('/projects/{pid}/material-substitutions')
def propose(pid: str, body: dict, a=Depends(core.actor)):
    core.edit(a)
    rid=body.get('request_id')
    if not isinstance(rid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}',rid):
        raise HTTPException(422,'缺少有效提交标识，请刷新后重试')
    name,reason,source,license_=[text(body,k,150 if k=='name' else 2000) for k in ['name','reason','source','license']]
    fingerprint=store.digest({k:v for k,v in body.items() if k!='request_id'})
    with store.transaction() as c:
        p=core.project(c,pid,a)
        old=next((r for r in p['resource_substitutions'] if r['request_id']==rid),None)
        if old:
            if old['request_hash']!=fingerprint:raise HTTPException(409,'相同提交标识的材料替换内容不一致')
            return {'substitution':old,'duplicate':True}
        row=next((r for r in resources(p) if r['key']==body.get('key')),None)
        if not row or row['boq_hash']!=body.get('boq_hash') or row['resource_hash']!=body.get('resource_hash'):
            raise HTTPException(409,'待替换材料已变化，请刷新后核对')
        if row['resource'].get('type')!='材料' or core.find(p,'boq',row['boq_id'])['state'] not in ('approved','stale'):
            raise HTTPException(409,'请选择已审或已失效清单中的材料；本流程不用于替换人工、机械或设备')
        after=replacement_resources(p,body.get('after'))
        if len(after)==1 and all(after[0].get(k)==row['resource'].get(k) for k in IDENTITY_FIELDS):
            raise HTTPException(422,'材料与消耗未变化，请使用补价或资源调价流程')
        record={**{k:row[k] for k in ['key','boq_id','boq_name','boq_hash','quota_index','resource_index','quota_code','resource_hash']},
                'id':store.uid(),'name':name,'reason':reason,'source':source,'license':license_,
                'evidence_id':body.get('evidence_id'),'before':copy.deepcopy(row['resource']),'after':after,
                'context_hash':context(p),'state':'draft','created_at':store.now(),'author':a['username'],
                'request_id':rid,'request_hash':fingerprint}
        validate(p,record)
        record['impact']=impact(p,row,after)
        p['resource_substitutions'].append(record)
        store.audit(c,p,a,'MATERIAL_SUBSTITUTION_PROPOSED',record);store.save(c,p)
    return {'substitution':record,'duplicate':False}


@router.post('/projects/{pid}/material-substitutions/{sid}/apply')
def apply(pid: str, sid: str, a=Depends(core.actor)):
    core.edit(a)
    with store.transaction() as c:
        p=core.project(c,pid,a);record=core.find(p,'resource_substitutions',sid)
        if record['state']!='approved':raise HTTPException(409,'材料替换尚未复核通过或审批已撤回')
        if sid in p['applied_resource_substitutions']:return {'id':sid,'duplicate':True}
        row=validate(p,record);b=core.find(p,'boq',row['boq_id'])
        p.setdefault('boq_history',[]).append(copy.deepcopy(b))
        after=[{**copy.deepcopy(r),'substitution_id':sid,'substitution_entry':i} for i,r in enumerate(record['after'])]
        b['quotas'][row['quota_index']]['resources'][row['resource_index']:row['resource_index']+1]=after
        p['applied_resource_substitutions'].append(sid);b['state']='draft'
        core.invalidate(p,'quotes')
        store.audit(c,p,a,'MATERIAL_SUBSTITUTION_APPLIED',{'id':sid,'boq_id':b['id'],'before':record['before'],'after':after});store.save(c,p)
    return {'id':sid,'duplicate':False,'boq_id':b['id']}
