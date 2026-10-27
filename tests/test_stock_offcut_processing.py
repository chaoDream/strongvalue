"""Synthetic measured offcut reprocessing, exact lineage and competing custody use."""
import copy
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import date,timedelta
from decimal import Decimal,localcontext
import pytest
from backend import store,stock_inventory as inv
from tests.test_stock_offcut_returns import setup,raw_return,ready_return,move as return_move
from tests.test_stock_offcut_reuse import value as reuse_value,approved as approve_reuse,movement as reuse_move
from tests.test_stock_processing_results import raw as raw_original,ready as ready_original,execute as execute_original,setup as original_setup,preview as original_preview
from tests.test_stock_inventory import read,common,base
from tests.test_workflow import get,approve,upload,create
from tests.test_polygon_takeoff import post,mutate
from tests.test_stock_operations import today

METRICS=('finished_length','kerf_length','trim_length','scrap_length')


def raw(source,eid,*,code='SYN-CHILD-A'):
    return dict(reference_id=source['id'],reference_hash=store.digest(source),business_date=today(),inspector='合成再加工验收人A',basis='合成实际再加工及新余料验收，按实际去向与计划偏差核定，非真实工程',evidence_id=eid,confirmed=True,rows=[dict(allocation_index=1,quantity=1,unit='m',finished_length='2.4',kerf_length='.006',trim_length='.02',scrap_length='0',measurement_basis='合成3574mm原段实际成品2400mm、锯缝6mm、切除20mm、新余料1148mm，逐去向实测精确闭合',basis_evidence_id=eid,evidence={k:eid for k in ('quantity',)+METRICS},offcuts=[dict(lot_code=code,length='1.148',quantity=1,warehouse='合成再加工余料库',acceptance_basis='合成同材质截面、3kg/m、实测无缺陷且可留用验收，仅软件验证',evidence={k:eid for k in ('lot_code','length','quantity','warehouse','acceptance_basis')})])])


def preview(e,pid,v,expected=200):
    response=e.post(base(pid)+'/offcut-processing/preview',json={'input':v});assert response.status_code==expected,response.text;return response.json()


def submission(e,pid,v):
    p=preview(e,pid,v);return dict(request_id=store.uid(),ledger_hash=p['ledger_hash'],input=v,preview_hash=p['preview_hash'])


def submit(e,pid,b,expected=200):
    response=e.post(base(pid)+'/offcut-processing',json=b);assert response.status_code==expected,response.text;return response.json()


def ready(e,r,pid,v):
    row=submit(e,pid,submission(e,pid,v));approve(r,pid,'stock_offcut_processing',row);return next(x for x in get(e,pid)['stock_offcut_processing'] if x['id']==row['id'])


def move(e,pid,row,kind='execute',eid=None,expected=200,b=None):
    b=b or dict(**common(e,pid),object_hash=store.digest(row),**(dict(basis='合成核查取消未执行再加工，非实物回库或冲销',evidence_id=eid) if kind=='cancel' else {}))
    response=e.post(base(pid)+'/offcut-processing/'+row['id']+'/'+kind,json=b);assert response.status_code==expected,response.text;return response.json(),b


def stop(e,pid,row,eid,expected=200):
    response=e.post(base(pid)+'/processing-results/'+row['id']+'/offcuts/1/1/void',json=dict(**common(e,pid),object_hash=store.digest(row),basis='合成有据停用新余料，不代表报废或冲销',evidence_id=eid));assert response.status_code==expected,response.text


def child_target(e,r,pid,comp,length='1148',cut='400'):
    v=copy.deepcopy(comp);v['name']='合成子代余料回用-'+store.uid();item=v['material_items'][1];item.update(quantity='1',cut_length=cut);item['cutting_plan']['stock_length']=length
    row=post(e,pid,v).json();approve(r,pid,'components',row);return row


def test_preview_review_execute_child_admission_and_history_keep_other_ledgers(clients):
    e,r,pid,comp,c,eid,recovery,lot,source=setup(clients);before=get(e,pid);old=read(e,pid);v=raw(source,eid)
    p=preview(e,pid,v);assert get(e,pid)==before
    t=p['result']['totals'];assert (t['input_length'],t['input_mass'],t['offcut_length'],t['offcut_mass'],t['balance_difference'])==('3.574','10.722','1.148','3.444','0')
    b=submission(e,pid,v);row=submit(e,pid,b);assert submit(e,pid,b)==row;move(e,pid,row,expected=409)
    assert len(read(e,pid)['processing']['lots'])==1
    approve(r,pid,'stock_offcut_processing',row);row=get(e,pid)['stock_offcut_processing'][0];assert len(read(e,pid)['processing']['lots'])==1
    frozen=e.get(base(pid)+'/export');event,b=move(e,pid,row);assert move(e,pid,row,b=b)[0]==event
    current=read(e,pid);parent,child=current['processing']['lots']
    assert (parent['in_stock_quantity'],parent['in_custody'],parent['processed_quantity'])==(0,0,1)
    assert parent['quantity']==parent['in_stock_quantity']+parent['in_custody']+parent['processed_quantity']
    assert child['parent_lot_key']==lot['lot_key'] and child['length']=='1.148' and child['adopted_quantity']==1 and child['mass']=='3.444'
    assert current['offcut_reuse']['reservations'][0]['balances'][0]['outstanding']==0
    approve(r,pid,'stock_offcut_processing',row,expected=409);move(e,pid,row,'cancel',eid,409);preview(e,pid,v,409)
    preview_return=e.post(base(pid)+'/offcut-returns/preview',json={'input':raw_return(source,eid)});assert preview_return.status_code==409
    stop(e,pid,recovery,eid,409);stop(e,pid,row,eid)
    assert read(e,pid)['processing']['lots'][1]['in_stock_quantity']==1 and read(e,pid)['processing']['lots'][1]['adopted_quantity']==0
    artifacts=e.get('/api/v1/projects/'+pid+'/artifacts').json();a=next(a for a in artifacts if a['hash']==hashlib.sha256(frozen.content).hexdigest());assert e.get('/api/v1/projects/'+pid+'/artifacts/'+a['id']+'/file').content==frozen.content
    after=get(e,pid)
    for k in ('components','takeoffs','boq','quotes','report_approval','stale_quotes','status','stock_operations','stock_receipts','stock_reservations','stock_processing_results','stock_offcut_reservations','stock_offcut_returns'):assert before[k]==after[k]
    assert current['receipts']==old['receipts'] and current['processing']['sources']==old['processing']['sources']


def test_child_can_be_reused_returned_and_reprocessed_to_grandchild(clients):
    e,r,pid,comp,c,eid,recovery,lot,source=setup(clients);row=ready(e,r,pid,raw(source,eid));move(e,pid,row)
    child=read(e,pid)['processing']['lots'][1];c2=child_target(e,r,pid,comp);source2=approve_reuse(e,r,pid,reuse_value(e,pid,c2,eid,child));reuse_move(e,pid,source2)
    # Actual unchanged child return works; original target still cannot issue twice.
    ret=ready_return(e,r,pid,raw_return(source2,eid));return_move(e,pid,ret)
    c3=child_target(e,r,pid,comp);source3=approve_reuse(e,r,pid,reuse_value(e,pid,c3,eid,child));reuse_move(e,pid,source3)
    v=raw(source3,eid,code='SYN-GRANDCHILD');v['rows'][0]['finished_length']='.8';v['rows'][0]['offcuts'][0]['length']='.322'
    row2=ready(e,r,pid,v);move(e,pid,row2)
    lots=read(e,pid)['processing']['lots'];assert len(lots)==3 and lots[2]['parent_lot_key']==child['lot_key'] and lots[2]['mass']=='0.966'
    assert (lots[1]['issued_quantity'],lots[1]['returned_quantity'],lots[1]['processed_quantity'],lots[1]['in_custody'],lots[1]['in_stock_quantity'])==(2,1,1,0,0)
    c4=child_target(e,r,pid,comp,length='322',cut='100');s4=approve_reuse(e,r,pid,reuse_value(e,pid,c4,eid,lots[2]));reuse_move(e,pid,s4)
    assert read(e,pid)['processing']['lots'][2]['in_custody']==1


@pytest.mark.parametrize('unit,factor',[('m','1'),('mm','1000'),('cm','100'),('dm','10'),('km','.001')])
def test_all_length_units_and_low_decimal_precision(clients,unit,factor):
    e,r,pid,comp,c,eid,recovery,lot,source=setup(clients);v=raw(source,eid);row=v['rows'][0];row['unit']=unit
    for k in METRICS:row[k]=str(Decimal(row[k])*Decimal(factor))
    row['offcuts'][0]['length']=str(Decimal('1.148')*Decimal(factor))
    with localcontext() as ctx:
        ctx.prec=3;p=preview(e,pid,v);assert p['result']['totals']['offcut_mass']=='3.444';record=ready(e,r,pid,v);move(e,pid,record)


@pytest.mark.parametrize('fault',[
 lambda v:v.update(confirmed=False),lambda v:v.update(inspector=''),lambda v:v.update(basis=''),lambda v:v.update(business_date=None),lambda v:v.update(business_date='2999-01-01'),lambda v:v.update(extra=1),lambda v:v.update(rows=[]),lambda v:v['rows'][0].update(quantity=True),lambda v:v['rows'][0].update(quantity=0),lambda v:v['rows'][0].update(quantity=1.5),lambda v:v['rows'][0].update(unit='ft'),lambda v:v['rows'].append(copy.deepcopy(v['rows'][0])),lambda v:v['rows'][0].update(evidence={}),lambda v:v['rows'][0].update(measurement_basis=''),lambda v:v['rows'][0]['offcuts'][0].update(length='3.574'),lambda v:v['rows'][0]['offcuts'][0].update(quantity=True),lambda v:v['rows'][0]['offcuts'][0].update(evidence={}),lambda v:v['rows'][0]['offcuts'].append(copy.deepcopy(v['rows'][0]['offcuts'][0])),lambda v:v['rows'][0].update(scrap_length='-1')])
def test_malformed_measurements_are_rejected_before_any_movement(clients,fault):
    e,r,pid,comp,c,eid,recovery,lot,source=setup(clients);v=raw(source,eid);fault(v);preview(e,pid,v,422);assert not read(e,pid)['offcut_processing']['results']


@pytest.mark.parametrize('fault',[
 lambda v:v.update(reference_hash='old'),lambda v:v.update(evidence_id='missing'),lambda v:v.update(business_date=(date.fromisoformat(today())-timedelta(days=1)).isoformat()),lambda v:v['rows'][0].update(quantity=2),lambda v:v['rows'][0].update(finished_length='2.400000000000001'),lambda v:v['rows'][0]['offcuts'][0].update(length='1.147999999999999'),lambda v:v['rows'][0].update(basis_evidence_id='missing')])
def test_stale_sources_unbalanced_lengths_and_overprocessing_block(clients,fault):
    e,r,pid,comp,c,eid,recovery,lot,source=setup(clients);v=raw(source,eid);fault(v);response=e.post(base(pid)+'/offcut-processing/preview',json={'input':v});assert response.status_code in (404,409),response.text


def test_actual_no_child_and_multiple_child_lengths_are_supported(clients):
    e,r,pid,comp,c,eid,recovery,lot,source=setup(clients,count=2);v=raw(source,eid);v['rows'][0].update(scrap_length='1.148',offcuts=[]);row=ready(e,r,pid,v);move(e,pid,row)
    v=raw(source,eid);a=v['rows'][0]['offcuts'][0];a['length']='.7';b=copy.deepcopy(a);b.update(lot_code='SYN-CHILD-B',length='.448');v['rows'][0]['offcuts'].append(b)
    row=ready(e,r,pid,v);move(e,pid,row);lots=read(e,pid)['processing']['lots'];assert len(lots)==3 and lots[0]['processed_quantity']==2 and {l['length'] for l in lots[1:]}=={'0.7','0.448'}


@pytest.mark.parametrize('decision',['reject','request_more_info'])
def test_cancel_and_rejected_drafts_do_not_claim_custody_or_admit_children(clients,decision):
    e,r,pid,comp,c,eid,recovery,lot,source=setup(clients);row=submit(e,pid,submission(e,pid,raw(source,eid)));approve(r,pid,'stock_offcut_processing',row,decision);row=get(e,pid)['stock_offcut_processing'][0]
    move(e,pid,row,expected=409);event,b=move(e,pid,row,'cancel',eid);assert move(e,pid,row,'cancel',eid,b=b)[0]==event
    assert read(e,pid)['processing']['lots'][0]['in_custody']==1;approve(r,pid,'stock_offcut_processing',row,expected=409)
    row=ready(e,r,pid,raw(source,eid));move(e,pid,row)


@pytest.mark.parametrize('winner',['processing','return'])
def test_processing_and_unchanged_return_compete_for_same_allocation(clients,winner):
    e,r,pid,comp,c,eid,recovery,lot,source=setup(clients);a=ready(e,r,pid,raw(source,eid));b=ready_return(e,r,pid,raw_return(source,eid))
    if winner=='processing':move(e,pid,a);return_move(e,pid,b,expected=409)
    else:return_move(e,pid,b);move(e,pid,a,expected=409)
    assert read(e,pid)['offcut_reuse']['reservations'][0]['balances'][0]['outstanding']==0


def test_parallel_executions_and_stale_preview_are_bounded(clients):
    e,r,pid,comp,c,eid,recovery,lot,source=setup(clients);v=raw(source,eid);a=ready(e,r,pid,v);b=ready(e,r,pid,raw(source,eid,code='OTHER'))
    common_body=common(e,pid);bodies=[{**common_body,'request_id':store.uid(),'object_hash':store.digest(row)} for row in (a,b)]
    with ThreadPoolExecutor(max_workers=2) as pool:codes=list(pool.map(lambda pair:e.post(base(pid)+'/offcut-processing/'+pair[0]['id']+'/execute',json=pair[1]).status_code,zip((a,b),bodies)))
    assert sorted(codes)==[200,409] and len(read(e,pid)['processing']['lots'])==2
    pending=next(r for r in read(e,pid)['offcut_processing']['results'] if r['phase']=='approved');assert pending['issue']


def test_new_child_identifier_cannot_be_registered_again_as_raw_stock(clients):
    e,r,pid,comp,c,eid,recovery,lot,source=setup(clients);row=ready(e,r,pid,raw(source,eid));move(e,pid,row)
    body=copy.deepcopy(read(e,pid)['receipts'][0]['record']['input']);body.update(lot_code='SYN-CHILD-A',warehouse='合成再加工余料库');response=e.post(base(pid)+'/receipts',json=dict(**common(e,pid),input=body));assert response.status_code==409


@pytest.mark.parametrize('when',['preview','submit','review','execute','historical'])
def test_new_actual_proof_gate_and_historical_child_quarantine(clients,when):
    e,r,pid,comp,c,eid,recovery,lot,source=setup(clients);upload(e,pid,'合成再加工当前实测原件，非真实工程','new-reprocessing.txt');fresh=get(e,pid)['evidence'][-1]['id'];v=raw(source,fresh);b=submission(e,pid,v)
    if when in ('review','execute','historical'):
        row=submit(e,pid,b)
        if when in ('execute','historical'):approve(r,pid,'stock_offcut_processing',row);row=get(e,pid)['stock_offcut_processing'][0]
        if when=='historical':move(e,pid,row)
    doc=get(e,pid)['documents'][-1];path=store.ROOT/'originals'/doc['hash'];data=path.read_bytes();path.unlink()
    try:
        if when=='preview':preview(e,pid,v,409)
        elif when=='submit':submit(e,pid,b,409)
        elif when=='review':approve(r,pid,'stock_offcut_processing',row,expected=409)
        elif when=='execute':move(e,pid,row,expected=409)
        else:
            lots=read(e,pid)['processing']['lots'];assert lots[0]['processed_quantity']==1 and lots[1]['in_stock_quantity']==1 and lots[1]['adopted_quantity']==0 and lots[1]['issue']
    finally:path.write_bytes(data)


def test_ancestor_proof_loss_propagates_to_descendants_without_undoing_processing(clients):
    e,r,pid,comp,c,eid,recovery,lot,source=setup(clients);upload(e,pid,'合成独立后续再加工原件','reprocessing-only.txt');fresh=get(e,pid)['evidence'][-1]['id'];row=ready(e,r,pid,raw(source,fresh));move(e,pid,row)
    doc=next(d for d in get(e,pid)['documents'] if d['id']==next(x for x in get(e,pid)['evidence'] if x['id']==eid)['document_id']);path=store.ROOT/'originals'/doc['hash'];data=path.read_bytes();path.write_bytes(b'tampered')
    try:
        lots=read(e,pid)['processing']['lots'];assert lots[0]['processed_quantity']==1 and lots[1]['quantity']==1 and lots[1]['adopted_quantity']==0 and not lots[1]['verified']
    finally:path.write_bytes(data)


def test_project_roles_author_and_forged_approval(clients):
    e,r,pid,comp,c,eid,recovery,lot,source=setup(clients);b=submission(e,pid,raw(source,eid));assert r.post(base(pid)+'/offcut-processing',json=b).status_code==403;row=submit(e,pid,b)
    other=create(clients['other']);assert clients['other'].post(base(pid)+'/offcut-processing/preview',json={'input':b['input']}).status_code==404;assert e.post(base(other['id'])+'/offcut-processing/preview',json={'input':b['input']}).status_code==404
    mutate(pid,lambda p:p['stock_offcut_processing'][0].update(state='approved'));row=get(e,pid)['stock_offcut_processing'][0];move(e,pid,row,expected=409)
    with store.transaction() as db:db.execute("UPDATE users SET role='reviewer' WHERE username='estimator'")
    approve(e,pid,'stock_offcut_processing',row,expected=403)


@pytest.mark.parametrize('fault',['record','event','reference','quantity','before-issue','cancel-link','child-stop-link'])
def test_tampered_processing_or_child_lineage_blocks_ledger_and_export(clients,fault):
    e,r,pid,comp,c,eid,recovery,lot,source=setup(clients);row=ready(e,r,pid,raw(source,eid));move(e,pid,row,'cancel',eid) if fault=='cancel-link' else move(e,pid,row)
    if fault=='child-stop-link':stop(e,pid,row,eid)
    def corrupt(p):
        if fault=='record':p['stock_offcut_processing'][0]['totals']['input_length']='wrong'
        elif fault=='event':p['stock_events'][-1]['totals']['input_length']='wrong'
        elif fault=='reference':p['stock_events'][-1]=inv.seal({**p['stock_events'][-1],'reference_id':'wrong'})
        elif fault=='quantity':p['stock_events'][-1]=inv.seal({**p['stock_events'][-1],'rows':[]})
        elif fault=='cancel-link':p['stock_events'][-1]=inv.seal({**p['stock_events'][-1],'target_id':'missing'})
        elif fault=='child-stop-link':p['stock_events'][-1]=inv.seal({**p['stock_events'][-1],'result_id':'missing'})
        else:
            event=p['stock_events'].pop();p['stock_events'].insert(next(i for i,x in enumerate(p['stock_events']) if x['operation']=='offcut_issue'),event)
    mutate(pid,corrupt);assert e.get(base(pid)).status_code==409 and e.get(base(pid)+'/export').status_code==409


@pytest.mark.parametrize('collision',['ancestor','receipt','stopped_child'])
def test_identifiers_stay_unique_across_full_stock_and_all_generations(clients,collision):
    e,r,pid,comp,c,eid,recovery,lot,source=setup(clients,count=2);v=raw(source,eid)
    if collision=='ancestor':v['rows'][0]['offcuts'][0].update(lot_code=lot['lot_code'],warehouse=lot['warehouse'])
    elif collision=='receipt':
        original=read(e,pid)['receipts'][0]['record']['input'];v['rows'][0]['offcuts'][0].update(lot_code=original['lot_code'],warehouse=original['warehouse'])
    else:
        row=ready(e,r,pid,v);move(e,pid,row);stop(e,pid,row,eid)
    preview(e,pid,v,409)


def test_new_raw_machining_admission_cannot_reuse_child_identifier(clients):
    e,r,pid,comp,eid,consumed,stocks=original_setup(clients,count=2);recovery=ready_original(e,r,pid,raw_original(consumed,eid));execute_original(e,pid,recovery)
    from tests.test_stock_offcut_reuse import target
    c=target(e,r,pid,comp);lot=read(e,pid)['processing']['lots'][0];source=approve_reuse(e,r,pid,reuse_value(e,pid,c,eid,lot));reuse_move(e,pid,source)
    row=ready(e,r,pid,raw(source,eid));move(e,pid,row)
    v=raw_original(consumed,eid,code='SYN-CHILD-A');v['rows'][0]['offcuts'][0]['warehouse']='合成再加工余料库';original_preview(e,pid,v,409)


def test_preview_binding_and_request_identity_do_not_allow_changed_results(clients):
    e,r,pid,comp,c,eid,recovery,lot,source=setup(clients);b=submission(e,pid,raw(source,eid));bad=copy.deepcopy(b);bad['input']['inspector']='changed';submit(e,pid,bad,409)
    row=submit(e,pid,b);bad=copy.deepcopy(b);bad['input']['basis']='changed';submit(e,pid,bad,409)
    stale=dict(**common(e,pid),object_hash=store.digest(row));approve(r,pid,'stock_offcut_processing',row);move(e,pid,row,expected=409,b=stale)
