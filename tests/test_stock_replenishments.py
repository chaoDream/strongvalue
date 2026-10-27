"""Synthetic reallocation of frozen released never-issued full-bar demand, not extra demand."""
import copy
from concurrent.futures import ThreadPoolExecutor
import pytest
from backend import store,stock_inventory as inv
from tests.test_stock_partial_issue import raw as issue_raw,ready as partial_ready,release_left
from tests.test_stock_operations import setup as original_setup,body as operation_body,approved_operation,execute,issue
from tests.test_stock_inventory import receipt,read,base,common,movement,reservation_body,submit as ordinary_reserve
from tests.test_workflow import get,approve,upload,create
from tests.test_polygon_takeoff import mutate


def setup(clients,mode='single'):
    e,r,pid,comp,eid,parent,stocks=original_setup(clients,mode)
    first=partial_ready(e,r,pid,issue_raw(e,pid,eid,parent));execute(e,pid,first);event,_=release_left(e,pid,parent,eid)
    new=[]
    for i,s in enumerate(stocks):new.append(receipt(e,r,pid,eid,length=s['normalized']['stock_length'],unit='m',quantity='10',lot='SYN-REPLACEMENT-'+str(i+1))[0])
    return e,r,pid,comp,eid,parent,stocks,new,first,event


def demand(e,pid,parent,expected=200):
    q=e.get(base(pid)+'/replenishments/demands',params={'reservation_id':parent['id']});assert q.status_code==expected,q.text;return q.json()


def raw(e,pid,parent,eid,stocks):
    q=demand(e,pid,parent);return dict(**{k:q[k] for k in ('parent_id','parent_hash','release_id','release_hash')},demand_hash=q['demand']['demand_hash'],allocations=[dict(demand_key=d['key'],receipt_id=stocks[i]['id'],quantity=d['quantity']) for i,d in enumerate(q['demand']['demands'])],basis='合成核查已释放未领额度，改由当前已审同材质截面定尺及每米质量批次供应，不补已领已退或加工额度；非真实仓库',evidence_id=eid,compatibility_confirmed=True)


def preview(e,pid,v,expected=200):
    q=e.post(base(pid)+'/replenishments/preview',json={'input':v});assert q.status_code==expected,q.text;return q.json()


def submission(e,pid,v):
    q=preview(e,pid,v);return dict(request_id=store.uid(),ledger_hash=q['ledger_hash'],input=v,preview_hash=q['preview_hash'])


def submit(e,pid,b,expected=200):
    q=e.post(base(pid)+'/replenishments',json=b);assert q.status_code==expected,q.text;return q.json()


def ready(e,r,pid,v):
    row=submit(e,pid,submission(e,pid,v));approve(r,pid,'stock_reservations',row);return next(x for x in get(e,pid)['stock_reservations'] if x['id']==row['id'])


def test_only_released_never_issued_demand_is_reserved_then_issued_without_changing_root(clients):
    e,r,pid,comp,eid,parent,stocks,new,first,event=setup(clients);before=get(e,pid);v=raw(e,pid,parent,eid,new);q=preview(e,pid,v);assert get(e,pid)==before
    assert q['result']['demand']['demands'][0]['quantity']==2 and q['result']['supply']['totals']['length']=='12' and q['result']['supply']['totals']['mass']=='36'
    assert q['result']['supply']['allocations'][0]['total_length']=='12' and q['result']['supply']['allocations'][0]['mass']=='36'
    b=submission(e,pid,v);row=submit(e,pid,b);assert submit(e,pid,b)==row
    s=read(e,pid);assert s['receipts'][0]['reserved']==0 and s['receipts'][1]['reserved']==2 and s['receipts'][0]['in_custody']==1
    assert not s['replenishments']['candidates'][0]['eligible'];demand(e,pid,parent,409)
    assert e.post(base(pid)+'/operations',json=operation_body(e,pid,eid,'issue',row)).status_code==409
    approve(r,pid,'stock_reservations',row);row=get(e,pid)['stock_reservations'][-1];source=issue(e,r,pid,eid,row)
    s=read(e,pid);assert s['receipts'][0]['issued']==1 and s['receipts'][1]['issued']==2 and s['receipts'][1]['reserved']==0
    assert s['reservations'][0]['record']==parent and s['reservations'][0]['phase']=='remaining_released'
    assert get(e,pid)['stock_operations'][0]==before['stock_operations'][0]
    for k in ('components','takeoffs','boq','quotes','report_approval','stock_receipts'):assert get(e,pid)[k]==before[k]
    ordinary_reserve(e,pid,reservation_body(e,pid,comp,eid,new),409)
    # A real return is not an authorization to reissue the released entitlement again.
    returned=approved_operation(e,r,pid,operation_body(e,pid,eid,'return',source,[dict(allocation_index=1,quantity=2)]));execute(e,pid,returned)
    demand(e,pid,parent,409);demand(e,pid,row,409)


def test_chain_follows_latest_released_remaining_and_never_reuses_ancestor_quota(clients):
    e,r,pid,comp,eid,parent,stocks,new,first,event=setup(clients);child=ready(e,r,pid,raw(e,pid,parent,eid,new))
    part=partial_ready(e,r,pid,issue_raw(e,pid,eid,child));execute(e,pid,part);release_left(e,pid,child,eid)
    demand(e,pid,parent,409);v=raw(e,pid,child,eid,new);assert v['allocations'][0]['quantity']==1
    grand=ready(e,r,pid,v);issue(e,r,pid,eid,grand)
    s=read(e,pid);assert sum(r['issued'] for r in s['receipts'])==3 and sum(r['reserved'] for r in s['receipts'])==0
    assert grand['replenishment']['parent_id']==child['id']
    demand(e,pid,parent,409);demand(e,pid,child,409)


def test_child_processing_and_actual_offcut_keep_new_receipt_and_issue_identity(clients):
    from tests.test_stock_processing_results import raw as actual_raw,ready as actual_ready,execute as actual_execute
    e,r,pid,comp,eid,parent,stocks,new,first,event=setup(clients)
    child=ready(e,r,pid,raw(e,pid,parent,eid,new));source=issue(e,r,pid,eid,child)
    consumed=approved_operation(e,r,pid,operation_body(e,pid,eid,'consume',source,[dict(allocation_index=1,quantity=1)]));execute(e,pid,consumed)
    result=actual_ready(e,r,pid,actual_raw(consumed,eid));actual_execute(e,pid,result)
    s=read(e,pid);lot=s['processing']['lots'][0]
    assert (s['receipts'][0]['in_custody'],s['receipts'][1]['in_custody'],s['receipts'][1]['consumed'])==(1,1,1)
    assert lot['length']=='3.574' and lot['in_stock_quantity']==1 and result['rows'][0]['receipt_id']==new[0]['id']
    assert consumed['input']['reference_id']==source['id'] and source['reservation_id']==child['id']
    assert s['reservations'][0]['record']==parent
    demand(e,pid,parent,409);demand(e,pid,child,409)


def test_unused_child_can_be_released_and_replaced_without_fork_or_stock_facts(clients):
    e,r,pid,comp,eid,parent,stocks,new,first,event=setup(clients);row=ready(e,r,pid,raw(e,pid,parent,eid,new));movement(e,pid,eid,row)
    assert read(e,pid)['receipts'][1]['reserved']==0
    later=ready(e,r,pid,raw(e,pid,parent,eid,new));assert later['id']!=row['id'] and later['demand']['demands'][0]['quantity']==2
    assert read(e,pid)['receipts'][1]['reserved']==2


@pytest.mark.parametrize('decision',['reject','request_more_info'])
def test_negative_review_keeps_new_hold_and_requires_explicit_release_before_redo(clients,decision):
    e,r,pid,comp,eid,parent,stocks,new,first,event=setup(clients);v=raw(e,pid,parent,eid,new);row=submit(e,pid,submission(e,pid,v));approve(r,pid,'stock_reservations',row,decision);row=get(e,pid)['stock_reservations'][-1]
    assert read(e,pid)['receipts'][1]['reserved']==2;demand(e,pid,parent,409)
    movement(e,pid,eid,row);assert demand(e,pid,parent)['demand']['demands'][0]['quantity']==2


def test_limited_multi_length_demand_uses_only_each_released_amount_and_can_split_new_batches(clients):
    e,r,pid,comp,eid,parent,stocks,new,first,event=setup(clients,'limited');q=demand(e,pid,parent)
    assert [d['quantity'] for d in q['demand']['demands']]==[1,2,2]
    v=raw(e,pid,parent,eid,new);extra,_=receipt(e,r,pid,eid,length=new[1]['normalized']['stock_length'],unit='m',quantity='1')
    original=v['allocations'][1];v['allocations'][1]={**original,'quantity':1};v['allocations'].append({**original,'receipt_id':extra['id'],'quantity':1})
    row=ready(e,r,pid,v);source=issue(e,r,pid,eid,row);assert source['totals']['quantity']==5
    assert sum(r['issued'] for r in read(e,pid)['receipts'])==6


@pytest.mark.parametrize('fault',[
 lambda v:v.update(compatibility_confirmed=False),lambda v:v.update(basis=''),lambda v:v.update(extra=1),lambda v:v.update(allocations=[]),lambda v:v['allocations'][0].update(quantity=0),lambda v:v['allocations'][0].update(quantity=True),lambda v:v['allocations'][0].update(quantity=1.5),lambda v:v['allocations'][0].update(quantity=3),lambda v:v['allocations'][0].update(quantity=1),lambda v:v['allocations'].append(copy.deepcopy(v['allocations'][0])),lambda v:v['allocations'][0].update(demand_key='other'),
])
def test_invalid_or_inexact_supply_never_holds_any_new_stock(clients,fault):
    e,r,pid,comp,eid,parent,stocks,new,first,event=setup(clients);v=raw(e,pid,parent,eid,new);fault(v);q=e.post(base(pid)+'/replenishments/preview',json={'input':v});assert q.status_code in (409,422),q.text
    assert read(e,pid)['receipts'][1]['reserved']==0


@pytest.mark.parametrize('field',['parent_hash','release_hash','release_id','demand_hash'])
def test_stale_or_foreign_link_is_not_authorization_to_add_demand(clients,field):
    e,r,pid,comp,eid,parent,stocks,new,first,event=setup(clients);v=raw(e,pid,parent,eid,new);v[field]='old';preview(e,pid,v,409)


def test_changed_material_source_stops_new_supply_and_preserves_existing_holds(clients):
    e,r,pid,comp,eid,parent,stocks,new,first,event=setup(clients);v=raw(e,pid,parent,eid,new);row=ready(e,r,pid,v)
    revised=copy.deepcopy(get(e,pid)['components'][-1]);revised['object_hash']=store.digest(revised);revised['name']='合成新修订范围，不沿用旧补料额度';q=e.put('/api/v1/projects/'+pid+'/components/'+revised['id'],json=revised);assert q.status_code==200,q.text
    assert read(e,pid)['receipts'][1]['reserved']==2
    assert e.post(base(pid)+'/operations',json=operation_body(e,pid,eid,'issue',row)).status_code==409
    movement(e,pid,eid,row);demand(e,pid,parent,409)


def test_concurrent_children_cannot_reserve_same_released_entitlement_twice(clients):
    e,r,pid,comp,eid,parent,stocks,new,first,event=setup(clients);a=submission(e,pid,raw(e,pid,parent,eid,new));b={**copy.deepcopy(a),'request_id':store.uid()}
    with ThreadPoolExecutor(max_workers=2) as pool:codes=list(pool.map(lambda body:e.post(base(pid)+'/replenishments',json=body).status_code,[a,b]))
    assert sorted(codes)==[200,409] and read(e,pid)['receipts'][1]['reserved']==2
    loser=b if codes[0]==200 else a;loser.update(common(e,pid));submit(e,pid,loser,409)


def test_no_supplement_for_unreleased_full_return_or_offcut_source(clients):
    e,r,pid,comp,eid,parent,stocks=original_setup(clients);demand(e,pid,parent,409);source=issue(e,r,pid,eid,parent)
    ret=approved_operation(e,r,pid,operation_body(e,pid,eid,'return',source,[dict(allocation_index=1,quantity=3)]));execute(e,pid,ret);demand(e,pid,parent,409)


def test_author_roles_project_scope_preview_binding_and_retry_identity(clients):
    e,r,pid,comp,eid,parent,stocks,new,first,event=setup(clients);b=submission(e,pid,raw(e,pid,parent,eid,new));bad=copy.deepcopy(b);bad['input']['basis']='changed';submit(e,pid,bad,409)
    assert r.post(base(pid)+'/replenishments',json=b).status_code==403
    row=submit(e,pid,b);assert submit(e,pid,b)==row;bad=copy.deepcopy(b);bad['input']['basis']='changed';submit(e,pid,bad,409)
    other=create(clients['other']);assert clients['other'].get(base(pid)+'/replenishments/demands',params={'reservation_id':parent['id']}).status_code==404
    assert e.get(base(other['id'])+'/replenishments/demands',params={'reservation_id':parent['id']}).status_code==404
    with store.transaction() as db:db.execute("UPDATE users SET role='reviewer' WHERE username='estimator'")
    approve(e,pid,'stock_reservations',row,expected=403)


@pytest.mark.parametrize('stage',['preview','submit','review','issue'])
def test_lost_release_bytes_block_new_actions_but_do_not_restore_old_holds(clients,stage):
    e,r,pid,comp,eid,parent,stocks,new,first,event=setup(clients);v=raw(e,pid,parent,eid,new);b=submission(e,pid,v);row=None
    if stage in ('review','issue'):row=submit(e,pid,b)
    if stage=='issue':approve(r,pid,'stock_reservations',row);row=get(e,pid)['stock_reservations'][-1]
    doc=get(e,pid)['documents'][-1];path=store.ROOT/'originals'/doc['hash'];saved=path.read_bytes();path.write_bytes(b'changed entitlement original')
    try:
        if stage=='preview':preview(e,pid,v,409)
        elif stage=='submit':submit(e,pid,b,409)
        elif stage=='review':approve(r,pid,'stock_reservations',row,expected=409)
        else:assert e.post(base(pid)+'/operations',json=operation_body(e,pid,eid,'issue',row)).status_code==409
        s=read(e,pid);assert s['receipts'][0]['in_custody']==1 and s['receipts'][0]['reserved']==0
        assert s['receipts'][1]['reserved']==(2 if row else 0)
    finally:path.write_bytes(saved)


@pytest.mark.parametrize('fault',['demand','parent','release','cycle','fork','before-release'])
def test_corrupted_entitlement_chain_blocks_read_and_export(clients,fault):
    e,r,pid,comp,eid,parent,stocks,new,first,event=setup(clients);row=ready(e,r,pid,raw(e,pid,parent,eid,new))
    if fault=='before-release':issue(e,r,pid,eid,row)
    def corrupt(p):
        child=p['stock_reservations'][-1]
        if fault=='demand':child['demand']['demands'][0]['quantity']=3
        elif fault=='parent':child['replenishment']['parent_id']='missing'
        elif fault=='release':child['replenishment']['release_hash']='wrong'
        elif fault=='cycle':child['replenishment']['parent_id']=child['id']
        elif fault=='fork':
            second=copy.deepcopy(child);second['id']=store.uid();p['stock_reservations'].append(inv.seal(second))
        else:
            released=next(x for x in p['stock_events'] if x['id']==event['id']);p['stock_events'].remove(released);p['stock_events'].append(released)
        if fault!='before-release':p['stock_reservations'][-1]=inv.seal(child if fault!='fork' else p['stock_reservations'][-1])
    mutate(pid,corrupt);assert e.get(base(pid)).status_code in (404,409) and e.get(base(pid)+'/export').status_code in (404,409)
