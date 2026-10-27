"""Released never-issued offcut demand, distinct child holds and actual batch custody."""
import copy
from concurrent.futures import ThreadPoolExecutor
import pytest
from backend import store,stock_inventory as inv
from tests.test_stock_processing_results import setup as raw_setup,raw as raw_result,ready as raw_ready,execute as recover
from tests.test_stock_offcut_reuse import target,value,approved as reuse_ready,movement as reuse_move
from tests.test_stock_offcut_issues import raw as batch_raw,ready as batch_ready,move as batch_move,release
from tests.test_stock_offcut_returns import raw_return,ready_return,move as return_move
from tests.test_stock_offcut_jobs import raw as job_raw,ready as job_ready,move as job_move,result as job_result
from tests.test_stock_offcut_processing import ready as result_ready,move as result_move
from tests.test_stock_inventory import base,common,read
from tests.test_stock_operations import today
from tests.test_workflow import get,approve,create,upload
from tests.test_polygon_takeoff import post,mutate


def setup(clients,split=False,separate_release=False):
    e,r,pid,comp,eid,consumed,stocks=raw_setup(clients,count=3)
    v=raw_result(consumed,eid);a=v['rows'][0];a.update(quantity=3,finished_length='7.2',kerf_length='.018',trim_length='.06');a['offcuts'][0]['quantity']=3
    if split:
        a['offcuts'][0]['quantity']=2;other=copy.deepcopy(a['offcuts'][0]);other.update(lot_code='SYN-OFFCUT-REPLACEMENT',quantity=1);a['offcuts'].append(other)
    recovery=raw_ready(e,r,pid,v);recover(e,pid,recovery)
    c=target(e,r,pid,comp);v=copy.deepcopy(c);v['material_items'][1]['quantity']='2' if split else '3';c=post(e,pid,v).json();approve(r,pid,'components',c)
    lots=read(e,pid)['processing']['lots'];parent=reuse_ready(e,r,pid,value(e,pid,c,eid,lots[0]))
    first=batch_ready(e,r,pid,batch_raw(parent,eid));batch_move(e,pid,first);release_eid=eid
    if separate_release:
        upload(e,pid,'合成独立未领释放核查凭据，不涉及原实物验收原件；仅软件验证','release-only.txt');release_eid=get(e,pid)['evidence'][-1]['id']
        for component in get(e,pid)['components']:approve(r,pid,'components',component)
    event,_=release(e,pid,parent,release_eid)
    return e,r,pid,comp,c,eid,parent,lots,first,event


def demand(e,pid,parent,expected=200):
    q=e.get(base(pid)+'/offcut-replenishments/demands',params={'reservation_id':parent['id']});assert q.status_code==expected,q.text;return q.json()


def raw(e,pid,parent,eid,lot):
    q=demand(e,pid,parent);return dict(**{k:q[k] for k in ('parent_id','parent_hash','release_id','release_hash')},demand_hash=q['demand']['demand_hash'],allocations=[dict(demand_key=d['key'],lot_key=lot['lot_key'],quantity=d['quantity']) for d in q['demand']['demands']],business_date=today(),custodian='合成补料拟接收班组',basis='合成核定未领原段重新供应，材质截面、质量合格、实测段长和每米质量一致，尚未实际交接，仅软件验证',evidence_id=eid,compatibility_confirmed=True)


def preview(e,pid,v,expected=200):
    q=e.post(base(pid)+'/offcut-replenishments/preview',json={'input':v});assert q.status_code==expected,q.text;return q.json()


def submission(e,pid,v):
    q=preview(e,pid,v);return dict(request_id=store.uid(),ledger_hash=q['ledger_hash'],input=v,preview_hash=q['preview_hash'])


def submit(e,pid,b,expected=200):
    q=e.post(base(pid)+'/offcut-replenishments',json=b);assert q.status_code==expected,q.text;return q.json()


def ready(e,r,pid,v):
    row=submit(e,pid,submission(e,pid,v));approve(r,pid,'stock_offcut_reservations',row);return next(x for x in get(e,pid)['stock_offcut_reservations'] if x['id']==row['id'])


def test_new_lot_preview_save_review_and_batch_issue_preserve_old_custody_and_engineering(clients):
    e,r,pid,comp,c,eid,parent,lots,first,event=setup(clients,split=True);before=get(e,pid);v=raw(e,pid,parent,eid,lots[1]);q=preview(e,pid,v)
    assert get(e,pid)==before and q['result']['totals']['length']=='3.574' and q['result']['totals']['mass']=='10.722'
    b=submission(e,pid,v);row=submit(e,pid,b);assert submit(e,pid,b)==row
    s=read(e,pid);old,new=s['processing']['lots'];assert (old['reserved'],old['in_custody'],new['reserved'],new['in_stock_quantity'])==(0,1,1,1)
    reuse_move(e,pid,row,expected=409);approve(r,pid,'stock_offcut_reservations',row);row=get(e,pid)['stock_offcut_reservations'][-1]
    assert read(e,pid)['processing']['lots'][1]['in_stock_quantity']==1
    reuse_move(e,pid,row,expected=409)  # Supply confirmation cannot stand in for actual handover.
    batch=batch_ready(e,r,pid,batch_raw(row,eid));batch_move(e,pid,batch);s=read(e,pid);assert (s['processing']['lots'][0]['in_custody'],s['processing']['lots'][1]['in_custody'])==(1,1)
    assert s['offcut_reuse']['reservations'][-1]['issue']==''
    ret=ready_return(e,r,pid,raw_return(batch,eid));return_move(e,pid,ret)
    demand(e,pid,parent,409);demand(e,pid,row,409)
    assert e.post(base(pid)+'/offcut-reuse/preview',json={'input':value(e,pid,c,eid,lots[1])}).status_code==409
    for k in ('components','takeoffs','boq','quotes','report_approval','stock_receipts','stock_reservations','stock_operations','stock_processing_results'):assert get(e,pid)[k]==before[k]
    assert get(e,pid)['stock_offcut_reservations'][0]==parent and get(e,pid)['stock_offcut_issues'][0]==first


def test_chain_partial_remaining_release_cannot_fork_or_refund_processed_or_returned_quota(clients):
    e,r,pid,comp,c,eid,parent,lots,first,event=setup(clients);child=ready(e,r,pid,raw(e,pid,parent,eid,lots[0]))
    batch=batch_ready(e,r,pid,batch_raw(child,eid));batch_move(e,pid,batch);release(e,pid,child,eid)
    demand(e,pid,parent,409);v=raw(e,pid,child,eid,lots[0]);assert v['allocations'][0]['quantity']==1
    grand=ready(e,r,pid,v);last=batch_ready(e,r,pid,batch_raw(grand,eid));batch_move(e,pid,last)
    s=read(e,pid);assert (s['processing']['lots'][0]['in_stock_quantity'],s['processing']['lots'][0]['in_custody'],s['processing']['lots'][0]['reserved'])==(0,3,0)
    job=job_ready(e,r,pid,job_raw(last,eid));job_move(e,pid,job);result=result_ready(e,r,pid,job_result(last,eid,job));result_move(e,pid,result)
    s=read(e,pid);assert s['processing']['lots'][0]['processed_quantity']==1 and s['processing']['lots'][1]['length']=='1.148'
    demand(e,pid,parent,409);demand(e,pid,child,409);demand(e,pid,grand,409)


def test_unissued_child_release_allows_fresh_reservation_but_rejection_does_not(clients):
    e,r,pid,comp,c,eid,parent,lots,first,event=setup(clients);v=raw(e,pid,parent,eid,lots[0]);row=submit(e,pid,submission(e,pid,v))
    approve(r,pid,'stock_offcut_reservations',row,'reject');row=get(e,pid)['stock_offcut_reservations'][-1]
    assert read(e,pid)['processing']['lots'][0]['reserved']==2;demand(e,pid,parent,409)
    reuse_move(e,pid,row,'release',eid);replacement=ready(e,r,pid,raw(e,pid,parent,eid,lots[0]));assert replacement['id']!=row['id']
    assert read(e,pid)['processing']['lots'][0]['reserved']==2


@pytest.mark.parametrize('fault',[
 lambda v:v.update(compatibility_confirmed=False),lambda v:v.update(basis=''),lambda v:v.update(custodian=''),lambda v:v.update(business_date=None),lambda v:v.update(business_date='2999-01-01'),lambda v:v.update(business_date='2026-1-1'),lambda v:v.update(extra=True),lambda v:v.update(allocations=[]),lambda v:v['allocations'][0].update(quantity=0),lambda v:v['allocations'][0].update(quantity=True),lambda v:v['allocations'][0].update(quantity=1.5),lambda v:v['allocations'][0].update(quantity=3),lambda v:v['allocations'][0].update(quantity=1),lambda v:v['allocations'].append(copy.deepcopy(v['allocations'][0])),lambda v:v['allocations'][0].update(lot_key='foreign'),lambda v:v['allocations'][0].update(demand_key='foreign'),lambda v:v['allocations'][0].update(receipt_id='full-bar')
])
def test_invalid_or_inexact_supply_never_creates_hold(clients,fault):
    e,r,pid,comp,c,eid,parent,lots,first,event=setup(clients);v=raw(e,pid,parent,eid,lots[0]);fault(v);q=e.post(base(pid)+'/offcut-replenishments/preview',json={'input':v});assert q.status_code in (409,422),q.text
    assert read(e,pid)['processing']['lots'][0]['reserved']==0


@pytest.mark.parametrize('field',['parent_hash','release_hash','release_id','demand_hash'])
def test_stale_entitlement_snapshot_is_not_authority_for_new_hold(clients,field):
    e,r,pid,comp,c,eid,parent,lots,first,event=setup(clients);v=raw(e,pid,parent,eid,lots[0]);v[field]='stale';preview(e,pid,v,409)


def test_changed_source_blocks_new_actions_and_preserves_held_and_actual_quantities(clients):
    e,r,pid,comp,c,eid,parent,lots,first,event=setup(clients);row=ready(e,r,pid,raw(e,pid,parent,eid,lots[0]))
    c=copy.deepcopy(get(e,pid)['components'][-1]);c['object_hash']=store.digest(c);c['name']='合成修订范围';q=e.put('/api/v1/projects/'+pid+'/components/'+c['id'],json=c);assert q.status_code==200,q.text
    preview(e,pid,raw_for_saved(row),409)
    assert read(e,pid)['processing']['lots'][0]['reserved']==2
    reuse_move(e,pid,row,expected=409);reuse_move(e,pid,row,'release',eid);demand(e,pid,parent,409)


def raw_for_saved(row):return copy.deepcopy(row['input'])


def test_concurrency_global_versions_and_exact_retries_hold_only_once(clients):
    e,r,pid,comp,c,eid,parent,lots,first,event=setup(clients);a=submission(e,pid,raw(e,pid,parent,eid,lots[0]));b={**copy.deepcopy(a),'request_id':store.uid()}
    with ThreadPoolExecutor(max_workers=2) as pool:codes=list(pool.map(lambda body:e.post(base(pid)+'/offcut-replenishments',json=body).status_code,[a,b]))
    assert sorted(codes)==[200,409] and read(e,pid)['processing']['lots'][0]['reserved']==2
    winner=a if codes[0]==200 else b;assert submit(e,pid,winner)['id']==get(e,pid)['stock_offcut_reservations'][-1]['id']
    bad=copy.deepcopy(winner);bad['input']['basis']='changed';submit(e,pid,bad,409)


def test_roles_tenant_scope_preview_binding_and_author_role_change(clients):
    e,r,pid,comp,c,eid,parent,lots,first,event=setup(clients);b=submission(e,pid,raw(e,pid,parent,eid,lots[0]));bad=copy.deepcopy(b);bad['input']['basis']='changed';submit(e,pid,bad,409)
    assert r.post(base(pid)+'/offcut-replenishments',json=b).status_code==403
    other=create(clients['other']);assert clients['other'].get(base(pid)+'/offcut-replenishments/demands',params={'reservation_id':parent['id']}).status_code==404
    assert e.get(base(other['id'])+'/offcut-replenishments/demands',params={'reservation_id':parent['id']}).status_code==404
    row=submit(e,pid,b)
    with store.transaction() as db:db.execute("UPDATE users SET role='reviewer' WHERE username='estimator'")
    approve(e,pid,'stock_offcut_reservations',row,expected=403)


@pytest.mark.parametrize('stage',['preview','submit','review','issue'])
def test_changed_release_original_bytes_block_new_actions_without_undoing_facts(clients,stage):
    e,r,pid,comp,c,eid,parent,lots,first,event=setup(clients);v=raw(e,pid,parent,eid,lots[0]);b=submission(e,pid,v);row=None
    if stage in ('review','issue'):row=submit(e,pid,b)
    if stage=='issue':
        approve(r,pid,'stock_offcut_reservations',row);row=get(e,pid)['stock_offcut_reservations'][-1];batch=batch_ready(e,r,pid,batch_raw(row,eid))
    doc=get(e,pid)['documents'][-1];path=store.ROOT/'originals'/doc['hash'];saved=path.read_bytes();path.write_bytes(b'changed release original')
    try:
        if stage=='preview':preview(e,pid,v,409)
        elif stage=='submit':submit(e,pid,b,409)
        elif stage=='review':approve(r,pid,'stock_offcut_reservations',row,expected=409)
        else:batch_move(e,pid,batch,expected=409)
        lot=read(e,pid)['processing']['lots'][0];assert lot['in_custody']==1 and lot['reserved']==(2 if row else 0)
    finally:path.write_bytes(saved)


@pytest.mark.parametrize('fault',['demand','parent','release','cycle','fork','before-release'])
def test_corrupt_link_or_reordered_issue_blocks_reads_and_export(clients,fault):
    e,r,pid,comp,c,eid,parent,lots,first,event=setup(clients);row=ready(e,r,pid,raw(e,pid,parent,eid,lots[0]))
    if fault=='before-release':
        batch=batch_ready(e,r,pid,batch_raw(row,eid));batch_move(e,pid,batch)
    def corrupt(p):
        child=p['stock_offcut_reservations'][-1]
        if fault=='demand':child['demand']['demands'][0]['quantity']+=1
        elif fault=='parent':child['replenishment']['parent_id']='missing'
        elif fault=='release':child['replenishment']['release_hash']='wrong'
        elif fault=='cycle':child['replenishment']['parent_id']=child['id']
        elif fault=='fork':
            duplicate=copy.deepcopy(child);duplicate['id']=store.uid();p['stock_offcut_reservations'].append(inv.seal(duplicate))
        else:
            ev=next(x for x in p['stock_events'] if x['id']==event['id']);p['stock_events'].remove(ev);p['stock_events'].append(ev)
        if fault!='before-release':p['stock_offcut_reservations'][-1]=inv.seal(child if fault!='fork' else p['stock_offcut_reservations'][-1])
    mutate(pid,corrupt);assert e.get(base(pid)).status_code in (404,409) and e.get(base(pid)+'/export').status_code in (404,409)


def test_lost_ancestor_release_proof_preserves_facts_and_isolates_derived_child_offcuts(clients):
    e,r,pid,comp,c,eid,parent,lots,first,event=setup(clients,separate_release=True)
    child=ready(e,r,pid,raw(e,pid,parent,eid,lots[0]));batch=batch_ready(e,r,pid,batch_raw(child,eid));batch_move(e,pid,batch);release(e,pid,child,eid)
    job=job_ready(e,r,pid,job_raw(batch,eid));job_move(e,pid,job);result=result_ready(e,r,pid,job_result(batch,eid,job));result_move(e,pid,result)
    before=read(e,pid);doc=next(d for d in get(e,pid)['documents'] if d['name']=='release-only.txt');path=store.ROOT/'originals'/doc['hash'];saved=path.read_bytes();path.write_bytes(b'missing upstream release proof')
    try:
        s=read(e,pid);assert s['processing']['lots'][0]['verified'] and not s['processing']['lots'][1]['verified']
        for old,new in zip(before['processing']['lots'],s['processing']['lots']):
            for k in ('quantity','in_stock_quantity','in_custody','processing_quantity','processed_quantity','reserved'):assert old[k]==new[k]
        assert '待核查' in s['offcut_reuse']['reservations'][-1]['issue'];demand(e,pid,child,409)
    finally:path.write_bytes(saved)
    assert read(e,pid)['processing']['lots'][1]['verified']


def test_forged_legacy_whole_event_cannot_turn_supply_confirmation_into_actual_handover(clients):
    e,r,pid,comp,c,eid,parent,lots,first,event=setup(clients);child=ready(e,r,pid,raw(e,pid,parent,eid,lots[0]))
    batch=batch_ready(e,r,pid,batch_raw(child,eid,quantity=2));batch_move(e,pid,batch)
    def corrupt(p):
        ev=p['stock_events'][-1]
        ev.update(operation='offcut_issue',target_id=child['id'],record_hash=store.digest(child),allocations=child['allocations'],totals=child['totals'])
        p['stock_events'][-1]=inv.seal(ev)
    mutate(pid,corrupt);assert e.get(base(pid)).status_code==409 and e.get(base(pid)+'/export').status_code==409
