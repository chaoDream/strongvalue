"""Synthetic already-issued orders; no external sending, payment or goods receipt."""
import copy
from datetime import date,timedelta
from concurrent.futures import ThreadPoolExecutor
import pytest
from backend import store,procurement_analyses as analyses
from tests import test_procurement_requests as requisitions
from tests import test_procurement_analyses as costs
from tests.test_workflow import get,approve,upload
from tests.test_supplier_quotes import mutate


def base(pid):return f'/api/v1/projects/{pid}/procurement-orders'


def setup(clients):
    e,r,pid,parent,raw=requisitions.setup(clients)
    req=requisitions.save(e,pid,requisitions.submission(e,pid,raw));approve(r,pid,'procurement_requests',req);req=get(e,pid)['procurement_requests'][0]
    body=dict(requisition_id=req['id'],requisition_hash=store.digest(req),order_number='SYN-PLACED-001',order_date=date.today().isoformat(),delivery_due=(date.today()+timedelta(days=7)).isoformat(),basis='合成实际下单原件核对，数量价格及费用与已审申请相同；非真实订单',evidence_id=raw['evidence_id'],placed_confirmed=True,revision_of=None,revision_hash=None)
    return e,r,pid,req,body


def preview(e,pid,raw,expected=200):
    q=e.post(base(pid)+'/preview',json={'input':raw});assert q.status_code==expected,q.text;return q.json()


def submission(e,pid,raw):return dict(request_id=store.uid(),input=raw,preview_hash=preview(e,pid,raw)['preview_hash'])


def save(e,pid,b,expected=200):
    q=e.post(base(pid),json=b);assert q.status_code==expected,q.text;return q.json()


def revision(row,**changes):return {**row['input'],'revision_of':row['id'],'revision_hash':store.digest(row),'basis':'合成有据修正登记，原下单事实不撤销',**changes}


def test_readonly_preview_register_review_request_lock_and_historical_exports(clients):
    e,r,pid,req,raw=setup(clients);before=get(e,pid);audit=e.get(f'/api/v1/projects/{pid}/audit').json();q=preview(e,pid,raw)
    assert q['revision']==1 and q['result']['requisition']['result']['scenario']['total']=='1396.15'
    assert get(e,pid)==before and e.get(f'/api/v1/projects/{pid}/audit').json()==audit
    b=dict(request_id=store.uid(),input=raw,preview_hash=q['preview_hash']);row=save(e,pid,b);assert save(e,pid,b)==row
    view=e.get(base(pid)).json()['items'][0];assert view['latest'] and view['current'] and not view['review_valid']
    requestview=e.get(requisitions.base(pid)).json()['items'][0];assert requestview['phase']=='order_registered' and requestview['order']['id']==row['id']
    requisitions.cancel(e,pid,req,raw['evidence_id'],409);approve(r,pid,'procurement_requests',req,decision='reject',expected=409)
    frozen=e.get(base(pid)+'/'+row['id']+'/export');approve(r,pid,'procurement_orders',row);view=e.get(base(pid)).json()['items'][0];assert view['review_valid']
    after=get(e,pid)
    for k in before:
        if k.startswith('stock_') or k in ('components','takeoffs','boq','quotes','rules','report_approval','supplier_quotes','procurement_analyses','procurement_requests','procurement_request_events'):assert after[k]==before[k],k
    artifact=next(a for a in e.get(f'/api/v1/projects/{pid}/artifacts').json() if a['hash']==frozen.headers['x-artifact-hash']);assert e.get(f'/api/v1/projects/{pid}/artifacts/'+artifact['id']+'/file').content==frozen.content


@pytest.mark.parametrize('fault',[
 lambda v:v.update(requisition_id='foreign'),lambda v:v.update(requisition_hash='0'*64),lambda v:v.update(order_number=' '),lambda v:v.update(order_number='x'*201),lambda v:v.update(order_date='2999-01-01'),lambda v:v.update(order_date='2026-1-1'),lambda v:v.update(order_date='2020-01-01'),lambda v:v.update(order_date=None),lambda v:v.update(delivery_due='2020-01-01'),lambda v:v.update(delivery_due='bad'),lambda v:v.update(basis=''),lambda v:v.update(evidence_id='foreign'),lambda v:v.update(placed_confirmed=False),lambda v:v.update(placed_confirmed=1),lambda v:v.update(quantity='999'),lambda v:v.update(price='1'),lambda v:v.update(supplier='other'),lambda v:v.update(revision_of='foreign'),lambda v:v.update(revision_hash='0'*64)
])
def test_invalid_registration_has_no_request_lock_or_partial_audit(clients,fault):
    e,r,pid,req,raw=setup(clients);fault(raw);before=get(e,pid);audit=e.get(f'/api/v1/projects/{pid}/audit').json();q=e.post(base(pid)+'/preview',json={'input':raw});assert q.status_code in (404,409,422),q.text
    assert get(e,pid)==before and e.get(f'/api/v1/projects/{pid}/audit').json()==audit
    assert e.get(requisitions.base(pid)).json()['items'][0]['order'] is None


@pytest.mark.parametrize('fault',['cancelled','unreviewed','expired','source_bytes'])
def test_first_registration_requires_current_approved_request(clients,fault,monkeypatch):
    e,r,pid,req,raw=setup(clients);path=saved=None
    if fault=='cancelled':requisitions.cancel(e,pid,req,raw['evidence_id'])
    elif fault=='unreviewed':approve(r,pid,'procurement_requests',req,decision='request_more_info');raw['requisition_hash']=store.digest(get(e,pid)['procurement_requests'][0])
    elif fault=='expired':
        from backend import procurement_requests as module
        monkeypatch.setattr(module,'today',lambda:date.today()+timedelta(days=400))
    else:
        doc=next(d for d in get(e,pid)['documents'] if d['name']=='synthetic-cost-scope.txt');path=store.ROOT/'originals'/doc['hash'];saved=path.read_bytes();path.write_bytes(b'changed request basis')
    try:preview(e,pid,raw,409)
    finally:
        if path:path.write_bytes(saved)


def test_revision_freezes_history_only_latest_reviewable_and_retries_cannot_fork(clients):
    e,r,pid,req,raw=setup(clients);first=save(e,pid,submission(e,pid,raw));approve(r,pid,'procurement_orders',first);first=get(e,pid)['procurement_orders'][0]
    frozen=e.get(base(pid)+'/'+first['id']+'/export');r2raw=revision(first,order_number='SYN-PLACED-CORRECTED');b=submission(e,pid,r2raw);second=save(e,pid,b);assert second['revision']==2 and second['family_id']==first['id'] and second['previous_hash']==store.digest(first)
    assert save(e,pid,b)==second and get(e,pid)['procurement_orders'][0]==first
    approve(r,pid,'procurement_orders',first,decision='reject',expected=409);preview(e,pid,revision(first),409)
    view=e.get(base(pid)).json()['items'];assert not view[1]['latest'] and not view[1]['review_valid'] and not view[0]['review_valid']
    targets=r.get(f'/api/v1/projects/{pid}/review-batch-targets',params={'collection':'procurement_orders'}).json();assert [x['object']['id'] for x in targets['targets']]==[second['id']]
    approve(r,pid,'procurement_orders',second);assert e.get(base(pid)).json()['items'][0]['review_valid']
    reqview=e.get(requisitions.base(pid)).json()['items'][0];assert reqview['order']['revision']==2 and reqview['order']['order_number']=='SYN-PLACED-CORRECTED'
    artifact=next(a for a in e.get(f'/api/v1/projects/{pid}/artifacts').json() if a['hash']==frozen.headers['x-artifact-hash']);assert e.get(f'/api/v1/projects/{pid}/artifacts/'+artifact['id']+'/file').content==frozen.content


@pytest.mark.parametrize('upstream',['analysis_rejected','quote_rejected','old_proof_lost','quote_expired'])
def test_recorded_fact_survives_planning_invalidity_can_review_actual_order_and_revise(clients,upstream,monkeypatch):
    e,r,pid,req,raw=setup(clients)
    # Own issued-order proof is separate from upstream plan originals.
    doc=upload(e,pid,'合成已下单原件，与原申请一致，只软件验证','issued-order-only.txt','合同计价');eid=next(x['id'] for x in get(e,pid)['evidence'] if x['document_id']==doc['document']['id']);raw['evidence_id']=eid
    # Upload does not change the current unpriced resource hashes.
    row=save(e,pid,submission(e,pid,raw));path=saved=None
    if upstream=='analysis_rejected':approve(r,pid,'procurement_analyses',get(e,pid)['procurement_analyses'][0],decision='reject')
    elif upstream=='quote_rejected':approve(r,pid,'supplier_quotes',get(e,pid)['supplier_quotes'][0],decision='reject')
    elif upstream=='quote_expired':
        from backend import procurement_requests as module
        monkeypatch.setattr(module,'today',lambda:date.today()+timedelta(days=400))
    else:
        d=next(d for d in get(e,pid)['documents'] if d['name']=='synthetic-cost-scope.txt');path=store.ROOT/'originals'/d['hash'];saved=path.read_bytes();path.unlink()
    try:
        view=e.get(base(pid)).json()['items'][0];assert view['current'] and view['planning_issue'] and not view['review_valid']
        target=r.get(f'/api/v1/projects/{pid}/review-target',params={'collection':'procurement_orders','object_id':row['id']}).json();assert target['context']['planning_issue']
        approve(r,pid,'procurement_orders',row);row=get(e,pid)['procurement_orders'][0];assert e.get(base(pid)).json()['items'][0]['review_valid']
        later=save(e,pid,submission(e,pid,revision(row,delivery_due=(date.today()+timedelta(days=14)).isoformat())));assert later['revision']==2
        requisitions.cancel(e,pid,req,eid,409)
    finally:
        if path:path.write_bytes(saved)


def test_lost_order_original_blocks_approval_not_history_and_fresh_revision_can_repair(clients):
    e,r,pid,req,raw=setup(clients);row=save(e,pid,submission(e,pid,raw));doc=next(d for d in get(e,pid)['documents'] if d['name']=='synthetic-cost-scope.txt');path=store.ROOT/'originals'/doc['hash'];saved=path.read_bytes();path.write_bytes(b'changed own order evidence')
    try:
        approve(r,pid,'procurement_orders',row,expected=409);view=e.get(base(pid)).json()['items'][0];assert not view['current'] and view['record']==row
        q=e.get(base(pid)+'/'+row['id']+'/export');assert q.status_code==200 and not q.json()['review_valid']
        new=upload(e,pid,'合成新实际下单原件用于修正登记，不恢复旧文件','fresh-issued-order.txt','合同计价');eid=next(x['id'] for x in get(e,pid)['evidence'] if x['document_id']==new['document']['id'])
        second=save(e,pid,submission(e,pid,revision(row,evidence_id=eid)));approve(r,pid,'procurement_orders',second);assert e.get(base(pid)).json()['items'][0]['review_valid']
    finally:path.write_bytes(saved)


def test_roles_actor_change_and_other_project_are_enforced(clients):
    e,r,pid,req,raw=setup(clients);b=submission(e,pid,raw)
    assert r.post(base(pid),json=b).status_code==403 and clients['other'].get(base(pid)).status_code==404
    row=save(e,pid,b);assert clients['other'].get(base(pid)+'/'+row['id']+'/export').status_code==404
    with store.transaction() as c:c.execute("UPDATE users SET role='reviewer' WHERE username='estimator'")
    approve(e,pid,'procurement_orders',row,expected=403)


def test_concurrent_register_and_cancel_never_create_contradictory_facts(clients):
    e,r,pid,req,raw=setup(clients);b=submission(e,pid,raw);cancel=requisitions.cancel_body(req,raw['evidence_id'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(lambda:e.post(base(pid),json=b));c=pool.submit(lambda:e.post(requisitions.base(pid)+'/'+req['id']+'/cancel',json=cancel));codes=[a.result().status_code,c.result().status_code]
    assert sorted(codes)==[200,409]
    p=get(e,pid);assert bool(p['procurement_orders'])!=bool(p['procurement_request_events'])


def test_concurrent_revision_stale_preview_and_exact_idempotency(clients):
    e,r,pid,req,raw=setup(clients);row=save(e,pid,submission(e,pid,raw));a=submission(e,pid,revision(row));b={**copy.deepcopy(a),'request_id':store.uid()}
    with ThreadPoolExecutor(max_workers=2) as pool:codes=list(pool.map(lambda x:e.post(base(pid),json=x).status_code,[a,b]))
    assert sorted(codes)==[200,409] and len(get(e,pid)['procurement_orders'])==2
    winner=a if codes[0]==200 else b;assert save(e,pid,winner)['revision']==2
    bad=copy.deepcopy(winner);bad['input']['basis']='changed';save(e,pid,bad,409)


def test_duplicate_current_supplier_number_across_requests_is_blocked(clients):
    e,r,pid,req,raw=setup(clients);first=save(e,pid,submission(e,pid,raw))
    original=get(e,pid)['procurement_analyses'][0];other=costs.save(e,pid,costs.submission(e,pid,original['input']));approve(r,pid,'procurement_analyses',other);other=get(e,pid)['procurement_analyses'][-1]
    v={**req['input'],'analysis_id':other['id'],'analysis_hash':store.digest(other)};newreq=requisitions.save(e,pid,requisitions.submission(e,pid,v));approve(r,pid,'procurement_requests',newreq);newreq=get(e,pid)['procurement_requests'][-1]
    v={**raw,'requisition_id':newreq['id'],'requisition_hash':store.digest(newreq),'order_number':'syn-placed-001'};preview(e,pid,v,409)
    save(e,pid,submission(e,pid,revision(first,order_number='SYN-PLACED-002')));assert save(e,pid,submission(e,pid,v))['revision']==1


@pytest.mark.parametrize('fault',['record','chain','request'])
def test_corrupt_links_block_new_reads_and_old_export_bytes_survive(clients,fault):
    e,r,pid,req,raw=setup(clients);row=save(e,pid,submission(e,pid,raw));f=e.get(base(pid)+'/'+row['id']+'/export');artifact=next(a for a in e.get(f'/api/v1/projects/{pid}/artifacts').json() if a['hash']==f.headers['x-artifact-hash'])
    def corrupt(p):
        row=p['procurement_orders'][0]
        if fault=='record':row['input']['order_number']='changed'
        elif fault=='chain':row['revision']=2;p['procurement_orders'][0]=analyses.seal(row)
        else:p['procurement_requests'][0]['state']='rejected'
    mutate(pid,corrupt);assert e.get(base(pid)).status_code==409 and e.get(base(pid)+'/'+row['id']+'/export').status_code==409
    assert e.get(f'/api/v1/projects/{pid}/artifacts/'+artifact['id']+'/file').content==f.content


def test_old_project_normalizes_without_inventing_external_orders(clients):
    e,r,pid,req,raw=setup(clients);mutate(pid,lambda p:p.pop('procurement_orders',None));assert e.get(base(pid)).json()['items']==[]


@pytest.mark.parametrize('decision',['reject','request_more_info'])
def test_negative_latest_review_keeps_order_fact_and_request_locked(clients,decision):
    e,r,pid,req,raw=setup(clients);row=save(e,pid,submission(e,pid,raw));approve(r,pid,'procurement_orders',row,decision=decision)
    row=get(e,pid)['procurement_orders'][0];assert not e.get(base(pid)).json()['items'][0]['review_valid']
    requisitions.cancel(e,pid,req,raw['evidence_id'],409)
    approve(r,pid,'procurement_requests',req,decision='request_more_info',expected=409)
    second=save(e,pid,submission(e,pid,revision(row)));assert second['revision']==2
    assert get(e,pid)['procurement_orders'][0]==row


def test_new_revision_hides_unreviewed_old_version_from_batch_candidates(clients):
    e,r,pid,req,raw=setup(clients);first=save(e,pid,submission(e,pid,raw));second=save(e,pid,submission(e,pid,revision(first)))
    q=r.get(f'/api/v1/projects/{pid}/review-batch-targets',params={'collection':'procurement_orders'}).json();assert [x['object']['id'] for x in q['targets']]==[second['id']]


def test_batch_own_proof_failure_rolls_back_prior_order_review_and_audit(clients):
    e,r,pid,req,raw=setup(clients);first=save(e,pid,submission(e,pid,raw))
    parent=get(e,pid)['procurement_analyses'][0];other=costs.save(e,pid,costs.submission(e,pid,parent['input']));approve(r,pid,'procurement_analyses',other);other=get(e,pid)['procurement_analyses'][-1]
    v={**req['input'],'analysis_id':other['id'],'analysis_hash':store.digest(other)};secondreq=requisitions.save(e,pid,requisitions.submission(e,pid,v));approve(r,pid,'procurement_requests',secondreq);secondreq=get(e,pid)['procurement_requests'][-1]
    doc=upload(e,pid,'合成另一份已下单原件，仅用于整批回滚验证','second-issued-order.txt','合同计价');eid=next(x['id'] for x in get(e,pid)['evidence'] if x['document_id']==doc['document']['id'])
    second=save(e,pid,submission(e,pid,{**raw,'requisition_id':secondreq['id'],'requisition_hash':store.digest(secondreq),'order_number':'SYN-SECOND-002','evidence_id':eid}))
    d=next(x for x in get(e,pid)['documents'] if x['id']==doc['document']['id']);path=store.ROOT/'originals'/d['hash'];saved=path.read_bytes();path.write_bytes(b'changed second order original')
    try:
        before=get(e,pid);url=f'/api/v1/projects/{pid}';audit=e.get(url+'/audit').json()
        body=dict(collection='procurement_orders',decision='approve',reason='合成逐份订单原件核查',targets=[dict(object_id=x['id'],object_hash=store.digest(x)) for x in (first,second)])
        q=r.post(url+'/reviews/batch',json=body);assert q.status_code==409,q.text
        assert get(e,pid)==before and e.get(url+'/audit').json()==audit
    finally:path.write_bytes(saved)
