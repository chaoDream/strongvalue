"""Synthetic partial full-bar handovers: residual holds, provenance and competing actions."""
import copy
from concurrent.futures import ThreadPoolExecutor
import pytest
from backend import store,stock_inventory as inv
from tests.test_stock_operations import setup,body as raw_body,approved_operation,execute,issue
from tests.test_stock_inventory import base,read,common,reservation_body,submit as reserve,movement
from tests.test_stock_counts import raw as count_raw,ready as count_ready,move as count_move
from tests.test_stock_processing_results import raw as processing_raw,ready as processing_ready,execute as recover
from tests.test_workflow import get,approve,upload,create
from tests.test_polygon_takeoff import mutate,post


def raw(e,pid,eid,reservation,quantity=1,index=1):return raw_body(e,pid,eid,'partial_issue',reservation,[dict(allocation_index=index,quantity=quantity)])['input']


def preview(e,pid,v,expected=200):
    response=e.post(base(pid)+'/operations/preview',json={'input':v});assert response.status_code==expected,response.text;return response.json()


def submission(e,pid,v):
    q=preview(e,pid,v);return dict(request_id=store.uid(),ledger_hash=q['ledger_hash'],input=v,preview_hash=q['preview_hash'])


def submit(e,pid,b,expected=200):
    response=e.post(base(pid)+'/operations',json=b);assert response.status_code==expected,response.text;return response.json()


def ready(e,r,pid,v):
    row=submit(e,pid,submission(e,pid,v));approve(r,pid,'stock_operations',row);return next(x for x in get(e,pid)['stock_operations'] if x['id']==row['id'])


def release_left(e,pid,reservation,eid,expected=200,b=None):
    b=b or dict(**common(e,pid),operation='release_remaining',target_id=reservation['id'],object_hash=store.digest(reservation),basis='合成有据释放未领余量，不退回已领数量，不重开材料表',evidence_id=eid)
    response=e.post(base(pid)+'/events',json=b);assert response.status_code==expected,response.text;return response.json(),b


def test_preview_review_partial_holds_and_finishing_do_not_free_stock_or_reopen_plan(clients):
    e,r,pid,comp,eid,reservation,stocks=setup(clients);before=get(e,pid);q=preview(e,pid,raw(e,pid,eid,reservation));assert get(e,pid)==before
    assert q['result']['totals']['quantity']==1 and q['result']['totals']['length']=='6' and q['result']['totals']['mass']=='18'
    b=submission(e,pid,raw(e,pid,eid,reservation));row=submit(e,pid,b);assert submit(e,pid,b)==row
    s=read(e,pid)['receipts'][0];assert (s['on_hand'],s['reserved'],s['available'])==(10,3,7)
    execute(e,pid,row,409);approve(r,pid,'stock_operations',row);row=get(e,pid)['stock_operations'][0];assert read(e,pid)['receipts'][0]['on_hand']==10
    frozen=e.get(base(pid)+'/export');event,b=execute(e,pid,row);assert execute(e,pid,row,b=b)[0]==event
    v=read(e,pid);s=v['receipts'][0];res=v['reservations'][0];assert (s['on_hand'],s['reserved'],s['available'],s['in_custody'])==(9,2,7,1)
    assert res['phase']=='partially_issued' and res['active'] and res['issued_quantity']==1 and res['unissued_quantity']==2 and res['held_quantity']==2 and not res['issue']
    approve(r,pid,'stock_reservations',reservation,expected=409);movement(e,pid,eid,reservation,expected=409)
    full=raw_body(e,pid,eid,'issue',reservation);assert e.post(base(pid)+'/operations',json=full).status_code==409
    last=ready(e,r,pid,raw(e,pid,eid,reservation,2));execute(e,pid,last)
    v=read(e,pid);s=v['receipts'][0];assert (s['on_hand'],s['reserved'],s['available'],s['in_custody'])==(7,0,7,3)
    assert v['reservations'][0]['phase']=='issued' and not v['reservations'][0]['active']
    release_left(e,pid,reservation,eid,409);reserve(e,pid,reservation_body(e,pid,comp,eid,stocks),409)
    for k in ('components','takeoffs','boq','quotes','report_approval','stock_receipts'):assert get(e,pid)[k]==before[k]
    artifact=next(a for a in e.get('/api/v1/projects/'+pid+'/artifacts').json() if a['purpose']=='stock_inventory');assert e.get('/api/v1/projects/'+pid+'/artifacts/'+artifact['id']+'/file').content==frozen.content


def test_release_only_unissued_then_process_and_return_prior_issue(clients):
    e,r,pid,comp,eid,reservation,stocks=setup(clients);source=ready(e,r,pid,raw(e,pid,eid,reservation));execute(e,pid,source)
    pending=ready(e,r,pid,raw(e,pid,eid,reservation));event,b=release_left(e,pid,reservation,eid);assert release_left(e,pid,reservation,eid,b=b)[0]==event
    assert event['allocations'][0]['quantity']==2
    v=read(e,pid);s=v['receipts'][0];assert (s['on_hand'],s['reserved'],s['available'],s['in_custody'])==(9,0,9,1)
    assert v['reservations'][0]['phase']=='remaining_released' and v['reservations'][0]['unissued_quantity']==2
    execute(e,pid,pending,409);preview(e,pid,raw(e,pid,eid,reservation),409);reserve(e,pid,reservation_body(e,pid,comp,eid,stocks),409)
    # Actual custody remains; the original partial issue is the sole source.
    returned=approved_operation(e,r,pid,raw_body(e,pid,eid,'return',source,[dict(allocation_index=1,quantity=1)]));execute(e,pid,returned)
    assert read(e,pid)['receipts'][0]['on_hand']==10 and read(e,pid)['reservations'][0]['issued_quantity']==1
    reserve(e,pid,reservation_body(e,pid,comp,eid,stocks),409)


def test_each_partial_has_own_custody_and_consumption_actual_results(clients):
    e,r,pid,comp,eid,reservation,stocks=setup(clients);a=ready(e,r,pid,raw(e,pid,eid,reservation));execute(e,pid,a);b=ready(e,r,pid,raw(e,pid,eid,reservation,2));execute(e,pid,b)
    bad=raw_body(e,pid,eid,'consume',a,[dict(allocation_index=1,quantity=2)]);assert e.post(base(pid)+'/operations',json=bad).status_code==409
    consumed=approved_operation(e,r,pid,raw_body(e,pid,eid,'consume',a,[dict(allocation_index=1,quantity=1)]));execute(e,pid,consumed)
    record=processing_ready(e,r,pid,processing_raw(consumed,eid));recover(e,pid,record)
    returned=approved_operation(e,r,pid,raw_body(e,pid,eid,'return',b,[dict(allocation_index=1,quantity=1)]));execute(e,pid,returned)
    v=read(e,pid);s=v['receipts'][0];assert (s['on_hand'],s['reserved'],s['in_custody'],s['consumed'])==(8,0,1,1)
    assert v['processing']['lots'][0]['quantity']==1
    rows={x['record']['id']:x for x in v['operations']};assert rows[a['id']]['balances'][0]['consumed']==1 and rows[b['id']]['balances'][0]['returned']==1


def test_nonfirst_original_allocation_and_multiple_batches_keep_original_indices(clients):
    e,r,pid,comp,eid,reservation,stocks=setup(clients,mode='limited');v=raw(e,pid,eid,reservation,1,index=2);source=ready(e,r,pid,v);execute(e,pid,source)
    s=read(e,pid);assert s['receipts'][0]['issued']==0 and s['receipts'][1]['issued']==1
    assert s['reservations'][0]['progress'][1]['remaining']==1
    consumed=approved_operation(e,r,pid,raw_body(e,pid,eid,'consume',source,[dict(allocation_index=2,quantity=1)]));execute(e,pid,consumed)
    assert consumed['allocations'][0]['allocation_index']==2 and consumed['totals']['length']=='3.523'
    v=raw(e,pid,eid,reservation,1,index=1);v['selections'].append(dict(allocation_index=3,quantity=1));source2=ready(e,r,pid,v);execute(e,pid,source2)
    release_left(e,pid,reservation,eid);s=read(e,pid);assert sum(x['reserved'] for x in s['receipts'])==0 and s['reservations'][0]['held_quantity']==0


@pytest.mark.parametrize('fault',[
 lambda v:v.update(selections=[]),lambda v:v.update(selections={}),lambda v:v['selections'][0].update(quantity=0),lambda v:v['selections'][0].update(quantity=-1),lambda v:v['selections'][0].update(quantity=True),lambda v:v['selections'][0].update(quantity=1.5),lambda v:v['selections'][0].update(quantity=1000001),lambda v:v['selections'][0].update(allocation_index=True),lambda v:v['selections'][0].update(allocation_index=2),lambda v:v['selections'][0].update(receipt_id='other'),lambda v:v['selections'].append(copy.deepcopy(v['selections'][0])),lambda v:v.update(confirmed=False),lambda v:v.update(business_date=None),lambda v:v.update(business_date='2999-01-01'),lambda v:v.update(extra=1),
])
def test_invalid_partial_inputs_do_not_release_any_holds(clients,fault):
    e,r,pid,comp,eid,reservation,stocks=setup(clients);v=raw(e,pid,eid,reservation);fault(v);preview(e,pid,v,422)
    s=read(e,pid)['receipts'][0];assert (s['on_hand'],s['reserved'])==(10,3)


def test_excess_and_competing_partial_or_full_orders_recheck_remaining(clients):
    e,r,pid,comp,eid,reservation,stocks=setup(clients);preview(e,pid,raw(e,pid,eid,reservation,4),409)
    a=ready(e,r,pid,raw(e,pid,eid,reservation,2));b=ready(e,r,pid,raw(e,pid,eid,reservation,2));full=approved_operation(e,r,pid,raw_body(e,pid,eid,'issue',reservation))
    execute(e,pid,a);execute(e,pid,b,409);execute(e,pid,full,409)
    assert read(e,pid)['receipts'][0]['reserved']==1


def test_concurrent_partial_execution_and_remaining_release_are_serialized(clients):
    e,r,pid,comp,eid,reservation,stocks=setup(clients);first=ready(e,r,pid,raw(e,pid,eid,reservation));execute(e,pid,first);row=ready(e,r,pid,raw(e,pid,eid,reservation,2));token=common(e,pid)['ledger_hash']
    b1=dict(request_id=store.uid(),ledger_hash=token,object_hash=store.digest(row));b2=dict(request_id=store.uid(),ledger_hash=token,object_hash=store.digest(reservation),target_id=reservation['id'],operation='release_remaining',basis='合成并发释放余量',evidence_id=eid)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(e.post,base(pid)+'/operations/'+row['id']+'/execute',json=b1),pool.submit(e.post,base(pid)+'/events',json=b2)];codes=[f.result().status_code for f in futures]
    assert sorted(codes)==[200,409];s=read(e,pid)['receipts'][0];assert (s['on_hand'],s['in_custody'],s['reserved']) in ((7,3,0),(9,1,0))


def test_count_protects_remaining_holds_and_partial_movement_stales_count(clients):
    e,r,pid,comp,eid,reservation,stocks=setup(clients);count=count_ready(e,r,pid,count_raw(e,pid,stocks[0],eid,10));source=ready(e,r,pid,raw(e,pid,eid,reservation));execute(e,pid,source);count_move(e,pid,count,expected=409)
    preview_count=e.post(base(pid)+'/stock-counts/preview',json={'input':count_raw(e,pid,stocks[0],eid,1)});assert preview_count.status_code==409
    count=count_ready(e,r,pid,count_raw(e,pid,stocks[0],eid,2));count_move(e,pid,count)
    assert read(e,pid)['receipts'][0]['available']==0 and read(e,pid)['receipts'][0]['reserved']==2
    last=ready(e,r,pid,raw(e,pid,eid,reservation,2));execute(e,pid,last);assert read(e,pid)['receipts'][0]['in_custody']==3


def test_stale_source_keeps_unissued_holds_but_allows_evidenced_release(clients):
    e,r,pid,comp,eid,reservation,stocks=setup(clients);source=ready(e,r,pid,raw(e,pid,eid,reservation));execute(e,pid,source)
    upload(e,pid,'合成新取消原因资料，未领余量2根不再交接，不冲销已领1根','release-rest.txt');fresh=get(e,pid)['evidence'][-1]['id']
    assert read(e,pid)['receipts'][0]['reserved']==2 and read(e,pid)['reservations'][0]['issue']
    preview(e,pid,raw(e,pid,fresh,reservation),409);release_left(e,pid,reservation,fresh)
    assert read(e,pid)['receipts'][0]['on_hand']==9 and read(e,pid)['receipts'][0]['reserved']==0


@pytest.mark.parametrize('stage',['preview','submit','review','execute'])
def test_original_bytes_gate_each_stage_preserves_partial_holds(clients,stage):
    e,r,pid,comp,eid,reservation,stocks=setup(clients);v=raw(e,pid,eid,reservation);b=submission(e,pid,v);row=None
    if stage in ('review','execute'):row=submit(e,pid,b)
    if stage=='execute':approve(r,pid,'stock_operations',row);row=get(e,pid)['stock_operations'][0]
    doc=get(e,pid)['documents'][-1];path=store.ROOT/'originals'/doc['hash'];saved=path.read_bytes();path.write_bytes(b'changed synthetic bytes')
    try:
        if stage=='preview':preview(e,pid,v,409)
        elif stage=='submit':submit(e,pid,b,409)
        elif stage=='review':approve(r,pid,'stock_operations',row,expected=409)
        else:execute(e,pid,row,409)
        assert read(e,pid)['receipts'][0]['reserved']==3
    finally:path.write_bytes(saved)


def test_preview_binding_permissions_and_idempotent_body(clients):
    e,r,pid,comp,eid,reservation,stocks=setup(clients);b=submission(e,pid,raw(e,pid,eid,reservation));bad=copy.deepcopy(b);bad['input']['selections'][0]['quantity']=2;submit(e,pid,bad,409)
    assert r.post(base(pid)+'/operations',json=b).status_code==403
    row=submit(e,pid,b);bad=copy.deepcopy(b);bad['input']['basis']='changed';submit(e,pid,bad,409)
    other=create(clients['other']);assert clients['other'].post(base(pid)+'/operations/preview',json={'input':b['input']}).status_code==404
    assert e.post(base(other['id'])+'/operations/preview',json={'input':b['input']}).status_code==404
    with store.transaction() as db:db.execute("UPDATE users SET role='reviewer' WHERE username='estimator'")
    approve(e,pid,'stock_operations',row,expected=403)


@pytest.mark.parametrize('decision',['reject','request_more_info'])
def test_negative_review_and_unexecuted_cancel_preserve_original_hold(clients,decision):
    e,r,pid,comp,eid,reservation,stocks=setup(clients);row=submit(e,pid,submission(e,pid,raw(e,pid,eid,reservation)));approve(r,pid,'stock_operations',row,decision);row=get(e,pid)['stock_operations'][0];execute(e,pid,row,409)
    movement(e,pid,eid,row,'cancel');assert read(e,pid)['receipts'][0]['reserved']==3


@pytest.mark.parametrize('fault',['allocation','reservation-link','duplicate','release-values','release-before-issue','issue-after-release'])
def test_corrupt_partial_history_blocks_read_and_export(clients,fault):
    e,r,pid,comp,eid,reservation,stocks=setup(clients);row=ready(e,r,pid,raw(e,pid,eid,reservation));execute(e,pid,row)
    if fault.startswith('release') or fault=='issue-after-release':release_left(e,pid,reservation,eid)
    def corrupt(p):
        if fault=='allocation':p['stock_events'][-1]=inv.seal({**p['stock_events'][-1],'allocations':[]})
        elif fault=='reservation-link':p['stock_events'][-1]=inv.seal({**p['stock_events'][-1],'reservation_id':'other'})
        elif fault=='duplicate':p['stock_events'].append(inv.seal({**p['stock_events'][-1],'id':store.uid()}))
        elif fault=='release-values':p['stock_events'][-1]=inv.seal({**p['stock_events'][-1],'allocations':[]})
        else:p['stock_events'].reverse()
    mutate(pid,corrupt);assert e.get(base(pid)).status_code==409 and e.get(base(pid)+'/export').status_code==409


def test_remaining_release_requires_partial_execution_and_cannot_repeat_or_cross_kind(clients):
    e,r,pid,comp,eid,reservation,stocks=setup(clients);release_left(e,pid,reservation,eid,409)
    source=ready(e,r,pid,raw(e,pid,eid,reservation));execute(e,pid,source);event,b=release_left(e,pid,reservation,eid)
    release_left(e,pid,reservation,eid,409);assert r.post(base(pid)+'/events',json=b).status_code==403
    assert e.post(base(pid)+'/events',json={**b,'basis':'changed'}).status_code==409
    assert len([x for x in read(e,pid)['events'] if x['operation']=='release_remaining'])==1


def test_concurrent_partial_orders_cannot_exceed_original_remaining_after_refresh(clients):
    e,r,pid,comp,eid,reservation,stocks=setup(clients);a=ready(e,r,pid,raw(e,pid,eid,reservation,2));b=ready(e,r,pid,raw(e,pid,eid,reservation,2));token=common(e,pid)['ledger_hash']
    requests=[dict(request_id=store.uid(),ledger_hash=token,object_hash=store.digest(row)) for row in (a,b)]
    with ThreadPoolExecutor(max_workers=2) as pool:codes=list(pool.map(lambda pair:e.post(base(pid)+'/operations/'+pair[0]['id']+'/execute',json=pair[1]).status_code,zip((a,b),requests)))
    assert sorted(codes)==[200,409];execute(e,pid,b if codes[0]==200 else a,409)
    assert read(e,pid)['receipts'][0]['reserved']==1 and read(e,pid)['receipts'][0]['issued']==2


def test_executed_partial_proof_loss_keeps_custody_and_remaining_hold(clients):
    e,r,pid,comp,eid,reservation,stocks=setup(clients)
    upload(e,pid,'合成独立分批交接原件：原预留3根，首批实际交接6m完整根1根，其余2根尚未领出，非真实仓库','partial-handover.txt');fresh=get(e,pid)['evidence'][-1]['id']
    for component in get(e,pid)['components']:approve(r,pid,'components',component)
    source=ready(e,r,pid,raw(e,pid,fresh,reservation));execute(e,pid,source)
    doc=get(e,pid)['documents'][-1];path=store.ROOT/'originals'/doc['hash'];saved=path.read_bytes();path.unlink()
    try:
        v=read(e,pid);s=v['receipts'][0];assert (s['on_hand'],s['reserved'],s['in_custody'],s['available'])==(9,2,1,7)
        assert next(x for x in v['operations'] if x['record']['id']==source['id'])['issue']
        release_left(e,pid,reservation,eid);assert read(e,pid)['receipts'][0]['in_custody']==1
        returned=approved_operation(e,r,pid,raw_body(e,pid,eid,'return',source,[dict(allocation_index=1,quantity=1)]));execute(e,pid,returned)
        assert read(e,pid)['receipts'][0]['on_hand']==10
    finally:path.write_bytes(saved)


def test_partial_requires_preview_hash_and_cannot_claim_another_operation_request(clients):
    e,r,pid,comp,eid,reservation,stocks=setup(clients);b=submission(e,pid,raw(e,pid,eid,reservation));bad=copy.deepcopy(b);bad.pop('preview_hash');submit(e,pid,bad,422)
    bad=copy.deepcopy(b);bad['preview_hash']='old';submit(e,pid,bad,409)
    source=submit(e,pid,b);approve(r,pid,'stock_operations',source);source=get(e,pid)['stock_operations'][0];execute(e,pid,source)
    release_left(e,pid,reservation,eid);changed={**b,**common(e,pid)};changed['request_id']=b['request_id'];submit(e,pid,changed,409)
