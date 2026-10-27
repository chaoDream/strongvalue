"""Synthetic complete-bar handover/custody ledger, independent of engineering quantities."""
import copy
import json
import hashlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor
import pytest
from backend import store
from backend import stock_inventory as inv
from tests.test_stock_inventory import setup as inventory_setup, receipt, reservation_body, submit as reserve, read, base, common, movement
from tests.test_workflow import get, approve, create, upload
from tests.test_polygon_takeoff import mutate


def today():return datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()


def setup(clients, mode='single'):
    e,r,pid,comp,eid=inventory_setup(clients,mode)
    d=e.get(base(pid)+'/demands',params={'component_id':comp['id']}).json()
    stocks=[receipt(e,r,pid,eid,length=v['stock_length'],unit='m',quantity='10')[0] for v in d['demands']]
    reservation=reserve(e,pid,reservation_body(e,pid,comp,eid,stocks));approve(r,pid,'stock_reservations',reservation)
    return e,r,pid,comp,eid,get(e,pid)['stock_reservations'][0],stocks


def body(e,pid,eid,kind,reference,selections=None):
    return {**common(e,pid),'input':dict(kind=kind,reference_id=reference['id'],reference_hash=store.digest(reference),selections=[] if selections is None else selections,
        custodian='合成加工班组A（非真实交接）',business_date=today(),basis='合成实际完整原材交接或加工凭据，仅软件验证；不作为真实仓库或净工程量验收',evidence_id=eid,confirmed=True)}


def submit(e,pid,b,expected=200):
    response=e.post(base(pid)+'/operations',json=b);assert response.status_code==expected,response.text;return response.json()


def execute(e,pid,row,expected=200,b=None):
    b=b or {**common(e,pid),'object_hash':store.digest(row)}
    response=e.post(base(pid)+'/operations/'+row['id']+'/execute',json=b);assert response.status_code==expected,response.text
    return response.json(),b


def approved_operation(e,r,pid,b):
    row=submit(e,pid,b);approve(r,pid,'stock_operations',row)
    return next(o for o in get(e,pid)['stock_operations'] if o['id']==row['id'])


def issue(e,r,pid,eid,reservation):
    row=approved_operation(e,r,pid,body(e,pid,eid,'issue',reservation));execute(e,pid,row);return row


def test_full_handover_consumption_return_balance_history_and_downstream_isolation(clients):
    e,r,pid,comp,eid,reservation,stocks=setup(clients);before=get(e,pid)
    b=body(e,pid,eid,'issue',reservation);row=submit(e,pid,b)
    assert submit(e,pid,b)==row
    state=read(e,pid);assert state['receipts'][0]['reserved']==3 and state['receipts'][0]['on_hand']==10 and state['receipts'][0]['available']==7
    execute(e,pid,row,409)
    approve(r,pid,'stock_operations',row);row=get(e,pid)['stock_operations'][0]
    assert read(e,pid)['receipts'][0]['on_hand']==10
    event,b=execute(e,pid,row);assert execute(e,pid,row,b=b)[0]==event
    v=read(e,pid);a=v['receipts'][0]
    assert (a['on_hand'],a['reserved'],a['available'],a['issued'],a['in_custody'])==(7,0,7,3,3)
    assert v['reservations'][0]['phase']=='issued' and not v['reservations'][0]['active']
    assert row['totals']==dict(quantity=3,length='18',mass='54',length_unit='m',mass_unit='kg')
    frozen=e.get(base(pid)+'/export');assert frozen.status_code==200
    consume=approved_operation(e,r,pid,body(e,pid,eid,'consume',row,[dict(allocation_index=1,quantity=1)]));execute(e,pid,consume)
    returned=approved_operation(e,r,pid,body(e,pid,eid,'return',row,[dict(allocation_index=1,quantity=1)]));execute(e,pid,returned)
    state=read(e,pid);a=state['receipts'][0]
    assert (a['on_hand'],a['available'],a['in_custody'],a['consumed'],a['returned'])==(8,8,1,1,1)
    assert a['on_hand']+a['in_custody']+a['consumed']==a['record']['normalized']['quantity']
    assert state['operations'][0]['balances'][0]['consumed']==state['operations'][0]['balances'][0]['returned']==1
    consume=approved_operation(e,r,pid,body(e,pid,eid,'consume',row,[dict(allocation_index=1,quantity=1)]));execute(e,pid,consume)
    a=read(e,pid)['receipts'][0];assert (a['on_hand'],a['in_custody'],a['consumed'])==(8,0,2)
    movement(e,pid,eid,reservation,'release',409)
    movement(e,pid,eid,read(e,pid)['receipts'][0]['record'],'void',409)
    reserve(e,pid,reservation_body(e,pid,comp,eid,stocks),409)
    approve(r,pid,'stock_operations',row,expected=409);approve(r,pid,'stock_reservations',reservation,expected=409)
    movement(e,pid,eid,row,'cancel',409)
    after=get(e,pid)
    for k in ('components','takeoffs','boq','quotes','report_approval','stale_quotes','status'):assert before[k]==after[k]
    assert frozen.json()['receipts'][0]['in_custody']==3
    artifact=next(a for a in e.get('/api/v1/projects/'+pid+'/artifacts').json() if a['hash']==hashlib.sha256(frozen.content).hexdigest())
    assert e.get('/api/v1/projects/'+pid+'/artifacts/'+artifact['id']+'/file').content==frozen.content


@pytest.mark.parametrize('mode',['mixed','limited'])
def test_all_stock_choices_and_allocations_inherit_once_with_exact_length_mass(clients,mode):
    e,r,pid,comp,eid,reservation,stocks=setup(clients,mode)
    row=issue(e,r,pid,eid,reservation)
    expected=[(1,4)] if mode=='mixed' else [(1,2),(2,2),(3,2)]
    assert [(a['allocation_index'],a['quantity']) for a in row['allocations']]==expected
    assert row['totals']['length']==('24' if mode=='mixed' else '23.892')
    assert row['totals']['mass']==('72' if mode=='mixed' else '71.676')
    choice=[dict(allocation_index=a['allocation_index'],quantity=a['quantity']) for a in row['allocations']]
    returned=approved_operation(e,r,pid,body(e,pid,eid,'return',row,choice));execute(e,pid,returned)
    assert all(a['on_hand']==10 and a['available']==10 and a['in_custody']==0 for a in read(e,pid)['receipts'])


def test_split_lots_cannot_transfer_another_allocation_balance(clients):
    e,r,pid,comp,eid=inventory_setup(clients)
    a,_=receipt(e,r,pid,eid,quantity='1');b,_=receipt(e,r,pid,eid,quantity='2')
    request=reservation_body(e,pid,comp,eid,[a]);key=request['allocations'][0]['demand_key'];request['allocations']=[dict(demand_key=key,receipt_id=a['id'],quantity=1),dict(demand_key=key,receipt_id=b['id'],quantity=2)]
    res=reserve(e,pid,request);approve(r,pid,'stock_reservations',res);res=get(e,pid)['stock_reservations'][0]
    row=issue(e,r,pid,eid,res)
    submit(e,pid,body(e,pid,eid,'consume',row,[dict(allocation_index=1,quantity=2)]),409)
    used=approved_operation(e,r,pid,body(e,pid,eid,'consume',row,[dict(allocation_index=2,quantity=2)]));execute(e,pid,used)
    assert [a['in_custody'] for a in read(e,pid)['receipts']]==[1,0]


@pytest.mark.parametrize('fault',['unknown-kind','missing','extra','not-confirmed','truthy','missing-basis','missing-proof','no-custodian','bad-date','future-date','noncanonical-date','wrong-hash','manual-issue-count'])
def test_issue_document_schema_and_current_reference_gates(clients,fault):
    e,r,pid,comp,eid,reservation,stocks=setup(clients);b=body(e,pid,eid,'issue',reservation);raw=b['input']
    if fault=='unknown-kind':raw['kind']='borrow'
    elif fault=='missing':raw.pop('basis')
    elif fault=='extra':raw['quantity']=3
    elif fault in ('not-confirmed','truthy'):raw['confirmed']=False if fault=='not-confirmed' else 1
    elif fault=='missing-basis':raw['basis']=''
    elif fault=='missing-proof':raw['evidence_id']='missing'
    elif fault=='no-custodian':raw['custodian']=''
    elif fault=='bad-date':raw['business_date']='2026-02-30'
    elif fault=='future-date':raw['business_date']=(datetime.now(ZoneInfo('Asia/Shanghai')).date()+timedelta(days=1)).isoformat()
    elif fault=='noncanonical-date':raw['business_date']=today().replace('-','')
    elif fault=='wrong-hash':raw['reference_hash']='old'
    elif fault=='manual-issue-count':raw['selections']=[dict(allocation_index=1,quantity=2)]
    response=e.post(base(pid)+'/operations',json=b);assert response.status_code in (404,409,422),response.text
    state=read(e,pid);assert not state['operations'] and state['receipts'][0]['reserved']==3 and state['receipts'][0]['on_hand']==10


@pytest.mark.parametrize('kind',['consume','return'])
@pytest.mark.parametrize('fault',['empty','zero','negative','float','bool','duplicate','wrong-index','index-bool','too-many','extra','not-list'])
def test_custody_choices_cannot_overdraw_or_forge_full_bar_counts(clients,kind,fault):
    e,r,pid,comp,eid,reservation,stocks=setup(clients);row=issue(e,r,pid,eid,reservation)
    b=body(e,pid,eid,kind,row,[dict(allocation_index=1,quantity=1)]);s=b['input']['selections']
    if fault=='empty':b['input']['selections']=[]
    elif fault in ('zero','negative','float','bool','too-many'):s[0]['quantity']={'zero':0,'negative':-1,'float':1.0,'bool':True,'too-many':4}[fault]
    elif fault=='duplicate':s.append(dict(s[0]))
    elif fault=='wrong-index':s[0]['allocation_index']=2
    elif fault=='index-bool':s[0]['allocation_index']=True
    elif fault=='extra':s[0]['receipt_id']=stocks[0]['id']
    else:b['input']['selections']=None
    response=e.post(base(pid)+'/operations',json=b);assert response.status_code in (409,422),response.text
    assert read(e,pid)['receipts'][0]['in_custody']==3


def test_cancel_unexecuted_record_and_retry_never_change_stock(clients):
    e,r,pid,comp,eid,reservation,stocks=setup(clients);row=submit(e,pid,body(e,pid,eid,'issue',reservation))
    event,b=movement(e,pid,eid,row,'cancel')
    assert e.post(base(pid)+'/events',json=b).json()==event
    approve(r,pid,'stock_operations',row,expected=409);execute(e,pid,row,409)
    state=read(e,pid);assert state['operations'][0]['phase']=='cancelled' and state['receipts'][0]['on_hand']==10 and state['receipts'][0]['reserved']==3
    row=issue(e,r,pid,eid,reservation);assert read(e,pid)['receipts'][0]['on_hand']==7


def test_two_reviewed_issue_orders_race_and_cannot_issue_same_reservation_twice(clients):
    e,r,pid,comp,eid,reservation,stocks=setup(clients)
    a=approved_operation(e,r,pid,body(e,pid,eid,'issue',reservation));b=approved_operation(e,r,pid,body(e,pid,eid,'issue',reservation))
    token=common(e,pid)['ledger_hash'];requests=[dict(request_id=store.uid(),ledger_hash=token,object_hash=store.digest(row)) for row in (a,b)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda pair:e.post(base(pid)+'/operations/'+pair[0]['id']+'/execute',json=pair[1]),zip((a,b),requests)))
    assert sorted(x.status_code for x in responses)==[200,409]
    loser=b if responses[0].status_code==200 else a;execute(e,pid,loser,409)
    a=read(e,pid)['receipts'][0];assert a['issued']==3 and a['on_hand']==7


def test_competing_consume_return_documents_recheck_balance_on_execution(clients):
    e,r,pid,comp,eid,reservation,stocks=setup(clients);source=issue(e,r,pid,eid,reservation)
    a=approved_operation(e,r,pid,body(e,pid,eid,'consume',source,[dict(allocation_index=1,quantity=3)]))
    b=approved_operation(e,r,pid,body(e,pid,eid,'return',source,[dict(allocation_index=1,quantity=3)]))
    token=common(e,pid)['ledger_hash'];requests=[dict(request_id=store.uid(),ledger_hash=token,object_hash=store.digest(row)) for row in (a,b)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda pair:e.post(base(pid)+'/operations/'+pair[0]['id']+'/execute',json=pair[1]),zip((a,b),requests)))
    assert sorted(x.status_code for x in responses)==[200,409]
    loser=b if responses[0].status_code==200 else a;execute(e,pid,loser,409)
    v=read(e,pid)['receipts'][0];assert v['in_custody']==0 and (v['consumed'],v['returned']) in ((3,0),(0,3))


@pytest.mark.parametrize('gate',['submit','review','execute'])
def test_operation_original_bytes_gate_keeps_inventory_unchanged(clients,gate):
    e,r,pid,comp,eid,reservation,stocks=setup(clients);b=body(e,pid,eid,'issue',reservation)
    row=None if gate=='submit' else submit(e,pid,b)
    if gate=='execute':approve(r,pid,'stock_operations',row);row=get(e,pid)['stock_operations'][0]
    doc=get(e,pid)['documents'][-1];path=store.ROOT/'originals'/doc['hash'];data=path.read_bytes();path.write_bytes(b'tampered')
    if gate=='submit':submit(e,pid,b,409)
    elif gate=='review':approve(r,pid,'stock_operations',row,expected=409)
    else:execute(e,pid,row,409)
    v=read(e,pid)['receipts'][0];assert v['on_hand']==10 and v['reserved']==3
    path.write_bytes(data)


def test_record_actual_custody_after_upstream_revision_and_missing_historical_proof(clients):
    e,r,pid,comp,eid,reservation,stocks=setup(clients);source=issue(e,r,pid,eid,reservation)
    old=get(e,pid)['components'][-1];revised=copy.deepcopy(old);revised['name']='合成修订材料表';revised['object_hash']=store.digest(old)
    assert e.put('/api/v1/projects/'+pid+'/components/'+comp['id'],json=revised).status_code==200
    upload(e,pid,'合成后续交接凭据：旧领料剩余完整根，一根实际投入加工、一根完整原状退回，仅软件验证。','v119-handover.txt')
    new_eid=get(e,pid)['evidence'][-1]['id'];old_doc=next(d for d in get(e,pid)['documents'] if d['name']=='stock-cutting-basis.txt');path=store.ROOT/'originals'/old_doc['hash'];data=path.read_bytes();path.unlink()
    for kind in ('consume','return'):
        row=approved_operation(e,r,pid,body(e,pid,new_eid,kind,source,[dict(allocation_index=1,quantity=1)]));execute(e,pid,row)
    v=read(e,pid);a=v['receipts'][0];assert (a['on_hand'],a['in_custody'],a['consumed'],a['returned'],a['available'])==(8,1,1,1,0)
    assert v['operations'][0]['issue']
    path.write_bytes(data)


def test_roles_project_scope_self_review_and_forged_approval(clients):
    e,r,pid,comp,eid,reservation,stocks=setup(clients);b=body(e,pid,eid,'issue',reservation)
    assert clients['other'].post(base(pid)+'/operations',json=b).status_code==404
    assert r.post(base(pid)+'/operations',json=b).status_code==403
    other=create(e)['id'];assert e.post(base(other)+'/operations',json={**b,**common(e,other)}).status_code==404
    row=submit(e,pid,b)
    mutate(pid,lambda p:p['stock_operations'][0].update(state='approved'))
    row=get(e,pid)['stock_operations'][0];execute(e,pid,row,409)
    with store.transaction() as c:c.execute("UPDATE users SET role='reviewer' WHERE username='estimator'")
    approve(e,pid,'stock_operations',row,expected=403)
    assert r.post(base(pid)+'/operations/'+row['id']+'/execute',json={**common(r,pid),'object_hash':store.digest(row)}).status_code==403


@pytest.mark.parametrize('target',['operation','event','executed-state','cross-link','event-overdraw'])
def test_ledger_tampering_fails_closed_without_recrediting_stock(clients,target):
    e,r,pid,comp,eid,reservation,stocks=setup(clients);source=issue(e,r,pid,eid,reservation)
    def corrupt(p):
        if target=='operation':p['stock_operations'][0]['totals']['quantity']=4
        elif target=='event':p['stock_events'][-1]['totals']['quantity']=4
        elif target=='executed-state':p['stock_operations'][0]['state']='draft'
        elif target=='cross-link':p['stock_events'][-1]['reservation_id']='missing';p['stock_events'][-1]=inv.seal(p['stock_events'][-1])
        else:
            p['stock_events'][-1]['allocations'][0]['quantity']=4;p['stock_events'][-1]=inv.seal(p['stock_events'][-1])
    mutate(pid,corrupt)
    assert e.get(base(pid)).status_code==409 and e.get(base(pid)+'/export').status_code==409


@pytest.mark.parametrize('gate',['submit','review','execute'])
@pytest.mark.parametrize('fault',['released','reservation-rejected','receipt-rejected','source-revised'])
def test_new_issue_must_keep_live_reservation_receipt_and_source_at_every_gate(clients,gate,fault):
    e,r,pid,comp,eid,reservation,stocks=setup(clients);b=body(e,pid,eid,'issue',reservation)
    row=None if gate=='submit' else submit(e,pid,b)
    if gate=='execute':approve(r,pid,'stock_operations',row);row=get(e,pid)['stock_operations'][0]
    if fault=='released':movement(e,pid,eid,reservation)
    elif fault=='reservation-rejected':approve(r,pid,'stock_reservations',reservation,'reject')
    elif fault=='receipt-rejected':approve(r,pid,'stock_receipts',read(e,pid)['receipts'][0]['record'],'reject')
    else:
        source=get(e,pid)['components'][-1];revised=copy.deepcopy(source);revised['name']='合成领料前修订';revised['object_hash']=store.digest(source)
        assert e.put('/api/v1/projects/'+pid+'/components/'+comp['id'],json=revised).status_code==200
    if gate=='submit':b.update(common(e,pid));submit(e,pid,b,409)
    elif gate=='review':approve(r,pid,'stock_operations',row,expected=409)
    else:execute(e,pid,row,409)
    a=read(e,pid)['receipts'][0];assert a['on_hand']==10 and a['issued']==0 and a['reserved']==(0 if fault=='released' else 3)


def test_all_uncut_bars_returned_allows_receipt_void_without_reopening_material_demand(clients):
    e,r,pid,comp,eid,reservation,stocks=setup(clients);source=issue(e,r,pid,eid,reservation)
    row=approved_operation(e,r,pid,body(e,pid,eid,'return',source,[dict(allocation_index=1,quantity=3)]));execute(e,pid,row)
    a=read(e,pid)['receipts'][0];assert a['on_hand']==10 and a['returned']==3 and a['consumed']==0
    reserve(e,pid,reservation_body(e,pid,comp,eid,stocks),409)
    movement(e,pid,eid,a['record'],'void')
    a=read(e,pid)['receipts'][0];assert a['on_hand']==10 and a['available']==0 and a['voided']
