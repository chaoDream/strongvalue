"""Synthetic machining starts, pending quantities, partial settlement and live gates."""
import copy
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import date,timedelta
from decimal import localcontext
import pytest
from backend import store,stock_inventory as inv
from tests.test_stock_offcut_returns import setup,raw_return,ready_return,move as return_move
from tests.test_stock_offcut_processing import raw as result_raw,ready as ready_result,move as result_move,preview as result_preview,stop
from tests.test_stock_inventory import read,common,base
from tests.test_workflow import get,approve,upload,create
from tests.test_polygon_takeoff import mutate
from tests.test_stock_operations import today


def raw(source,eid,quantity=1):
    return dict(reference_id=source['id'],reference_hash=store.digest(source),selections=[dict(allocation_index=1,quantity=quantity)],business_date=today(),workshop='合成加工工位A',custodian='合成加工接收班组A',basis='合成原段实际投入加工，尚未取得完整结果，不推定成品损耗或新余料，非真实工程',evidence_id=eid,confirmed=True)


def preview(e,pid,v,expected=200):
    response=e.post(base(pid)+'/offcut-jobs/preview',json={'input':v});assert response.status_code==expected,response.text;return response.json()


def submission(e,pid,v):
    p=preview(e,pid,v);return dict(request_id=store.uid(),ledger_hash=p['ledger_hash'],input=v,preview_hash=p['preview_hash'])


def submit(e,pid,b,expected=200):
    response=e.post(base(pid)+'/offcut-jobs',json=b);assert response.status_code==expected,response.text;return response.json()


def ready(e,r,pid,v):
    row=submit(e,pid,submission(e,pid,v));approve(r,pid,'stock_offcut_jobs',row);return next(x for x in get(e,pid)['stock_offcut_jobs'] if x['id']==row['id'])


def move(e,pid,row,kind='execute',eid=None,expected=200,b=None):
    b=b or dict(**common(e,pid),object_hash=store.digest(row),**(dict(basis='合成取消未执行投入，不回滚实际投入或退回实物',evidence_id=eid) if kind=='cancel' else {}))
    response=e.post(base(pid)+'/offcut-jobs/'+row['id']+'/'+kind,json=b);assert response.status_code==expected,response.text;return response.json(),b


def result(source,eid,job,code='SYN-JOB-CHILD'):
    return dict(**result_raw(source,eid,code=code),job_id=job['id'],job_hash=store.digest(job))


def test_start_preview_review_execute_then_partial_results_never_double_debit(clients):
    e,r,pid,comp,c,eid,recovery,lot,source=setup(clients,count=2);before=get(e,pid);old=read(e,pid)
    p=preview(e,pid,raw(source,eid,2));assert get(e,pid)==before
    assert p['result']['totals']['length']=='7.148' and p['result']['totals']['mass']=='21.444'
    b=submission(e,pid,raw(source,eid,2));row=submit(e,pid,b);assert submit(e,pid,b)==row
    assert read(e,pid)['processing']['lots'][0]['in_custody']==2
    move(e,pid,row,expected=409);approve(r,pid,'stock_offcut_jobs',row);row=get(e,pid)['stock_offcut_jobs'][0]
    assert read(e,pid)['processing']['lots'][0]['in_custody']==2
    frozen=e.get(base(pid)+'/export');event,b=move(e,pid,row);assert move(e,pid,row,b=b)[0]==event
    v=read(e,pid);parent=v['processing']['lots'][0];job=v['offcut_jobs']['records'][0]
    assert (parent['in_stock_quantity'],parent['in_custody'],parent['processing_quantity'],parent['processed_quantity'])==(0,0,2,0)
    assert len(v['processing']['lots'])==1 and job['remaining']==2 and job['accounted']==0 and job['progress']=='pending'
    assert v['offcut_reuse']['reservations'][0]['balances'][0]['outstanding']==0
    result_preview(e,pid,result_raw(source,eid),409);response=e.post(base(pid)+'/offcut-returns/preview',json={'input':raw_return(source,eid)});assert response.status_code==409
    stop(e,pid,recovery,eid,409);move(e,pid,row,'cancel',eid,409);approve(r,pid,'stock_offcut_jobs',row,expected=409)
    a=ready_result(e,r,pid,result(source,eid,row));assert read(e,pid)['offcut_jobs']['records'][0]['remaining']==2
    result_move(e,pid,a);v=read(e,pid);job=v['offcut_jobs']['records'][0];parent=v['processing']['lots'][0]
    assert (job['remaining'],job['accounted'],job['progress'])==(1,1,'partial')
    assert (parent['in_custody'],parent['processing_quantity'],parent['processed_quantity'])==(0,1,1)
    b=ready_result(e,r,pid,result(source,eid,row,'SYN-JOB-CHILD-B'));result_move(e,pid,b);v=read(e,pid);job=v['offcut_jobs']['records'][0];parent=v['processing']['lots'][0]
    assert (job['remaining'],job['accounted'],job['progress'])==(0,2,'complete') and parent['processed_quantity']==2 and parent['processing_quantity']==0
    assert parent['quantity']==parent['in_stock_quantity']+parent['in_custody']+parent['processing_quantity']+parent['processed_quantity']
    result_preview(e,pid,result(source,eid,row,'EXTRA'),409)
    artifacts=e.get('/api/v1/projects/'+pid+'/artifacts').json();a=next(a for a in artifacts if a['hash']==hashlib.sha256(frozen.content).hexdigest());assert e.get('/api/v1/projects/'+pid+'/artifacts/'+a['id']+'/file').content==frozen.content
    assert frozen.json()['offcut_jobs']['records'][0]['phase']=='approved'
    after=get(e,pid)
    for key in ('components','takeoffs','boq','quotes','report_approval','stale_quotes','status','stock_operations','stock_receipts','stock_reservations','stock_processing_results','stock_offcut_reservations','stock_offcut_returns'):assert before[key]==after[key]
    assert old['receipts']==v['receipts'] and old['processing']['sources']==v['processing']['sources']


def test_different_jobs_cannot_borrow_each_others_pending_quantity_and_row_units_exact(clients):
    e,r,pid,comp,c,eid,recovery,lot,source=setup(clients,count=2)
    with localcontext() as ctx:
        ctx.prec=3;a=ready(e,r,pid,raw(source,eid));assert a['allocations'][0]['mass']=='10.722' and a['allocations'][0]['total_length']=='3.574';move(e,pid,a)
        b=ready(e,r,pid,raw(source,eid));move(e,pid,b)
    first=ready_result(e,r,pid,result(source,eid,a));result_move(e,pid,first)
    result_preview(e,pid,result(source,eid,a,'SYN-EXTRA'),409)
    v=read(e,pid);assert [j['remaining'] for j in v['offcut_jobs']['records']]==[0,1] and v['processing']['lots'][0]['processing_quantity']==1
    second=ready_result(e,r,pid,result(source,eid,b,'SYN-B'));result_move(e,pid,second)
    assert read(e,pid)['processing']['lots'][0]['in_custody']==0


@pytest.mark.parametrize('winner',['job','result','return'])
def test_start_direct_result_and_return_compete_for_one_unprocessed_segment(clients,winner):
    e,r,pid,comp,c,eid,recovery,lot,source=setup(clients);job=ready(e,r,pid,raw(source,eid));direct=ready_result(e,r,pid,result_raw(source,eid));ret=ready_return(e,r,pid,raw_return(source,eid))
    if winner=='job':move(e,pid,job);result_move(e,pid,direct,expected=409);return_move(e,pid,ret,expected=409)
    elif winner=='result':result_move(e,pid,direct);move(e,pid,job,expected=409);return_move(e,pid,ret,expected=409)
    else:return_move(e,pid,ret);move(e,pid,job,expected=409);result_move(e,pid,direct,expected=409)


@pytest.mark.parametrize('fault',[
 lambda v:v.update(confirmed=False),lambda v:v.update(workshop=''),lambda v:v.update(custodian=''),lambda v:v.update(basis=''),lambda v:v.update(business_date=None),lambda v:v.update(business_date='2999-01-01'),lambda v:v.update(business_date='2026-1-1'),lambda v:v.update(extra=1),lambda v:v.update(selections=[]),lambda v:v['selections'][0].update(quantity=True),lambda v:v['selections'][0].update(quantity=0),lambda v:v['selections'][0].update(quantity=1.5),lambda v:v['selections'][0].update(quantity=1000001),lambda v:v['selections'][0].update(allocation_index=True),lambda v:v['selections'][0].update(allocation_index=2),lambda v:v['selections'].append(copy.deepcopy(v['selections'][0]))])
def test_invalid_actual_start_inputs_never_move_material(clients,fault):
    e,r,pid,comp,c,eid,recovery,lot,source=setup(clients);v=raw(source,eid);fault(v);preview(e,pid,v,422);assert not read(e,pid)['offcut_jobs']['records']


@pytest.mark.parametrize('fault',[lambda v:v.update(reference_hash='old'),lambda v:v.update(evidence_id='missing'),lambda v:v.update(business_date=(date.fromisoformat(today())-timedelta(days=1)).isoformat()),lambda v:v['selections'][0].update(quantity=2)])
def test_invalid_source_proof_or_start_quantity_blocks(clients,fault):
    e,r,pid,comp,c,eid,recovery,lot,source=setup(clients);v=raw(source,eid);fault(v);response=e.post(base(pid)+'/offcut-jobs/preview',json={'input':v});assert response.status_code in (404,409),response.text


@pytest.mark.parametrize('fault',[lambda v:v.pop('job_hash'),lambda v:v.update(job_id=None),lambda v:v.update(job_hash='')])
def test_job_result_fields_cannot_be_partial_or_blank(clients,fault):
    e,r,pid,comp,c,eid,recovery,lot,source=setup(clients);job=ready(e,r,pid,raw(source,eid));move(e,pid,job);v=result(source,eid,job);fault(v);result_preview(e,pid,v,422)


def test_result_requires_executed_matching_job_and_not_old_hash(clients):
    e,r,pid,comp,c,eid,recovery,lot,source=setup(clients);job=ready(e,r,pid,raw(source,eid));result_preview(e,pid,result(source,eid,job),409);move(e,pid,job)
    v=result(source,eid,job);v['job_hash']='old';result_preview(e,pid,v,409)
    v=result(source,eid,job);v['job_id']='missing';response=e.post(base(pid)+'/offcut-processing/preview',json={'input':v});assert response.status_code==404


@pytest.mark.parametrize('decision',['reject','request_more_info'])
def test_rejection_and_pending_cancel_never_change_quantities(clients,decision):
    e,r,pid,comp,c,eid,recovery,lot,source=setup(clients);job=submit(e,pid,submission(e,pid,raw(source,eid)));approve(r,pid,'stock_offcut_jobs',job,decision);job=get(e,pid)['stock_offcut_jobs'][0]
    move(e,pid,job,expected=409);event,b=move(e,pid,job,'cancel',eid);assert move(e,pid,job,'cancel',eid,b=b)[0]==event
    assert read(e,pid)['processing']['lots'][0]['in_custody']==1;approve(r,pid,'stock_offcut_jobs',job,expected=409)
    result_preview(e,pid,result(source,eid,job),409)


@pytest.mark.parametrize('when',['preview','submit','review','execute','historical'])
def test_start_proof_byte_gates_and_history_remains_in_process(clients,when):
    e,r,pid,comp,c,eid,recovery,lot,source=setup(clients);upload(e,pid,'合成独立实际投入凭据，仅软件验证','actual-start.txt');fresh=get(e,pid)['evidence'][-1]['id'];v=raw(source,fresh);b=submission(e,pid,v)
    if when in ('review','execute','historical'):
        job=submit(e,pid,b)
        if when in ('execute','historical'):approve(r,pid,'stock_offcut_jobs',job);job=get(e,pid)['stock_offcut_jobs'][0]
        if when=='historical':move(e,pid,job)
    doc=get(e,pid)['documents'][-1];path=store.ROOT/'originals'/doc['hash'];data=path.read_bytes();path.unlink()
    try:
        if when=='preview':preview(e,pid,v,409)
        elif when=='submit':submit(e,pid,b,409)
        elif when=='review':approve(r,pid,'stock_offcut_jobs',job,expected=409)
        elif when=='execute':move(e,pid,job,expected=409)
        else:
            state=read(e,pid);assert state['processing']['lots'][0]['processing_quantity']==1 and state['offcut_jobs']['records'][0]['issue']
    finally:path.write_bytes(data)


def test_lost_start_proof_allows_new_actual_results_but_quarantines_new_lot(clients):
    e,r,pid,comp,c,eid,recovery,lot,source=setup(clients);upload(e,pid,'合成实际投入原件','start-only.txt');start_eid=get(e,pid)['evidence'][-1]['id'];job=ready(e,r,pid,raw(source,start_eid));move(e,pid,job)
    upload(e,pid,'合成后续实测原件，与投入原件独立','results-only.txt');fresh=get(e,pid)['evidence'][-1]['id'];doc=next(d for d in get(e,pid)['documents'] if d['id']==next(x for x in get(e,pid)['evidence'] if x['id']==start_eid)['document_id']);path=store.ROOT/'originals'/doc['hash'];data=path.read_bytes();path.write_bytes(b'tampered')
    try:
        record=ready_result(e,r,pid,result(source,fresh,job));result_move(e,pid,record);state=read(e,pid);parent,child=state['processing']['lots']
        assert parent['processing_quantity']==0 and parent['processed_quantity']==1 and child['in_stock_quantity']==1 and child['adopted_quantity']==0
        assert state['offcut_jobs']['records'][0]['progress']=='complete' and state['offcut_processing']['results'][0]['issue']
    finally:path.write_bytes(data)
    assert read(e,pid)['processing']['lots'][1]['adopted_quantity']==1


def test_parallel_starts_and_parallel_results_recheck_current_balances(clients):
    e,r,pid,comp,c,eid,recovery,lot,source=setup(clients);a=ready(e,r,pid,raw(source,eid));b=ready(e,r,pid,raw(source,eid));version=common(e,pid);bodies=[{**version,'request_id':store.uid(),'object_hash':store.digest(x)} for x in (a,b)]
    with ThreadPoolExecutor(max_workers=2) as pool:codes=list(pool.map(lambda pair:e.post(base(pid)+'/offcut-jobs/'+pair[0]['id']+'/execute',json=pair[1]).status_code,zip((a,b),bodies)))
    assert sorted(codes)==[200,409];job=next(x['record'] for x in read(e,pid)['offcut_jobs']['records'] if x['phase']=='executed')
    a=ready_result(e,r,pid,result(source,eid,job,'SYN-A'));b=ready_result(e,r,pid,result(source,eid,job,'SYN-B'));version=common(e,pid);bodies=[{**version,'request_id':store.uid(),'object_hash':store.digest(x)} for x in (a,b)]
    with ThreadPoolExecutor(max_workers=2) as pool:codes=list(pool.map(lambda pair:e.post(base(pid)+'/offcut-processing/'+pair[0]['id']+'/execute',json=pair[1]).status_code,zip((a,b),bodies)))
    assert sorted(codes)==[200,409] and next(x for x in read(e,pid)['offcut_jobs']['records'] if x['record']['id']==job['id'])['remaining']==0


def test_preview_stale_ledger_identity_and_author_separation(clients):
    e,r,pid,comp,c,eid,recovery,lot,source=setup(clients);b=submission(e,pid,raw(source,eid));bad=copy.deepcopy(b);bad['input']['workshop']='other';submit(e,pid,bad,409)
    assert r.post(base(pid)+'/offcut-jobs',json=b).status_code==403;job=submit(e,pid,b);bad=copy.deepcopy(b);bad['input']['basis']='changed';submit(e,pid,bad,409)
    stale=dict(**common(e,pid),object_hash=store.digest(job));approve(r,pid,'stock_offcut_jobs',job);move(e,pid,job,expected=409,b=stale)
    other=create(clients['other']);assert clients['other'].post(base(pid)+'/offcut-jobs/preview',json={'input':b['input']}).status_code==404;assert e.post(base(other['id'])+'/offcut-jobs/preview',json={'input':b['input']}).status_code==404
    with store.transaction() as db:db.execute("UPDATE users SET role='reviewer' WHERE username='estimator'")
    approve(e,pid,'stock_offcut_jobs',get(e,pid)['stock_offcut_jobs'][0],expected=403)


@pytest.mark.parametrize('fault',['start-record','start-event','start-link','before-issue','result-before-start','cancel-link'])
def test_invalid_start_history_blocks_read_and_export(clients,fault):
    e,r,pid,comp,c,eid,recovery,lot,source=setup(clients);job=ready(e,r,pid,raw(source,eid));move(e,pid,job,'cancel',eid) if fault=='cancel-link' else move(e,pid,job)
    if fault=='result-before-start':record=ready_result(e,r,pid,result(source,eid,job));result_move(e,pid,record)
    def corrupt(p):
        if fault=='start-record':p['stock_offcut_jobs'][0]['totals']['length']='wrong'
        elif fault=='start-event':p['stock_events'][-1]['totals']['length']='wrong'
        elif fault=='start-link':p['stock_events'][-1]=inv.seal({**p['stock_events'][-1],'reference_id':'missing'})
        elif fault=='cancel-link':p['stock_events'][-1]=inv.seal({**p['stock_events'][-1],'target_id':'missing'})
        elif fault=='before-issue':
            event=p['stock_events'].pop();p['stock_events'].insert(next(i for i,x in enumerate(p['stock_events']) if x['operation']=='offcut_issue'),event)
        else:
            event=p['stock_events'].pop();p['stock_events'].insert(next(i for i,x in enumerate(p['stock_events']) if x['operation']=='offcut_start'),event)
    mutate(pid,corrupt);assert e.get(base(pid)).status_code==409 and e.get(base(pid)+'/export').status_code==409


def test_job_keeps_original_nonfirst_allocation_index_and_cannot_select_another(clients):
    from tests.test_stock_offcut_reuse import value as reuse_value,approved as reuse_ready,movement as reuse_move
    from tests.test_polygon_takeoff import post
    e,r,pid,comp,c,eid,recovery,lot,source=setup(clients,count=2);ret=ready_return(e,r,pid,raw_return(source,eid,2));return_move(e,pid,ret)
    v=copy.deepcopy(c);v['name']='合成两项领用测试';v['material_items'][1]['quantity']='1';extra=copy.deepcopy(v['material_items'][1]);extra['name']='合成第二相同规格项';v['material_items'].append(extra);c2=post(e,pid,v).json();approve(r,pid,'components',c2)
    source2=reuse_ready(e,r,pid,reuse_value(e,pid,c2,eid,lot));reuse_move(e,pid,source2)
    v=raw(source2,eid);v['selections'][0]['allocation_index']=2;job=ready(e,r,pid,v);move(e,pid,job)
    v=result(source2,eid,job);result_preview(e,pid,v,422);v['rows'][0]['allocation_index']=2;record=ready_result(e,r,pid,v);result_move(e,pid,record)
    balances=next(x for x in read(e,pid)['offcut_reuse']['reservations'] if x['record']['id']==source2['id'])['balances']
    assert balances[0]['outstanding']==1 and balances[1]['outstanding']==0 and balances[1]['consumed']==1


def test_result_date_cannot_precede_job_even_when_after_original_issue(clients):
    from tests.test_stock_offcut_reuse import setup as initial_setup,value as reuse_value,approved as reuse_ready,movement as reuse_move
    e,r,pid,comp,c,eid,recovery,lot,stocks=initial_setup(clients);v=reuse_value(e,pid,c,eid,lot);v['business_date']=(date.fromisoformat(today())-timedelta(days=2)).isoformat();source=reuse_ready(e,r,pid,v);reuse_move(e,pid,source)
    job=ready(e,r,pid,raw(source,eid));move(e,pid,job);v=result(source,eid,job);v['business_date']=(date.fromisoformat(today())-timedelta(days=1)).isoformat();response=e.post(base(pid)+'/offcut-processing/preview',json={'input':v})
    assert response.status_code==409 and '投入' in response.json()['detail']
