"""Actual machining conservation, evidence-backed offcuts, immutable admission and duplicate guards."""
import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, localcontext
import pytest
from backend import store, stock_inventory as inv
from tests.test_stock_operations import setup as operation_setup, issue, approved_operation, body as operation_body, execute as execute_operation, today
from tests.test_stock_inventory import base, common, read, receipt, reservation_body, submit as reserve
from tests.test_workflow import get, approve, create, upload
from tests.test_polygon_takeoff import mutate, post

METRICS=('finished_length','kerf_length','trim_length','scrap_length')


def setup(clients, count=1, mode='single'):
    e,r,pid,comp,eid,res,stocks=operation_setup(clients,mode)
    source=issue(e,r,pid,eid,res)
    selections=[dict(allocation_index=a['allocation_index'],quantity=count) for a in source['allocations']]
    consumed=approved_operation(e,r,pid,operation_body(e,pid,eid,'consume',source,selections));execute_operation(e,pid,consumed)
    return e,r,pid,comp,eid,consumed,stocks


def raw(consumed,eid,code='SYN-OFFCUT-A'):
    a=consumed['allocations'][0]
    return dict(consumption_id=consumed['id'],consumption_hash=store.digest(consumed),business_date=today(),inspector='合成实物验收人（非真实工程）',basis='合成实测加工及可留用余料，仅软件验收；与计划偏差须据原件核定，不按计划自动入库',evidence_id=eid,confirmed=True,
        rows=[dict(allocation_index=a['allocation_index'],quantity=1,unit='m',finished_length='2.4',kerf_length='0.006',trim_length='0.02',scrap_length='0',measurement_basis='合成一根6m实际产出2.4m成品，锯缝.006m、两端切除.02m、实物余料3.574m，偏差按实际凭据说明，非真实交接',basis_evidence_id=eid,evidence={k:eid for k in ('quantity',)+METRICS},
            offcuts=[dict(lot_code=code,length='3.574',quantity=1,warehouse='合成余料库',acceptance_basis='合成实测尺寸、无缺陷可留用，同材质同截面及3kg/m已核，非真实仓库验收',evidence={k:eid for k in ('lot_code','length','quantity','warehouse','acceptance_basis')})])])


def preview(e,pid,value,expected=200):
    response=e.post(base(pid)+'/processing-results/preview',json={'input':value});assert response.status_code==expected,response.text;return response.json()


def submission(e,pid,value):
    v=preview(e,pid,value)
    return dict(request_id=store.uid(),ledger_hash=v['ledger_hash'],input=value,preview_hash=v['preview_hash'])


def submit(e,pid,b,expected=200):
    response=e.post(base(pid)+'/processing-results',json=b);assert response.status_code==expected,response.text;return response.json()


def ready(e,r,pid,value):
    row=submit(e,pid,submission(e,pid,value));approve(r,pid,'stock_processing_results',row)
    return next(x for x in get(e,pid)['stock_processing_results'] if x['id']==row['id'])


def execute(e,pid,row,expected=200,b=None):
    b=b or {**common(e,pid),'object_hash':store.digest(row)}
    response=e.post(base(pid)+'/processing-results/'+row['id']+'/execute',json=b);assert response.status_code==expected,response.text;return response.json(),b


def stop(e,pid,row,eid,expected=200):
    b={**common(e,pid),'object_hash':store.digest(row),'basis':'合成核查后停用余料登记，不表示实物报废或退还加工额度','evidence_id':eid}
    response=e.post(base(pid)+'/processing-results/'+row['id']+'/offcuts/1/1/void',json=b);assert response.status_code==expected,response.text;return response.json(),b


def test_actual_preview_review_admission_stop_and_archived_bytes_keep_raw_stock_unchanged(clients):
    e,r,pid,comp,eid,source,stocks=setup(clients);before=get(e,pid);before_stock=read(e,pid)['receipts']
    value=raw(source,eid);pview=preview(e,pid,value);assert get(e,pid)==before
    t=pview['result']['totals'];assert t['raw_count']==t['offcut_count']==1 and t['input_length']=='6' and t['offcut_length']=='3.574'
    assert t['input_mass']=='18' and t['finished_mass']=='7.2' and t['offcut_mass']=='10.722' and t['balance_difference']=='0'
    b=dict(request_id=store.uid(),ledger_hash=pview['ledger_hash'],input=value,preview_hash=pview['preview_hash']);row=submit(e,pid,b)
    assert submit(e,pid,b)==row and not read(e,pid)['processing']['lots']
    execute(e,pid,row,409);approve(r,pid,'stock_processing_results',row);row=get(e,pid)['stock_processing_results'][0]
    assert not read(e,pid)['processing']['lots']
    event,b=execute(e,pid,row);assert execute(e,pid,row,b=b)[0]==event
    v=read(e,pid);lot=v['processing']['lots'][0]
    assert lot['length']=='3.574' and lot['in_stock_quantity']==1 and lot['mass']=='10.722' and lot['verified']
    assert v['processing']['sources'][0]['allocations'][0]['unaccounted']==0
    assert v['receipts']==before_stock
    frozen=e.get(base(pid)+'/export');assert frozen.status_code==200
    done,b=stop(e,pid,row,eid);assert e.post(base(pid)+'/processing-results/'+row['id']+'/offcuts/1/1/void',json=b).json()==done
    v=read(e,pid);assert v['processing']['lots'][0]['in_stock_quantity']==1 and v['processing']['lots'][0]['adopted_quantity']==0 and not v['processing']['lots'][0]['verified']
    assert v['processing']['sources'][0]['allocations'][0]['unaccounted']==0
    preview(e,pid,value,409);approve(r,pid,'stock_processing_results',row,expected=409)
    artifact=next(a for a in e.get('/api/v1/projects/'+pid+'/artifacts').json() if a['hash']==hashlib.sha256(frozen.content).hexdigest())
    assert e.get('/api/v1/projects/'+pid+'/artifacts/'+artifact['id']+'/file').content==frozen.content
    after=get(e,pid)
    for k in ('components','takeoffs','boq','quotes','report_approval','stale_quotes','status','stock_operations','stock_receipts','stock_reservations'):assert before[k]==after[k]


def test_actual_results_may_differ_from_plan_and_no_offcut_is_also_valid(clients):
    e,r,pid,comp,eid,source,stocks=setup(clients,count=2);v=raw(source,eid);x=v['rows'][0]
    x.update(finished_length='3',kerf_length='.01',trim_length='.04',scrap_length='.45');x['offcuts'][0]['length']='2.5'
    row=ready(e,r,pid,v);execute(e,pid,row)
    assert read(e,pid)['processing']['lots'][0]['length']=='2.5'
    v=raw(source,eid,code='unused');x=v['rows'][0];x.update(finished_length='5.9',kerf_length='.01',trim_length='.02',scrap_length='.07',offcuts=[])
    row=ready(e,r,pid,v);execute(e,pid,row)
    s=read(e,pid)['processing'];assert len(s['lots'])==1 and s['sources'][0]['allocations'][0]['unaccounted']==0


@pytest.mark.parametrize('unit,multiplier',[('m','1'),('mm','1000'),('cm','100'),('dm','10'),('km','.001')])
def test_units_multiple_lengths_and_low_caller_precision(unit,multiplier,clients):
    e,r,pid,comp,eid,source,stocks=setup(clients);v=raw(source,eid);x=v['rows'][0];x['unit']=unit
    x['offcuts']*=1
    x['offcuts']=[{**copy.deepcopy(x['offcuts'][0]),'lot_code':'A','length':'1.5','quantity':2},{**copy.deepcopy(x['offcuts'][0]),'lot_code':'B','length':'.574','quantity':1}]
    for k in METRICS:x[k]=str(Decimal(x[k])*Decimal(multiplier))
    for o in x['offcuts']:o['length']=str(Decimal(o['length'])*Decimal(multiplier))
    with localcontext() as ctx:
        ctx.prec=6;view=preview(e,pid,v)
    assert view['result']['totals']['offcut_count']==3 and view['result']['totals']['offcut_length']=='3.574' and view['result']['totals']['offcut_mass']=='10.722'


def test_multichoice_materials_and_each_allocation_balance(clients):
    e,r,pid,comp,eid,source,stocks=setup(clients,mode='limited');v=raw(source,eid);template=v['rows'][0];v['rows']=[]
    for i,a in enumerate(source['allocations']):
        x=copy.deepcopy(template);half=Decimal(a['stock_length'])/2;x.update(allocation_index=a['allocation_index'],finished_length=str(half),kerf_length='0',trim_length='0',scrap_length='0');x['offcuts'][0].update(lot_code='O'+str(i),length=str(half));v['rows'].append(x)
    row=ready(e,r,pid,v);execute(e,pid,row);s=read(e,pid)['processing']
    assert len(s['lots'])==3 and row['totals']['input_length']=='11.946' and row['totals']['offcut_length']=='5.973' and row['totals']['offcut_mass']=='17.919'
    assert all(a['unaccounted']==0 for a in s['sources'][0]['allocations'])


@pytest.mark.parametrize('fault',['null','missing','extra','false','truthy','bad-date','no-inspector','no-basis','no-proof','wrong-hash','empty-rows','too-many-rows','duplicate-row','wrong-index','index-bool','quantity-bool','quantity-float','zero-quantity','over-quantity','bad-unit','negative-metric','missing-metric-proof','missing-row-basis','missing-offcut-proof','zero-length','full-length','negative-count','decimal-count','duplicate-lot','too-many-offcuts','not-closed','tiny-discrepancy','extra-offcut','complete-lot-code'])
def test_actual_input_and_exact_conservation_fail_closed(clients,fault):
    e,r,pid,comp,eid,source,stocks=setup(clients);v=raw(source,eid);x=v['rows'][0];o=x['offcuts'][0]
    if fault=='null':v=None
    elif fault=='missing':v.pop('basis')
    elif fault=='extra':v['planned_offcut']=3.574
    elif fault in ('false','truthy'):v['confirmed']=False if fault=='false' else 'true'
    elif fault=='bad-date':v['business_date']='2026-02-30'
    elif fault=='no-inspector':v['inspector']=''
    elif fault=='no-basis':v['basis']=''
    elif fault=='no-proof':v['evidence_id']='missing'
    elif fault=='wrong-hash':v['consumption_hash']='old'
    elif fault=='empty-rows':v['rows']=[]
    elif fault=='too-many-rows':v['rows']*=21
    elif fault=='duplicate-row':v['rows']*=2
    elif fault=='wrong-index':x['allocation_index']=99
    elif fault=='index-bool':x['allocation_index']=True
    elif fault=='quantity-bool':x['quantity']=True
    elif fault=='quantity-float':x['quantity']=1.0
    elif fault=='zero-quantity':x['quantity']=0
    elif fault=='over-quantity':x['quantity']=2
    elif fault=='bad-unit':x['unit']='m²'
    elif fault=='negative-metric':x['trim_length']='-.01'
    elif fault=='missing-metric-proof':x['evidence'].pop('trim_length')
    elif fault=='missing-row-basis':x['basis_evidence_id']='missing'
    elif fault=='missing-offcut-proof':o['evidence'].pop('length')
    elif fault=='zero-length':o['length']='0'
    elif fault=='full-length':o['length']='6'
    elif fault=='negative-count':o['quantity']=-1
    elif fault=='decimal-count':o['quantity']=1.2
    elif fault=='duplicate-lot':x['offcuts']*=2
    elif fault=='too-many-offcuts':x['offcuts']*=21
    elif fault=='not-closed':o['length']='3.5'
    elif fault=='tiny-discrepancy':o['length']='3.574000000000001'
    elif fault=='extra-offcut':o['weight']='3'
    else:o['lot_code']=stocks[0]['input']['lot_code'];o['warehouse']=stocks[0]['input']['warehouse']
    response=e.post(base(pid)+'/processing-results/preview',json={'input':v});assert response.status_code in (404,409,422),response.text
    assert not read(e,pid)['processing']['results'] and not read(e,pid)['processing']['lots']


def test_preview_staleness_and_edits_require_new_preview(clients):
    e,r,pid,comp,eid,source,stocks=setup(clients);v=raw(source,eid);b=submission(e,pid,v)
    b['input']['inspector']='另一个合成验收人';submit(e,pid,b,409)
    b=submission(e,pid,v);b['ledger_hash']='old';submit(e,pid,b,409)
    assert not read(e,pid)['processing']['results']


def test_unexecuted_result_can_be_cancelled_and_does_not_reserve_capacity(clients):
    e,r,pid,comp,eid,source,stocks=setup(clients);v=raw(source,eid);row=submit(e,pid,submission(e,pid,v))
    b={**common(e,pid),'operation':'cancel_processing','target_id':row['id'],'object_hash':store.digest(row),'basis':'合成未执行验收取消，仅软件验证','evidence_id':eid}
    assert e.post(base(pid)+'/events',json=b).status_code==200
    approve(r,pid,'stock_processing_results',row,expected=409)
    assert read(e,pid)['processing']['sources'][0]['allocations'][0]['unaccounted']==1
    row=ready(e,r,pid,v);execute(e,pid,row)
    assert read(e,pid)['processing']['results'][0]['phase']=='cancelled'


@pytest.mark.parametrize('duplicate_code',[False,True])
def test_reviewed_results_race_without_duplicate_processing_or_lot_admission(clients,duplicate_code):
    e,r,pid,comp,eid,source,stocks=setup(clients,count=2 if duplicate_code else 1)
    a=ready(e,r,pid,raw(source,eid,'SAME'));b=ready(e,r,pid,raw(source,eid,'SAME' if duplicate_code else 'OTHER'))
    token=common(e,pid)['ledger_hash'];requests=[dict(request_id=store.uid(),ledger_hash=token,object_hash=store.digest(row)) for row in (a,b)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda pair:e.post(base(pid)+'/processing-results/'+pair[0]['id']+'/execute',json=pair[1]),zip((a,b),requests)))
    assert sorted(x.status_code for x in responses)==[200,409]
    execute(e,pid,b if responses[0].status_code==200 else a,409)
    assert len(read(e,pid)['processing']['lots'])==1


@pytest.mark.parametrize('gate',['preview','save','review','execute'])
def test_actual_original_bytes_gate_preserves_capacity_and_no_stock_admission(clients,gate):
    e,r,pid,comp,eid,source,stocks=setup(clients);v=raw(source,eid);b=submission(e,pid,v)
    row=submit(e,pid,b) if gate in ('review','execute') else None
    if gate=='execute':approve(r,pid,'stock_processing_results',row);row=get(e,pid)['stock_processing_results'][0]
    doc=get(e,pid)['documents'][-1];path=store.ROOT/'originals'/doc['hash'];data=path.read_bytes();path.write_bytes(b'tampered')
    if gate=='preview':preview(e,pid,v,409)
    elif gate=='save':submit(e,pid,b,409)
    elif gate=='review':approve(r,pid,'stock_processing_results',row,expected=409)
    else:execute(e,pid,row,409)
    s=read(e,pid)['processing'];assert not s['lots'] and s['sources'][0]['allocations'][0]['unaccounted']==1
    path.write_bytes(data)


def test_executed_historical_proof_loss_never_recredits_processing_capacity(clients):
    e,r,pid,comp,eid,source,stocks=setup(clients);row=ready(e,r,pid,raw(source,eid));execute(e,pid,row)
    doc=get(e,pid)['documents'][-1];path=store.ROOT/'originals'/doc['hash'];data=path.read_bytes();path.unlink()
    s=read(e,pid)['processing'];assert s['lots'][0]['in_stock_quantity']==1 and not s['lots'][0]['verified'] and s['results'][0]['issue']
    assert s['sources'][0]['allocations'][0]['unaccounted']==0
    path.write_bytes(data)


def test_project_roles_self_review_and_forged_approval_do_not_admit_stock(clients):
    e,r,pid,comp,eid,source,stocks=setup(clients);v=raw(source,eid);b=submission(e,pid,v)
    assert r.post(base(pid)+'/processing-results',json=b).status_code==403
    assert clients['other'].post(base(pid)+'/processing-results/preview',json={'input':v}).status_code==404
    other=create(e)['id'];assert e.post(base(other)+'/processing-results/preview',json={'input':v}).status_code==404
    row=submit(e,pid,b);mutate(pid,lambda p:p['stock_processing_results'][0].update(state='approved'));row=get(e,pid)['stock_processing_results'][0]
    execute(e,pid,row,409)
    with store.transaction() as c:c.execute("UPDATE users SET role='reviewer' WHERE username='estimator'")
    approve(e,pid,'stock_processing_results',row,expected=403)
    assert not read(e,pid)['processing']['lots']


@pytest.mark.parametrize('fault',['record','event','cross-link','void-link'])
def test_recovery_ledger_tampering_blocks_read_and_export(clients,fault):
    e,r,pid,comp,eid,source,stocks=setup(clients);row=ready(e,r,pid,raw(source,eid));execute(e,pid,row)
    if fault=='void-link':stop(e,pid,row,eid)
    def corrupt(p):
        if fault=='record':p['stock_processing_results'][0]['totals']['offcut_length']='9'
        elif fault=='event':p['stock_events'][-1]['totals']['offcut_length']='9'
        elif fault=='cross-link':p['stock_events'][-1]['record_hash']='wrong';p['stock_events'][-1]=inv.seal(p['stock_events'][-1])
        else:p['stock_events'][-1]['result_id']='missing';p['stock_events'][-1]=inv.seal(p['stock_events'][-1])
    mutate(pid,corrupt);assert e.get(base(pid)).status_code==409 and e.get(base(pid)+'/export').status_code==409


def test_new_reservations_use_actual_on_hand_after_issue_not_initial_registration(clients):
    e,r,pid,comp,eid,source,stocks=setup(clients)
    second=copy.deepcopy(comp);second['name']='合成另一材料表';second=post(e,pid,second).json();approve(r,pid,'components',second)
    # All ten original roots are no longer available when two further demands of three are held.
    first=reserve(e,pid,reservation_body(e,pid,second,eid,stocks))
    third=copy.deepcopy(comp);third['name']='合成第三材料表';third=post(e,pid,third).json();approve(r,pid,'components',third)
    reserve(e,pid,reservation_body(e,pid,third,eid,stocks))
    fourth=copy.deepcopy(comp);fourth['name']='合成第四材料表';fourth=post(e,pid,fourth).json();approve(r,pid,'components',fourth)
    reserve(e,pid,reservation_body(e,pid,fourth,eid,stocks),409)
    a=read(e,pid)['receipts'][0];assert a['on_hand']==7 and a['reserved']==6 and a['available']==1


def test_offcut_identifier_cannot_be_redeclared_as_complete_stock_after_admission(clients):
    e,r,pid,comp,eid,source,stocks=setup(clients);row=ready(e,r,pid,raw(source,eid));execute(e,pid,row)
    old=read(e,pid)['receipts'][0]['record']['input'];value=copy.deepcopy(old);value.update(lot_code='SYN-OFFCUT-A',warehouse='合成余料库',stock_length='3574',quantity='1')
    b={**common(e,pid),'input':value};response=e.post(base(pid)+'/receipts',json=b)
    assert response.status_code==409 and '余料' in response.json()['detail']
    assert len(read(e,pid)['receipts'])==1
