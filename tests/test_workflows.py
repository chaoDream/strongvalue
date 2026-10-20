import copy
import json
from datetime import date, timedelta

from backend import store
from backend.workflows import allocation_diagnostics
from tests.test_workflow import get, approve, setup_boq, priced_project


def batch_body(client, pid, collection, objects, decision='approve'):
    hashes = client.get(f'/api/v1/projects/{pid}/hashes').json()[collection]
    return {'collection': collection, 'decision': decision, 'reason': '合成测试逐项核查，共同专业依据',
            'targets': [{'object_id': obj['id'], 'object_hash': hashes[obj['id']]} for obj in objects]}


def split_items(clients):
    e,r,pid,comp,t,b,eid = setup_boq(clients)
    base=f'/api/v1/projects/{pid}'
    e.put(base+'/boq/'+b['id'],json={'code':'TEST-CODE','features':'测试编码及计量特征'})
    response=e.post(base+'/boq/'+b['id']+'/split',json={'parts':[{'name':'测试一','quantity':'5'},{'name':'测试二','quantity':'10'}]})
    assert response.status_code==200,response.text
    return e,r,pid,response.json()


def test_batch_atomic_hash_validation_and_individual_records(clients):
    e,r,pid,items=split_items(clients);base=f'/api/v1/projects/{pid}'
    snapshot=r.get(base+'/review-batch-targets?collection=boq').json()
    assert snapshot['total']==2
    assert all(t['object_hash']==store.digest(t['object']) for t in snapshot['targets'])
    body=batch_body(r,pid,'boq',items)
    body['targets'][1]['object_hash']='stale'
    before=get(e,pid)
    assert r.post(base+'/reviews/batch',json=body).status_code==409
    assert get(e,pid)['reviews']==before['reviews']
    assert all(b['state']=='draft' for b in get(e,pid)['boq'])
    body=batch_body(r,pid,'boq',items)
    assert e.post(base+'/reviews/batch',json=body).status_code==403
    response=r.post(base+'/reviews/batch',json=body)
    assert response.status_code==200,response.text
    assert len(response.json()['reviews'])==2
    assert all(b['state']=='approved' for b in get(e,pid)['boq'])
    assert r.get(base+'/review-batch-targets?collection=boq').json()['total']==0
    # Rejecting the first approved item invalidates siblings. Both original hashes
    # remain the reviewed baseline, and the second decision must still succeed.
    body=batch_body(r,pid,'boq',items,'reject')
    expected=[x['object_hash'] for x in body['targets']]
    result=r.post(base+'/reviews/batch',json=body)
    assert result.status_code==200,result.text
    assert [x['object_hash'] for x in result.json()['reviews']]==expected
    assert all(b['state']=='rejected' for b in get(e,pid)['boq'])


def test_batch_business_failure_rolls_back_earlier_approvals_and_audit(clients):
    e,r,pid,items=split_items(clients);base=f'/api/v1/projects/{pid}'
    e.put(base+'/boq/'+items[1]['id'],json={'features':''})
    before=get(e,pid);audit=e.get(base+'/audit').json()
    body=batch_body(r,pid,'boq',items)
    response=r.post(base+'/reviews/batch',json=body)
    assert response.status_code==409
    assert get(e,pid)['reviews']==before['reviews']
    assert e.get(base+'/audit').json()==audit
    assert all(b['state']=='draft' for b in get(e,pid)['boq'])
    body['targets']=[body['targets'][0]]*2
    assert r.post(base+'/reviews/batch',json=body).status_code==422
    body['targets']=[body['targets'][0]];body['reason']=None
    assert r.post(base+'/reviews/batch',json=body).status_code==422
    assert clients['other'].get(base+'/review-batch-targets?collection=boq').status_code==404


def proposal_body(e,pid,price='120.1234'):
    rows=e.get(f'/api/v1/projects/{pid}/resource-prices').json()
    p=get(e,pid);eid=p['evidence'][0]['id']
    return {'reason':'合成调价测试，合同口径及当前来源均已核查',
            'entries':[{**{k:row[k] for k in ['key','boq_hash','resource_hash']},'price':price} for row in rows],
            'metadata':{'source':'数学测试供应商报价','license':'仅用于测试','period':'2026-10','region':'河南省','tax_mode':'不含税',
                        'valid_from':(date.today()-timedelta(days=1)).isoformat(),'valid_until':(date.today()+timedelta(days=365)).isoformat(),
                        'evidence_id':eid,'source_type':'供应商报价'}}


def test_price_change_review_apply_and_immutable_quote(clients):
    e,r,pid,comp,q=priced_project(clients);base=f'/api/v1/projects/{pid}'
    approve(r,pid,'quotes',q)
    old=json.dumps(get(e,pid)['quotes'],sort_keys=True)
    body=proposal_body(e,pid)
    proposal=e.post(base+'/price-changes',json=body)
    assert proposal.status_code==200,proposal.text
    ch=proposal.json()
    assert ch['impact']['before_subtotal']=='1575.15'
    assert ch['impact']['after_subtotal']=='1891.95'
    assert ch['impact']['delta']=='316.80'
    assert any('未应用的资源调价' in x for x in get(e,pid)['blockers'])
    assert get(e,pid)['boq'][0]['quotas'][0]['resources'][0]['price']=='100.005'
    assert e.post(base+'/price-changes/'+ch['id']+'/apply').status_code==409
    approve(r,pid,'price_changes',ch)
    assert r.post(base+'/price-changes/'+ch['id']+'/apply').status_code==403
    result=e.post(base+'/price-changes/'+ch['id']+'/apply')
    assert result.status_code==200,result.text
    assert e.post(base+'/price-changes/'+ch['id']+'/apply').json()['duplicate']
    p=get(e,pid);b=p['boq'][0]
    assert b['state']=='draft' and q['id'] in p['stale_quotes']
    assert json.dumps(p['quotes'],sort_keys=True)==old
    assert b['quotas'][0]['resources'][0]['price_change_id']==ch['id']
    assert e.post(base+'/quotes/calculate').status_code==409
    approve(r,pid,'boq',b)
    new=e.post(base+'/quotes/calculate')
    assert new.status_code==200,new.text
    assert new.json()['total']=='1948.71'
    assert new.json()['snapshot']['price_changes'][0]['entries'][0]['before']['price']=='100.005'
    exported=e.get(base+'/exports?format=json&quote_id='+new.json()['id']).json()
    ledger=next(table for table in exported['tables'] if table[0]=='资源调价台账')
    assert ledger[2][0][2:4]==['100.005','120.1234']


def test_price_revocation_blocks_even_after_boq_reapproval_and_new_correction_recovers(clients):
    e,r,pid,comp,q=priced_project(clients);base=f'/api/v1/projects/{pid}'
    ch=e.post(base+'/price-changes',json=proposal_body(e,pid)).json()
    approve(r,pid,'price_changes',ch);e.post(base+'/price-changes/'+ch['id']+'/apply')
    approve(r,pid,'price_changes',ch,'reject')
    approve(r,pid,'boq',get(e,pid)['boq'][0])
    response=e.post(base+'/quotes/calculate')
    assert response.status_code==409 and '调价审批已失效' in response.text
    correction=e.post(base+'/price-changes',json=proposal_body(e,pid,'115')).json()
    approve(r,pid,'price_changes',correction)
    assert e.post(base+'/price-changes/'+correction['id']+'/apply').status_code==200
    approve(r,pid,'boq',get(e,pid)['boq'][0])
    assert e.post(base+'/quotes/calculate').status_code==200


def test_price_baseline_permissions_and_metadata_validation(clients):
    e,r,pid,comp,q=priced_project(clients);base=f'/api/v1/projects/{pid}'
    body=proposal_body(e,pid)
    assert r.post(base+'/price-changes',json=body).status_code==403
    assert clients['other'].get(base+'/resource-prices').status_code==404
    assert clients['other'].post(base+'/price-changes',json=body).status_code==404
    for field,value in [('source_type','历史审核价'),('region','不适用地区'),('valid_until','1900-01-01'),('evidence_id','missing')]:
        invalid=copy.deepcopy(body);invalid['metadata'][field]=value
        assert e.post(base+'/price-changes',json=invalid).status_code in (404,409)
    invalid=copy.deepcopy(body);invalid['entries']*=2
    assert e.post(base+'/price-changes',json=invalid).status_code==422
    ch=e.post(base+'/price-changes',json=body).json()
    b=get(e,pid)['boq'][0]
    e.put(base+'/boq/'+b['id'],json={'features':'编制期间已修改特征'})
    approve(r,pid,'price_changes',ch,expected=409)
    assert e.post(base+'/price-changes',json=body).status_code==409


def test_approved_price_change_cannot_apply_after_rules_change(clients):
    e,r,pid,comp,q=priced_project(clients);base=f'/api/v1/projects/{pid}'
    ch=e.post(base+'/price-changes',json=proposal_body(e,pid)).json()
    approve(r,pid,'price_changes',ch)
    rules=get(e,pid)['rules'][0];rules['rounding_config']={'unit_price_digits':4}
    assert e.put(base+'/rules',json=rules).status_code==200
    assert e.post(base+'/price-changes/'+ch['id']+'/apply').status_code==409


def test_price_rejection_after_boq_split_does_not_leave_permanent_pending_gate(clients):
    e,r,pid,comp,q=priced_project(clients);base=f'/api/v1/projects/{pid}'
    ch=e.post(base+'/price-changes',json=proposal_body(e,pid)).json()
    b=get(e,pid)['boq'][0]
    assert e.post(base+'/boq/'+b['id']+'/split',json={'parts':[{'name':'一','quantity':'5'},{'name':'二','quantity':'10'}]}).status_code==200
    approve(r,pid,'price_changes',ch,'reject')
    assert not any('未应用的资源调价' in x for x in get(e,pid)['blockers'])


def test_allocation_diagnostics_units_overallocation_and_missing_quantity_gate(clients):
    e,r,pid,comp,q=priced_project(clients);base=f'/api/v1/projects/{pid}'
    assert e.get(base+'/boq/diagnostics').json()['passed']
    assert clients['other'].get(base+'/boq/diagnostics').status_code==404
    original=get(e,pid)
    p=copy.deepcopy(original);p['boq'][0]['unit']='m'
    assert 'unit_mismatch' in {x['kind'] for x in allocation_diagnostics(p)['issues']}
    p=copy.deepcopy(original);p['boq'].append(copy.deepcopy(p['boq'][0]))
    assert 'overallocated' in {x['kind'] for x in allocation_diagnostics(p)['issues']}
    with store.transaction() as c:
        p=copy.deepcopy(original);extra=copy.deepcopy(p['takeoffs'][0]);extra.update(id=store.uid(),name='未编入清单的测试工程量')
        p['takeoffs'].append(extra);store.save(c,p)
    diag=e.get(base+'/boq/diagnostics').json()
    assert not diag['passed'] and any(x['kind']=='unallocated' for x in diag['issues'])
    quote=e.post(base+'/quotes/calculate').json()
    assert any('未编入清单' in x for x in quote['warnings'])
    approve(r,pid,'quotes',quote,expected=409)
