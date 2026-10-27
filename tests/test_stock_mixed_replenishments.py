"""Frozen joint unissued releases, cross-ledger reassignment and optional supply sides."""
import copy
from concurrent.futures import ThreadPoolExecutor
import pytest
from backend import store,stock_inventory as inv
from tests.test_stock_mixed_supply import setup as joint_setup,ready as joint_ready,children
from tests.test_stock_inventory import base,read,common,movement as stock_release
from tests.test_stock_operations import issue,execute as op_execute,body as op_body,today
from tests.test_stock_partial_issue import raw as partial_raw,ready as partial_ready,release_left
from tests.test_stock_offcut_reuse import movement as offcut_move
from tests.test_stock_offcut_issues import raw as batch_raw,ready as batch_ready,move as batch_move,release as offcut_release
from tests.test_stock_offcut_returns import raw_return,ready_return,move as return_move
from tests.test_stock_offcut_jobs import raw as job_raw,ready as job_ready,move as job_move,result as job_result
from tests.test_stock_offcut_processing import ready as result_ready,move as result_move
from tests.test_workflow import get,approve,create,upload
from tests.test_polygon_takeoff import post,mutate


def setup(clients,remaining=1):
    e,r,pid,c,eid,stock,lot,v=joint_setup(clients)
    if remaining==2:
        c=copy.deepcopy(c);c['name']='合成联合剩余补料3根需求';c['material_items'][1]['quantity']='3';c=post(e,pid,c).json();approve(r,pid,'components',c)
        d=e.get(base(pid)+'/demands',params={'component_id':c['id']}).json();v.update(source_id=c['id'],demand_hash=d['demand_hash']);v['stock_allocations'][0]['quantity']=2
    parent,sr,orr=joint_ready(e,r,pid,v)
    first=issue(e,r,pid,eid,sr) if remaining==1 else partial_ready(e,r,pid,partial_raw(e,pid,eid,sr))
    if remaining==2:op_execute(e,pid,first);release_left(e,pid,sr,eid)
    offcut_move(e,pid,orr,'release',eid)
    return e,r,pid,c,eid,stock,lot,parent,first


def demand(e,pid,parent,expected=200):
    q=e.get(base(pid)+'/mixed-replenishments/demands',params={'group_id':parent['id']});assert q.status_code==expected,q.text;return q.json()


def raw(e,pid,parent,eid,stock,lot,mode='stock'):
    q=demand(e,pid,parent);key=q['demand']['demands'][0]['key'];n=q['demand']['demands'][0]['quantity']
    return dict(source_id=q['demand']['source_id'],demand_hash=q['demand']['demand_hash'],parent_id=q['parent_id'],parent_hash=q['parent_hash'],releases_hash=q['releases_hash'],stock_allocations=[] if mode=='offcut' else [dict(demand_key=key,receipt_id=stock['id'],quantity=n if mode=='stock' else 1)],offcut_allocations=[] if mode=='stock' else [dict(demand_key=key,lot_key=lot['lot_key'],quantity=n if mode=='offcut' else n-1)],business_date=today(),custodian='合成联合补料拟交接对象',basis='合成核查两侧已释放未领额度，原定尺和每米质量不变，同材质截面且余料质量合格，不补已领或已退，仅软件验证',evidence_id=eid,compatibility_confirmed=True)


def preview(e,pid,v,expected=200):
    q=e.post(base(pid)+'/mixed-replenishments/preview',json={'input':v});assert q.status_code==expected,q.text;return q.json()


def submission(e,pid,v):
    q=preview(e,pid,v);return dict(request_id=store.uid(),ledger_hash=q['ledger_hash'],input=v,preview_hash=q['preview_hash'])


def submit(e,pid,b,expected=200):
    q=e.post(base(pid)+'/mixed-replenishments',json=b);assert q.status_code==expected,q.text;return q.json()


def ready(e,r,pid,v):
    group=submit(e,pid,submission(e,pid,v));stock,offcut=children_optional(e,pid,group)
    for collection,row in (('stock_reservations',stock),('stock_offcut_reservations',offcut)):
        if row:approve(r,pid,collection,row)
    return group,*children_optional(e,pid,group)


def children_optional(e,pid,g):
    p=get(e,pid);return next((r for r in p['stock_reservations'] if r['id']==g['stock_id']),None),next((r for r in p['stock_offcut_reservations'] if r['id']==g['offcut_id']),None)


@pytest.mark.parametrize('mode',['stock','offcut'])
def test_single_remaining_root_demand_can_change_supply_ledger_without_empty_hold_or_fake_review(clients,mode):
    e,r,pid,c,eid,stock,lot,parent,first=setup(clients);before=get(e,pid);v=raw(e,pid,parent,eid,stock,lot,mode);q=preview(e,pid,v);assert get(e,pid)==before
    assert q['result']['totals']['quantity']==1 and q['result']['totals']['length']=='3.574' and q['result']['totals']['mass']=='10.722'
    b=submission(e,pid,v);group=submit(e,pid,b);assert submit(e,pid,b)==group;sr,orr=children_optional(e,pid,group)
    assert bool(sr)==(mode=='stock') and bool(orr)==(mode=='offcut');assert group['stock_id']=='' if mode=='offcut' else group['offcut_id']==''
    if sr:
        assert e.post(base(pid)+'/operations',json=op_body(e,pid,eid,'issue',sr)).status_code==409
        approve(r,pid,'stock_reservations',sr);sr=children_optional(e,pid,group)[0];issue(e,r,pid,eid,sr)
    else:
        assert e.post(base(pid)+'/offcut-issues/preview',json={'input':batch_raw(orr,eid)}).status_code==409
        approve(r,pid,'stock_offcut_reservations',orr);orr=children_optional(e,pid,group)[1];offcut_move(e,pid,orr,expected=409)
        batch=batch_ready(e,r,pid,batch_raw(orr,eid));batch_move(e,pid,batch)
        ret=ready_return(e,r,pid,raw_return(batch,eid));return_move(e,pid,ret)
    s=read(e,pid);assert s['mixed_supply']['records'][-1]['issued_quantity']==1 and not s['mixed_supply']['records'][-1]['issue']
    assert get(e,pid)['stock_mixed_reservations'][0]==parent and get(e,pid)['stock_operations'][2]==first
    for key in ('components','takeoffs','boq','quotes','report_approval','stock_receipts','stock_processing_results'):assert get(e,pid)[key]==before[key]
    demand(e,pid,parent,409);demand(e,pid,group,409)


def test_merge_both_releases_split_supply_two_reviews_and_keep_original_demand_once(clients):
    e,r,pid,c,eid,stock,lot,parent,first=setup(clients,remaining=2);q=demand(e,pid,parent);assert q['demand']['demands'][0]['quantity']==2 and len(q['releases'])==2
    v=raw(e,pid,parent,eid,stock,lot,'both');group=submit(e,pid,submission(e,pid,v));sr,orr=children_optional(e,pid,group)
    approve(r,pid,'stock_reservations',sr);sr,orr=children_optional(e,pid,group)
    assert e.post(base(pid)+'/operations',json=op_body(e,pid,eid,'issue',sr)).status_code==409
    approve(r,pid,'stock_offcut_reservations',orr);sr,orr=children_optional(e,pid,group);issue(e,r,pid,eid,sr)
    approve(r,pid,'stock_offcut_reservations',orr,'reject',expected=409)
    batch=batch_ready(e,r,pid,batch_raw(orr,eid));batch_move(e,pid,batch)
    s=read(e,pid);assert sum(g['issued_quantity'] for g in s['mixed_supply']['records'])==3 and sum(g['held_quantity'] for g in s['mixed_supply']['records'])==0
    assert group['demand']['demands'][0]['quantity']==2 and parent['demand']['demands'][0]['quantity']==3


def test_chain_follows_latest_release_and_does_not_reuse_ancestor_after_actual_issue_or_processing(clients):
    e,r,pid,c,eid,stock,lot,parent,first=setup(clients,remaining=2);child,sr,orr=ready(e,r,pid,raw(e,pid,parent,eid,stock,lot,'stock'))
    assert orr is None;part=partial_ready(e,r,pid,partial_raw(e,pid,eid,sr));op_execute(e,pid,part);release_left(e,pid,sr,eid)
    demand(e,pid,parent,409);grand,gs,go=ready(e,r,pid,raw(e,pid,child,eid,stock,lot,'offcut'));assert gs is None and grand['demand']['demands'][0]['quantity']==1
    batch=batch_ready(e,r,pid,batch_raw(go,eid));batch_move(e,pid,batch);job=job_ready(e,r,pid,job_raw(batch,eid));job_move(e,pid,job);result=result_ready(e,r,pid,job_result(batch,eid,job));result_move(e,pid,result)
    s=read(e,pid);assert sum(g['issued_quantity'] for g in s['mixed_supply']['records'])==3 and s['processing']['lots'][-1]['length']=='1.148'
    demand(e,pid,parent,409);demand(e,pid,child,409);demand(e,pid,grand,409)


@pytest.mark.parametrize('decision',['reject','request_more_info'])
def test_rejected_or_unissued_child_must_release_all_actual_sides_before_retry(clients,decision):
    e,r,pid,c,eid,stock,lot,parent,first=setup(clients,remaining=2);v=raw(e,pid,parent,eid,stock,lot,'both');child=submit(e,pid,submission(e,pid,v));sr,orr=children_optional(e,pid,child)
    approve(r,pid,'stock_offcut_reservations',orr,decision);sr,orr=children_optional(e,pid,child)
    stock_release(e,pid,eid,sr);demand(e,pid,parent,409);assert read(e,pid)['processing']['lots'][0]['reserved']==1
    offcut_move(e,pid,orr,'release',eid);later=submit(e,pid,submission(e,pid,v));assert later['id']!=child['id']


def test_unreleased_counterpart_or_no_actual_parent_issue_does_not_authorize_replenishment(clients):
    e,r,pid,c,eid,stock,lot,v=joint_setup(clients);parent,sr,orr=joint_ready(e,r,pid,v);demand(e,pid,parent,409)
    issue(e,r,pid,eid,sr);demand(e,pid,parent,409);offcut_move(e,pid,orr,'release',eid);assert demand(e,pid,parent)['demand']['demands'][0]['quantity']==1


@pytest.mark.parametrize('fault',[
 lambda v:v.update(parent_hash='old'),lambda v:v.update(releases_hash='old'),lambda v:v.update(demand_hash='old'),lambda v:v.update(parent_id='foreign'),lambda v:v.update(compatibility_confirmed=False),lambda v:v.update(custodian=''),lambda v:v.update(basis=''),lambda v:v.update(business_date=None),lambda v:v.update(business_date='2999-01-01'),lambda v:v.update(extra=1),lambda v:v.update(stock_allocations=[],offcut_allocations=[]),lambda v:v['stock_allocations'][0].update(quantity=2),lambda v:v['stock_allocations'][0].update(quantity=True),lambda v:v['stock_allocations'][0].update(quantity=1.5),lambda v:v['stock_allocations'][0].update(lot_key='wrong-ledger'),lambda v:v['stock_allocations'][0].update(demand_key='foreign')
])
def test_invalid_or_stale_replenishment_never_creates_another_hold(clients,fault):
    e,r,pid,c,eid,stock,lot,parent,first=setup(clients);v=raw(e,pid,parent,eid,stock,lot);fault(v);q=e.post(base(pid)+'/mixed-replenishments/preview',json={'input':v});assert q.status_code in (404,409,422),q.text
    assert len(get(e,pid)['stock_mixed_reservations'])==1 and read(e,pid)['receipts'][-1]['reserved']==0


def test_concurrency_and_exact_retry_never_allocate_the_same_released_joint_quota_twice(clients):
    e,r,pid,c,eid,stock,lot,parent,first=setup(clients);a=submission(e,pid,raw(e,pid,parent,eid,stock,lot));b={**copy.deepcopy(a),'request_id':store.uid()}
    with ThreadPoolExecutor(max_workers=2) as pool:codes=list(pool.map(lambda b:e.post(base(pid)+'/mixed-replenishments',json=b).status_code,[a,b]))
    assert sorted(codes)==[200,409];winner=a if codes[0]==200 else b;assert submit(e,pid,winner)['id']==get(e,pid)['stock_mixed_reservations'][-1]['id']
    assert read(e,pid)['receipts'][-1]['reserved']==1


def test_changed_original_source_blocks_new_supply_without_releasing_saved_child(clients):
    e,r,pid,c,eid,stock,lot,parent,first=setup(clients);child,sr,orr=ready(e,r,pid,raw(e,pid,parent,eid,stock,lot))
    revised=copy.deepcopy(next(c for c in get(e,pid)['components'] if c['id']==parent['source_id']));revised['object_hash']=store.digest(revised);revised['name']='合成修订后的范围';q=e.put('/api/v1/projects/'+pid+'/components/'+revised['id'],json=revised);assert q.status_code==200,q.text
    assert e.post(base(pid)+'/operations',json=op_body(e,pid,eid,'issue',sr)).status_code==409
    assert read(e,pid)['receipts'][-1]['reserved']==1;stock_release(e,pid,eid,sr);demand(e,pid,parent,409)


def test_roles_scope_preview_identity_and_author_cannot_review_after_role_change(clients):
    e,r,pid,c,eid,stock,lot,parent,first=setup(clients);b=submission(e,pid,raw(e,pid,parent,eid,stock,lot));bad=copy.deepcopy(b);bad['input']['basis']='changed';submit(e,pid,bad,409)
    assert r.post(base(pid)+'/mixed-replenishments',json=b).status_code==403
    assert clients['other'].get(base(pid)+'/mixed-replenishments/demands',params={'group_id':parent['id']}).status_code==404
    other=create(clients['other']);assert e.get(base(other['id'])+'/mixed-replenishments/demands',params={'group_id':parent['id']}).status_code==404
    child=submit(e,pid,b);sr,orr=children_optional(e,pid,child)
    with store.transaction() as db:db.execute("UPDATE users SET role='reviewer' WHERE username='estimator'")
    approve(e,pid,'stock_reservations',sr,expected=403)


@pytest.mark.parametrize('stage',['preview','submit','review','issue'])
def test_lost_release_bytes_block_actions_without_reversing_old_facts_or_new_holds(clients,stage):
    e,r,pid,c,eid,stock,lot,parent,first=setup(clients);v=raw(e,pid,parent,eid,stock,lot);b=submission(e,pid,v);child=sr=None
    if stage in ('review','issue'):child=submit(e,pid,b);sr,orr=children_optional(e,pid,child)
    if stage=='issue':approve(r,pid,'stock_reservations',sr);sr=children_optional(e,pid,child)[0]
    doc=get(e,pid)['documents'][-1];path=store.ROOT/'originals'/doc['hash'];saved=path.read_bytes();path.write_bytes(b'changed release original')
    try:
        if stage=='preview':preview(e,pid,v,409)
        elif stage=='submit':submit(e,pid,b,409)
        elif stage=='review':approve(r,pid,'stock_reservations',sr,expected=409)
        else:assert e.post(base(pid)+'/operations',json=op_body(e,pid,eid,'issue',sr)).status_code==409
        s=read(e,pid);assert s['receipts'][-1]['in_custody']==1 and s['receipts'][-1]['reserved']==(1 if child else 0)
    finally:path.write_bytes(saved)


@pytest.mark.parametrize('fault',['parent','cycle','release','fork','demand','input','before-release'])
def test_invalid_joint_entitlement_chain_blocks_reads_and_new_exports(clients,fault):
    e,r,pid,c,eid,stock,lot,parent,first=setup(clients);child,sr,orr=ready(e,r,pid,raw(e,pid,parent,eid,stock,lot))
    if fault=='before-release':issue(e,r,pid,eid,sr)
    def corrupt(p):
        g=p['stock_mixed_reservations'][-1]
        if fault=='parent':g['replenishment']['parent_id']='missing'
        elif fault=='cycle':g['replenishment']['parent_id']=g['id']
        elif fault=='release':g['replenishment']['releases'][0]['event_hash']='wrong'
        elif fault=='fork':
            duplicate=copy.deepcopy(g);duplicate['id']=store.uid();p['stock_mixed_reservations'].append(inv.seal(duplicate))
        elif fault=='demand':g['demand']['demands'][0]['quantity']=2
        elif fault=='input':g['input']['releases_hash']='wrong'
        else:
            rid=g['replenishment']['releases'][0]['event_id'];ev=next(e for e in p['stock_events'] if e['id']==rid);p['stock_events'].remove(ev);p['stock_events'].append(ev)
        if fault!='before-release':p['stock_mixed_reservations'][-1]=inv.seal(g if fault!='fork' else p['stock_mixed_reservations'][-1])
    mutate(pid,corrupt);assert e.get(base(pid)).status_code in (404,409) and e.get(base(pid)+'/export').status_code in (404,409)


def test_fully_issued_other_length_does_not_reappear_in_replenishment_demands(clients):
    e,r,pid,c,eid,stock,lot,v=joint_setup(clients)
    c=copy.deepcopy(c);extra=copy.deepcopy(c['material_items'][1]);extra['name']='合成6m另一需求';extra['cutting_plan']['stock_length']='6000';c['material_items'].append(extra);c['name']='合成多定尺联合补料';c=post(e,pid,c).json();approve(r,pid,'components',c)
    d=e.get(base(pid)+'/demands',params={'component_id':c['id']}).json();v.update(source_id=c['id'],demand_hash=d['demand_hash']);v['stock_allocations'].append(dict(demand_key=d['demands'][1]['key'],receipt_id=get(e,pid)['stock_receipts'][0]['id'],quantity=1))
    parent,sr,orr=joint_ready(e,r,pid,v);part=partial_ready(e,r,pid,partial_raw(e,pid,eid,sr,index=2));op_execute(e,pid,part);release_left(e,pid,sr,eid);offcut_move(e,pid,orr,'release',eid)
    q=demand(e,pid,parent);assert [(d['stock_length'],d['quantity']) for d in q['demand']['demands']]==[('3.574',2)]
    child,cs,co=ready(e,r,pid,raw(e,pid,parent,eid,stock,lot,'stock'));assert co is None;issue(e,r,pid,eid,cs)
    assert sum(g['issued_quantity'] for g in read(e,pid)['mixed_supply']['records'])==3


def test_lost_separate_ancestor_release_file_preserves_facts_and_quarantines_descendant_offcuts(clients):
    e,r,pid,c,eid,stock,lot,v=joint_setup(clients);parent,sr,orr=joint_ready(e,r,pid,v);issue(e,r,pid,eid,sr)
    upload(e,pid,'合成联合上游单独未领释放依据，仅软件验证','joint-release-only.txt');release_eid=get(e,pid)['evidence'][-1]['id']
    for component in get(e,pid)['components']:approve(r,pid,'components',component)
    offcut_move(e,pid,orr,'release',release_eid);child,cs,co=ready(e,r,pid,raw(e,pid,parent,eid,stock,lot,'offcut'))
    batch=batch_ready(e,r,pid,batch_raw(co,eid));batch_move(e,pid,batch);job=job_ready(e,r,pid,job_raw(batch,eid));job_move(e,pid,job);result=result_ready(e,r,pid,job_result(batch,eid,job));result_move(e,pid,result)
    before=read(e,pid);doc=next(d for d in get(e,pid)['documents'] if d['name']=='joint-release-only.txt');path=store.ROOT/'originals'/doc['hash'];saved=path.read_bytes();path.write_bytes(b'changed joint release original')
    try:
        s=read(e,pid);assert s['processing']['lots'][0]['verified'] and not s['processing']['lots'][1]['verified']
        for old,new in zip(before['processing']['lots'],s['processing']['lots']):
            for k in ('quantity','in_stock_quantity','in_custody','processed_quantity','reserved'):assert old[k]==new[k]
        assert '待核查' in s['mixed_supply']['records'][-1]['issue']
    finally:path.write_bytes(saved)
    assert read(e,pid)['processing']['lots'][1]['verified']
