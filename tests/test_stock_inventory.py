"""Synthetic full-bar inventory: atomic holds, evidence gates, stale plans and scope."""
import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from backend import store
from tests.test_stock_cutting import setup as single_setup
from tests.test_limited_stock_cutting import setup as limited_setup
from tests.test_mixed_stock_cutting import setup as mixed_setup
from tests.test_polygon_takeoff import post, mutate
from tests.test_workflow import get, approve, create, upload

FIELDS=['lot_code','warehouse','material_spec','stock_length','length_unit','unit_weight','weight_unit','quantity','basis']


def setup(clients, mode='single'):
    e,r,pid,s,b,eid={'single':single_setup,'limited':limited_setup,'mixed':mixed_setup}[mode](clients)
    comp=post(e,pid,b).json();approve(r,pid,'components',comp)
    return e,r,pid,comp,eid


def base(pid):return '/api/v1/projects/'+pid+'/stock-inventory'
def read(e,pid):
    response=e.get(base(pid));assert response.status_code==200,response.text;return response.json()
def common(e,pid):return dict(request_id=store.uid(),ledger_hash=read(e,pid)['ledger_hash'])


def receipt(e,r,pid,eid,*,length='6000',unit='mm',weight='3000',weight_unit='g/m',quantity='10',lot=None,approved=True):
    raw=dict(lot_code=lot or store.uid(),warehouse='合成A库（非真实仓库）',material_spec='合成同规格方管A',stock_length=length,length_unit=unit,unit_weight=weight,weight_unit=weight_unit,quantity=quantity,basis='合成实物凭据登记，仅软件验证，不代表真实入库',evidence={k:eid for k in FIELDS})
    body={**common(e,pid),'input':raw};response=e.post(base(pid)+'/receipts',json=body);assert response.status_code==200,response.text;row=response.json()
    if approved:approve(r,pid,'stock_receipts',row)
    return row,body


def demand(e,pid,comp):
    response=e.get(base(pid)+'/demands',params={'component_id':comp['id']});assert response.status_code==200,response.text;return response.json()


def reservation_body(e,pid,comp,eid,receipts):
    d=demand(e,pid,comp)
    return dict(**common(e,pid),source_id=comp['id'],demand_hash=d['demand_hash'],allocations=[dict(demand_key=x['key'],receipt_id=receipts[i]['id'],quantity=x['quantity']) for i,x in enumerate(d['demands'])],basis='合成原材与对应需求同材质、同截面、定尺和每米质量，核对本项目全部已占用；仅软件验证',evidence_id=eid,compatibility_confirmed=True)


def submit(e,pid,body,expected=200):
    response=e.post(base(pid)+'/reservations',json=body);assert response.status_code==expected,response.text;return response.json()


def movement(e,pid,eid,target,operation='release',expected=200):
    body=dict(**common(e,pid),operation=operation,target_id=target['id'],object_hash=store.digest(target),basis='合成核查后显式释放或停用，非实际退库',evidence_id=eid)
    response=e.post(base(pid)+'/events',json=body);assert response.status_code==expected,response.text;return response.json(),body


def test_receipt_review_hold_release_void_and_export_do_not_change_engineering(clients):
    e,r,pid,comp,eid=setup(clients);before=get(e,pid)
    stock,body=receipt(e,r,pid,eid,approved=False)
    assert read(e,pid)['receipts'][0]['available']==0
    assert e.post(base(pid)+'/receipts',json=body).json()==stock
    approve(r,pid,'stock_receipts',stock)
    state=read(e,pid);assert state['receipts'][0]['available']==10
    assert state['receipts'][0]['record']['normalized']==dict(quantity=10,stock_length='6',unit_weight='3')
    body=reservation_body(e,pid,comp,eid,[stock]);row=submit(e,pid,body)
    assert read(e,pid)['receipts'][0]['reserved']==3 and read(e,pid)['receipts'][0]['available']==7
    assert e.post(base(pid)+'/reservations',json=body).json()==row
    approve(r,pid,'stock_reservations',row)
    row=get(e,pid)['stock_reservations'][0]
    saved=e.get(base(pid)+'/export');assert saved.status_code==200 and saved.json()['schema']=='project-stock-inventory-v1'
    assert saved.headers['X-Artifact-Hash']==hashlib.sha256(saved.content).hexdigest()
    artifact=next(a for a in e.get('/api/v1/projects/'+pid+'/artifacts').json() if a.get('purpose')=='stock_inventory')
    assert e.get('/api/v1/projects/'+pid+'/artifacts/'+artifact['id']+'/file').content==saved.content
    movement(e,pid,eid,read(e,pid)['receipts'][0]['record'],'void',409)
    released,body=movement(e,pid,eid,row)
    assert e.post(base(pid)+'/events',json=body).json()==released
    assert read(e,pid)['receipts'][0]['available']==10
    approve(r,pid,'stock_reservations',row,expected=409)
    stock=read(e,pid)['receipts'][0]['record'];movement(e,pid,eid,stock,'void')
    state=read(e,pid);assert state['receipts'][0]['voided'] and state['receipts'][0]['available']==0
    approve(r,pid,'stock_receipts',stock,expected=409)
    # Same physical lot can be corrected only after original registration is voided.
    receipt(e,r,pid,eid,lot=stock['input']['lot_code'])
    after=get(e,pid)
    for k in ('components','takeoffs','boq','quotes','report_approval','stale_quotes','status'):
        assert before[k]==after[k]
    assert saved.json()['reservations'][0]['active'] # Old export bytes remain unchanged.


@pytest.mark.parametrize('mode,expected',[('single',[('6',3)]),('mixed',[('6',4)]),('limited',[('6',2),('3.523',2),('2.423',2)])])
def test_extracts_all_final_counts_once_including_finite_stock_options(clients,mode,expected):
    e,r,pid,comp,eid=setup(clients,mode);d=demand(e,pid,comp)
    assert [(x['stock_length'],x['quantity']) for x in d['demands']]==expected
    receipts=[receipt(e,r,pid,eid,length=x['stock_length'],unit='m',quantity=str(x['quantity']))[0] for x in d['demands']]
    row=submit(e,pid,reservation_body(e,pid,comp,eid,receipts));approve(r,pid,'stock_reservations',row)
    assert all(x['available']==0 for x in read(e,pid)['receipts'])


def test_split_batches_and_no_pooled_length_substitution(clients):
    e,r,pid,comp,eid=setup(clients);a,_=receipt(e,r,pid,eid,quantity='1');b,_=receipt(e,r,pid,eid,quantity='2')
    body=reservation_body(e,pid,comp,eid,[a]);key=body['allocations'][0]['demand_key']
    body['allocations']=[dict(demand_key=key,receipt_id=a['id'],quantity=1),dict(demand_key=key,receipt_id=b['id'],quantity=2)]
    row=submit(e,pid,body);approve(r,pid,'stock_reservations',row)
    assert sum(x['reserved'] for x in read(e,pid)['receipts'])==3


@pytest.mark.parametrize('fault',['empty','missing','extra','false','truthy','duplicate','zero','negative','float','bool','wrong-demand','wrong-stock','no-basis','no-evidence','wrong-length','wrong-weight','not-approved','insufficient','wrong-demand-hash','wrong-ledger'])
def test_invalid_or_incompatible_allocations_never_occupy(clients,fault):
    e,r,pid,comp,eid=setup(clients)
    stock,_=receipt(e,r,pid,eid,length='5000' if fault=='wrong-length' else '6000',weight='2' if fault=='wrong-weight' else '3000',quantity='2' if fault=='insufficient' else '10',approved=fault!='not-approved')
    body=reservation_body(e,pid,comp,eid,[stock])
    if fault=='empty':body['allocations']=[]
    elif fault=='missing':body.pop('basis')
    elif fault=='extra':body['quantity']=3
    elif fault in ('false','truthy'):body['compatibility_confirmed']=False if fault=='false' else 'true'
    elif fault=='duplicate':body['allocations']*=2
    elif fault in ('zero','negative','float','bool'):body['allocations'][0]['quantity']={'zero':0,'negative':-1,'float':3.0,'bool':True}[fault]
    elif fault=='wrong-demand':body['allocations'][0]['demand_key']='group:88:1'
    elif fault=='wrong-stock':body['allocations'][0]['receipt_id']='unavailable'
    elif fault=='no-basis':body['basis']=''
    elif fault=='no-evidence':body['evidence_id']='missing'
    elif fault=='wrong-demand-hash':body['demand_hash']='old'
    elif fault=='wrong-ledger':body['ledger_hash']='old'
    response=e.post(base(pid)+'/reservations',json=body);assert response.status_code in (404,409,422),response.text
    assert not read(e,pid)['reservations'] and read(e,pid)['receipts'][0]['reserved']==0


@pytest.mark.parametrize('fault',['null','extra','missing-proof','zero','fractional','too-many','bad-unit','bad-weight-unit','missing-evidence','duplicate-lot'])
def test_invalid_receipts_never_add_available_stock(clients,fault):
    e,r,pid,comp,eid=setup(clients);stock,body=receipt(e,r,pid,eid)
    body={**body,**common(e,pid)}
    if fault=='null':body['input']=None
    elif fault=='extra':body['input']['unknown']=1
    elif fault=='missing-proof':body['input']['evidence'].pop('basis')
    elif fault in ('zero','fractional','too-many'):body['input']['quantity']={'zero':'0','fractional':'2.5','too-many':'1000001'}[fault]
    elif fault=='bad-unit':body['input']['length_unit']='m²'
    elif fault=='bad-weight-unit':body['input']['weight_unit']='kg'
    elif fault=='missing-evidence':body['input']['evidence']['basis']='missing'
    response=e.post(base(pid)+'/receipts',json=body);assert response.status_code in (404,409,422),response.text
    assert len(read(e,pid)['receipts'])==1


def test_duplicate_plan_stale_source_rejection_and_explicit_release(clients):
    e,r,pid,comp,eid=setup(clients);stock,_=receipt(e,r,pid,eid);row=submit(e,pid,reservation_body(e,pid,comp,eid,[stock]))
    submit(e,pid,reservation_body(e,pid,comp,eid,[stock]),409)
    approve(r,pid,'stock_reservations',row,'reject')
    assert read(e,pid)['receipts'][0]['reserved']==3
    # A real revision through the normal component editor leaves old hold occupied.
    revised=copy.deepcopy(get(e,pid)['components'][-1]);old_hash=store.digest(revised);revised['name']='合成修订材料表';revised['object_hash']=old_hash;response=e.put('/api/v1/projects/'+pid+'/components/'+comp['id'],json=revised);assert response.status_code==200,response.text
    state=read(e,pid);assert state['receipts'][0]['reserved']==3 and state['reservations'][0]['issue']
    approve(r,pid,'stock_reservations',get(e,pid)['stock_reservations'][0],expected=409)
    movement(e,pid,eid,get(e,pid)['stock_reservations'][0])
    assert read(e,pid)['receipts'][0]['reserved']==0


@pytest.mark.parametrize('action',['preview','register','reserve','review','export'])
def test_original_bytes_missing_or_tampered_fail_closed_without_freeing_holds(clients,action):
    e,r,pid,comp,eid=setup(clients);stock,_=receipt(e,r,pid,eid);body=reservation_body(e,pid,comp,eid,[stock]);row=submit(e,pid,body)
    doc=get(e,pid)['documents'][-1];path=store.ROOT/'originals'/doc['hash'];raw=path.read_bytes();path.write_bytes(b'tampered')
    if action=='preview':assert e.get(base(pid)+'/demands',params={'component_id':comp['id']}).status_code==409
    elif action=='register':
        rawinput=copy.deepcopy(stock['input']);rawinput['lot_code']='new';assert e.post(base(pid)+'/receipts',json={**common(e,pid),'input':rawinput}).status_code==409
    elif action=='reserve':
        body.update(common(e,pid));assert e.post(base(pid)+'/reservations',json=body).status_code==409
    elif action=='review':approve(r,pid,'stock_reservations',row,expected=409)
    else:
        exported=e.get(base(pid)+'/export');assert exported.status_code==200 and exported.json()['reservations'][0]['issue']
    state=read(e,pid);assert state['receipts'][0]['reserved']==3 and state['receipts'][0]['available']==0
    path.write_bytes(raw)
    assert read(e,pid)['receipts'][0]['available']==7


def test_scope_roles_idempotency_and_serialized_concurrent_claims(clients):
    e,r,pid,comp,eid=setup(clients);stock,body=receipt(e,r,pid,eid,quantity='3');reserve=reservation_body(e,pid,comp,eid,[stock])
    other=create(e)['id'];assert e.get(base(other)+'/demands',params={'component_id':comp['id']}).status_code==404
    for path in ('','/demands?component_id='+comp['id'],'/export'):
        assert clients['other'].get(base(pid)+path).status_code==404
    assert r.post(base(pid)+'/receipts',json=body).status_code==403
    assert r.post(base(pid)+'/reservations',json=reserve).status_code==403
    competing={**reserve,'request_id':store.uid()}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda b:e.post(base(pid)+'/reservations',json=b),[reserve,competing]))
    assert sorted(x.status_code for x in results)==[200,409]
    assert read(e,pid)['receipts'][0]['available']==0 and len(read(e,pid)['reservations'])==1
    winning=reserve if results[0].status_code==200 else competing
    assert e.post(base(pid)+'/reservations',json=winning).status_code==200
    assert e.post(base(pid)+'/reservations',json={**winning,'basis':'different'}).status_code==409
    row=read(e,pid)['reservations'][0]['record'];release,body=movement(e,pid,eid,row)
    assert r.post(base(pid)+'/events',json=body).status_code==403
    assert clients['other'].post(base(pid)+'/events',json=body).status_code==404


@pytest.mark.parametrize('collection',['stock_receipts','stock_reservations','stock_events'])
def test_tampered_ledger_blocks_all_occupancy_and_export(clients,collection):
    e,r,pid,comp,eid=setup(clients);stock,_=receipt(e,r,pid,eid);row=submit(e,pid,reservation_body(e,pid,comp,eid,[stock]));movement(e,pid,eid,row)
    with store.transaction() as c:
        p=json.loads(c.execute('SELECT data FROM projects WHERE id=?',(pid,)).fetchone()['data']);p[collection][0]['author']='tampered';store.save(c,p)
    assert e.get(base(pid)).status_code==409 and e.get(base(pid)+'/export').status_code==409


def test_two_different_plans_cannot_overbook_same_batch_even_after_refresh(clients):
    e,r,pid,comp,eid=setup(clients)
    second=copy.deepcopy(comp);second['name']='另一合成材料表';second=post(e,pid,second).json();approve(r,pid,'components',second)
    stock,_=receipt(e,r,pid,eid,quantity='3')
    a=reservation_body(e,pid,comp,eid,[stock]);b=reservation_body(e,pid,second,eid,[stock])
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda x:e.post(base(pid)+'/reservations',json=x),[a,b]))
    assert sorted(x.status_code for x in responses)==[200,409]
    loser=b if responses[0].status_code==200 else a
    loser.update(common(e,pid));response=e.post(base(pid)+'/reservations',json=loser)
    assert response.status_code==409 and '可用根数不足' in response.json()['detail']
    assert read(e,pid)['receipts'][0]['reserved']==3


def test_missing_original_and_receipt_review_revocation_freeze_held_stock(clients):
    e,r,pid,comp,eid=setup(clients);stock,_=receipt(e,r,pid,eid);row=submit(e,pid,reservation_body(e,pid,comp,eid,[stock]));approve(r,pid,'stock_reservations',row)
    stock=read(e,pid)['receipts'][0]['record'];approve(r,pid,'stock_receipts',stock,'request_more_info')
    state=read(e,pid);assert state['receipts'][0]['available']==0 and state['receipts'][0]['reserved']==3 and state['reservations'][0]['issue']
    approve(r,pid,'stock_receipts',read(e,pid)['receipts'][0]['record'])
    doc=get(e,pid)['documents'][-1];path=store.ROOT/'originals'/doc['hash'];raw=path.read_bytes();path.unlink()
    assert read(e,pid)['receipts'][0]['available']==0 and read(e,pid)['receipts'][0]['reserved']==3
    approve(r,pid,'stock_reservations',get(e,pid)['stock_reservations'][0],expected=409)
    path.write_bytes(raw)


def test_review_cannot_be_signed_by_original_author_after_role_change(clients):
    e,r,pid,comp,eid=setup(clients);stock,_=receipt(e,r,pid,eid,approved=False)
    with store.transaction() as c:c.execute("UPDATE users SET role='reviewer' WHERE username='estimator'")
    approve(e,pid,'stock_receipts',stock,expected=403)


def test_stale_event_hash_and_changed_idempotent_body_do_not_free_stock(clients):
    e,r,pid,comp,eid=setup(clients);stock,_=receipt(e,r,pid,eid);row=submit(e,pid,reservation_body(e,pid,comp,eid,[stock]))
    body=dict(**common(e,pid),operation='release',target_id=row['id'],object_hash='old',basis='合成检查',evidence_id=eid)
    assert e.post(base(pid)+'/events',json=body).status_code==409
    assert read(e,pid)['receipts'][0]['reserved']==3
    row=read(e,pid)['reservations'][0]['record'];done,body=movement(e,pid,eid,row)
    assert e.post(base(pid)+'/events',json={**body,'basis':'changed'}).status_code==409
    assert len(read(e,pid)['events'])==1


@pytest.mark.parametrize('collection',['stock_receipts','stock_reservations'])
def test_forged_approved_state_without_matching_independent_review_is_not_effective(clients,collection):
    e,r,pid,comp,eid=setup(clients);stock,_=receipt(e,r,pid,eid,approved=collection!='stock_receipts')
    if collection=='stock_reservations':submit(e,pid,reservation_body(e,pid,comp,eid,[stock]))
    mutate(pid,lambda p:p[collection][0].update(state='approved'))
    state=read(e,pid)
    if collection=='stock_receipts':
        assert state['receipts'][0]['available']==0 and state['receipts'][0]['issue']
        submit(e,pid,reservation_body(e,pid,comp,eid,[stock]),409)
    else:
        assert state['reservations'][0]['issue'] and state['receipts'][0]['reserved']==3


def test_receipt_approval_and_void_preserve_existing_approved_quote_and_export(clients):
    from tests.test_workflow import priced_project
    e,r,pid,_,q=priced_project(clients)
    approve(r,pid,'quotes',q)
    before=get(e,pid);eid=before['evidence'][-1]['id']
    assert before['report_approval']['quote_id']==q['id']
    archived=e.get('/api/v1/projects/'+pid+'/exports?format=json&quote_id='+q['id']);assert archived.status_code==200
    stock,_=receipt(e,r,pid,eid);movement(e,pid,eid,read(e,pid)['receipts'][0]['record'],'void')
    after=get(e,pid)
    for k in ('components','takeoffs','boq','quotes','report_approval','stale_quotes','status'):
        assert before[k]==after[k]
    exported=e.get('/api/v1/projects/'+pid+'/exports?format=json&quote_id='+q['id']);assert exported.status_code==200
    assert exported.json()['quote']==archived.json()['quote']
