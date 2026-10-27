"""Exact joint full-bar/offcut supply with atomic holds and separately reviewed custody."""
import copy
from concurrent.futures import ThreadPoolExecutor
import pytest
from backend import store,stock_inventory as inv
from tests.test_stock_offcut_reuse import setup as offcut_setup,value as ordinary_offcut,movement as offcut_move
from tests.test_stock_inventory import base,read,common,receipt,reservation_body,submit as ordinary_stock,movement as stock_release
from tests.test_stock_operations import body as op_body,approved_operation,execute as op_execute,issue,today
from tests.test_stock_offcut_issues import raw as batch_raw,ready as batch_ready,move as batch_move,release as offcut_release_remaining
from tests.test_stock_partial_issue import raw as partial_raw,ready as partial_ready,release_left
from tests.test_stock_offcut_returns import raw_return,ready_return,move as return_move
from tests.test_stock_offcut_jobs import raw as job_raw,ready as job_ready,move as job_move,result as job_result
from tests.test_stock_offcut_processing import ready as result_ready,move as result_move
from tests.test_workflow import get,approve,create,upload
from tests.test_polygon_takeoff import post,mutate


def setup(clients):
    e,r,pid,comp,c,eid,recovery,lot,stocks=offcut_setup(clients)
    v=copy.deepcopy(c);v['name']='合成原材余料联合供应材料表';v['material_items'][1]['quantity']='2';c=post(e,pid,v).json();approve(r,pid,'components',c)
    stock,_=receipt(e,r,pid,eid,length='3574',quantity='5',lot='SYN-JOINT-STOCK')
    d=e.get(base(pid)+'/demands',params={'component_id':c['id']}).json();assert d['demands'][0]['quantity']==2
    raw=dict(source_id=c['id'],demand_hash=d['demand_hash'],stock_allocations=[dict(demand_key=d['demands'][0]['key'],receipt_id=stock['id'],quantity=1)],offcut_allocations=[dict(demand_key=d['demands'][0]['key'],lot_key=lot['lot_key'],quantity=1)],business_date=today(),custodian='合成联合供应拟接收人',basis='合成核定同材质截面、余料质量合格、3574mm及3kg每米，原材1加余料1恰好覆盖原需求2，非真实仓库',evidence_id=eid,compatibility_confirmed=True)
    return e,r,pid,c,eid,stock,lot,raw


def preview(e,pid,v,expected=200):
    q=e.post(base(pid)+'/mixed-supply/preview',json={'input':v});assert q.status_code==expected,q.text;return q.json()


def submission(e,pid,v):
    q=preview(e,pid,v);return dict(**common(e,pid),input=v,preview_hash=q['preview_hash'])


def submit(e,pid,b,expected=200):
    q=e.post(base(pid)+'/mixed-supply',json=b);assert q.status_code==expected,q.text;return q.json()


def children(e,pid,group):
    p=get(e,pid);return next(r for r in p['stock_reservations'] if r['id']==group['stock_id']),next(r for r in p['stock_offcut_reservations'] if r['id']==group['offcut_id'])


def ready(e,r,pid,v):
    group=submit(e,pid,submission(e,pid,v));stock,offcut=children(e,pid,group);approve(r,pid,'stock_reservations',stock);approve(r,pid,'stock_offcut_reservations',offcut);return group,*children(e,pid,group)


def test_preview_atomic_holds_two_reviews_then_separate_actual_issues_keep_full_demand_and_engineering(clients):
    e,r,pid,c,eid,stock,lot,v=setup(clients);before=get(e,pid);q=preview(e,pid,v);assert get(e,pid)==before
    assert q['result']['totals']['quantity']==2 and q['result']['totals']['length']=='7.148' and q['result']['totals']['mass']=='21.444'
    b=submission(e,pid,v);group=submit(e,pid,b);assert submit(e,pid,b)==group;sr,orr=children(e,pid,group)
    s=read(e,pid);assert s['receipts'][-1]['reserved']==1 and s['processing']['lots'][0]['reserved']==1
    approve(r,pid,'stock_reservations',sr);sr,orr=children(e,pid,group)
    assert e.post(base(pid)+'/operations',json=op_body(e,pid,eid,'issue',sr)).status_code==409
    assert e.post(base(pid)+'/offcut-issues/preview',json={'input':batch_raw(orr,eid)}).status_code==409
    approve(r,pid,'stock_offcut_reservations',orr);sr,orr=children(e,pid,group)
    source=issue(e,r,pid,eid,sr);approve(r,pid,'stock_offcut_reservations',orr,'reject',expected=409)
    offcut_move(e,pid,orr,expected=409);batch=batch_ready(e,r,pid,batch_raw(orr,eid));batch_move(e,pid,batch)
    s=read(e,pid);assert s['receipts'][-1]['in_custody']==1 and s['processing']['lots'][0]['in_custody']==1
    assert s['mixed_supply']['records'][0]['issued_quantity']==2 and s['mixed_supply']['records'][0]['held_quantity']==0
    assert not s['offcut_reuse']['reservations'][-1]['issue']
    for k in ('components','takeoffs','boq','quotes','report_approval','stock_receipts','stock_processing_results'):assert get(e,pid)[k]==before[k]
    assert get(e,pid)['stock_operations'][0]==before['stock_operations'][0]
    # Returns credit only their own physical ledgers; the original full demand stays closed.
    ret=approved_operation(e,r,pid,op_body(e,pid,eid,'return',source,[dict(allocation_index=1,quantity=1)]));op_execute(e,pid,ret)
    ret=ready_return(e,r,pid,raw_return(batch,eid));return_move(e,pid,ret);preview(e,pid,v,409)
    ordinary_stock(e,pid,reservation_body(e,pid,c,eid,[stock]),409)
    assert e.post(base(pid)+'/offcut-reuse/preview',json={'input':ordinary_offcut(e,pid,c,eid,lot)}).status_code==409


@pytest.mark.parametrize('decision',['reject','request_more_info'])
def test_negative_review_keeps_both_holds_until_each_side_explicitly_released(clients,decision):
    e,r,pid,c,eid,stock,lot,v=setup(clients);group=submit(e,pid,submission(e,pid,v));sr,orr=children(e,pid,group)
    approve(r,pid,'stock_offcut_reservations',orr,decision);sr,orr=children(e,pid,group)
    s=read(e,pid);assert s['receipts'][-1]['reserved']==s['processing']['lots'][0]['reserved']==1
    stock_release(e,pid,eid,sr);s=read(e,pid);assert s['receipts'][-1]['reserved']==0 and s['processing']['lots'][0]['reserved']==1
    preview(e,pid,v,409);offcut_move(e,pid,orr,'release',eid);replacement=submit(e,pid,submission(e,pid,v));assert replacement['id']!=group['id']


def test_executed_one_side_can_continue_other_but_cannot_reopen_or_review_either(clients):
    e,r,pid,c,eid,stock,lot,v=setup(clients);group,sr,orr=ready(e,r,pid,v)
    batch=batch_ready(e,r,pid,batch_raw(orr,eid));batch_move(e,pid,batch)
    approve(r,pid,'stock_reservations',sr,'reject',expected=409);source=issue(e,r,pid,eid,sr)
    job=job_ready(e,r,pid,job_raw(batch,eid));job_move(e,pid,job);result=result_ready(e,r,pid,job_result(batch,eid,job));result_move(e,pid,result)
    consumed=approved_operation(e,r,pid,op_body(e,pid,eid,'consume',source,[dict(allocation_index=1,quantity=1)]));op_execute(e,pid,consumed)
    s=read(e,pid);assert s['receipts'][-1]['consumed']==1 and s['processing']['lots'][0]['processed_quantity']==1
    assert s['processing']['lots'][1]['length']=='1.148';preview(e,pid,v,409)


@pytest.mark.parametrize('fault',[
 lambda v:v.update(compatibility_confirmed=False),lambda v:v.update(basis=''),lambda v:v.update(custodian=''),lambda v:v.update(business_date=None),lambda v:v.update(business_date='2999-01-01'),lambda v:v.update(extra=1),lambda v:v.update(stock_allocations=[]),lambda v:v.update(offcut_allocations=[]),lambda v:v['stock_allocations'][0].update(quantity=True),lambda v:v['offcut_allocations'][0].update(quantity=0),lambda v:v['stock_allocations'][0].update(quantity=1.5),lambda v:v['stock_allocations'][0].update(quantity=2),lambda v:v['offcut_allocations'][0].update(quantity=2),lambda v:v['stock_allocations'].append(copy.deepcopy(v['stock_allocations'][0])),lambda v:v['offcut_allocations'][0].update(receipt_id='wrong-ledger'),lambda v:v['stock_allocations'][0].update(demand_key='foreign'),lambda v:v['offcut_allocations'][0].update(lot_key='foreign'),lambda v:v.update(demand_hash='stale')
])
def test_invalid_inputs_or_partial_coverage_never_create_either_hold(clients,fault):
    e,r,pid,c,eid,stock,lot,v=setup(clients);fault(v);q=e.post(base(pid)+'/mixed-supply/preview',json={'input':v});assert q.status_code in (404,409,422),q.text
    s=read(e,pid);assert not s['mixed_supply']['records'] and s['receipts'][-1]['reserved']==0 and s['processing']['lots'][0]['reserved']==0


def test_failed_offcut_availability_rolls_back_fullbar_side_and_concurrent_save_only_once(clients):
    e,r,pid,c,eid,stock,lot,v=setup(clients);a=submission(e,pid,v);b={**copy.deepcopy(a),'request_id':store.uid()}
    with ThreadPoolExecutor(max_workers=2) as pool:codes=list(pool.map(lambda b:e.post(base(pid)+'/mixed-supply',json=b).status_code,[a,b]))
    assert sorted(codes)==[200,409];p=get(e,pid);assert len(p['stock_mixed_reservations'])==1
    assert len([x for x in p['stock_reservations'] if 'mixed_supply' in x])==len([x for x in p['stock_offcut_reservations'] if 'mixed_supply' in x])==1
    loser=b if codes[0]==200 else a;loser.update(common(e,pid));submit(e,pid,loser,409)
    s=read(e,pid);assert s['receipts'][-1]['reserved']==s['processing']['lots'][0]['reserved']==1


def test_preview_and_request_binding_project_roles_and_no_author_self_review(clients):
    e,r,pid,c,eid,stock,lot,v=setup(clients);b=submission(e,pid,v);bad=copy.deepcopy(b);bad['input']['basis']='changed';submit(e,pid,bad,409)
    assert r.post(base(pid)+'/mixed-supply',json=b).status_code==403
    other=create(clients['other']);assert clients['other'].post(base(pid)+'/mixed-supply/preview',json={'input':v}).status_code==404
    assert e.post(base(other['id'])+'/mixed-supply/preview',json={'input':v}).status_code==404
    group=submit(e,pid,b);bad=copy.deepcopy(b);bad['input']['basis']='changed';submit(e,pid,bad,409)
    sr,orr=children(e,pid,group)
    with store.transaction() as db:db.execute("UPDATE users SET role='reviewer' WHERE username='estimator'")
    approve(e,pid,'stock_reservations',sr,expected=403);approve(e,pid,'stock_offcut_reservations',orr,expected=403)


def test_changed_original_plan_blocks_both_sides_without_freeing_holds(clients):
    e,r,pid,c,eid,stock,lot,v=setup(clients);group,sr,orr=ready(e,r,pid,v)
    revised=copy.deepcopy(next(x for x in get(e,pid)['components'] if x['id']==c['id']));revised['object_hash']=store.digest(revised);revised['name']='合成修订后的范围';q=e.put('/api/v1/projects/'+pid+'/components/'+c['id'],json=revised);assert q.status_code==200,q.text
    assert e.post(base(pid)+'/operations',json=op_body(e,pid,eid,'issue',sr)).status_code==409
    assert e.post(base(pid)+'/offcut-issues/preview',json={'input':batch_raw(orr,eid)}).status_code==409
    s=read(e,pid);assert s['receipts'][-1]['reserved']==s['processing']['lots'][0]['reserved']==1
    stock_release(e,pid,eid,sr);offcut_move(e,pid,orr,'release',eid)


@pytest.mark.parametrize('stage',['submit','review','issue'])
def test_lost_compatibility_bytes_never_undo_existing_holds(clients,stage):
    e,r,pid,c,eid,stock,lot,v=setup(clients);b=submission(e,pid,v);group=sr=orr=None
    if stage!='submit':group=submit(e,pid,b);sr,orr=children(e,pid,group)
    if stage=='issue':approve(r,pid,'stock_reservations',sr);approve(r,pid,'stock_offcut_reservations',orr);sr,orr=children(e,pid,group)
    doc=get(e,pid)['documents'][-1];path=store.ROOT/'originals'/doc['hash'];saved=path.read_bytes();path.write_bytes(b'changed compatibility original')
    try:
        if stage=='submit':submit(e,pid,b,409)
        elif stage=='review':approve(r,pid,'stock_reservations',sr,expected=409)
        else:assert e.post(base(pid)+'/offcut-issues/preview',json={'input':batch_raw(orr,eid)}).status_code==409
        s=read(e,pid);assert s['receipts'][-1]['reserved']==s['processing']['lots'][0]['reserved']==(1 if group else 0)
    finally:path.write_bytes(saved)


@pytest.mark.parametrize('fault',['group','child','partition','demand','preview','fork','whole'])
def test_corrupted_joint_links_or_fake_whole_offcut_handover_block_read_and_export(clients,fault):
    e,r,pid,c,eid,stock,lot,v=setup(clients);group,sr,orr=ready(e,r,pid,v)
    if fault=='whole':batch=batch_ready(e,r,pid,batch_raw(orr,eid));batch_move(e,pid,batch)
    def corrupt(p):
        group=p['stock_mixed_reservations'][0];child=next(x for x in p['stock_reservations'] if x['id']==group['stock_id'])
        if fault=='group':child['mixed_supply']['group_id']='missing'
        elif fault=='child':group['stock_id']='missing'
        elif fault=='partition':child['mixed_supply']['partition']='offcut'
        elif fault=='demand':child['demand']['demands'][0]['quantity']=2
        elif fault=='preview':group['preview_hash']='wrong'
        elif fault=='fork':
            duplicate=copy.deepcopy(child);duplicate['id']=store.uid();p['stock_reservations'].append(inv.seal(duplicate))
        else:
            ev=p['stock_events'][-1];ev.update(operation='offcut_issue',target_id=orr['id'],record_hash=store.digest(orr),allocations=orr['allocations'],totals=orr['totals']);p['stock_events'][-1]=inv.seal(ev)
        p['stock_reservations'][next(i for i,x in enumerate(p['stock_reservations']) if x['id']==child['id'])]=inv.seal(child);p['stock_mixed_reservations'][0]=inv.seal(group)
    mutate(pid,corrupt);assert e.get(base(pid)).status_code in (404,409) and e.get(base(pid)+'/export').status_code in (404,409)


def test_unavailable_offcut_does_not_leave_a_half_saved_stock_hold(clients):
    from tests.test_stock_offcut_reuse import approved as reserve_offcut
    e,r,pid,c,eid,stock,lot,v=setup(clients);b=submission(e,pid,v)
    other=copy.deepcopy(c);other['name']='合成另一份已核定余料需求';other['material_items'][1]['quantity']='1';other=post(e,pid,other).json();approve(r,pid,'components',other)
    reserve_offcut(e,r,pid,ordinary_offcut(e,pid,other,eid,lot));b.update(common(e,pid));submit(e,pid,b,409)
    s=read(e,pid);assert not s['mixed_supply']['records'] and s['receipts'][-1]['reserved']==0 and s['processing']['lots'][0]['reserved']==1
    assert not any('mixed_supply' in x for x in get(e,pid)['stock_reservations'])


def test_multiple_demands_can_split_across_ledgers_without_borrowing_a_different_length(clients):
    e,r,pid,c,eid,stock,lot,v=setup(clients)
    c=copy.deepcopy(c);extra=copy.deepcopy(c['material_items'][1]);extra['name']='合成另一项6m定尺';extra['cutting_plan']['stock_length']='6000';c['material_items'].append(extra);c['name']='合成不同需求两账联合供应';c=post(e,pid,c).json();approve(r,pid,'components',c)
    d=e.get(base(pid)+'/demands',params={'component_id':c['id']}).json();assert [a['stock_length'] for a in d['demands']]==['3.574','6']
    v.update(source_id=c['id'],demand_hash=d['demand_hash']);v['stock_allocations'].append(dict(demand_key=d['demands'][1]['key'],receipt_id=get(e,pid)['stock_receipts'][0]['id'],quantity=d['demands'][1]['quantity']))
    group,sr,orr=ready(e,r,pid,v);assert len(sr['demand']['demands'])==2 and len(orr['demand']['demands'])==1
    assert group['totals']['quantity']==3 and group['totals']['length']=='13.148'
    issue(e,r,pid,eid,sr);batch=batch_ready(e,r,pid,batch_raw(orr,eid));batch_move(e,pid,batch)
    assert read(e,pid)['mixed_supply']['records'][0]['issued_quantity']==3


def test_partial_release_only_frees_its_ledger_and_never_enables_single_side_replenishment(clients):
    e,r,pid,c,eid,stock,lot,v=setup(clients);c=copy.deepcopy(c);c['name']='合成联合预留3根';c['material_items'][1]['quantity']='3';c=post(e,pid,c).json();approve(r,pid,'components',c)
    d=e.get(base(pid)+'/demands',params={'component_id':c['id']}).json();v.update(source_id=c['id'],demand_hash=d['demand_hash']);v['stock_allocations'][0]['quantity']=2
    group,sr,orr=ready(e,r,pid,v);first=partial_ready(e,r,pid,partial_raw(e,pid,eid,sr));op_execute(e,pid,first);release_left(e,pid,sr,eid)
    s=read(e,pid);assert s['receipts'][-1]['reserved']==0 and s['processing']['lots'][0]['reserved']==1
    assert e.get(base(pid)+'/replenishments/demands',params={'reservation_id':sr['id']}).status_code==409
    batch=batch_ready(e,r,pid,batch_raw(orr,eid));batch_move(e,pid,batch)
    s=read(e,pid);assert s['mixed_supply']['records'][0]['issued_quantity']==2 and s['mixed_supply']['records'][0]['held_quantity']==0
    assert s['receipts'][-1]['in_custody']==1 and s['processing']['lots'][0]['in_custody']==1;preview(e,pid,v,409)


def test_lost_joint_proof_retains_both_ledger_facts_and_isolates_derived_offcuts(clients):
    e,r,pid,c,eid,stock,lot,v=setup(clients)
    upload(e,pid,'合成两账供应单独兼容性核定原件，仅软件验证','joint-proof-only.txt');proof=get(e,pid)['evidence'][-1]['id']
    for component in get(e,pid)['components']:approve(r,pid,'components',component)
    v['evidence_id']=proof;group,sr,orr=ready(e,r,pid,v);batch=batch_ready(e,r,pid,batch_raw(orr,eid));batch_move(e,pid,batch)
    job=job_ready(e,r,pid,job_raw(batch,eid));job_move(e,pid,job);result=result_ready(e,r,pid,job_result(batch,eid,job));result_move(e,pid,result)
    before=read(e,pid);doc=next(d for d in get(e,pid)['documents'] if d['name']=='joint-proof-only.txt');path=store.ROOT/'originals'/doc['hash'];saved=path.read_bytes();path.write_bytes(b'changed joint allocation evidence')
    try:
        s=read(e,pid);assert s['processing']['lots'][0]['verified'] and not s['processing']['lots'][1]['verified']
        assert s['receipts'][-1]['reserved']==1
        for old,new in zip(before['processing']['lots'],s['processing']['lots']):
            for k in ('quantity','in_stock_quantity','in_custody','processed_quantity','reserved'):assert old[k]==new[k]
        assert e.post(base(pid)+'/operations',json=op_body(e,pid,eid,'issue',sr)).status_code==409
    finally:path.write_bytes(saved)
    assert read(e,pid)['processing']['lots'][1]['verified']
