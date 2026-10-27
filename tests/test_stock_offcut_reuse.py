"""Synthetic accepted-offcut reuse: exact stock compatibility and immutable movement ledger."""
import copy
import hashlib
from concurrent.futures import ThreadPoolExecutor
import pytest
from backend import store, stock_inventory as inv
from tests.test_stock_processing_results import setup as processing_setup, raw, ready, execute as recover, stop
from tests.test_stock_inventory import read, common, base, demand, receipt, reservation_body, submit as reserve, movement as stock_movement
from tests.test_workflow import get, approve, create, upload
from tests.test_polygon_takeoff import post, mutate
from tests.test_stock_operations import today


def target(e,r,pid,comp,*,name='合成余料定尺回用材料表',length='3574',weight='3000'):
    v=copy.deepcopy(comp);v['name']=name;v['material_items'][1]['quantity']='1';v['material_items'][1]['cutting_plan']['stock_length']=length;v['material_items'][1]['unit_weight']=weight
    row=post(e,pid,v).json();approve(r,pid,'components',row);return row


def setup(clients):
    e,r,pid,comp,eid,consumed,stocks=processing_setup(clients)
    row=ready(e,r,pid,raw(consumed,eid));recover(e,pid,row)
    component=target(e,r,pid,comp);lot=read(e,pid)['processing']['lots'][0]
    return e,r,pid,comp,component,eid,row,lot,stocks


def value(e,pid,component,eid,lot):
    d=demand(e,pid,component)
    return dict(source_id=component['id'],demand_hash=d['demand_hash'],allocations=[dict(demand_key=x['key'],lot_key=lot['lot_key'],quantity=x['quantity']) for x in d['demands']],basis='合成实物同材质同截面、质量合格、实测定尺相等，核查未重复占用及实际交接，非真实工程验收',evidence_id=eid,confirmed=True,custodian='合成领用人A',business_date=today())


def preview(e,pid,v,expected=200):
    response=e.post(base(pid)+'/offcut-reuse/preview',json={'input':v});assert response.status_code==expected,response.text;return response.json()


def submission(e,pid,v):
    p=preview(e,pid,v);return dict(request_id=store.uid(),ledger_hash=p['ledger_hash'],input=v,preview_hash=p['preview_hash'])


def submit(e,pid,b,expected=200):
    response=e.post(base(pid)+'/offcut-reuse',json=b);assert response.status_code==expected,response.text;return response.json()


def movement(e,pid,row,kind='execute',eid=None,expected=200,b=None):
    b=b or dict(**common(e,pid),object_hash=store.digest(row),**(dict(basis='合成核查后释放未领用余料，非实际退库',evidence_id=eid) if kind=='release' else {}))
    response=e.post(base(pid)+'/offcut-reuse/'+row['id']+'/'+kind,json=b);assert response.status_code==expected,response.text;return response.json(),b


def approved(e,r,pid,v):
    row=submit(e,pid,submission(e,pid,v));approve(r,pid,'stock_offcut_reservations',row);return next(x for x in get(e,pid)['stock_offcut_reservations'] if x['id']==row['id'])


def test_preview_hold_review_issue_and_archive_preserve_engineering_and_original_stock(clients):
    e,r,pid,comp,c,eid,recovery,lot,stocks=setup(clients);before=get(e,pid);old=read(e,pid)
    v=value(e,pid,c,eid,lot);p=preview(e,pid,v);assert before==get(e,pid)
    assert p['result']['totals']==dict(quantity=1,length='3.574',mass='10.722',length_unit='m',mass_unit='kg')
    b=submission(e,pid,v);row=submit(e,pid,b);assert submit(e,pid,b)==row
    x=read(e,pid)['processing']['lots'][0];assert x['in_stock_quantity']==1 and x['reserved']==1 and x['adopted_quantity']==0
    movement(e,pid,row,expected=409);stop(e,pid,recovery,eid,409)
    approve(r,pid,'stock_offcut_reservations',row);row=get(e,pid)['stock_offcut_reservations'][0]
    assert read(e,pid)['processing']['lots'][0]['in_stock_quantity']==1
    saved=e.get(base(pid)+'/export');assert saved.status_code==200
    event,b=movement(e,pid,row);assert movement(e,pid,row,b=b)[0]==event
    x=read(e,pid)['processing']['lots'][0];assert x['in_stock_quantity']==0 and x['in_custody']==1 and x['reserved']==0 and x['adopted_quantity']==0
    assert x['quantity']==x['in_stock_quantity']+x['in_custody']==1
    movement(e,pid,row,'release',eid,409);approve(r,pid,'stock_offcut_reservations',row,expected=409);stop(e,pid,recovery,eid,409)
    artifact=next(x for x in e.get('/api/v1/projects/'+pid+'/artifacts').json() if x['hash']==hashlib.sha256(saved.content).hexdigest())
    assert e.get('/api/v1/projects/'+pid+'/artifacts/'+artifact['id']+'/file').content==saved.content
    now=get(e,pid)
    for k in ('components','takeoffs','boq','quotes','report_approval','stale_quotes','status','stock_operations','stock_receipts','stock_reservations','stock_processing_results'):assert before[k]==now[k]
    current=read(e,pid);assert current['receipts']==old['receipts'] and current['processing']['sources']==old['processing']['sources']
    assert saved.json()['offcut_reuse']['reservations'][0]['phase']=='approved'
    preview(e,pid,v,409)


@pytest.mark.parametrize('decision',['reject','request_more_info'])
def test_rejection_keeps_hold_until_evidenced_release(clients,decision):
    e,r,pid,comp,c,eid,recovery,lot,stocks=setup(clients);row=submit(e,pid,submission(e,pid,value(e,pid,c,eid,lot)))
    approve(r,pid,'stock_offcut_reservations',row,decision);row=get(e,pid)['stock_offcut_reservations'][0]
    assert read(e,pid)['processing']['lots'][0]['reserved']==1
    movement(e,pid,row,expected=409);event,b=movement(e,pid,row,'release',eid)
    assert movement(e,pid,row,'release',eid,b=b)[0]==event
    x=read(e,pid)['processing']['lots'][0];assert x['adopted_quantity']==1 and x['in_stock_quantity']==1
    approve(r,pid,'stock_offcut_reservations',row,expected=409)
    replacement=submit(e,pid,submission(e,pid,value(e,pid,c,eid,lot)));assert replacement['id']!=row['id']


@pytest.mark.parametrize('change',[
    lambda v:v.update(confirmed=False),lambda v:v.update(custodian=''),lambda v:v.update(business_date='2999-01-01'),lambda v:v.update(business_date='2026-1-1'),lambda v:v.update(business_date=None),lambda v:v.update(basis=''),lambda v:v.update(extra='bad'),
    lambda v:v.update(allocations=[]),lambda v:v['allocations'][0].update(quantity=True),lambda v:v['allocations'][0].update(quantity=0),lambda v:v['allocations'][0].update(quantity=1.5),lambda v:v['allocations'][0].update(quantity=1000001),lambda v:v['allocations'][0].update(extra=1),lambda v:v['allocations'].append(copy.deepcopy(v['allocations'][0])),
])
def test_invalid_inputs_block_before_holds(clients,change):
    e,r,pid,comp,c,eid,recovery,lot,stocks=setup(clients);v=value(e,pid,c,eid,lot);change(v);preview(e,pid,v,422)
    assert not read(e,pid)['offcut_reuse']['reservations']


@pytest.mark.parametrize('change',[
 lambda v:v.update(demand_hash='old'),lambda v:v.update(evidence_id='missing'),lambda v:v['allocations'][0].update(lot_key='foreign'),lambda v:v['allocations'][0].update(demand_key='missing'),lambda v:v['allocations'][0].update(quantity=2),
])
def test_invalid_sources_and_quantities_block(clients,change):
    e,r,pid,comp,c,eid,recovery,lot,stocks=setup(clients);v=value(e,pid,c,eid,lot);change(v);response=e.post(base(pid)+'/offcut-reuse/preview',json={'input':v})
    assert response.status_code in (404,409),response.text


@pytest.mark.parametrize('length,weight',[('3573','3000'),('3575','3000'),('3574','3001')])
def test_equal_total_length_or_near_weight_is_not_compatible(clients,length,weight):
    e,r,pid,comp,c,eid,recovery,lot,stocks=setup(clients);other=target(e,r,pid,comp,length=length,weight=weight);preview(e,pid,value(e,pid,other,eid,lot),409)


def test_stopped_offcut_not_reusable_and_reused_identity_not_original_stock(clients):
    e,r,pid,comp,c,eid,recovery,lot,stocks=setup(clients);v=value(e,pid,c,eid,lot);stop(e,pid,recovery,eid);preview(e,pid,v,409)


def test_changed_preview_ledger_and_request_id_are_rejected(clients):
    e,r,pid,comp,c,eid,recovery,lot,stocks=setup(clients);b=submission(e,pid,value(e,pid,c,eid,lot));bad=copy.deepcopy(b);bad['input']['custodian']='different';submit(e,pid,bad,409)
    row=submit(e,pid,b);bad=copy.deepcopy(b);bad['input']['basis']='changed';submit(e,pid,bad,409)
    v=value(e,pid,c,eid,lot);preview(e,pid,v,409)
    movement(e,pid,row,'release',eid);b['request_id']=store.uid();submit(e,pid,b,409)


def test_same_material_table_cannot_mix_full_stock_and_offcut_reservations_in_both_directions(clients):
    e,r,pid,comp,c,eid,recovery,lot,stocks=setup(clients);row=submit(e,pid,submission(e,pid,value(e,pid,c,eid,lot)))
    stock,_=receipt(e,r,pid,eid,length='3574');reserve(e,pid,reservation_body(e,pid,c,eid,[stock]),409)
    movement(e,pid,row,'release',eid);full=reserve(e,pid,reservation_body(e,pid,c,eid,[stock]));preview(e,pid,value(e,pid,c,eid,lot),409)
    stock_movement(e,pid,eid,full);submit(e,pid,submission(e,pid,value(e,pid,c,eid,lot)))


def test_other_material_table_cannot_overbook_same_offcut_even_with_fresh_ledger(clients):
    e,r,pid,comp,c,eid,recovery,lot,stocks=setup(clients);other=target(e,r,pid,comp,name='合成另一回用表')
    submit(e,pid,submission(e,pid,value(e,pid,c,eid,lot)));preview(e,pid,value(e,pid,other,eid,lot),409)


def test_concurrent_holds_are_serial_and_never_overbook(clients):
    e,r,pid,comp,c,eid,recovery,lot,stocks=setup(clients);other=target(e,r,pid,comp,name='合成竞争回用表')
    b1=submission(e,pid,value(e,pid,c,eid,lot));b2=submission(e,pid,value(e,pid,other,eid,lot))
    with ThreadPoolExecutor(max_workers=2) as pool:codes=list(pool.map(lambda b:e.post(base(pid)+'/offcut-reuse',json=b).status_code,[b1,b2]))
    assert sorted(codes)==[200,409] and read(e,pid)['processing']['lots'][0]['reserved']==1


@pytest.mark.parametrize('when',['held','issued'])
def test_stale_engineering_never_returns_reserved_or_issued_quantity(clients,when):
    e,r,pid,comp,c,eid,recovery,lot,stocks=setup(clients);row=approved(e,r,pid,value(e,pid,c,eid,lot))
    if when=='issued':movement(e,pid,row)
    mutate(pid,lambda p:next(x for x in p['components'] if x['id']==c['id']).update(state='stale'))
    v=read(e,pid);assert v['offcut_reuse']['reservations'][0]['issue']
    x=v['processing']['lots'][0];assert x['in_stock_quantity']==(0 if when=='issued' else 1) and x['adopted_quantity']==0
    if when=='held':
        movement(e,pid,row,expected=409);movement(e,pid,row,'release',eid)
        assert read(e,pid)['processing']['lots'][0]['adopted_quantity']==1


@pytest.mark.parametrize('when',['before','held','issued'])
def test_missing_actual_evidence_blocks_new_use_without_rolling_back_facts(clients,when):
    e,r,pid,comp,c,eid,recovery,lot,stocks=setup(clients);v=value(e,pid,c,eid,lot);row=None
    if when!='before':row=approved(e,r,pid,v)
    if when=='issued':movement(e,pid,row)
    doc=next(d for d in get(e,pid)['documents'] if d['id']==next(x for x in get(e,pid)['evidence'] if x['id']==eid)['document_id']);path=store.ROOT/'originals'/doc['hash'];original=path.read_bytes();path.unlink()
    try:
        current=read(e,pid);x=current['processing']['lots'][0];assert x['adopted_quantity']==0 and x['quantity']==1
        if when=='before':preview(e,pid,v,409)
        elif when=='held':movement(e,pid,row,expected=409);assert x['reserved']==1
        else:assert x['in_custody']==1 and x['in_stock_quantity']==0 and current['offcut_reuse']['reservations'][0]['issue']
    finally:path.write_bytes(original)


def test_project_permissions_and_author_separation(clients):
    e,r,pid,comp,c,eid,recovery,lot,stocks=setup(clients);b=submission(e,pid,value(e,pid,c,eid,lot));assert r.post(base(pid)+'/offcut-reuse',json=b).status_code==403
    row=submit(e,pid,b);foreign=create(clients['other']);assert clients['other'].get(base(pid)).status_code==404
    assert e.post(base(foreign['id'])+'/offcut-reuse/preview',json={'input':b['input']}).status_code==404
    with store.transaction() as db:db.execute("UPDATE users SET role='reviewer' WHERE username='estimator'")
    approve(e,pid,'stock_offcut_reservations',row,expected=403)


@pytest.mark.parametrize('fault',['record','event','link','quantity','release-link','early-issue'])
def test_tampered_ledger_blocks_read_and_export(clients,fault):
    e,r,pid,comp,c,eid,recovery,lot,stocks=setup(clients);row=approved(e,r,pid,value(e,pid,c,eid,lot))
    movement(e,pid,row,'release',eid) if fault=='release-link' else movement(e,pid,row)
    def corrupt(p):
        if fault=='record':p['stock_offcut_reservations'][0]['totals']['length']='100'
        elif fault=='event':p['stock_events'][-1]['totals']['length']='100'
        elif fault=='link':p['stock_events'][-1]=inv.seal({**p['stock_events'][-1],'record_hash':'wrong'})
        elif fault=='quantity':p['stock_events'][-1]=inv.seal({**p['stock_events'][-1],'allocations':[]})
        elif fault=='release-link':p['stock_events'][-1]=inv.seal({**p['stock_events'][-1],'target_id':'missing'})
        else:
            event=p['stock_events'].pop();p['stock_events'].insert(next(i for i,x in enumerate(p['stock_events']) if x['operation']=='recover'),event)
    mutate(pid,corrupt);assert e.get(base(pid)).status_code==409 and e.get(base(pid)+'/export').status_code==409


def test_one_demand_can_split_across_two_accepted_lots_and_low_decimal_precision(clients):
    from decimal import localcontext
    e,r,pid,comp,eid,consumed,stocks=processing_setup(clients,count=2)
    v=raw(consumed,eid);a=v['rows'][0];a.update(quantity=2,finished_length='4.8',kerf_length='.012',trim_length='.04')
    second=copy.deepcopy(a['offcuts'][0]);second['lot_code']='SYN-OFFCUT-B';a['offcuts'].append(second)
    recovery=ready(e,r,pid,v);recover(e,pid,recovery)
    c=target(e,r,pid,comp);edited=copy.deepcopy(c);edited['material_items'][1]['quantity']='2';c=post(e,pid,edited).json();approve(r,pid,'components',c)
    lots=read(e,pid)['processing']['lots'];v=value(e,pid,c,eid,lots[0]);assert v['allocations'][0]['quantity']==2
    v['allocations']=[dict(demand_key=v['allocations'][0]['demand_key'],lot_key=l['lot_key'],quantity=1) for l in lots]
    with localcontext() as ctx:
        ctx.prec=3;row=approved(e,r,pid,v);assert row['totals']['length']=='7.148' and row['totals']['mass']=='21.444';movement(e,pid,row)
        assert all(l['in_custody']==1 and l['in_stock_quantity']==0 for l in read(e,pid)['processing']['lots'])


def test_forged_approval_state_alone_cannot_issue(clients):
    e,r,pid,comp,c,eid,recovery,lot,stocks=setup(clients);row=submit(e,pid,submission(e,pid,value(e,pid,c,eid,lot)))
    mutate(pid,lambda p:p['stock_offcut_reservations'][0].update(state='approved'))
    row=get(e,pid)['stock_offcut_reservations'][0];movement(e,pid,row,expected=409)
    assert read(e,pid)['processing']['lots'][0]['in_stock_quantity']==1


def test_old_ledger_and_object_hash_cannot_execute_after_review(clients):
    e,r,pid,comp,c,eid,recovery,lot,stocks=setup(clients);row=submit(e,pid,submission(e,pid,value(e,pid,c,eid,lot)));old=dict(**common(e,pid),object_hash=store.digest(row))
    approve(r,pid,'stock_offcut_reservations',row);movement(e,pid,row,expected=409,b=old)
    stale=dict(**common(e,pid),object_hash=store.digest(row));movement(e,pid,row,expected=409,b=stale)
    row=get(e,pid)['stock_offcut_reservations'][0];movement(e,pid,row)


def test_concurrent_execution_deducts_only_once(clients):
    e,r,pid,comp,c,eid,recovery,lot,stocks=setup(clients);row=approved(e,r,pid,value(e,pid,c,eid,lot));a=dict(**common(e,pid),object_hash=store.digest(row));b={**a,'request_id':store.uid()}
    with ThreadPoolExecutor(max_workers=2) as pool:codes=list(pool.map(lambda data:e.post(base(pid)+'/offcut-reuse/'+row['id']+'/execute',json=data).status_code,[a,b]))
    assert sorted(codes)==[200,409] and read(e,pid)['processing']['lots'][0]['in_custody']==1
