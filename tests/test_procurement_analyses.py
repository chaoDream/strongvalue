"""Synthetic cost scenarios; never an order, payment, tax assessment or stock acceptance."""
import copy
from datetime import date,timedelta
from concurrent.futures import ThreadPoolExecutor
import pytest
from backend import store
from tests.test_supplier_quotes import fixture as offer_fixture,register,mutate,adopt
from tests.test_workflow import get,approve,upload


def setup(clients,tax='不含税'):
    e,r,pid,old,b=offer_fixture(clients)
    doc=upload(e,pid,'合成采购测算范围：TEST-MAT，15.75m³，两份报价同不含税。A运费另计10.05元、其他0.02元；B运费20元、其他30元，均未包含在原货款内。不是真实采购。','synthetic-cost-scope.txt','合同计价')
    eid=next(x['id'] for x in get(e,pid)['evidence'] if x['document_id']==doc['document']['id'])
    for collection in ('components','takeoffs'):
        for row in get(e,pid)[collection]:approve(r,pid,collection,row)
    row=e.get(f'/api/v1/projects/{pid}/resource-prices').json()[0]
    b.update(boq_hash=row['boq_hash'],resource_hash=row['resource_hash'])
    a=register(e,pid,b)['offer'];c=register(e,pid,{**b,'request_id':store.uid(),'supplier':'合成供应商B','reference':'SYN-COST-B','price':'80.123456','tax_mode':tax})['offer']
    for item in (a,c):approve(r,pid,'supplier_quotes',item)
    p=get(e,pid);a,c=p['supplier_quotes']
    raw=dict(name='合成采购成本核查',key=row['key'],boq_hash=row['boq_hash'],resource_hash=row['resource_hash'],quantity='15.75',analysis_date=date.today().isoformat(),basis='仅合成范围核查，原件明确数量与附加费用，同税制且不重复，非真实采购',evidence_id=eid,conditions_confirmed=True,scenarios=[dict(quote_id=a['id'],quote_hash=store.digest(a),freight='10.05',other_charges='0.02',adjustment_basis='合成A报价不含本次运费与其他费用，按核查原件另加'),dict(quote_id=c['id'],quote_hash=store.digest(c),freight='20',other_charges='30',adjustment_basis='合成B同税制，附加费用均未含在原货款内')])
    return e,r,pid,raw


def base(pid):return f'/api/v1/projects/{pid}/procurement-analyses'


def preview(e,pid,raw,expected=200):
    q=e.post(base(pid)+'/preview',json={'input':raw});assert q.status_code==expected,q.text;return q.json()


def submission(e,pid,raw):return dict(request_id=store.uid(),input=raw,preview_hash=preview(e,pid,raw)['preview_hash'])


def save(e,pid,body,expected=200):
    q=e.post(base(pid),json=body);assert q.status_code==expected,q.text;return q.json()


def test_preview_save_review_exports_and_no_engineering_price_or_stock_changes(clients):
    e,r,pid,raw=setup(clients);before=get(e,pid);audit=e.get(f'/api/v1/projects/{pid}/audit').json();q=preview(e,pid,raw)
    assert get(e,pid)==before and e.get(f'/api/v1/projects/{pid}/audit').json()==audit
    a,b=q['result']['scenarios'];assert a['goods_exact']=='1386.07875' and a['goods_amount']=='1386.08' and a['total']=='1396.15'
    assert b['goods_exact']=='1261.94443200' and b['total']=='1311.94'
    body=dict(request_id=store.uid(),input=raw,preview_hash=q['preview_hash']);row=save(e,pid,body);assert save(e,pid,body)==row
    frozen=e.get(base(pid)+'/'+row['id']+'/export');assert frozen.status_code==200 and not frozen.json()['review_valid']
    approve(e,pid,'procurement_analyses',row,expected=403);approve(r,pid,'procurement_analyses',row)
    view=e.get(base(pid)).json()['items'][0];assert view['review_valid'] and view['current']
    p=get(e,pid)
    for key in before:
        if key.startswith('stock_') or key in ('components','takeoffs','boq','quotes','report_approval','supplier_quotes','supplier_quote_adoptions'):assert p[key]==before[key],key
    exported=e.get(base(pid)+'/'+row['id']+'/export').json();assert exported['review_valid'] and len(exported['reviews'])==1
    artifacts=e.get(f'/api/v1/projects/{pid}/artifacts').json();artifact=next(a for a in artifacts if a['hash']==frozen.headers['x-artifact-hash'])
    assert e.get(f'/api/v1/projects/{pid}/artifacts/'+artifact['id']+'/file').content==frozen.content
    assert p['boq'][0]['quotas'][0]['resources'][0]['price'] is None


@pytest.mark.parametrize('fault',[
 lambda v:v.update(quantity='0'),lambda v:v.update(quantity='-1'),lambda v:v.update(quantity='1e2'),lambda v:v.update(quantity=2),lambda v:v.update(quantity=True),lambda v:v.update(quantity='1000000001'),lambda v:v.update(quantity='1.1234567890123456789'),lambda v:v.update(analysis_date='2999-01-01'),lambda v:v.update(analysis_date=None),lambda v:v.update(analysis_date='2026-1-1'),lambda v:v.update(basis=' '),lambda v:v.update(conditions_confirmed=False),lambda v:v.update(conditions_confirmed=1),lambda v:v.update(extra='currency'),lambda v:v.update(scenarios=[]),lambda v:v['scenarios'][0].update(freight='-1'),lambda v:v['scenarios'][0].update(freight='0.001'),lambda v:v['scenarios'][0].update(other_charges='NaN'),lambda v:v['scenarios'][0].update(other_charges=3),lambda v:v['scenarios'][0].update(freight='1000000000001'),lambda v:v['scenarios'][0].update(adjustment_basis=''),lambda v:v.update(scenarios=v['scenarios'][:1]*2),lambda v:v.update(scenarios=v['scenarios'][:1]*11)
])
def test_invalid_inputs_never_save_or_audit(clients,fault):
    e,r,pid,raw=setup(clients);fault(raw);before=get(e,pid);audit=e.get(f'/api/v1/projects/{pid}/audit').json()
    preview(e,pid,raw,422);assert get(e,pid)==before and e.get(f'/api/v1/projects/{pid}/audit').json()==audit


def test_mixed_tax_rejected_and_single_tax_separately_supported(clients):
    e,r,pid,raw=setup(clients,tax='含税');preview(e,pid,raw,422)
    raw['scenarios']=raw['scenarios'][1:];q=preview(e,pid,raw);assert q['result']['tax_mode']=='含税' and q['result']['scenarios'][0]['total']=='1311.94'


@pytest.mark.parametrize('stage',['preview','save','review'])
@pytest.mark.parametrize('fault',['quote_hash','withdrawal','bytes','quote_bytes','target','date_context'])
def test_changed_source_gates_every_stage_and_preserves_frozen_records(clients,stage,fault):
    e,r,pid,raw=setup(clients);body=submission(e,pid,raw);row=save(e,pid,body) if stage=='review' else None;path=None;saved=None
    if fault=='quote_hash':raw['scenarios'][1]['quote_hash']='0'*64
    elif fault=='withdrawal':approve(r,pid,'supplier_quotes',get(e,pid)['supplier_quotes'][1],decision='reject')
    elif fault in ('bytes','quote_bytes'):
        doc=next(d for d in get(e,pid)['documents'] if d['name']==('supplier.txt' if fault=='quote_bytes' else 'synthetic-cost-scope.txt'));path=store.ROOT/'originals'/doc['hash'];saved=path.read_bytes();path.write_bytes(b'changed synthetic procurement scope')
    elif fault=='target':mutate(pid,lambda p:p['boq'][0].update(features='合成已变更范围'))
    else:mutate(pid,lambda p:p.update(pricing_date=(date.today()+timedelta(days=1)).isoformat()))
    before=get(e,pid);audit=e.get(f'/api/v1/projects/{pid}/audit').json()
    try:
        if stage=='preview':
            if fault=='date_context':assert preview(e,pid,raw)['preview_hash']!=body['preview_hash']
            else:preview(e,pid,raw,409)
        elif stage=='save':save(e,pid,{**body,'input':raw},409)
        elif fault=='quote_hash':
            # The stored immutable input is independent from a changed caller copy.
            approve(r,pid,'procurement_analyses',row)
            return
        else:approve(r,pid,'procurement_analyses',row,expected=409)
        assert get(e,pid)==before and e.get(f'/api/v1/projects/{pid}/audit').json()==audit
        if row:
            q=e.get(base(pid)+'/'+row['id']+'/export');assert q.status_code==200 and not q.json()['current']
            assert q.json()['record']==row
    finally:
        if path:path.write_bytes(saved)


def test_permissions_scope_self_review_and_no_approval_side_effects(clients):
    e,r,pid,raw=setup(clients);b=submission(e,pid,raw)
    assert r.post(base(pid),json=b).status_code==403
    assert clients['other'].get(base(pid)).status_code==404
    assert clients['other'].get(base(pid)+'/options').status_code==404
    row=save(e,pid,b);p=get(e,pid)
    with store.transaction() as db:db.execute("UPDATE users SET role='reviewer' WHERE username='estimator'")
    approve(e,pid,'procurement_analyses',row,expected=403)
    for decision in ('request_more_info','reject'):
        row=next(x for x in get(r,pid)['procurement_analyses'] if x['id']==row['id']);approve(r,pid,'procurement_analyses',row,decision=decision)
    after=get(r,pid)
    for k in ('boq','components','takeoffs','quotes','stale_quotes','report_approval'):assert after[k]==p[k]


def test_retry_snapshot_tamper_concurrent_save_and_original_quote_revision(clients):
    e,r,pid,raw=setup(clients);b=submission(e,pid,raw)
    with ThreadPoolExecutor(max_workers=2) as pool:rows=list(pool.map(lambda _:save(e,pid,b),range(2)))
    assert rows[0]==rows[1] and len(get(e,pid)['procurement_analyses'])==1
    bad=copy.deepcopy(b);bad['input']['name']='changed request';save(e,pid,bad,409)
    bad=copy.deepcopy(b);bad['request_id']=store.uid();bad['input']['scenarios'][0]['freight']='22';save(e,pid,bad,409)
    offer=get(e,pid)['supplier_quotes'][0];row=offer['target']
    # Revising a quote leaves the saved scenario unchanged but makes it historical.
    original=offer['metadata'];body=dict(request_id=store.uid(),key=row['key'],boq_hash=row['boq_hash'],resource_hash=row['resource_hash'],supplier=offer['supplier'],reference='SYN-COST-REV2',received_on=offer['received_on'],terms=offer['terms'],reason='合成报价修订',price='90',license=original['license'],period=original['period'],region=original['region'],tax_mode=original['tax_mode'],valid_from=original['valid_from'],valid_until=original['valid_until'],evidence_id=offer['evidence_id'],revision_of=offer['id'],revision_hash=store.digest(offer))
    register(e,pid,body);view=e.get(base(pid)).json()['items'][0];assert not view['current'] and view['record']==rows[0]
    assert save(e,pid,b)==rows[0]


def test_source_adoption_is_supported_only_in_a_new_current_snapshot(clients):
    e,r,pid,raw=setup(clients);old=save(e,pid,submission(e,pid,raw));offer=get(e,pid)['supplier_quotes'][0];adopt(e,pid,offer)
    assert not e.get(base(pid)).json()['items'][0]['current']
    options=e.get(base(pid)+'/options').json()['groups'][0];assert all(x['eligible'] for x in options['offers'])
    raw.update(boq_hash=options['target']['boq_hash'],resource_hash=options['target']['resource_hash'])
    new=save(e,pid,submission(e,pid,raw));assert new['result']['scenarios'][0]['total']==old['result']['scenarios'][0]['total']


def test_minimum_fraction_zero_fees_and_half_cent_rounding(clients):
    e,r,pid,raw=setup(clients);raw['scenarios']=raw['scenarios'][:1];raw['scenarios'][0].update(freight='0',other_charges='0')
    raw['quantity']='1';assert preview(e,pid,raw)['result']['scenarios'][0]['total']=='88.01'
    raw['quantity']='0.000000000000000001';v=preview(e,pid,raw)['result']['scenarios'][0];assert v['total']=='0.00' and v['goods_exact']=='0.000000000000000088005'


def test_record_tamper_blocks_current_and_new_exports_not_old_artifact(clients):
    e,r,pid,raw=setup(clients);row=save(e,pid,submission(e,pid,raw));f=e.get(base(pid)+'/'+row['id']+'/export')
    artifact=next(x for x in e.get(f'/api/v1/projects/{pid}/artifacts').json() if x['hash']==f.headers['x-artifact-hash'])
    mutate(pid,lambda p:p['procurement_analyses'][0]['result']['scenarios'][0].update(total='0.00'))
    assert e.get(base(pid)).status_code==409 and e.get(base(pid)+'/'+row['id']+'/export').status_code==409
    assert e.get(f'/api/v1/projects/{pid}/artifacts/'+artifact['id']+'/file').content==f.content


def test_unreviewed_foreign_quote_and_stale_lease_are_blocked(clients):
    e,r,pid,raw=setup(clients)
    for key in ('boq_hash','resource_hash'):
        v=copy.deepcopy(raw);v[key]='0'*64;preview(e,pid,v,409)
    v=copy.deepcopy(raw);v['scenarios'][1]['quote_id']='foreign-or-missing';preview(e,pid,v,404)
    offer=get(e,pid)['supplier_quotes'][1];approve(r,pid,'supplier_quotes',offer,decision='request_more_info')
    v=copy.deepcopy(raw);v['scenarios'][1]['quote_hash']=store.digest(get(e,pid)['supplier_quotes'][1]);preview(e,pid,v,409)
    choices=e.get(base(pid)+'/options').json()['groups'][0]['offers'];assert sum(x['eligible'] for x in choices)==1


def test_analysis_date_must_not_precede_offer_or_follow_expiry(clients):
    e,r,pid,raw=setup(clients);raw['analysis_date']=(date.today()-timedelta(days=2)).isoformat();preview(e,pid,raw,409)


def test_old_project_normalizes_without_fake_analysis(clients):
    e,r,pid,raw=setup(clients);mutate(pid,lambda p:p.pop('procurement_analyses',None))
    assert e.get(base(pid)).json()['items']==[] and get(e,pid)['procurement_analyses']==[]
