"""Synthetic unchanged-offcut returns, review gates, physical balances and repeat-use limits."""
import copy
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import pytest
from backend import store, stock_inventory as inv
from tests.test_stock_offcut_reuse import setup as reuse_setup, value as reuse_value, approved as approve_reuse, movement as reuse_movement, target
from tests.test_stock_processing_results import setup as processing_setup, raw, ready, execute as recover, stop
from tests.test_stock_inventory import base, read, common
from tests.test_workflow import get, approve, upload, create
from tests.test_polygon_takeoff import post, mutate
from tests.test_stock_operations import today


def setup(clients,count=1):
    if count==1:e,r,pid,comp,c,eid,recovery,lot,stocks=reuse_setup(clients)
    else:
        e,r,pid,comp,eid,consumed,stocks=processing_setup(clients,count=count)
        v=raw(consumed,eid);a=v['rows'][0];a.update(quantity=count,finished_length=str(2.4*count),kerf_length=str(.006*count),trim_length=str(.02*count));a['offcuts'][0]['quantity']=count
        recovery=ready(e,r,pid,v);recover(e,pid,recovery)
        c=target(e,r,pid,comp);v=copy.deepcopy(c);v['material_items'][1]['quantity']=str(count);c=post(e,pid,v).json();approve(r,pid,'components',c)
        lot=read(e,pid)['processing']['lots'][0]
    source=approve_reuse(e,r,pid,reuse_value(e,pid,c,eid,lot));reuse_movement(e,pid,source)
    return e,r,pid,comp,c,eid,recovery,lot,source


def raw_return(source,eid,quantity=1):
    return dict(reference_id=source['id'],reference_hash=store.digest(source),selections=[dict(allocation_index=1,quantity=quantity)],business_date=today(),custodian='合成原段接收人A',basis='合成实际退回，未加工、保持3574mm原长、同材质截面且质量合格，返回原编号原库位，非真实仓库验收',evidence_id=eid,confirmed=True)


def preview(e,pid,v,expected=200):
    response=e.post(base(pid)+'/offcut-returns/preview',json={'input':v});assert response.status_code==expected,response.text;return response.json()


def submission(e,pid,v):
    p=preview(e,pid,v);return dict(request_id=store.uid(),ledger_hash=p['ledger_hash'],input=v,preview_hash=p['preview_hash'])


def submit(e,pid,b,expected=200):
    response=e.post(base(pid)+'/offcut-returns',json=b);assert response.status_code==expected,response.text;return response.json()


def ready_return(e,r,pid,v):
    row=submit(e,pid,submission(e,pid,v));approve(r,pid,'stock_offcut_returns',row);return next(x for x in get(e,pid)['stock_offcut_returns'] if x['id']==row['id'])


def move(e,pid,row,kind='execute',eid=None,expected=200,b=None):
    b=b or dict(**common(e,pid),object_hash=store.digest(row),**(dict(basis='合成核查取消未执行退回，非冲销或实际出入库',evidence_id=eid) if kind=='cancel' else {}))
    response=e.post(base(pid)+'/offcut-returns/'+row['id']+'/'+kind,json=b);assert response.status_code==expected,response.text;return response.json(),b


def test_preview_save_review_execute_and_archive_preserve_other_ledgers(clients):
    e,r,pid,comp,c,eid,recovery,lot,source=setup(clients);before=get(e,pid);old=read(e,pid)
    v=raw_return(source,eid);p=preview(e,pid,v);assert get(e,pid)==before
    assert p['result']['totals']==dict(quantity=1,length='3.574',mass='10.722',length_unit='m',mass_unit='kg')
    b=submission(e,pid,v);row=submit(e,pid,b);assert submit(e,pid,b)==row
    assert read(e,pid)['processing']['lots'][0]['in_stock_quantity']==0
    move(e,pid,row,expected=409);approve(r,pid,'stock_offcut_returns',row);row=get(e,pid)['stock_offcut_returns'][0]
    assert read(e,pid)['processing']['lots'][0]['in_stock_quantity']==0
    frozen=e.get(base(pid)+'/export');event,b=move(e,pid,row);assert move(e,pid,row,b=b)[0]==event
    current=read(e,pid);x=current['processing']['lots'][0]
    assert (x['quantity'],x['in_stock_quantity'],x['in_custody'],x['issued_quantity'],x['returned_quantity'],x['adopted_quantity'])==(1,1,0,1,1,1)
    assert current['offcut_reuse']['reservations'][0]['balances'][0]['outstanding']==0
    assert current['offcut_returns']['records'][0]['phase']=='executed'
    approve(r,pid,'stock_offcut_returns',row,expected=409);move(e,pid,row,'cancel',eid,409);preview(e,pid,v,409)
    artifacts=e.get('/api/v1/projects/'+pid+'/artifacts').json();a=next(x for x in artifacts if x['hash']==hashlib.sha256(frozen.content).hexdigest())
    assert e.get('/api/v1/projects/'+pid+'/artifacts/'+a['id']+'/file').content==frozen.content
    after=get(e,pid)
    for k in ('components','takeoffs','boq','quotes','report_approval','stale_quotes','status','stock_operations','stock_receipts','stock_reservations','stock_processing_results','stock_offcut_reservations'):assert before[k]==after[k]
    assert current['receipts']==old['receipts'] and current['processing']['sources']==old['processing']['sources']
    stop(e,pid,recovery,eid)
    assert read(e,pid)['processing']['lots'][0]['in_stock_quantity']==1 and read(e,pid)['processing']['lots'][0]['adopted_quantity']==0


def test_partial_returns_and_later_new_material_table_reuse_do_not_reopen_original(clients):
    e,r,pid,comp,c,eid,recovery,lot,source=setup(clients,count=2)
    row=ready_return(e,r,pid,raw_return(source,eid));move(e,pid,row)
    x=read(e,pid)['processing']['lots'][0];assert (x['in_stock_quantity'],x['in_custody'],x['returned_quantity'])==(1,1,1)
    preview(e,pid,raw_return(source,eid,2),409)
    other=target(e,r,pid,comp,name='合成另一材料表复用已退原段');new=approve_reuse(e,r,pid,reuse_value(e,pid,other,eid,lot));reuse_movement(e,pid,new)
    x=read(e,pid)['processing']['lots'][0];assert (x['issued_quantity'],x['returned_quantity'],x['in_custody'],x['in_stock_quantity'])==(3,1,2,0)
    row=ready_return(e,r,pid,raw_return(source,eid));move(e,pid,row)
    assert read(e,pid)['offcut_reuse']['reservations'][0]['balances'][0]['outstanding']==0
    response=e.post(base(pid)+'/offcut-reuse/preview',json={'input':reuse_value(e,pid,c,eid,lot)});assert response.status_code==409
    row=ready_return(e,r,pid,raw_return(new,eid));move(e,pid,row)
    x=read(e,pid)['processing']['lots'][0];assert x['in_stock_quantity']==2 and x['in_custody']==0 and x['issued_quantity']==x['returned_quantity']==3


@pytest.mark.parametrize('decision',['reject','request_more_info'])
def test_rejection_and_cancel_do_not_change_physical_counts(clients,decision):
    e,r,pid,comp,c,eid,recovery,lot,source=setup(clients);row=submit(e,pid,submission(e,pid,raw_return(source,eid)))
    approve(r,pid,'stock_offcut_returns',row,decision);row=get(e,pid)['stock_offcut_returns'][0];move(e,pid,row,expected=409)
    assert read(e,pid)['processing']['lots'][0]['in_custody']==1
    event,b=move(e,pid,row,'cancel',eid);assert move(e,pid,row,'cancel',eid,b=b)[0]==event
    approve(r,pid,'stock_offcut_returns',row,expected=409);move(e,pid,row,expected=409)
    row=ready_return(e,r,pid,raw_return(source,eid));move(e,pid,row)


@pytest.mark.parametrize('fault',[
 lambda v:v.update(confirmed=False),lambda v:v.update(custodian=''),lambda v:v.update(basis=''),lambda v:v.update(business_date='2999-01-01'),lambda v:v.update(business_date='2026-1-1'),lambda v:v.update(business_date=None),lambda v:v.update(extra=1),
 lambda v:v.update(selections=[]),lambda v:v.update(selections={}),lambda v:v['selections'][0].update(quantity=True),lambda v:v['selections'][0].update(quantity=0),lambda v:v['selections'][0].update(quantity=1.5),lambda v:v['selections'][0].update(quantity=1000001),lambda v:v['selections'][0].update(allocation_index=True),lambda v:v['selections'][0].update(allocation_index=2),lambda v:v['selections'][0].update(lot_key='foreign'),lambda v:v['selections'].append(copy.deepcopy(v['selections'][0])),
])
def test_invalid_inputs_never_create_stock(clients,fault):
    e,r,pid,comp,c,eid,recovery,lot,source=setup(clients);v=raw_return(source,eid);fault(v);preview(e,pid,v,422)
    assert not read(e,pid)['offcut_returns']['records']


@pytest.mark.parametrize('fault',[lambda v:v.update(reference_hash='old'),lambda v:v.update(reference_id='missing'),lambda v:v.update(evidence_id='missing'),lambda v:v.update(business_date=(date.fromisoformat(today())-timedelta(days=1)).isoformat()),lambda v:v['selections'][0].update(quantity=2)])
def test_invalid_reference_date_or_over_return_blocks(clients,fault):
    e,r,pid,comp,c,eid,recovery,lot,source=setup(clients);v=raw_return(source,eid);fault(v);response=e.post(base(pid)+'/offcut-returns/preview',json={'input':v});assert response.status_code in (404,409),response.text


def test_unexecuted_reuse_is_not_returnable(clients):
    e,r,pid,comp,c,eid,recovery,lot,stocks=reuse_setup(clients);source=approve_reuse(e,r,pid,reuse_value(e,pid,c,eid,lot));preview(e,pid,raw_return(source,eid),409)


def test_stale_original_engineering_and_handover_allow_actual_return_with_new_proof(clients):
    e,r,pid,comp,c,eid,recovery,lot,source=setup(clients)
    upload(e,pid,'合成新退回验收：原3574mm未加工方管A原段1根，质量合格退原库位，仅软件验收','new-offcut-return.txt');fresh=get(e,pid)['evidence'][-1]['id']
    doc=next(d for d in get(e,pid)['documents'] if d['id']==next(x for x in get(e,pid)['evidence'] if x['id']==eid)['document_id']);path=store.ROOT/'originals'/doc['hash'];data=path.read_bytes();path.unlink()
    try:
        row=ready_return(e,r,pid,raw_return(source,fresh));move(e,pid,row)
        v=read(e,pid);x=v['processing']['lots'][0];assert x['in_stock_quantity']==1 and x['in_custody']==0 and x['adopted_quantity']==0 and x['issue']
        assert v['offcut_reuse']['reservations'][0]['issue'] and not v['offcut_returns']['records'][0]['issue']
    finally:path.write_bytes(data)


@pytest.mark.parametrize('when',['preview','submit','review','execute','historical'])
def test_current_return_proof_bytes_gate_and_historical_quarantine(clients,when):
    e,r,pid,comp,c,eid,recovery,lot,source=setup(clients)
    upload(e,pid,'合成原段退回独立验收原件，非真实仓库','return-proof.txt');fresh=get(e,pid)['evidence'][-1]['id'];v=raw_return(source,fresh)
    b=submission(e,pid,v)
    if when in ('review','execute','historical'):
        row=submit(e,pid,b)
        if when in ('execute','historical'):approve(r,pid,'stock_offcut_returns',row);row=get(e,pid)['stock_offcut_returns'][0]
        if when=='historical':move(e,pid,row)
    doc=get(e,pid)['documents'][-1];path=store.ROOT/'originals'/doc['hash'];data=path.read_bytes();path.write_bytes(b'tampered')
    try:
        if when=='preview':preview(e,pid,v,409)
        elif when=='submit':submit(e,pid,b,409)
        elif when=='review':approve(r,pid,'stock_offcut_returns',row,expected=409)
        elif when=='execute':move(e,pid,row,expected=409)
        else:
            state=read(e,pid);x=state['processing']['lots'][0];assert x['in_stock_quantity']==1 and x['returned_quantity']==1 and x['adopted_quantity']==0 and not x['verified']
            assert state['offcut_returns']['records'][0]['issue'] and '退回' in x['issue']
    finally:path.write_bytes(data)
    if when=='historical':assert read(e,pid)['processing']['lots'][0]['adopted_quantity']==1


def test_preview_binding_stale_ledger_and_idempotency_collisions(clients):
    e,r,pid,comp,c,eid,recovery,lot,source=setup(clients);b=submission(e,pid,raw_return(source,eid));bad=copy.deepcopy(b);bad['input']['custodian']='changed';submit(e,pid,bad,409)
    row=submit(e,pid,b);bad=copy.deepcopy(b);bad['input']['basis']='changed';submit(e,pid,bad,409)
    b['request_id']=store.uid();submit(e,pid,b,409)
    old=dict(**common(e,pid),object_hash=store.digest(row));approve(r,pid,'stock_offcut_returns',row);move(e,pid,row,expected=409,b=old)


def test_parallel_approved_returns_recheck_live_custody_at_execution(clients):
    e,r,pid,comp,c,eid,recovery,lot,source=setup(clients);a=ready_return(e,r,pid,raw_return(source,eid));b=ready_return(e,r,pid,raw_return(source,eid))
    assert read(e,pid)['processing']['lots'][0]['in_stock_quantity']==0
    common_body=common(e,pid);bodies=[{**common_body,'request_id':store.uid(),'object_hash':store.digest(row)} for row in (a,b)]
    with ThreadPoolExecutor(max_workers=2) as pool:codes=list(pool.map(lambda pair:e.post(base(pid)+'/offcut-returns/'+pair[0]['id']+'/execute',json=pair[1]).status_code,zip((a,b),bodies)))
    assert sorted(codes)==[200,409];x=read(e,pid)['processing']['lots'][0];assert x['in_stock_quantity']==1 and x['returned_quantity']==1
    pending=next(x for x in read(e,pid)['offcut_returns']['records'] if x['phase']=='approved');assert pending['issue']
    move(e,pid,pending['record'],expected=409)


def test_roles_tenant_author_separation_and_forged_approval(clients):
    e,r,pid,comp,c,eid,recovery,lot,source=setup(clients);b=submission(e,pid,raw_return(source,eid));assert r.post(base(pid)+'/offcut-returns',json=b).status_code==403
    row=submit(e,pid,b);foreign=create(clients['other']);assert clients['other'].post(base(pid)+'/offcut-returns/preview',json={'input':b['input']}).status_code==404
    assert e.post(base(foreign['id'])+'/offcut-returns/preview',json={'input':b['input']}).status_code==404
    mutate(pid,lambda p:p['stock_offcut_returns'][0].update(state='approved'));row=get(e,pid)['stock_offcut_returns'][0];move(e,pid,row,expected=409)
    with store.transaction() as db:db.execute("UPDATE users SET role='reviewer' WHERE username='estimator'")
    approve(e,pid,'stock_offcut_returns',row,expected=403)


@pytest.mark.parametrize('fault',['record','event','link','source','allocations','before-issue','cancel-link','over-return'])
def test_tampered_return_ledger_blocks_read_and_export(clients,fault):
    e,r,pid,comp,c,eid,recovery,lot,source=setup(clients);row=ready_return(e,r,pid,raw_return(source,eid));move(e,pid,row,'cancel',eid) if fault=='cancel-link' else move(e,pid,row)
    def corrupt(p):
        if fault=='record':p['stock_offcut_returns'][0]['totals']['length']='wrong'
        elif fault=='event':p['stock_events'][-1]['totals']['length']='wrong'
        elif fault=='link':p['stock_events'][-1]=inv.seal({**p['stock_events'][-1],'record_hash':'wrong'})
        elif fault=='source':p['stock_events'][-1]=inv.seal({**p['stock_events'][-1],'reference_id':'missing'})
        elif fault=='allocations':p['stock_events'][-1]=inv.seal({**p['stock_events'][-1],'allocations':[]})
        elif fault=='cancel-link':p['stock_events'][-1]=inv.seal({**p['stock_events'][-1],'target_id':'missing'})
        elif fault=='over-return':
            event=copy.deepcopy(p['stock_events'][-1]);event.update(id=store.uid(),target_id=store.uid());r2=copy.deepcopy(p['stock_offcut_returns'][0]);r2['id']=event['target_id'];r2=inv.seal(r2);event['record_hash']=store.digest(r2);p['stock_offcut_returns'].append(r2);p['stock_events'].append(inv.seal(event))
        else:
            event=p['stock_events'].pop();p['stock_events'].insert(next(i for i,x in enumerate(p['stock_events']) if x['operation']=='offcut_issue'),event)
    mutate(pid,corrupt);assert e.get(base(pid)).status_code==409 and e.get(base(pid)+'/export').status_code==409


def test_returns_cannot_spend_another_allocation_of_the_same_physical_lot(clients):
    from decimal import localcontext
    e,r,pid,comp,eid,consumed,stocks=processing_setup(clients,count=2)
    v=raw(consumed,eid);a=v['rows'][0];a.update(quantity=2,finished_length='4.8',kerf_length='.012',trim_length='.04');a['offcuts'][0]['quantity']=2
    recovery=ready(e,r,pid,v);recover(e,pid,recovery);c=target(e,r,pid,comp)
    new=copy.deepcopy(c);item=copy.deepcopy(new['material_items'][1]);item['name']='合成第二相同规格需求';new['material_items'].append(item);c=post(e,pid,new).json();approve(r,pid,'components',c)
    lot=read(e,pid)['processing']['lots'][0];source=approve_reuse(e,r,pid,reuse_value(e,pid,c,eid,lot));reuse_movement(e,pid,source)
    assert len(source['allocations'])==2
    with localcontext() as ctx:
        ctx.prec=3;row=ready_return(e,r,pid,raw_return(source,eid));assert row['totals']['mass']=='10.722';move(e,pid,row)
    preview(e,pid,raw_return(source,eid),409)
    v=raw_return(source,eid);v['selections'][0]['allocation_index']=2;row=ready_return(e,r,pid,v);move(e,pid,row)
    balances=read(e,pid)['offcut_reuse']['reservations'][0]['balances'];assert [a['returned'] for a in balances]==[1,1]
    assert read(e,pid)['processing']['lots'][0]['in_stock_quantity']==2
