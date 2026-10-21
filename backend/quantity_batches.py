"""Audited quantity changes: reviewed individually, applied atomically as a batch."""
import copy
import re
from datetime import date
from decimal import Decimal, localcontext
from fastapi import APIRouter, Depends, HTTPException
from . import main as core, store
from .extensions import build_change, apply_change_data
from .engine import calculate, money, DomainError
from .task_execution import original_bytes

router = APIRouter(prefix='/api/v1')


def context_hash(p):
    return store.digest({'boq':p['boq'], 'rules':p['rules'], 'date':core.effective_date(p).isoformat(),'engineering_structures':p['engineering_structures']})


def text(body, key, limit=3000):
    v = body.get(key)
    if not isinstance(v, str) or not v.strip() or len(v)>limit:
        raise HTTPException(422, '请填写调量单名称、批量原因和各项计量依据')
    return v.strip()


def evidence(p, eid):
    e = core.require_evidence(p, eid)
    doc = core.find(p, 'documents', e['document_id'])
    if e['document_hash'] != doc['hash']:
        raise HTTPException(409, '调量依据的原件关联已失效')
    original_bytes(doc)


def impact(p, changes):
    from .pricing_basis import issues
    from .scoped_rules import issues as scoped_issues
    from .engineering_structure import latest, issues as structure_issues
    rule = p['rules'][0] if p['rules'] else None
    if not rule or rule['state']!='approved' or any(b['state']!='approved' for b in p['boq']) or core.price_blockers(p) or issues(p) or scoped_issues(p) or structure_issues(p):
        return {'available':False, 'notice':'当前没有完整审定的组价与规则，仅记录数量变化，不推算金额'}
    if rule['region']!=p['region'] or not date.fromisoformat(rule['valid_from'])<=core.effective_date(p)<=date.fromisoformat(rule['valid_until']):
        return {'available':False, 'notice':'规则地区或有效期不适用，未推算金额'}
    try:
        before = calculate(p['boq'], rule, latest(p))
        items = copy.deepcopy(p['boq'])
        for change in changes:
            next(b for b in items if b['id']==change['boq_id'])['quantity']=change['quantity']
        after = calculate(items, rule, latest(p))
        return {'available':True, 'before_total':before['total'], 'after_total':after['total'],
                'delta':money(Decimal(after['total'])-Decimal(before['total'])),
                'rules_snapshot':copy.deepcopy(rule), 'pricing_date':core.effective_date(p).isoformat(),
                'before_subtotal':before['subtotal'], 'after_subtotal':after['subtotal'],
                'fees_before':before['fees'], 'fees_after':after['fees'], 'rounding':after['rounding'],
                'notice':'按当前全部清单、原组价和同一费用规则重算的条件性试算；应用后仍须复核清单并重生成正式报价'}
    except DomainError:
        return {'available':False, 'notice':'当前计价输入不能形成有效试算，仅记录数量变化'}


@router.post('/projects/{pid}/quantity-change-batches')
def create(pid: str, body: dict, a=Depends(core.actor)):
    core.edit(a)
    name, reason = text(body,'name',150), text(body,'reason',1000)
    request_id = body.get('request_id')
    entries = body.get('entries')
    if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', request_id):
        raise HTTPException(422, '缺少有效的提交标识，请刷新后重新填写')
    if not isinstance(entries,list) or not 1<=len(entries)<=200 or any(not isinstance(e,dict) or not isinstance(e.get('boq_id'),str) for e in entries):
        raise HTTPException(422, '请选择1至200个不同清单项')
    if len({e['boq_id'] for e in entries})!=len(entries):
        raise HTTPException(422, '同一清单项不能重复调量')
    fingerprint = store.digest({'name':name,'reason':reason,'entries':entries})
    with store.transaction() as c, localcontext() as ctx:
        ctx.prec=80
        p = core.project(c,pid,a)
        previous = next((b for b in p['quantity_batches'] if b['request_id']==request_id),None)
        if previous:
            if previous['request_hash']!=fingerprint:
                raise HTTPException(409, '同一提交标识的内容不一致，请重新建立调量单')
            return {'batch':previous,'duplicate':True}
        changes=[];bid=store.uid()
        for entry in entries:
            b = core.find(p,'boq',entry['boq_id'])
            if store.digest(b)!=entry.get('object_hash'):
                raise HTTPException(409, '部分清单已变化，本批次未提交，请刷新核对')
            basis = text(entry,'basis',1500)
            change = build_change(p,{'boq_id':b['id'],'quantity':entry.get('quantity'),
                                     'reason':reason+'；逐项计量依据：'+basis,'evidence_id':entry.get('evidence_id')})
            if Decimal(change['delta'])==0:
                raise HTTPException(422, '所选清单数量未变化，请取消选择：'+b['name'])
            evidence(p,change['evidence_id'])
            change['batch_id']=bid
            changes.append(change)
        batch = {'id':bid,'name':name,'reason':reason,'request_id':request_id,'request_hash':fingerprint,
                 'input_hash':context_hash(p),'change_ids':[v['id'] for v in changes], 'impact':impact(p,changes),
                 'state':'draft','created_by':a['username'],'created_at':store.now()}
        p['quantity_batches'].append(batch);p['changes'].extend(changes)
        core.invalidate(p,'quotes')
        store.audit(c,p,a,'QUANTITY_BATCH_CREATED',{'batch':batch,'changes':changes})
        store.save(c,p)
    return {'batch':batch,'duplicate':False}


@router.post('/projects/{pid}/quantity-change-batches/{bid}/apply')
def apply(pid: str, bid: str, a=Depends(core.actor)):
    core.edit(a)
    with store.transaction() as c:
        p = core.project(c,pid,a);batch = core.find(p,'quantity_batches',bid)
        if batch['state']=='applied':return {'duplicate':True,'batch':batch}
        if batch['state']=='cancelled':raise HTTPException(409,'调量单已取消')
        if batch['input_hash']!=context_hash(p):
            raise HTTPException(409,'清单、计价规则或适用日期已变化，请取消旧单并重新建立调量单')
        changes = [core.find(p,'changes',cid) for cid in batch['change_ids']]
        for change in changes:
            if change['state']!='approved' or change['base_hash']!=store.digest(core.find(p,'boq',change['boq_id'])):
                raise HTTPException(409,'全部子项须先独立复核通过且原清单未变，整批未应用')
            evidence(p,change['evidence_id'])
        for change in changes:
            b=apply_change_data(p,change)
            store.audit(c,p,a,'CHANGE_APPLIED',{'batch_id':bid,'change_id':change['id'],'boq_id':b['id'],'takeoff_id':b['takeoff_id']})
        batch.update(state='applied',applied_at=store.now(),applied_by=a['username'])
        store.audit(c,p,a,'QUANTITY_BATCH_APPLIED',{'batch_id':bid,'change_ids':batch['change_ids']})
        store.save(c,p)
    return {'duplicate':False,'batch':batch}


@router.post('/projects/{pid}/quantity-change-batches/{bid}/cancel')
def cancel(pid: str, bid: str, body: dict, a=Depends(core.actor)):
    core.edit(a);reason=text(body,'reason')
    with store.transaction() as c:
        p=core.project(c,pid,a);batch=core.find(p,'quantity_batches',bid)
        if body.get('object_hash')!=store.digest(batch):raise HTTPException(409,'调量单已变化，请刷新后重新操作')
        if batch['state']!='draft':raise HTTPException(409,'只能取消尚未应用的调量单')
        for cid in batch['change_ids']:
            core.find(p,'changes',cid)['state']='cancelled'
        batch.update(state='cancelled',cancel_reason=reason,cancelled_at=store.now(),cancelled_by=a['username'])
        core.invalidate(p,'quotes')
        store.audit(c,p,a,'QUANTITY_BATCH_CANCELLED',{'batch_id':bid,'reason':reason})
        store.save(c,p)
    return batch


@router.get('/projects/{pid}/quantity-change-targets')
def targets(pid: str, a=Depends(core.actor)):
    with store.connect() as c:
        p=core.project(c,pid,a)
        items=[]
        for b in p['boq']:
            revoked = b.get('change_id') in p['applied_changes'] and core.find(p,'changes',b['change_id'])['state']=='rejected' if b.get('change_id') else False
            if b['state']=='approved' or b['state']=='stale' and revoked:
                items.append({**{k:b[k] for k in ['id','name','quantity','unit']}, 'object_hash':store.digest(b)})
        return items


@router.get('/projects/{pid}/quantity-change-batches/{bid}')
def read(pid: str, bid: str, a=Depends(core.actor)):
    with store.connect() as c:
        batch=core.find(core.project(c,pid,a),'quantity_batches',bid)
        return {'batch':batch,'object_hash':store.digest(batch)}
