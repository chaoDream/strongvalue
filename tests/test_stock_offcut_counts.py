"""Synthetic physical count adjustments, stale snapshots, quarantine and immutability."""
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import date,timedelta
import pytest
from backend import store,stock_inventory as inv,stock_offcut_counts as counts
from tests.test_stock_offcut_reuse import setup,value as reuse_value,approved as reuse_ready,movement as reuse_move,target
from tests.test_stock_inventory import base,read,common
from tests.test_workflow import get,approve,create,upload
from tests.test_polygon_takeoff import mutate
from tests.test_stock_operations import today


def raw(e,pid,lot,eid,quantity=0):
    snapshot=next(l for l in read(e,pid)['offcut_counts']['lots'] if l['lot_key']==lot['lot_key'])['lot_snapshot']
    return dict(lot_key=lot['lot_key'],lot_snapshot=snapshot,counted_quantity=quantity,business_date=today(),custodian='合成盘点人A',basis='合成同组在库原段现场盘点及差异核查，有据登记，非真实工程或仓库验收',count_evidence_id=eid,variance_evidence_id=eid,confirmed=True)


def preview(e,pid,v,expected=200):
    q=e.post(base(pid)+'/offcut-counts/preview',json={'input':v});assert q.status_code==expected,q.text;return q.json()


def submission(e,pid,v):
    q=preview(e,pid,v);return dict(request_id=store.uid(),ledger_hash=q['ledger_hash'],input=v,preview_hash=q['preview_hash'])


def submit(e,pid,b,expected=200):
    q=e.post(base(pid)+'/offcut-counts',json=b);assert q.status_code==expected,q.text;return q.json()


def ready(e,r,pid,v):
    row=submit(e,pid,submission(e,pid,v));approve(r,pid,counts.COLLECTION,row);return next(x for x in get(e,pid)[counts.COLLECTION] if x['id']==row['id'])


def move(e,pid,row,kind='execute',eid=None,expected=200,b=None):
    b=b or dict(**common(e,pid),object_hash=store.digest(row),**(dict(basis='合成取消过期或未执行盘点，重新核实，不回滚实物',evidence_id=eid) if kind=='cancel' else {}))
    q=e.post(base(pid)+'/offcut-counts/'+row['id']+'/'+kind,json=b);assert q.status_code==expected,q.text;return q.json(),b


@pytest.mark.parametrize('quantity',[0,1,2])
def test_preview_review_and_explicit_adjustment_preserve_originals_and_engineering(clients,quantity):
    e,r,pid,comp,c,eid,recovery,lot,stocks=setup(clients);before=get(e,pid);old=read(e,pid);q=preview(e,pid,raw(e,pid,lot,eid,quantity));assert get(e,pid)==before
    assert q['result']['delta']==quantity-1 and q['result']['mass_delta'] in ('-10.722','0','10.722')
    b=submission(e,pid,raw(e,pid,lot,eid,quantity));row=submit(e,pid,b);assert submit(e,pid,b)==row
    assert read(e,pid)['processing']['lots'][0]['in_stock_quantity']==1
    move(e,pid,row,expected=409);approve(r,pid,counts.COLLECTION,row);row=get(e,pid)[counts.COLLECTION][0]
    assert read(e,pid)['processing']['lots'][0]['in_stock_quantity']==1
    frozen=e.get(base(pid)+'/export');event,b=move(e,pid,row);assert move(e,pid,row,b=b)[0]==event
    v=read(e,pid);l=v['processing']['lots'][0];assert l['quantity']==1 and l['in_stock_quantity']==quantity and l['adjusted_quantity']==quantity and l['adopted_quantity']==quantity and l['count_revision']==1
    assert l['adjustment_gain']==max(0,quantity-1) and l['adjustment_loss']==max(0,1-quantity)
    assert v['receipts']==old['receipts']
    after=get(e,pid)
    for k in ('components','takeoffs','boq','quotes','stock_processing_results','stock_operations'):assert after[k]==before[k]
    move(e,pid,row,expected=409);move(e,pid,row,'cancel',eid,409);approve(r,pid,counts.COLLECTION,row,expected=409)
    assert frozen.status_code==200 and e.get(base(pid)+'/export').content!=frozen.content
    if quantity==2:
        source=reuse_ready(e,r,pid,reuse_value(e,pid,c,eid,lot));reuse_move(e,pid,source)
        c2=target(e,r,pid,comp,name='合成第二材料表');source2=reuse_ready(e,r,pid,reuse_value(e,pid,c2,eid,lot));reuse_move(e,pid,source2)
        l=read(e,pid)['processing']['lots'][0];assert l['in_custody']==2 and l['in_stock_quantity']==0 and l['quantity']==1


@pytest.mark.parametrize('field,value,code',[
 ('counted_quantity',True,422),('counted_quantity',-1,422),('counted_quantity',1.5,422),('counted_quantity','1',422),('counted_quantity',1000001,422),('counted_quantity',None,422),
 ('business_date',None,422),('business_date','2026-1-1',422),('business_date','2999-01-01',409),('business_date','2000-01-01',409),
 ('custodian','',422),('basis','',422),('confirmed',False,422),('lot_key','foreign',409),('lot_snapshot','old',409),('count_evidence_id','missing',404),('variance_evidence_id','missing',404),
])
def test_invalid_count_never_mutates_stock(clients,field,value,code):
    e,r,pid,comp,c,eid,recovery,lot,stocks=setup(clients);v=raw(e,pid,lot,eid);v[field]=value;preview(e,pid,v,code)
    assert not read(e,pid)['offcut_counts']['records'] and read(e,pid)['processing']['lots'][0]['in_stock_quantity']==1


def test_hold_not_silently_released_and_new_hold_rechecked_at_execution(clients):
    e,r,pid,comp,c,eid,recovery,lot,stocks=setup(clients);row=ready(e,r,pid,raw(e,pid,lot,eid))
    source=reuse_ready(e,r,pid,reuse_value(e,pid,c,eid,lot));preview(e,pid,raw(e,pid,lot,eid),409);move(e,pid,row,expected=409)
    assert read(e,pid)['processing']['lots'][0]['reserved']==1
    reuse_move(e,pid,source,'release',eid);move(e,pid,row);assert read(e,pid)['processing']['lots'][0]['in_stock_quantity']==0


def test_physical_movement_stales_count_even_when_return_restores_same_on_hand(clients):
    from tests.test_stock_offcut_returns import raw_return,ready_return,move as return_move
    e,r,pid,comp,c,eid,recovery,lot,stocks=setup(clients);row=ready(e,r,pid,raw(e,pid,lot,eid))
    source=reuse_ready(e,r,pid,reuse_value(e,pid,c,eid,lot));reuse_move(e,pid,source);ret=ready_return(e,r,pid,raw_return(source,eid));return_move(e,pid,ret)
    assert read(e,pid)['processing']['lots'][0]['in_stock_quantity']==1
    move(e,pid,row,expected=409);assert read(e,pid)['offcut_counts']['records'][0]['issue']
    move(e,pid,row,'cancel',eid)


def test_racing_counts_same_snapshot_only_one_execution(clients):
    e,r,pid,comp,c,eid,recovery,lot,stocks=setup(clients);v=raw(e,pid,lot,eid,2);a=ready(e,r,pid,v);b=ready(e,r,pid,v)
    def run(row):return e.post(base(pid)+'/offcut-counts/'+row['id']+'/execute',json=dict(**common(e,pid),object_hash=store.digest(row))).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:codes=list(pool.map(run,[a,b]))
    assert sorted(codes)==[200,409] and read(e,pid)['processing']['lots'][0]['in_stock_quantity']==2
    remaining=next(x for x in read(e,pid)['offcut_counts']['records'] if x['phase']!='executed');move(e,pid,remaining['record'],'cancel',eid)


def test_next_day_requires_recount_and_cancel_still_available(clients,monkeypatch):
    e,r,pid,comp,c,eid,recovery,lot,stocks=setup(clients);row=ready(e,r,pid,raw(e,pid,lot,eid));monkeypatch.setattr(counts,'today',lambda:(date.fromisoformat(today())+timedelta(days=1)).isoformat())
    move(e,pid,row,expected=409);move(e,pid,row,'cancel',eid)


@pytest.mark.parametrize('decision',['reject','request_more_info'])
def test_reject_and_cancel_leave_quantity_and_engineering_untouched(clients,decision):
    e,r,pid,comp,c,eid,recovery,lot,stocks=setup(clients);before=get(e,pid);row=submit(e,pid,submission(e,pid,raw(e,pid,lot,eid)));approve(r,pid,counts.COLLECTION,row,decision);row=get(e,pid)[counts.COLLECTION][0]
    move(e,pid,row,expected=409);event,b=move(e,pid,row,'cancel',eid);assert move(e,pid,row,'cancel',eid,b=b)[0]==event
    assert read(e,pid)['processing']['lots'][0]['in_stock_quantity']==1
    for k in ('components','takeoffs','boq','quotes'):assert get(e,pid)[k]==before[k]


def test_access_preview_binding_stale_ledger_and_author_role_switch(clients):
    e,r,pid,comp,c,eid,recovery,lot,stocks=setup(clients);b=submission(e,pid,raw(e,pid,lot,eid));bad=copy.deepcopy(b);bad['input']['counted_quantity']=2;submit(e,pid,bad,409)
    assert r.post(base(pid)+'/offcut-counts',json=b).status_code==403
    row=submit(e,pid,b);bad=copy.deepcopy(b);bad['input']['basis']='changed';submit(e,pid,bad,409)
    old=dict(**common(e,pid),object_hash=store.digest(row));approve(r,pid,counts.COLLECTION,row);row=get(e,pid)[counts.COLLECTION][0];move(e,pid,row,expected=409,b=old)
    other=create(clients['other']);assert clients['other'].post(base(pid)+'/offcut-counts/preview',json={'input':b['input']}).status_code==404
    assert e.post(base(other['id'])+'/offcut-counts/preview',json={'input':b['input']}).status_code==404
    with store.transaction() as db:db.execute("UPDATE users SET role='reviewer' WHERE username='estimator'")
    approve(e,pid,counts.COLLECTION,row,expected=403)


def test_count_proof_loss_preserves_adjustment_but_quarantines_lot_and_children(clients):
    from tests.test_stock_offcut_processing import raw as process_raw,ready as process_ready,move as process_move
    e,r,pid,comp,c,eid,recovery,lot,stocks=setup(clients)
    upload(e,pid,'合成独立盘点及差异证据，3574mm同组方管A在库2根，非真实仓库','count-proof.txt');fresh=get(e,pid)['evidence'][-1]['id']
    row=ready(e,r,pid,raw(e,pid,lot,fresh,2));move(e,pid,row)
    # Fresh material table after upload, with current engineering proof.
    for component in get(e,pid)['components']:approve(r,pid,'components',component)
    c=target(e,r,pid,comp,name='合成盘点后领用');source=reuse_ready(e,r,pid,reuse_value(e,pid,c,eid,lot));reuse_move(e,pid,source)
    result=process_ready(e,r,pid,process_raw(source,eid));process_move(e,pid,result)
    doc=next(d for d in get(e,pid)['documents'] if d['id']==next(x for x in get(e,pid)['evidence'] if x['id']==fresh)['document_id']);path=store.ROOT/'originals'/doc['hash'];saved=path.read_bytes();path.write_bytes(b'corrupted count original')
    try:
        v=read(e,pid);parent=v['processing']['lots'][0];assert parent['in_stock_quantity']==1 and parent['processed_quantity']==1 and parent['adjustment_gain']==1
        assert all(l['adopted_quantity']==0 and l['issue'] for l in v['processing']['lots'])
        assert v['offcut_counts']['records'][0]['issue'] and e.get(base(pid)+'/export').status_code==200
        v0=raw(e,pid,lot,eid) if v['offcut_counts']['lots'] else dict(row['input']);preview(e,pid,v0,409)
    finally:path.write_bytes(saved)
    assert all(l['verified'] for l in read(e,pid)['processing']['lots'])


def test_counts_do_not_consume_in_custody_wip_or_processed_and_child_groups_supported(clients):
    from tests.test_stock_offcut_jobs import raw as job_raw,ready as job_ready,move as job_move,result as job_result
    from tests.test_stock_offcut_processing import ready as result_ready,move as result_move
    e,r,pid,comp,c,eid,recovery,lot,stocks=setup(clients);gain=ready(e,r,pid,raw(e,pid,lot,eid,2));move(e,pid,gain)
    source=reuse_ready(e,r,pid,reuse_value(e,pid,c,eid,lot));reuse_move(e,pid,source);job=job_ready(e,r,pid,job_raw(source,eid));job_move(e,pid,job)
    loss=ready(e,r,pid,raw(e,pid,lot,eid,0));move(e,pid,loss)
    l=read(e,pid)['processing']['lots'][0];assert l['quantity']==1 and l['adjusted_quantity']==1 and l['processing_quantity']==1 and l['in_stock_quantity']==0
    result=result_ready(e,r,pid,job_result(source,eid,job));result_move(e,pid,result)
    child=read(e,pid)['processing']['lots'][1];row=ready(e,r,pid,raw(e,pid,child,eid,2));move(e,pid,row)
    parent,child=read(e,pid)['processing']['lots'];assert parent['processed_quantity']==1 and child['quantity']==1 and child['in_stock_quantity']==2


@pytest.mark.parametrize('fault',['record','event','reordered','wrong-lot','cancel-link','missing-review'])
def test_corrupted_history_blocks_read_and_export(clients,fault):
    e,r,pid,comp,c,eid,recovery,lot,stocks=setup(clients);row=ready(e,r,pid,raw(e,pid,lot,eid));move(e,pid,row,'cancel',eid) if fault=='cancel-link' else move(e,pid,row)
    def corrupt(p):
        if fault=='record':p[counts.COLLECTION][0]['after']=99
        elif fault=='event':p['stock_events'][-1]=inv.seal({**p['stock_events'][-1],'after':99})
        elif fault=='wrong-lot':p['stock_events'][-1]=inv.seal({**p['stock_events'][-1],'lot_key':'other'})
        elif fault=='cancel-link':p['stock_events'][-1]=inv.seal({**p['stock_events'][-1],'target_id':'missing'})
        elif fault=='missing-review':p['reviews']=[x for x in p['reviews'] if x['collection']!=counts.COLLECTION]
        else:
            event=p['stock_events'].pop();p['stock_events'].insert(next(i for i,x in enumerate(p['stock_events']) if x['operation']=='recover'),event)
    mutate(pid,corrupt);assert e.get(base(pid)).status_code==409 and e.get(base(pid)+'/export').status_code==409


@pytest.mark.parametrize('stage',['preview','submit','review','execute'])
def test_actual_original_byte_gate_at_each_stage(clients,stage):
    e,r,pid,comp,c,eid,recovery,lot,stocks=setup(clients);v=raw(e,pid,lot,eid);b=submission(e,pid,v);row=None
    if stage in ('review','execute'):row=submit(e,pid,b)
    if stage=='execute':approve(r,pid,counts.COLLECTION,row);row=get(e,pid)[counts.COLLECTION][0]
    doc=next(d for d in get(e,pid)['documents'] if d['id']==next(x for x in get(e,pid)['evidence'] if x['id']==eid)['document_id']);path=store.ROOT/'originals'/doc['hash'];saved=path.read_bytes();path.write_bytes(b'synthetic changed original bytes')
    try:
        if stage=='preview':preview(e,pid,v,409)
        elif stage=='submit':submit(e,pid,b,409)
        elif stage=='review':approve(r,pid,counts.COLLECTION,row,expected=409)
        else:move(e,pid,row,expected=409)
        assert read(e,pid)['processing']['lots'][0]['in_stock_quantity']==1
    finally:path.write_bytes(saved)


def test_signed_length_and_mass_remain_exact_under_low_decimal_context(clients):
    from decimal import localcontext
    e,r,pid,comp,c,eid,recovery,lot,stocks=setup(clients);v=raw(e,pid,lot,eid,4)
    with localcontext() as context:
        context.prec=3
        row=ready(e,r,pid,v);assert row['length_delta']=='10.722' and row['mass_delta']=='32.166';move(e,pid,row)
    row=ready(e,r,pid,raw(e,pid,lot,eid,0));assert row['length_delta']=='-14.296' and row['mass_delta']=='-42.888';move(e,pid,row)
    l=read(e,pid)['processing']['lots'][0];assert l['adjustment_gain']==3 and l['adjustment_loss']==4 and l['in_stock_quantity']==0
