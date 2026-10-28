"""Synthetic physical handovers; no installed consumption or real warehouse acceptance."""
import copy
from decimal import Decimal,localcontext
from concurrent.futures import ThreadPoolExecutor
import pytest
from backend import store,procurement_receipts as receipt_module
from tests import test_procurement_receipts as receipts,test_procurement_orders as orders
from tests.test_workflow import get,approve,upload
from tests.test_supplier_quotes import mutate


def base(pid):return f'/api/v1/projects/{pid}/procurement-issues'


def setup(clients):
    e,r,pid,order,raw=receipts.setup(clients);source=receipts.ready(e,r,pid,raw);receipts.move(e,pid,source)
    doc=upload(e,pid,'合成领用交接原件，非真实交接；SYN-PURCHASE-LOT-001，原m³单位，合格入库5，本次领用2.125至合成作业区，合成接收人，不代表安装或消耗。','synthetic-procurement-handover.txt','合同计价')
    eid=next(x['id'] for x in get(e,pid)['evidence'] if x['document_id']==doc['document']['id'])
    raw=dict(receipt_id=source['id'],receipt_hash=store.digest(source),issue_number='SYN-ISSUE-001',issued_on=raw['received_on'],quantity='2.125',destination='合成作业区（非真实地点）',receiver='合成接收人（非真实人员）',basis='合成按原单位实际交接2.125m³，不代表消耗或安装',evidence_id=eid,handover_confirmed=True)
    return e,r,pid,source,raw


def read(e,pid):
    q=e.get(base(pid));assert q.status_code==200,q.text;return q.json()


def preview(e,pid,raw,expected=200):
    q=e.post(base(pid)+'/preview',json={'input':raw});assert q.status_code==expected,q.text;return q.json()


def submission(e,pid,raw):
    q=preview(e,pid,raw);return dict(request_id=store.uid(),input=raw,ledger_hash=q['ledger_hash'],preview_hash=q['preview_hash'])


def save(e,pid,b,expected=200):
    q=e.post(base(pid),json=b);assert q.status_code==expected,q.text;return q.json()


def ready(e,r,pid,raw):
    row=save(e,pid,submission(e,pid,raw));approve(r,pid,'procurement_issues',row);return next(x for x in get(e,pid)['procurement_issues'] if x['id']==row['id'])


def move(e,pid,row,kind='execute',eid=None,expected=200,body=None):
    body=body or dict(request_id=store.uid(),ledger_hash=read(e,pid)['ledger_hash'],object_hash=store.digest(row),**(dict(reason='合成取消未执行领用，不回滚实物',evidence_id=eid) if kind=='cancel' else {}))
    q=e.post(base(pid)+'/'+row['id']+'/'+kind,json=body);assert q.status_code==expected,q.text;return q.json(),body


def test_readonly_save_review_explicit_partial_issue_and_historical_exports(clients):
    e,r,pid,source,raw=setup(clients);before=get(e,pid);audit=e.get(f'/api/v1/projects/{pid}/audit').json();preview(e,pid,raw);assert get(e,pid)==before and e.get(f'/api/v1/projects/{pid}/audit').json()==audit
    b=submission(e,pid,raw);row=save(e,pid,b);assert save(e,pid,b)==row and read(e,pid)['lots'][0]['in_stock_quantity']=='5'
    move(e,pid,row,expected=409);approve(r,pid,'procurement_issues',row);row=get(e,pid)['procurement_issues'][0];assert read(e,pid)['lots'][0]['in_stock_quantity']=='5'
    frozen=e.get(base(pid)+'/'+row['id']+'/export');assert frozen.json()['phase']=='approved'
    event,body=move(e,pid,row);assert move(e,pid,row,body=body)[0]==event
    view=read(e,pid);lot=view['lots'][0];assert (lot['accepted_quantity'],lot['issued_quantity'],lot['in_stock_quantity'],lot['unit'])==('5','2.125','2.875','m³')
    assert receipts.read(e,pid)['lots'][0]['in_stock_quantity']=='2.875' and receipts.read(e,pid)['sources'][0]['balance']['remaining_quantity']=='10.50'
    approve(r,pid,'procurement_issues',row,decision='reject',expected=409);move(e,pid,row,'cancel',raw['evidence_id'],409)
    after=get(e,pid)
    for k in before:
        if k.startswith('stock_') or k in ('boq','components','takeoffs','quotes','rules','report_approval','procurement_orders','procurement_requests','procurement_analyses','supplier_quotes','procurement_receipts','procurement_receipt_events'):assert after[k]==before[k],k
    artifact=next(a for a in e.get(f'/api/v1/projects/{pid}/artifacts').json() if a['hash']==frozen.headers['x-artifact-hash']);assert e.get(f'/api/v1/projects/{pid}/artifacts/'+artifact['id']+'/file').content==frozen.content


@pytest.mark.parametrize('fault',[
 lambda v:v.update(quantity='0'),lambda v:v.update(quantity='-1'),lambda v:v.update(quantity='5.000000000000000001'),lambda v:v.update(quantity='1000000001'),lambda v:v.update(quantity=2),lambda v:v.update(quantity=True),lambda v:v.update(quantity='NaN'),lambda v:v.update(quantity='1e-1'),lambda v:v.update(quantity='0.0000000000000000001'),lambda v:v.update(issued_on='2999-01-01'),lambda v:v.update(issued_on='2020-01-01'),lambda v:v.update(issued_on='2026-1-1'),lambda v:v.update(issue_number=' '),lambda v:v.update(destination=''),lambda v:v.update(receiver=''),lambda v:v.update(basis=''),lambda v:v.update(evidence_id='foreign'),lambda v:v.update(receipt_id='foreign'),lambda v:v.update(receipt_hash='0'*64),lambda v:v.update(handover_confirmed=False),lambda v:v.update(handover_confirmed=1),lambda v:v.update(unit='根'),lambda v:v.update(price='1')
])
def test_invalid_input_never_changes_inventory(clients,fault):
    e,r,pid,source,raw=setup(clients);fault(raw);before=get(e,pid);q=e.post(base(pid)+'/preview',json={'input':raw});assert q.status_code in (404,409,422),q.text;assert get(e,pid)==before


def test_competing_approved_drafts_cannot_overdraw_and_do_not_reserve(clients):
    e,r,pid,source,raw=setup(clients);raw['quantity']='3';a=ready(e,r,pid,raw);b=ready(e,r,pid,{**raw,'issue_number':'SYN-OTHER'});assert read(e,pid)['lots'][0]['in_stock_quantity']=='5'
    common=dict(ledger_hash=read(e,pid)['ledger_hash']);ba=dict(**common,request_id=store.uid(),object_hash=store.digest(a));bb=dict(**common,request_id=store.uid(),object_hash=store.digest(b))
    with ThreadPoolExecutor(max_workers=2) as pool:
        x=pool.submit(lambda:e.post(base(pid)+'/'+a['id']+'/execute',json=ba));y=pool.submit(lambda:e.post(base(pid)+'/'+b['id']+'/execute',json=bb));assert sorted([x.result().status_code,y.result().status_code])==[200,409]
    view=read(e,pid);assert view['lots'][0]['in_stock_quantity']=='2' and len(get(e,pid)['procurement_issue_events'])==1
    loser=next(i['record'] for i in view['items'] if not i['event']);assert next(i for i in view['items'] if i['record']['id']==loser['id'])['issue'];move(e,pid,loser,expected=409);move(e,pid,loser,'cancel',raw['evidence_id'])


def test_cancel_preserves_review_releases_number_and_never_restores_stock(clients):
    e,r,pid,source,raw=setup(clients);row=ready(e,r,pid,raw);event,b=move(e,pid,row,'cancel',raw['evidence_id']);assert move(e,pid,row,'cancel',raw['evidence_id'],body=b)[0]==event
    view=read(e,pid);assert view['items'][0]['phase']=='cancel' and not view['items'][0]['review_valid'] and view['lots'][0]['in_stock_quantity']=='5';assert get(e,pid)['procurement_issues'][0]==row
    approve(r,pid,'procurement_issues',row,expected=409);later=ready(e,r,pid,raw);move(e,pid,later);assert read(e,pid)['lots'][0]['in_stock_quantity']=='2.875'
    path=store.ROOT/'originals'/next(d['hash'] for d in get(e,pid)['documents'] if d['name']=='synthetic-procurement-handover.txt');saved=path.read_bytes();path.unlink()
    try:assert next(i for i in read(e,pid)['items'] if i['record']['id']==row['id'])['phase']=='cancel'
    finally:path.write_bytes(saved)


@pytest.mark.parametrize('stage',['preview','save','review','execute'])
@pytest.mark.parametrize('file',['synthetic-procurement-handover.txt','synthetic-cost-scope.txt'])
def test_each_new_action_requires_actual_source_and_handover_bytes(clients,stage,file):
    e,r,pid,source,raw=setup(clients);b=submission(e,pid,raw);row=None
    if stage in ('review','execute'):row=save(e,pid,b)
    if stage=='execute':approve(r,pid,'procurement_issues',row);row=get(e,pid)['procurement_issues'][0]
    path=store.ROOT/'originals'/next(d['hash'] for d in get(e,pid)['documents'] if d['name']==file);old=path.read_bytes();path.write_bytes(b'changed synthetic proof')
    try:
        if stage=='preview':preview(e,pid,raw,409)
        elif stage=='save':save(e,pid,b,409)
        elif stage=='review':approve(r,pid,'procurement_issues',row,expected=409)
        else:move(e,pid,row,expected=409)
        assert read(e,pid)['lots'][0]['in_stock_quantity']=='5'
    finally:path.write_bytes(old)


def test_executed_handover_proof_loss_quarantines_remaining_without_reversing_goods(clients):
    e,r,pid,source,raw=setup(clients);row=ready(e,r,pid,raw);move(e,pid,row);view=read(e,pid);path=store.ROOT/'originals'/next(d['hash'] for d in get(e,pid)['documents'] if d['name']=='synthetic-procurement-handover.txt');old=path.read_bytes();path.unlink()
    try:
        now=read(e,pid);assert now['lots'][0]['in_stock_quantity']=='2.875' and now['lots'][0]['issued_quantity']=='2.125' and not now['lots'][0]['verified'];assert receipts.read(e,pid)['lots'][0]['in_stock_quantity']=='2.875' and not receipts.read(e,pid)['lots'][0]['verified']
        assert now['items'][0]['phase']=='issue' and not now['items'][0]['review_valid'];preview(e,pid,{**raw,'issue_number':'MORE','evidence_id':source['input']['evidence_id']},409);assert e.get(base(pid)+'/'+row['id']+'/export').status_code==200
    finally:path.write_bytes(old)
    assert read(e,pid)['lots']==view['lots']


def test_two_issues_exact_total_full_draw_and_minimum_quantity(clients):
    e,r,pid,source,raw=setup(clients);raw['quantity']='0.000000000000000001';a=ready(e,r,pid,raw);move(e,pid,a);assert read(e,pid)['lots'][0]['in_stock_quantity']=='4.999999999999999999'
    raw.update(issue_number='SECOND',quantity='4.999999999999999999');b=ready(e,r,pid,raw);move(e,pid,b);lot=read(e,pid)['lots'][0]
    assert Decimal(lot['in_stock_quantity'])==0 and Decimal(lot['issued_quantity'])==5;preview(e,pid,{**raw,'issue_number':'OVER','quantity':'0.000000000000000001'},409)


def test_order_metadata_revision_and_upstream_withdrawal_do_not_reset_actual_stock(clients):
    e,r,pid,source,raw=setup(clients);row=ready(e,r,pid,raw);move(e,pid,row);order=get(e,pid)['procurement_orders'][0]
    newer=orders.save(e,pid,orders.submission(e,pid,orders.revision(order,order_number='SYN-CORRECTED')));approve(r,pid,'procurement_orders',newer);approve(r,pid,'procurement_analyses',get(e,pid)['procurement_analyses'][0],decision='reject')
    raw.update(issue_number='SECOND',quantity='1');later=ready(e,r,pid,raw);move(e,pid,later);assert read(e,pid)['lots'][0]['in_stock_quantity']=='1.875' and receipts.read(e,pid)['sources'][0]['balance']['remaining_quantity']=='10.50'


def test_roles_source_state_duplicate_number_and_self_review(clients):
    e,r,pid,source,raw=setup(clients);b=submission(e,pid,raw);assert r.post(base(pid),json=b).status_code==403 and r.post(base(pid)+'/preview',json={'input':raw}).status_code==403;assert clients['other'].get(base(pid)).status_code==404
    row=save(e,pid,b);assert clients['other'].get(base(pid)+'/'+row['id']+'/export').status_code==404;preview(e,pid,{**raw,'issue_number':raw['issue_number'].lower()},409)
    with store.transaction() as c:c.execute("UPDATE users SET role='reviewer' WHERE username='estimator'")
    approve(e,pid,'procurement_issues',row,expected=403)


def test_unexecuted_or_all_rejected_receipt_is_never_issue_supply(clients):
    e,r,pid,order,rr=receipts.setup(clients);source=receipts.ready(e,r,pid,rr)
    raw=dict(receipt_id=source['id'],receipt_hash=store.digest(source),issue_number='NO-SUPPLY',issued_on=rr['received_on'],quantity='1',destination='合成去向',receiver='合成人',basis='合成原件',evidence_id=rr['evidence_id'],handover_confirmed=True);preview(e,pid,raw,409)
    receipts.move(e,pid,source,'cancel',rr['evidence_id']);rr.update(accepted_quantity='0',rejected_quantity='5.25');source=receipts.ready(e,r,pid,rr);receipts.move(e,pid,source);raw.update(receipt_id=source['id'],receipt_hash=store.digest(source));preview(e,pid,raw,409)


def test_stale_preview_idempotency_identity_and_cancel_execute_race(clients):
    e,r,pid,source,raw=setup(clients);b=submission(e,pid,raw);altered=copy.deepcopy(b);altered['input']['quantity']='1';save(e,pid,altered,409)
    row=save(e,pid,b);save(e,pid,{**b,'input':{**raw,'issue_number':'DIFFERENT'}},409);approve(r,pid,'procurement_issues',row);row=get(e,pid)['procurement_issues'][0]
    common=dict(ledger_hash=read(e,pid)['ledger_hash'],object_hash=store.digest(row));exe=dict(**common,request_id=store.uid());cancel=dict(**common,request_id=store.uid(),reason='合成取消未执行',evidence_id=raw['evidence_id'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        x=pool.submit(lambda:e.post(base(pid)+'/'+row['id']+'/execute',json=exe));y=pool.submit(lambda:e.post(base(pid)+'/'+row['id']+'/cancel',json=cancel));assert sorted([x.result().status_code,y.result().status_code])==[200,409]
    assert len(get(e,pid)['procurement_issue_events'])==1


def test_corrupt_event_blocks_new_movements_but_not_saved_artifact(clients):
    e,r,pid,source,raw=setup(clients);row=ready(e,r,pid,raw);move(e,pid,row);f=e.get(base(pid)+'/'+row['id']+'/export');artifact=next(a for a in e.get(f'/api/v1/projects/{pid}/artifacts').json() if a['hash']==f.headers['x-artifact-hash'])
    mutate(pid,lambda p:p['procurement_issue_events'][0].update(quantity='999'));assert e.get(base(pid)).status_code==409 and e.get(receipts.base(pid)).status_code==409
    assert e.get(f'/api/v1/projects/{pid}/artifacts/'+artifact['id']+'/file').content==f.content


def test_legacy_empty_collections_and_old_receipt_notice_remain_reviewable(clients,monkeypatch):
    e,r,pid,order,raw=receipts.setup(clients)
    with monkeypatch.context() as patch:
        patch.setattr(receipt_module,'NOTICE','合成旧版说明：当时尚无采购领用');source=receipts.save(e,pid,receipts.submission(e,pid,raw))
    approve(r,pid,'procurement_receipts',source);source=get(e,pid)['procurement_receipts'][0];receipts.move(e,pid,source)
    mutate(pid,lambda p:(p.pop('procurement_issues',None),p.pop('procurement_issue_events',None)));assert not read(e,pid)['items'] and read(e,pid)['lots'][0]['in_stock_quantity']=='5'


def test_batch_review_shared_allowance_and_closed_targets(clients):
    from tests.test_workflows import batch_body
    e,r,pid,source,raw=setup(clients);a=save(e,pid,submission(e,pid,{**raw,'quantity':'2'}));b=save(e,pid,submission(e,pid,{**raw,'issue_number':'SECOND','quantity':'3'}));url=f'/api/v1/projects/{pid}'
    q=r.post(url+'/reviews/batch',json=batch_body(r,pid,'procurement_issues',[a,b]));assert q.status_code==200,q.text;assert read(e,pid)['lots'][0]['in_stock_quantity']=='5'
    rows=get(e,pid)['procurement_issues'];move(e,pid,rows[0]);move(e,pid,rows[1]);assert Decimal(read(e,pid)['lots'][0]['in_stock_quantity'])==0
    assert not r.get(url+'/review-batch-targets',params={'collection':'procurement_issues'}).json()['targets']


def test_batch_failure_rolls_back_prior_review_and_audit(clients):
    from tests.test_workflows import batch_body
    e,r,pid,source,raw=setup(clients);a=save(e,pid,submission(e,pid,raw));b=save(e,pid,submission(e,pid,{**raw,'issue_number':'SECOND'}));url=f'/api/v1/projects/{pid}'
    body=batch_body(r,pid,'procurement_issues',[a,b]);move(e,pid,b,'cancel',raw['evidence_id']);before=get(e,pid);audit=e.get(url+'/audit').json()
    q=r.post(url+'/reviews/batch',json=body);assert q.status_code==409,q.text;assert get(e,pid)==before and e.get(url+'/audit').json()==audit


def test_rejected_issue_does_not_invalidate_engineering_or_change_source_stock(clients):
    e,r,pid,source,raw=setup(clients);row=save(e,pid,submission(e,pid,raw));before=get(e,pid);approve(r,pid,'procurement_issues',row,decision='reject');after=get(e,pid)
    for key in ('components','takeoffs','boq','quotes','report_approval','procurement_receipts','procurement_receipt_events'):assert after[key]==before[key],key
    assert read(e,pid)['lots'][0]['in_stock_quantity']=='5'
