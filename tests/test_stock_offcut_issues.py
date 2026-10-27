"""Synthetic independently reviewed partial offcut handovers and batch-specific custody."""
import copy
from concurrent.futures import ThreadPoolExecutor
import pytest
from backend import store,stock_inventory as inv
from tests.test_stock_processing_results import setup as raw_setup,raw as raw_result,ready as raw_ready,execute as recover
from tests.test_stock_offcut_reuse import target,value,approved,movement as reuse_move
from tests.test_stock_offcut_returns import raw_return,ready_return,move as return_move
from tests.test_stock_offcut_jobs import raw as job_raw,ready as job_ready,move as job_move,result as job_result
from tests.test_stock_offcut_processing import raw as process_raw,ready as process_ready,move as process_move
from tests.test_stock_inventory import read,common,base
from tests.test_workflow import get,approve,upload,create
from tests.test_polygon_takeoff import post,mutate
from tests.test_stock_operations import today


def setup(clients,split=False):
    e,r,pid,comp,eid,consumed,stocks=raw_setup(clients,count=2)
    v=raw_result(consumed,eid);row=v['rows'][0];row.update(quantity=2,finished_length='4.8',kerf_length='.012',trim_length='.04');row['offcuts'][0]['quantity']=2
    if split:
        row['offcuts'][0]['quantity']=1;other=copy.deepcopy(row['offcuts'][0]);other['lot_code']='SYN-OFFCUT-B';row['offcuts'].append(other)
    recovery=raw_ready(e,r,pid,v);recover(e,pid,recovery)
    c=target(e,r,pid,comp);v=copy.deepcopy(c);v['material_items'][1]['quantity']='2';c=post(e,pid,v).json();approve(r,pid,'components',c)
    lots=read(e,pid)['processing']['lots'];v=value(e,pid,c,eid,lots[0])
    if split:v['allocations']=[dict(demand_key=v['allocations'][0]['demand_key'],lot_key=l['lot_key'],quantity=1) for l in lots]
    reservation=approved(e,r,pid,v)
    return e,r,pid,comp,c,eid,recovery,lots,reservation


def raw(reservation,eid,quantity=1,index=1):return dict(reservation_id=reservation['id'],reservation_hash=store.digest(reservation),selections=[dict(allocation_index=index,quantity=quantity)],business_date=today(),custodian='合成余料分批接收人A',basis='合成同组原段本批实际交接，按原预留未领余额，未领继续占用；非真实仓库',evidence_id=eid,confirmed=True)


def preview(e,pid,v,expected=200):
    q=e.post(base(pid)+'/offcut-issues/preview',json={'input':v});assert q.status_code==expected,q.text;return q.json()


def submission(e,pid,v):
    q=preview(e,pid,v);return dict(request_id=store.uid(),ledger_hash=q['ledger_hash'],input=v,preview_hash=q['preview_hash'])


def submit(e,pid,b,expected=200):
    q=e.post(base(pid)+'/offcut-issues',json=b);assert q.status_code==expected,q.text;return q.json()


def ready(e,r,pid,v):
    row=submit(e,pid,submission(e,pid,v));approve(r,pid,'stock_offcut_issues',row);return next(x for x in get(e,pid)['stock_offcut_issues'] if x['id']==row['id'])


def move(e,pid,row,kind='execute',eid=None,expected=200,b=None):
    b=b or dict(**common(e,pid),object_hash=store.digest(row),**(dict(basis='合成取消未执行余料批单，不回滚原预留或实物',evidence_id=eid) if kind=='cancel' else {}))
    q=e.post(base(pid)+'/offcut-issues/'+row['id']+'/'+kind,json=b);assert q.status_code==expected,q.text;return q.json(),b


def release(e,pid,reservation,eid,expected=200,b=None):
    b=b or dict(**common(e,pid),object_hash=store.digest(reservation),basis='合成核查未领原段不再交接，仅释放原未领占用',evidence_id=eid)
    q=e.post(base(pid)+'/offcut-reuse/'+reservation['id']+'/release-remaining',json=b);assert q.status_code==expected,q.text;return q.json(),b


def test_preview_review_partial_and_finish_preserve_root_history_and_hold_only_remainder(clients):
    e,r,pid,comp,c,eid,recovery,lots,res=setup(clients);before=get(e,pid);q=preview(e,pid,raw(res,eid));assert get(e,pid)==before
    assert q['result']['totals']['length']=='3.574' and q['result']['totals']['mass']=='10.722'
    b=submission(e,pid,raw(res,eid));row=submit(e,pid,b);assert submit(e,pid,b)==row
    assert read(e,pid)['processing']['lots'][0]['reserved']==2
    move(e,pid,row,expected=409);approve(r,pid,'stock_offcut_issues',row);row=get(e,pid)['stock_offcut_issues'][0]
    assert read(e,pid)['processing']['lots'][0]['in_stock_quantity']==2
    frozen=e.get(base(pid)+'/export');event,b=move(e,pid,row);assert move(e,pid,row,b=b)[0]==event
    v=read(e,pid);lot=v['processing']['lots'][0];reservation=v['offcut_reuse']['reservations'][0]
    assert (lot['in_stock_quantity'],lot['reserved'],lot['adopted_quantity'],lot['in_custody'])==(1,1,0,1)
    assert reservation['phase']=='partially_issued' and reservation['issued_quantity']==1 and reservation['unissued_quantity']==1
    reuse_move(e,pid,res,expected=409);reuse_move(e,pid,res,'release',eid,409);approve(r,pid,'stock_offcut_reservations',res,expected=409)
    last=ready(e,r,pid,raw(res,eid));move(e,pid,last)
    v=read(e,pid);lot=v['processing']['lots'][0];assert (lot['in_stock_quantity'],lot['reserved'],lot['in_custody'])==(0,0,2)
    assert v['offcut_reuse']['reservations'][0]['phase']=='issued'
    assert e.post(base(pid)+'/offcut-returns/preview',json={'input':raw_return(res,eid)}).status_code==409
    release(e,pid,res,eid,409)
    for k in ('components','takeoffs','boq','quotes','report_approval','stock_receipts','stock_operations','stock_processing_results','stock_offcut_reservations'):assert get(e,pid)[k]==before[k]
    artifact=next(a for a in e.get('/api/v1/projects/'+pid+'/artifacts').json() if a['purpose']=='stock_inventory');assert e.get('/api/v1/projects/'+pid+'/artifacts/'+artifact['id']+'/file').content==frozen.content


def test_release_unissued_remainder_preserves_batch_custody_and_disallows_reopening_original(clients):
    e,r,pid,comp,c,eid,recovery,lots,res=setup(clients);release(e,pid,res,eid,409);batch=ready(e,r,pid,raw(res,eid));move(e,pid,batch)
    pending=ready(e,r,pid,raw(res,eid));event,b=release(e,pid,res,eid);assert release(e,pid,res,eid,b=b)[0]==event
    assert event['allocations'][0]['quantity']==1
    v=read(e,pid);lot=v['processing']['lots'][0];assert (lot['in_stock_quantity'],lot['reserved'],lot['adopted_quantity'],lot['in_custody'])==(1,0,1,1)
    assert v['offcut_reuse']['reservations'][0]['phase']=='remaining_released'
    move(e,pid,pending,expected=409);preview(e,pid,raw(res,eid),409);release(e,pid,res,eid,409)
    ret=ready_return(e,r,pid,raw_return(batch,eid));return_move(e,pid,ret)
    assert read(e,pid)['processing']['lots'][0]['in_stock_quantity']==2
    assert e.post(base(pid)+'/offcut-reuse/preview',json={'input':value(e,pid,c,eid,lots[0])}).status_code==409


def test_each_batch_has_independent_return_job_and_result_quota(clients):
    e,r,pid,comp,c,eid,recovery,lots,res=setup(clients);a=ready(e,r,pid,raw(res,eid));move(e,pid,a);b=ready(e,r,pid,raw(res,eid));move(e,pid,b)
    assert e.post(base(pid)+'/offcut-returns/preview',json={'input':raw_return(a,eid,2)}).status_code==409
    returned=ready_return(e,r,pid,raw_return(a,eid));return_move(e,pid,returned)
    job=job_ready(e,r,pid,job_raw(b,eid));job_move(e,pid,job);result=process_ready(e,r,pid,job_result(b,eid,job));process_move(e,pid,result)
    v=read(e,pid);root,child=v['processing']['lots'];assert (root['in_stock_quantity'],root['processing_quantity'],root['processed_quantity'],root['returned_quantity'])==(1,0,1,1)
    assert child['length']=='1.148' and child['adopted_quantity']==1
    batches={x['record']['id']:x for x in v['offcut_issues']['records']};assert batches[a['id']]['balances'][0]['returned']==1 and batches[b['id']]['balances'][0]['consumed']==1


def test_nonfirst_original_index_kept_through_batch_job_result_and_direct_processing(clients):
    e,r,pid,comp,c,eid,recovery,lots,res=setup(clients,split=True);batch=ready(e,r,pid,raw(res,eid,index=2));move(e,pid,batch)
    v=raw_return(batch,eid);assert e.post(base(pid)+'/offcut-returns/preview',json={'input':v}).status_code==422;v['selections'][0]['allocation_index']=2
    ret=ready_return(e,r,pid,v);return_move(e,pid,ret)
    # A fresh batch of allocation1 can be processed directly; allocation2 is not borrowed.
    other=ready(e,r,pid,raw(res,eid,index=1));move(e,pid,other);record=process_ready(e,r,pid,process_raw(other,eid));process_move(e,pid,record)
    assert read(e,pid)['offcut_issues']['records'][0]['balances'][0]['allocation_index']==2


@pytest.mark.parametrize('fault',[
 lambda v:v.update(selections=[]),lambda v:v.update(selections={}),lambda v:v['selections'][0].update(quantity=0),lambda v:v['selections'][0].update(quantity=-1),lambda v:v['selections'][0].update(quantity=True),lambda v:v['selections'][0].update(quantity=1.5),lambda v:v['selections'][0].update(quantity=1000001),lambda v:v['selections'][0].update(allocation_index=True),lambda v:v['selections'][0].update(allocation_index=2),lambda v:v['selections'][0].update(lot_key='foreign'),lambda v:v['selections'].append(copy.deepcopy(v['selections'][0])),lambda v:v.update(confirmed=False),lambda v:v.update(custodian=''),lambda v:v.update(business_date=None),lambda v:v.update(business_date='2999-01-01'),lambda v:v.update(extra=1),
])
def test_invalid_batch_never_changes_held_or_stock(clients,fault):
    e,r,pid,comp,c,eid,recovery,lots,res=setup(clients);v=raw(res,eid);fault(v);preview(e,pid,v,422);assert read(e,pid)['processing']['lots'][0]['reserved']==2


def test_excess_batch_competing_whole_and_partial_recheck_current_remaining(clients):
    e,r,pid,comp,c,eid,recovery,lots,res=setup(clients);preview(e,pid,raw(res,eid,3),409)
    a=ready(e,r,pid,raw(res,eid,2));b=ready(e,r,pid,raw(res,eid,2));move(e,pid,a);move(e,pid,b,expected=409);reuse_move(e,pid,res,expected=409)
    assert read(e,pid)['processing']['lots'][0]['issued_quantity']==2


def test_simultaneous_batch_and_remaining_release_are_atomic(clients):
    e,r,pid,comp,c,eid,recovery,lots,res=setup(clients);first=ready(e,r,pid,raw(res,eid));move(e,pid,first);batch=ready(e,r,pid,raw(res,eid));ledger=common(e,pid)['ledger_hash']
    b1=dict(request_id=store.uid(),ledger_hash=ledger,object_hash=store.digest(batch));b2=dict(request_id=store.uid(),ledger_hash=ledger,object_hash=store.digest(res),basis='合成并发未领余量释放',evidence_id=eid)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(e.post,base(pid)+'/offcut-issues/'+batch['id']+'/execute',json=b1),pool.submit(e.post,base(pid)+'/offcut-reuse/'+res['id']+'/release-remaining',json=b2)];codes=[f.result().status_code for f in futures]
    assert sorted(codes)==[200,409];lot=read(e,pid)['processing']['lots'][0];assert lot['reserved']==0 and lot['in_custody'] in (1,2)


def test_stale_source_keeps_remaining_hold_and_can_release_with_current_proof(clients):
    e,r,pid,comp,c,eid,recovery,lots,res=setup(clients);batch=ready(e,r,pid,raw(res,eid));move(e,pid,batch)
    upload(e,pid,'合成余料未领1根不再交接，仅释放未领占用，非实际退回','release-left.txt');fresh=get(e,pid)['evidence'][-1]['id']
    preview(e,pid,raw(res,fresh),409);assert read(e,pid)['processing']['lots'][0]['reserved']==1
    release(e,pid,res,fresh);assert read(e,pid)['processing']['lots'][0]['in_custody']==1


@pytest.mark.parametrize('stage',['preview','submit','review','execute'])
def test_actual_original_bytes_gate_at_each_stage(clients,stage):
    e,r,pid,comp,c,eid,recovery,lots,res=setup(clients);v=raw(res,eid);b=submission(e,pid,v);row=None
    if stage in ('review','execute'):row=submit(e,pid,b)
    if stage=='execute':approve(r,pid,'stock_offcut_issues',row);row=get(e,pid)['stock_offcut_issues'][0]
    doc=get(e,pid)['documents'][-1];path=store.ROOT/'originals'/doc['hash'];saved=path.read_bytes();path.write_bytes(b'changed synthetic original')
    try:
        if stage=='preview':preview(e,pid,v,409)
        elif stage=='submit':submit(e,pid,b,409)
        elif stage=='review':approve(r,pid,'stock_offcut_issues',row,expected=409)
        else:move(e,pid,row,expected=409)
        assert read(e,pid)['processing']['lots'][0]['reserved']==2
    finally:path.write_bytes(saved)


@pytest.mark.parametrize('decision',['reject','request_more_info'])
def test_negative_review_and_cancel_leave_original_hold_unchanged(clients,decision):
    e,r,pid,comp,c,eid,recovery,lots,res=setup(clients);row=submit(e,pid,submission(e,pid,raw(res,eid)));approve(r,pid,'stock_offcut_issues',row,decision);row=get(e,pid)['stock_offcut_issues'][0]
    move(e,pid,row,expected=409);event,b=move(e,pid,row,'cancel',eid);assert move(e,pid,row,'cancel',eid,b=b)[0]==event
    assert read(e,pid)['processing']['lots'][0]['reserved']==2


def test_preview_identity_roles_scope_and_author_separation(clients):
    e,r,pid,comp,c,eid,recovery,lots,res=setup(clients);b=submission(e,pid,raw(res,eid));bad=copy.deepcopy(b);bad['input']['selections'][0]['quantity']=2;submit(e,pid,bad,409)
    assert r.post(base(pid)+'/offcut-issues',json=b).status_code==403
    row=submit(e,pid,b);bad=copy.deepcopy(b);bad['input']['basis']='changed';submit(e,pid,bad,409)
    other=create(clients['other']);assert clients['other'].post(base(pid)+'/offcut-issues/preview',json={'input':b['input']}).status_code==404
    assert e.post(base(other['id'])+'/offcut-issues/preview',json={'input':b['input']}).status_code==404
    with store.transaction() as db:db.execute("UPDATE users SET role='reviewer' WHERE username='estimator'")
    approve(e,pid,'stock_offcut_issues',row,expected=403)


@pytest.mark.parametrize('fault',['record','event','reservation-link','duplicate','release-values','release-before-issue','cancel-link'])
def test_invalid_history_blocks_read_and_export(clients,fault):
    e,r,pid,comp,c,eid,recovery,lots,res=setup(clients);row=ready(e,r,pid,raw(res,eid));move(e,pid,row,'cancel',eid) if fault=='cancel-link' else move(e,pid,row)
    if fault.startswith('release'):release(e,pid,res,eid)
    def corrupt(p):
        if fault=='record':p['stock_offcut_issues'][0]['totals']['quantity']=99
        elif fault=='event':p['stock_events'][-1]=inv.seal({**p['stock_events'][-1],'allocations':[]})
        elif fault=='reservation-link':p['stock_events'][-1]=inv.seal({**p['stock_events'][-1],'reservation_id':'other'})
        elif fault=='duplicate':p['stock_events'].append(inv.seal({**p['stock_events'][-1],'id':store.uid()}))
        elif fault=='cancel-link':p['stock_events'][-1]=inv.seal({**p['stock_events'][-1],'target_id':'missing'})
        elif fault=='release-values':p['stock_events'][-1]=inv.seal({**p['stock_events'][-1],'allocations':[]})
        else:
            event=p['stock_events'].pop();p['stock_events'].insert(next(i for i,x in enumerate(p['stock_events']) if x['operation']=='offcut_partial_issue'),event)
    mutate(pid,corrupt);assert e.get(base(pid)).status_code==409 and e.get(base(pid)+'/export').status_code==409


def test_sparse_original_index_survives_job_and_result_and_cannot_borrow_other_root(clients):
    e,r,pid,comp,c,eid,recovery,lots,res=setup(clients,split=True);batch=ready(e,r,pid,raw(res,eid,index=2));move(e,pid,batch)
    v=job_raw(batch,eid);v['selections'][0]['allocation_index']=2;job=job_ready(e,r,pid,v);job_move(e,pid,job)
    result=job_result(batch,eid,job);result['rows'][0]['allocation_index']=2;row=process_ready(e,r,pid,result);process_move(e,pid,row)
    v=read(e,pid);assert v['offcut_issues']['records'][0]['balances'][0]['allocation_index']==2 and v['offcut_issues']['records'][0]['balances'][0]['consumed']==1
    assert v['processing']['lots'][-1]['parent_lot_key']==lots[1]['lot_key']


def test_batch_proof_loss_preserves_quantities_and_holds_but_quarantines_child_results(clients):
    e,r,pid,comp,c,eid,recovery,lots,res=setup(clients)
    upload(e,pid,'合成独立分批交接：原余料3574mm本批1根、3kg/m，其他1根未领继续预留','batch-proof.txt');fresh=get(e,pid)['evidence'][-1]['id']
    for component in get(e,pid)['components']:approve(r,pid,'components',component)
    batch=ready(e,r,pid,raw(res,fresh));move(e,pid,batch);result=process_ready(e,r,pid,process_raw(batch,eid));process_move(e,pid,result)
    doc=get(e,pid)['documents'][-1];path=store.ROOT/'originals'/doc['hash'];saved=path.read_bytes();path.write_bytes(b'changed batch original')
    try:
        v=read(e,pid);root,child=v['processing']['lots'];assert (root['in_stock_quantity'],root['reserved'],root['processed_quantity'])==(1,1,1)
        assert child['adopted_quantity']==0 and child['issue'] and v['offcut_issues']['records'][0]['issue']
        release(e,pid,res,eid);assert read(e,pid)['processing']['lots'][0]['adopted_quantity']==1
    finally:path.write_bytes(saved)
    assert read(e,pid)['processing']['lots'][1]['adopted_quantity']==1


def test_batch_moves_stale_stocktake_and_count_cannot_erase_remaining_hold(clients):
    from tests.test_stock_offcut_counts import raw as count_raw,ready as count_ready,move as count_move
    e,r,pid,comp,c,eid,recovery,lots,res=setup(clients);count=count_ready(e,r,pid,count_raw(e,pid,lots[0],eid,2));batch=ready(e,r,pid,raw(res,eid));move(e,pid,batch);count_move(e,pid,count,expected=409)
    assert e.post(base(pid)+'/offcut-counts/preview',json={'input':count_raw(e,pid,lots[0],eid,0)}).status_code==409


def test_partial_and_legacy_whole_cannot_both_use_original_reservation(clients):
    e,r,pid,comp,c,eid,recovery,lots,res=setup(clients);row=ready(e,r,pid,raw(res,eid));reuse_move(e,pid,res);move(e,pid,row,expected=409)
    assert read(e,pid)['processing']['lots'][0]['issued_quantity']==2


def test_low_decimal_precision_does_not_round_partial_length_and_mass(clients):
    from decimal import localcontext
    e,r,pid,comp,c,eid,recovery,lots,res=setup(clients)
    with localcontext() as context:
        context.prec=3;row=ready(e,r,pid,raw(res,eid));assert row['totals']['mass']=='10.722' and row['allocations'][0]['total_length']=='3.574';move(e,pid,row)


def test_pending_and_executed_batches_cannot_be_reused_as_previews_or_cancelled_after_execution(clients):
    e,r,pid,comp,c,eid,recovery,lots,res=setup(clients);body=submission(e,pid,raw(res,eid));bad=copy.deepcopy(body);bad.pop('preview_hash');submit(e,pid,bad,422)
    row=submit(e,pid,body);assert e.post(base(pid)+'/offcut-returns/preview',json={'input':raw_return(row,eid)}).status_code==409
    approve(r,pid,'stock_offcut_issues',row);row=get(e,pid)['stock_offcut_issues'][0];old=dict(**common(e,pid),object_hash='old');move(e,pid,row,expected=409,b=old);move(e,pid,row)
    move(e,pid,row,'cancel',eid,409);approve(r,pid,'stock_offcut_issues',row,expected=409)
