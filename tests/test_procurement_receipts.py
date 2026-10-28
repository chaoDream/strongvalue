"""Synthetic partial arrivals, accepted original-unit stock and immutable executed facts."""
import copy
from decimal import Decimal
from datetime import date
from concurrent.futures import ThreadPoolExecutor
import pytest
from backend import store,procurement_analyses as analyses
from tests import test_procurement_orders as orders
from tests import test_procurement_requests as requests
from tests.test_workflow import get,approve,upload
from tests.test_supplier_quotes import mutate


def base(pid):return f'/api/v1/projects/{pid}/procurement-receipts'


def setup(clients):
    e,r,pid,req,raw=orders.setup(clients);order=orders.save(e,pid,orders.submission(e,pid,raw));approve(r,pid,'procurement_orders',order);order=get(e,pid)['procurement_orders'][0]
    raw=dict(order_id=order['id'],order_hash=store.digest(order),delivery_number='SYN-DELIVERY-001',lot_code='SYN-PURCHASE-LOT-001',warehouse='合成采购库位A',received_on=date.today().isoformat(),received_quantity='5.25',accepted_quantity='5',rejected_quantity='0.25',inspector='合成验收人A（非真实人员）',basis='合成实到及验收原件，合格量已交所填库位，不合格待处置；非真实收货',evidence_id=raw['evidence_id'],received_confirmed=True)
    return e,r,pid,order,raw


def read(e,pid):
    q=e.get(base(pid));assert q.status_code==200,q.text;return q.json()


def preview(e,pid,raw,expected=200):
    q=e.post(base(pid)+'/preview',json={'input':raw});assert q.status_code==expected,q.text;return q.json()


def submission(e,pid,raw):
    q=preview(e,pid,raw);return dict(request_id=store.uid(),input=raw,ledger_hash=q['ledger_hash'],preview_hash=q['preview_hash'])


def save(e,pid,b,expected=200):
    q=e.post(base(pid),json=b);assert q.status_code==expected,q.text;return q.json()


def ready(e,r,pid,raw):
    row=save(e,pid,submission(e,pid,raw));approve(r,pid,'procurement_receipts',row);return next(x for x in get(e,pid)['procurement_receipts'] if x['id']==row['id'])


def move(e,pid,row,kind='execute',eid=None,expected=200,body=None):
    body=body or dict(request_id=store.uid(),ledger_hash=read(e,pid)['ledger_hash'],object_hash=store.digest(row),**(dict(reason='合成取消未执行收货，不撤销实物',evidence_id=eid) if kind=='cancel' else {}))
    q=e.post(base(pid)+'/'+row['id']+'/'+kind,json=body);assert q.status_code==expected,q.text;return q.json(),body


def test_preview_review_execute_partial_inventory_and_exact_retry(clients):
    e,r,pid,order,raw=setup(clients);before=get(e,pid);audit=e.get(f'/api/v1/projects/{pid}/audit').json();q=preview(e,pid,raw)
    assert q['balance']['remaining_quantity']=='15.75' and get(e,pid)==before and e.get(f'/api/v1/projects/{pid}/audit').json()==audit
    b=submission(e,pid,raw);row=save(e,pid,b);assert save(e,pid,b)==row and read(e,pid)['lots']==[]
    move(e,pid,row,expected=409);approve(r,pid,'procurement_receipts',row);row=get(e,pid)['procurement_receipts'][0];assert read(e,pid)['lots']==[]
    frozen=e.get(base(pid)+'/'+row['id']+'/export');event,b=move(e,pid,row);assert move(e,pid,row,body=b)[0]==event
    v=read(e,pid);assert v['sources'][0]['balance']['remaining_quantity']=='10.50' and v['lots'][0]['in_stock_quantity']=='5' and v['lots'][0]['unit']=='m³' and v['lots'][0]['verified']
    approve(r,pid,'procurement_orders',order,decision='reject',expected=409);approve(r,pid,'procurement_receipts',row,decision='reject',expected=409);move(e,pid,row,'cancel',raw['evidence_id'],409)
    after=get(e,pid)
    for k in before:
        if k.startswith('stock_') or k in ('boq','components','takeoffs','quotes','rules','report_approval','procurement_orders','procurement_requests','procurement_analyses','supplier_quotes'):assert after[k]==before[k],k
    artifact=next(a for a in e.get(f'/api/v1/projects/{pid}/artifacts').json() if a['hash']==frozen.headers['x-artifact-hash']);assert e.get(f'/api/v1/projects/{pid}/artifacts/'+artifact['id']+'/file').content==frozen.content


@pytest.mark.parametrize('fault',[
 lambda v:v.update(received_quantity='0'),lambda v:v.update(received_quantity='1000000001'),lambda v:v.update(received_quantity=5),lambda v:v.update(received_quantity='1e1'),lambda v:v.update(accepted_quantity='-1'),lambda v:v.update(accepted_quantity='5.1'),lambda v:v.update(rejected_quantity='NaN'),lambda v:v.update(rejected_quantity=True),lambda v:v.update(received_on='2999-01-01'),lambda v:v.update(received_on='2020-01-01'),lambda v:v.update(received_on='2026-1-1'),lambda v:v.update(delivery_number=''),lambda v:v.update(lot_code=' '),lambda v:v.update(warehouse=''),lambda v:v.update(inspector=''),lambda v:v.update(basis=''),lambda v:v.update(evidence_id='foreign'),lambda v:v.update(order_id='foreign'),lambda v:v.update(order_hash='0'*64),lambda v:v.update(received_confirmed=False),lambda v:v.update(received_confirmed=1),lambda v:v.update(unit='kg'),lambda v:v.update(price='0')
])
def test_invalid_input_never_records_goods(clients,fault):
    e,r,pid,order,raw=setup(clients);fault(raw);before=get(e,pid);q=e.post(base(pid)+'/preview',json={'input':raw});assert q.status_code in (404,409,422),q.text;assert get(e,pid)==before


def test_order_revision_keeps_executed_receipts_and_family_quota_but_invalidates_pending(clients):
    e,r,pid,order,raw=setup(clients);first=ready(e,r,pid,raw);move(e,pid,first)
    v={**raw,'delivery_number':'SYN-DELIVERY-2','lot_code':'SYN-LOT-2'};pending=ready(e,r,pid,v)
    newer=orders.save(e,pid,orders.submission(e,pid,orders.revision(order,order_number='SYN-CORRECTED')));approve(r,pid,'procurement_orders',newer);newer=get(e,pid)['procurement_orders'][-1]
    assert read(e,pid)['sources'][0]['balance']['remaining_quantity']=='10.50';move(e,pid,pending,expected=409);move(e,pid,pending,'cancel',raw['evidence_id'])
    v.update(order_id=newer['id'],order_hash=store.digest(newer),received_quantity='10.5',accepted_quantity='10.5',rejected_quantity='0');last=ready(e,r,pid,v);move(e,pid,last)
    view=read(e,pid);assert view['sources'][0]['balance']['remaining_quantity']=='0.00' and sum(Decimal(x['in_stock_quantity']) for x in view['lots'])==Decimal('15.5')
    preview(e,pid,{**v,'delivery_number':'over','lot_code':'over','received_quantity':'0.25','accepted_quantity':'0.25'},409)


def test_competing_approved_drafts_are_not_extra_quota_and_execution_is_atomic(clients):
    e,r,pid,order,raw=setup(clients);raw.update(received_quantity='10',accepted_quantity='10',rejected_quantity='0');a=ready(e,r,pid,raw);b=ready(e,r,pid,{**raw,'delivery_number':'other','lot_code':'other'})
    common=dict(request_id=store.uid(),ledger_hash=read(e,pid)['ledger_hash']);ba={**common,'object_hash':store.digest(a)};bb={**common,'request_id':store.uid(),'object_hash':store.digest(b)}
    with ThreadPoolExecutor(max_workers=2) as pool:
        x=pool.submit(lambda:e.post(base(pid)+'/'+a['id']+'/execute',json=ba));y=pool.submit(lambda:e.post(base(pid)+'/'+b['id']+'/execute',json=bb));codes=[x.result().status_code,y.result().status_code]
    assert sorted(codes)==[200,409] and len(get(e,pid)['procurement_receipt_events'])==1
    assert read(e,pid)['sources'][0]['balance']['remaining_quantity']=='5.75'


def test_cancel_preserves_record_allows_rebuild_and_lost_cancel_proof_never_reopens(clients):
    e,r,pid,order,raw=setup(clients);row=ready(e,r,pid,raw);event,b=move(e,pid,row,'cancel',raw['evidence_id']);assert move(e,pid,row,'cancel',raw['evidence_id'],body=b)[0]==event
    assert not read(e,pid)['items'][0]['review_valid'];assert read(e,pid)['sources'][0]['balance']['remaining_quantity']=='15.75'
    approve(r,pid,'procurement_receipts',row,expected=409);later=save(e,pid,submission(e,pid,raw));assert later['id']!=row['id']
    assert get(e,pid)['procurement_receipts'][0]==row and read(e,pid)['lots']==[]


@pytest.mark.parametrize('stage',['preview','save','review','execute'])
def test_original_bytes_gate_each_new_action_without_reversing_goods(clients,stage):
    e,r,pid,order,raw=setup(clients);b=submission(e,pid,raw);row=None
    if stage in ('review','execute'):row=save(e,pid,b)
    if stage=='execute':approve(r,pid,'procurement_receipts',row);row=get(e,pid)['procurement_receipts'][0]
    doc=next(d for d in get(e,pid)['documents'] if d['name']=='synthetic-cost-scope.txt');path=store.ROOT/'originals'/doc['hash'];old=path.read_bytes();path.write_bytes(b'changed receipt/order source')
    try:
        if stage=='preview':preview(e,pid,raw,409)
        elif stage=='save':save(e,pid,b,409)
        elif stage=='review':approve(r,pid,'procurement_receipts',row,expected=409)
        else:move(e,pid,row,expected=409)
        assert read(e,pid)['lots']==[] and read(e,pid)['sources'][0]['balance']['received_quantity']=='0'
    finally:path.write_bytes(old)


def test_executed_goods_survive_lost_proof_and_upstream_plan_withdrawal(clients):
    e,r,pid,order,raw=setup(clients);row=ready(e,r,pid,raw);move(e,pid,row);before=read(e,pid)
    parent=get(e,pid)['procurement_analyses'][0];approve(r,pid,'procurement_analyses',parent,decision='reject');assert read(e,pid)['lots'][0]['verified']
    doc=next(d for d in get(e,pid)['documents'] if d['name']=='synthetic-cost-scope.txt');path=store.ROOT/'originals'/doc['hash'];old=path.read_bytes();path.unlink()
    try:
        view=read(e,pid);assert not view['lots'][0]['verified'] and view['lots'][0]['in_stock_quantity']=='5';assert view['sources'][0]['balance']==before['sources'][0]['balance']
        assert view['items'][0]['phase']=='receive';assert e.get(base(pid)+'/'+row['id']+'/export').status_code==200
    finally:path.write_bytes(old)


def test_roles_leases_duplicate_delivery_lot_and_self_review(clients):
    e,r,pid,order,raw=setup(clients);b=submission(e,pid,raw);assert r.post(base(pid),json=b).status_code==403 and clients['other'].get(base(pid)).status_code==404
    row=save(e,pid,b);assert clients['other'].get(base(pid)+'/'+row['id']+'/export').status_code==404
    preview(e,pid,{**raw,'lot_code':'other'},409);preview(e,pid,{**raw,'delivery_number':'other'},409)
    with store.transaction() as c:c.execute("UPDATE users SET role='reviewer' WHERE username='estimator'")
    approve(e,pid,'procurement_receipts',row,expected=403)


def test_all_rejected_arrival_uses_quota_without_creating_good_stock(clients):
    e,r,pid,order,raw=setup(clients);raw.update(received_quantity='15.75',accepted_quantity='0',rejected_quantity='15.75');row=ready(e,r,pid,raw);move(e,pid,row)
    assert not read(e,pid)['lots'] and read(e,pid)['sources'][0]['balance']['remaining_quantity']=='0.00'


def test_corrupted_execution_blocks_new_views_but_old_export_survives(clients):
    e,r,pid,order,raw=setup(clients);row=ready(e,r,pid,raw);move(e,pid,row);f=e.get(base(pid)+'/'+row['id']+'/export');a=next(a for a in e.get(f'/api/v1/projects/{pid}/artifacts').json() if a['hash']==f.headers['x-artifact-hash'])
    mutate(pid,lambda p:p['procurement_receipt_events'][0].update(quantities={'accepted_quantity':'999'}));assert e.get(base(pid)).status_code==409
    assert e.get(f'/api/v1/projects/{pid}/artifacts/'+a['id']+'/file').content==f.content


def test_legacy_project_normalizes_empty_receipt_collections(clients):
    e,r,pid,order,raw=setup(clients);mutate(pid,lambda p:(p.pop('procurement_receipts',None),p.pop('procurement_receipt_events',None)));assert not read(e,pid)['items']


def test_cross_ledger_same_warehouse_batch_claims_are_bidirectional_and_cancel_releases_only_draft(clients):
    from tests import test_stock_inventory as stock
    e,r,pid,order,raw=setup(clients);legacy,body=stock.receipt(e,r,pid,raw['evidence_id'],lot='SYN-LEGACY-001')
    raw.update(warehouse=legacy['input']['warehouse'],lot_code=legacy['input']['lot_code']);preview(e,pid,raw,409)
    raw['lot_code']='SYN-PROCUREMENT-ONLY';row=save(e,pid,submission(e,pid,raw))
    rootbody={**stock.common(e,pid),'input':{**body['input'],'lot_code':raw['lot_code']}}
    before=get(e,pid);q=e.post(stock.base(pid)+'/receipts',json=rootbody);assert q.status_code==409,q.text;assert get(e,pid)==before
    move(e,pid,row,'cancel',raw['evidence_id'])
    rootbody.update(**stock.common(e,pid));q=e.post(stock.base(pid)+'/receipts',json=rootbody);assert q.status_code==200,q.text


def test_tiny_exact_quantity_does_not_round_away_arrival_or_order_balance(clients):
    e,r,pid,order,raw=setup(clients);raw.update(received_quantity='0.000000000000000001',accepted_quantity='0.000000000000000001',rejected_quantity='0');row=ready(e,r,pid,raw);move(e,pid,row)
    v=read(e,pid);assert v['lots'][0]['in_stock_quantity']=='0.000000000000000001' and v['sources'][0]['balance']['remaining_quantity']=='15.749999999999999999'


def test_receipt_execution_cancel_race_closes_once_and_stale_previews_do_not_mutate(clients):
    e,r,pid,order,raw=setup(clients);row=ready(e,r,pid,raw);h=read(e,pid)['ledger_hash'];a=dict(request_id=store.uid(),ledger_hash=h,object_hash=store.digest(row));b={**a,'request_id':store.uid(),'reason':'合成取消未执行','evidence_id':raw['evidence_id']}
    with ThreadPoolExecutor(max_workers=2) as pool:
        x=pool.submit(lambda:e.post(base(pid)+'/'+row['id']+'/execute',json=a));y=pool.submit(lambda:e.post(base(pid)+'/'+row['id']+'/cancel',json=b));codes=[x.result().status_code,y.result().status_code]
    assert sorted(codes)==[200,409] and len(get(e,pid)['procurement_receipt_events'])==1


def test_stale_ledger_and_preview_body_and_duplicate_request_id_are_rejected(clients):
    e,r,pid,order,raw=setup(clients);b=submission(e,pid,raw);bad=copy.deepcopy(b);bad['input']['basis']='changed after preview';save(e,pid,bad,409)
    row=save(e,pid,b);bad=copy.deepcopy(b);bad['input']['delivery_number']='other';save(e,pid,bad,409)
    bad={**b,'request_id':store.uid()};save(e,pid,bad,409)
    assert len(get(e,pid)['procurement_receipts'])==1
