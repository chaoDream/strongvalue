"""Synthetic full-bar stocktaking: additive facts, custody protection and live evidence."""
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import date,timedelta
from decimal import localcontext
import pytest
from backend import store,stock_inventory as inv,stock_counts as counts
from tests.test_stock_inventory import setup as inventory_setup,receipt,read,base,common,reservation_body,submit as reserve,movement as inventory_move
from tests.test_stock_operations import body as operation_body,approved_operation,execute as operation_execute,issue,today
from tests.test_stock_processing_results import raw as processing_raw,ready as processing_ready,execute as recover
from tests.test_workflow import get,approve,upload,create
from tests.test_polygon_takeoff import mutate


def setup(clients,**kwargs):
    e,r,pid,comp,eid=inventory_setup(clients);row,_=receipt(e,r,pid,eid,**kwargs);row=read(e,pid)['receipts'][0]['record'];return e,r,pid,comp,eid,row


def raw(e,pid,receipt,eid,quantity=0):
    snapshot=next(x for x in read(e,pid)['stock_counts']['receipts'] if x['receipt_id']==receipt['id'])['receipt_snapshot']
    return dict(receipt_id=receipt['id'],receipt_snapshot=snapshot,counted_quantity=quantity,business_date=today(),custodian='合成原材盘点人A',basis='合成核查同批号库位、规格定尺及质量，在库完整根实盘差异有据；非真实仓库验收',count_evidence_id=eid,variance_evidence_id=eid,confirmed=True)


def preview(e,pid,v,expected=200):
    q=e.post(base(pid)+'/stock-counts/preview',json={'input':v});assert q.status_code==expected,q.text;return q.json()


def submission(e,pid,v):
    q=preview(e,pid,v);return dict(request_id=store.uid(),ledger_hash=q['ledger_hash'],input=v,preview_hash=q['preview_hash'])


def submit(e,pid,b,expected=200):
    q=e.post(base(pid)+'/stock-counts',json=b);assert q.status_code==expected,q.text;return q.json()


def ready(e,r,pid,v):
    row=submit(e,pid,submission(e,pid,v));approve(r,pid,counts.COLLECTION,row);return next(x for x in get(e,pid)[counts.COLLECTION] if x['id']==row['id'])


def move(e,pid,row,kind='execute',eid=None,expected=200,b=None):
    b=b or dict(**common(e,pid),object_hash=store.digest(row),**(dict(basis='合成取消未执行或过期原材盘点，不回滚物料',evidence_id=eid) if kind=='cancel' else {}))
    q=e.post(base(pid)+'/stock-counts/'+row['id']+'/'+kind,json=b);assert q.status_code==expected,q.text;return q.json(),b


@pytest.mark.parametrize('quantity',[0,10,12])
def test_preview_review_explicit_execution_preserves_original_and_engineering(clients,quantity):
    e,r,pid,comp,eid,receipt=setup(clients);before=get(e,pid);v=raw(e,pid,receipt,eid,quantity);q=preview(e,pid,v);assert get(e,pid)==before
    assert q['result']['before']==10 and q['result']['after']==quantity and q['result']['delta']==quantity-10
    b=submission(e,pid,v);row=submit(e,pid,b);assert submit(e,pid,b)==row;assert read(e,pid)['receipts'][0]['on_hand']==10
    move(e,pid,row,expected=409);approve(r,pid,counts.COLLECTION,row);row=get(e,pid)[counts.COLLECTION][0];assert read(e,pid)['receipts'][0]['on_hand']==10
    frozen=e.get(base(pid)+'/export');event,b=move(e,pid,row);assert move(e,pid,row,b=b)[0]==event
    s=read(e,pid)['receipts'][0];assert s['record']==receipt and s['record']['normalized']['quantity']==10 and s['on_hand']==quantity and s['available']==quantity and s['adjusted_quantity']==quantity and s['count_revision']==1
    assert s['adjustment_gain']==max(0,quantity-10) and s['adjustment_loss']==max(0,10-quantity)
    for k in ('components','takeoffs','boq','quotes','report_approval','stock_operations','stock_processing_results'):assert get(e,pid)[k]==before[k]
    artifact=next(a for a in e.get('/api/v1/projects/'+pid+'/artifacts').json() if a['purpose']=='stock_inventory');assert e.get('/api/v1/projects/'+pid+'/artifacts/'+artifact['id']+'/file').content==frozen.content
    move(e,pid,row,expected=409);move(e,pid,row,'cancel',eid,409);approve(r,pid,counts.COLLECTION,row,expected=409)


@pytest.mark.parametrize('field,value,code',[
 ('counted_quantity',True,422),('counted_quantity',-1,422),('counted_quantity',1.5,422),('counted_quantity','1',422),('counted_quantity',1000001,422),('counted_quantity',None,422),
 ('business_date',None,422),('business_date','2026-1-1',422),('business_date','2999-01-01',409),('business_date','2000-01-01',409),
 ('custodian','',422),('basis','',422),('confirmed',False,422),('receipt_id','foreign',404),('receipt_snapshot','old',409),('count_evidence_id','missing',404),('variance_evidence_id','missing',404),
])
def test_invalid_input_never_adjusts(clients,field,value,code):
    e,r,pid,comp,eid,receipt=setup(clients);v=raw(e,pid,receipt,eid);v[field]=value;preview(e,pid,v,code)
    assert not read(e,pid)['stock_counts']['records'] and read(e,pid)['receipts'][0]['on_hand']==10


def test_previews_and_execution_cannot_overwrite_pending_holds(clients):
    e,r,pid,comp,eid,receipt=setup(clients);row=ready(e,r,pid,raw(e,pid,receipt,eid))
    reserved=reserve(e,pid,reservation_body(e,pid,comp,eid,[receipt]));preview(e,pid,raw(e,pid,receipt,eid,2),409);move(e,pid,row,expected=409)
    assert read(e,pid)['receipts'][0]['reserved']==3
    inventory_move(e,pid,eid,reserved);move(e,pid,row);assert read(e,pid)['receipts'][0]['on_hand']==0


def test_issue_and_complete_return_stale_count_even_if_on_hand_restored(clients):
    e,r,pid,comp,eid,receipt=setup(clients);row=ready(e,r,pid,raw(e,pid,receipt,eid))
    reserved=reserve(e,pid,reservation_body(e,pid,comp,eid,[receipt]));approve(r,pid,'stock_reservations',reserved);reserved=get(e,pid)['stock_reservations'][0]
    source=issue(e,r,pid,eid,reserved);returned=approved_operation(e,r,pid,operation_body(e,pid,eid,'return',source,[dict(allocation_index=1,quantity=3)]));operation_execute(e,pid,returned)
    assert read(e,pid)['receipts'][0]['on_hand']==10
    move(e,pid,row,expected=409);assert read(e,pid)['stock_counts']['records'][0]['issue'];move(e,pid,row,'cancel',eid)


def test_racing_counts_same_snapshot_execute_only_once(clients):
    e,r,pid,comp,eid,receipt=setup(clients);v=raw(e,pid,receipt,eid,11);a=ready(e,r,pid,v);b=ready(e,r,pid,v)
    def run(row):return e.post(base(pid)+'/stock-counts/'+row['id']+'/execute',json=dict(**common(e,pid),object_hash=store.digest(row))).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:codes=list(pool.map(run,[a,b]))
    assert sorted(codes)==[200,409] and read(e,pid)['receipts'][0]['on_hand']==11


def test_next_day_requires_actual_recount_cancel_still_available(clients,monkeypatch):
    e,r,pid,comp,eid,receipt=setup(clients);row=ready(e,r,pid,raw(e,pid,receipt,eid));monkeypatch.setattr(counts,'today',lambda:(date.fromisoformat(today())+timedelta(days=1)).isoformat());move(e,pid,row,expected=409);move(e,pid,row,'cancel',eid)


@pytest.mark.parametrize('decision',['reject','request_more_info'])
def test_negative_review_and_cancel_preserve_engineering_and_quantity(clients,decision):
    e,r,pid,comp,eid,receipt=setup(clients);before=get(e,pid);row=submit(e,pid,submission(e,pid,raw(e,pid,receipt,eid)));approve(r,pid,counts.COLLECTION,row,decision);row=get(e,pid)[counts.COLLECTION][0]
    move(e,pid,row,expected=409);event,b=move(e,pid,row,'cancel',eid);assert move(e,pid,row,'cancel',eid,b=b)[0]==event
    assert read(e,pid)['receipts'][0]['on_hand']==10
    for k in ('components','takeoffs','boq','quotes','report_approval'):assert get(e,pid)[k]==before[k]


def test_source_receipt_must_be_approved_current_and_not_voided(clients):
    e,r,pid,comp,eid,receipt=setup(clients);v=raw(e,pid,receipt,eid)
    approve(r,pid,'stock_receipts',receipt,'request_more_info');preview(e,pid,v,409)
    receipt=get(e,pid)['stock_receipts'][0];approve(r,pid,'stock_receipts',receipt);receipt=get(e,pid)['stock_receipts'][0];inventory_move(e,pid,eid,receipt,'void');preview(e,pid,v,409)


def test_roles_project_scope_preview_binding_object_hash_and_author_switch(clients):
    e,r,pid,comp,eid,receipt=setup(clients);b=submission(e,pid,raw(e,pid,receipt,eid));bad=copy.deepcopy(b);bad['input']['counted_quantity']=2;submit(e,pid,bad,409)
    assert r.post(base(pid)+'/stock-counts',json=b).status_code==403
    row=submit(e,pid,b);bad=copy.deepcopy(b);bad['input']['basis']='changed';submit(e,pid,bad,409)
    old=dict(**common(e,pid),object_hash=store.digest(row));approve(r,pid,counts.COLLECTION,row);row=get(e,pid)[counts.COLLECTION][0];move(e,pid,row,expected=409,b=old)
    other=create(clients['other']);assert clients['other'].post(base(pid)+'/stock-counts/preview',json={'input':b['input']}).status_code==404
    assert e.post(base(other['id'])+'/stock-counts/preview',json={'input':b['input']}).status_code==404
    with store.transaction() as db:db.execute("UPDATE users SET role='reviewer' WHERE username='estimator'")
    approve(e,pid,counts.COLLECTION,row,expected=403)


@pytest.mark.parametrize('stage',['preview','submit','review','execute'])
def test_actual_original_byte_gate_each_stage(clients,stage):
    e,r,pid,comp,eid,receipt=setup(clients);v=raw(e,pid,receipt,eid);b=submission(e,pid,v);row=None
    if stage in ('review','execute'):row=submit(e,pid,b)
    if stage=='execute':approve(r,pid,counts.COLLECTION,row);row=get(e,pid)[counts.COLLECTION][0]
    doc=next(d for d in get(e,pid)['documents'] if d['id']==next(x for x in get(e,pid)['evidence'] if x['id']==eid)['document_id']);path=store.ROOT/'originals'/doc['hash'];saved=path.read_bytes();path.write_bytes(b'changed synthetic original')
    try:
        if stage=='preview':preview(e,pid,v,409)
        elif stage=='submit':submit(e,pid,b,409)
        elif stage=='review':approve(r,pid,counts.COLLECTION,row,expected=409)
        else:move(e,pid,row,expected=409)
        assert read(e,pid)['receipts'][0]['on_hand']==10
    finally:path.write_bytes(saved)


def test_custody_processing_quota_and_descendant_quarantine_not_reset_by_count(clients):
    e,r,pid,comp,eid,receipt=setup(clients)
    upload(e,pid,'合成独立原材盘点：原批完整6m、3kg/m，在库8根，漏记1根，非真实仓库','count-proof.txt');fresh=get(e,pid)['evidence'][-1]['id']
    for component in get(e,pid)['components']:approve(r,pid,'components',component)
    reserved=reserve(e,pid,reservation_body(e,pid,comp,eid,[receipt]));approve(r,pid,'stock_reservations',reserved);reserved=get(e,pid)['stock_reservations'][0];source=issue(e,r,pid,eid,reserved)
    consumed=approved_operation(e,r,pid,operation_body(e,pid,eid,'consume',source,[dict(allocation_index=1,quantity=1)]));operation_execute(e,pid,consumed)
    result=processing_ready(e,r,pid,processing_raw(consumed,eid));recover(e,pid,result)
    row=ready(e,r,pid,raw(e,pid,receipt,fresh,8));move(e,pid,row)
    s=read(e,pid)['receipts'][0];assert (s['on_hand'],s['in_custody'],s['consumed'],s['adjustment_gain'])==(8,2,1,1)
    from tests.test_stock_offcut_reuse import target,value as reuse_value,approved as reuse_ready,movement as reuse_move
    from tests.test_stock_offcut_processing import raw as child_raw,ready as child_ready,move as child_move
    c=target(e,r,pid,comp);lot=read(e,pid)['processing']['lots'][0];offcut_source=reuse_ready(e,r,pid,reuse_value(e,pid,c,eid,lot));reuse_move(e,pid,offcut_source)
    child=child_ready(e,r,pid,child_raw(offcut_source,eid));child_move(e,pid,child)
    frozen=get(e,pid)
    doc=next(d for d in frozen['documents'] if d['id']==next(x for x in frozen['evidence'] if x['id']==fresh)['document_id']);path=store.ROOT/'originals'/doc['hash'];saved=path.read_bytes();path.write_bytes(b'changed count only')
    try:
        v=read(e,pid);s=v['receipts'][0];assert (s['on_hand'],s['in_custody'],s['consumed'])==(8,2,1) and s['available']==0 and s['issue']
        assert all(l['adopted_quantity']==0 and l['issue'] for l in v['processing']['lots'])
        assert v['stock_counts']['records'][0]['issue'] and not v['stock_counts']['receipts']
        assert e.get(base(pid)+'/export').status_code==200
        # Real outstanding operations still record against fresh own proof; no count rollback.
        returned=approved_operation(e,r,pid,operation_body(e,pid,eid,'return',source,[dict(allocation_index=1,quantity=1)]));operation_execute(e,pid,returned)
        assert read(e,pid)['receipts'][0]['on_hand']==9 and read(e,pid)['receipts'][0]['available']==0
    finally:path.write_bytes(saved)
    v=read(e,pid);assert v['receipts'][0]['available']==9 and all(l['verified'] for l in v['processing']['lots'])
    for k in ('stock_processing_results','stock_offcut_processing'):assert get(e,pid)[k]==frozen[k]


def test_gain_allows_real_later_reservation_and_issue_from_same_original_batch(clients):
    e,r,pid,comp,eid,receipt=setup(clients,quantity='1');row=ready(e,r,pid,raw(e,pid,receipt,eid,3));move(e,pid,row)
    reserved=reserve(e,pid,reservation_body(e,pid,comp,eid,[receipt]));approve(r,pid,'stock_reservations',reserved);reserved=get(e,pid)['stock_reservations'][0];issue(e,r,pid,eid,reserved)
    s=read(e,pid)['receipts'][0];assert s['on_hand']==0 and s['in_custody']==3 and s['record']['normalized']['quantity']==1


def test_signed_changes_exact_under_low_decimal_precision_and_multiple_batches(clients):
    e,r,pid,comp,eid,receipt=setup(clients,length='3523',weight='3',weight_unit='kg/m');other,_=globals()['receipt'](e,r,pid,eid,quantity='2')
    v=raw(e,pid,receipt,eid,13)
    with localcontext() as context:
        context.prec=3;row=ready(e,r,pid,v);assert row['length_delta']=='10.569' and row['mass_delta']=='31.707';move(e,pid,row)
    row=ready(e,r,pid,raw(e,pid,receipt,eid,0));move(e,pid,row)
    s=read(e,pid)['receipts'];assert s[0]['adjustment_gain']==3 and s[0]['adjustment_loss']==13 and s[0]['on_hand']==0 and s[1]['on_hand']==2


@pytest.mark.parametrize('fault',['record','event','wrong-batch','reordered','cancel-link','missing-review'])
def test_corrupt_history_blocks_read_and_export(clients,fault):
    e,r,pid,comp,eid,receipt=setup(clients);row=ready(e,r,pid,raw(e,pid,receipt,eid,11));move(e,pid,row,'cancel',eid) if fault=='cancel-link' else move(e,pid,row)
    def corrupt(p):
        if fault=='record':p[counts.COLLECTION][0]['after']=99
        elif fault=='event':p['stock_events'][-1]=inv.seal({**p['stock_events'][-1],'after':99})
        elif fault=='wrong-batch':p['stock_events'][-1]=inv.seal({**p['stock_events'][-1],'receipt_id':'other'})
        elif fault=='cancel-link':p['stock_events'][-1]=inv.seal({**p['stock_events'][-1],'target_id':'missing'})
        elif fault=='missing-review':p['reviews']=[x for x in p['reviews'] if x['collection']!=counts.COLLECTION]
        else:
            # Stopped original cannot receive a later count event, even if content is sealed.
            stop=dict(operation='void',target_id=receipt['id'],id=store.uid());p['stock_events'].insert(0,inv.seal(stop))
    mutate(pid,corrupt);assert e.get(base(pid)).status_code==409 and e.get(base(pid)+'/export').status_code==409


def test_gain_cannot_be_reordered_after_issue_to_retroactively_fund_stock(clients):
    e,r,pid,comp,eid,receipt=setup(clients,quantity='1');row=ready(e,r,pid,raw(e,pid,receipt,eid,3));move(e,pid,row)
    reserved=reserve(e,pid,reservation_body(e,pid,comp,eid,[receipt]));approve(r,pid,'stock_reservations',reserved);reserved=get(e,pid)['stock_reservations'][0];issue(e,r,pid,eid,reserved)
    def corrupt(p):
        event=next(x for x in p['stock_events'] if x['operation']=='stock_count');p['stock_events'].remove(event);p['stock_events'].append(event)
    mutate(pid,corrupt);assert e.get(base(pid)).status_code==409 and e.get(base(pid)+'/export').status_code==409


def test_changed_unrelated_batch_does_not_invalidate_count_but_global_version_is_required(clients):
    e,r,pid,comp,eid,stock=setup(clients);other,_=receipt(e,r,pid,eid,quantity='2');a=ready(e,r,pid,raw(e,pid,stock,eid,11));b=ready(e,r,pid,raw(e,pid,other,eid,3))
    stale=dict(**common(e,pid),object_hash=store.digest(a));move(e,pid,b);move(e,pid,a,expected=409,b=stale);move(e,pid,a)
    assert [s['on_hand'] for s in read(e,pid)['receipts']]==[11,3]
