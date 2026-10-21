import copy
from datetime import date, timedelta
import pytest
from backend import store
from backend.reports import tables
from tests.test_workflow import create, get, upload, approve, priced_project


def proposal(c, pid, eid, chosen=None):
    chosen = chosen or date.today().isoformat()
    p = get(c, pid)
    old = p['pricing_bases'][-1] if p['pricing_bases'] else None
    return {'milestones':{'tender':{'date':chosen,'evidence_id':eid},
                           'contract':{'date':(date.fromisoformat(chosen)+timedelta(days=20)).isoformat(),'evidence_id':eid},
                           'design':{'date':chosen,'edition':'测试设计V1','evidence_id':eid}},
            'selected':'tender','pricing_date':chosen,'evidence_id':eid,
            'reason':'合成合同明确采用招标日期；合同签订为后续事件，不据此自动采用新标准。',
            'previous_hash':store.digest(old) if old else ''}


def submit(c, pid, body):
    response = c.post(f'/api/v1/projects/{pid}/pricing-bases',json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_review_applies_date_and_snapshot_is_immutable(clients):
    e,r,pid,comp,old = priced_project(clients)
    base = f'/api/v1/projects/{pid}'
    p = get(e,pid);eid=p['evidence'][0]['id']
    b=submit(e,pid,proposal(e,pid,eid))
    p=get(e,pid)
    assert old['id'] in p['stale_quotes']
    assert p['quotes'][0] == old
    assert e.post(base+'/quotes/calculate').status_code == 409
    assert 'pricing_date' not in p
    approve(r,pid,'pricing_bases',b)
    p=get(e,pid)
    assert p['pricing_date']==b['pricing_date']
    q=e.post(base+'/quotes/calculate').json()
    assert q['snapshot']['pricing_bases'][-1]['state']=='approved'
    assert q['snapshot']['pricing_bases'][-1]['milestones']==b['milestones']
    summary=dict(tables(p,q,False)[1][0][2])
    assert summary['招标日期'].startswith(b['pricing_date'])
    # A later proposal never rewrites an archived quote or its report dates.
    submit(e,pid,proposal(e,pid,eid,(date.today()+timedelta(days=1)).isoformat()))
    assert get(e,pid)['quotes'][-1]==q
    assert dict(tables(get(e,pid),q,False)[1][0][2])['招标日期'].startswith(b['pricing_date'])


@pytest.mark.parametrize('mutation',[
    lambda b:b.update(milestones={}),
    lambda b:b['milestones']['tender'].update(date='2026-02-30'),
    lambda b:b['milestones']['tender'].update(date='20261021'),
    lambda b:b.update(selected='unknown'),
    lambda b:b.update(pricing_date='1990-01-01'),
    lambda b:b['milestones']['design'].update(edition=''),
    lambda b:b.update(reason=''),
    lambda b:b.update(evidence_id={'not':'an id'}),
])
def test_invalid_date_decisions_are_atomic(clients,mutation):
    e=clients['estimator'];pid=create(e)['id'];upload(e,pid)
    p=get(e,pid);body=proposal(e,pid,p['evidence'][0]['id']);mutation(body)
    res=e.post(f'/api/v1/projects/{pid}/pricing-bases',json=body)
    assert res.status_code == 422, res.text
    assert get(e,pid)['pricing_bases']==[]


def test_roles_tenant_hash_and_history_guards(clients):
    e,r=clients['estimator'],clients['reviewer'];pid=create(e)['id'];upload(e,pid)
    base=f'/api/v1/projects/{pid}';body=proposal(e,pid,get(e,pid)['evidence'][0]['id'])
    assert r.post(base+'/pricing-bases',json=body).status_code==403
    assert clients['other'].post(base+'/pricing-bases',json=body).status_code==404
    b=submit(e,pid,body)
    assert e.post(base+'/pricing-bases',json=body).status_code==409
    approve(e,pid,'pricing_bases',b,expected=403)
    wrong=r.post(base+'/reviews',json={'collection':'pricing_bases','object_id':b['id'],'object_hash':'old','decision':'approve','reason':'test'})
    assert wrong.status_code==409
    newer=submit(e,pid,proposal(e,pid,body['evidence_id']))
    approve(r,pid,'pricing_bases',b,expected=409)
    targets=r.get(base+'/review-batch-targets?collection=pricing_bases').json()['targets']
    assert [t['object']['id'] for t in targets]==[newer['id']]
    approve(r,pid,'pricing_bases',newer)


def test_custom_date_and_manual_override_require_reapproval(clients):
    e,r,pid,comp,q=priced_project(clients);base=f'/api/v1/projects/{pid}'
    eid=get(e,pid)['evidence'][0]['id'];body=proposal(e,pid,eid)
    body.update(selected='custom',pricing_date=(date.today()+timedelta(days=3)).isoformat())
    b=submit(e,pid,body);approve(r,pid,'pricing_bases',b)
    assert e.post(base+'/quotes/calculate').status_code==200
    override=(date.today()+timedelta(days=4)).isoformat()
    assert e.patch(base+'/settings',json={'pricing_date':override,'reason':'人工修改测试'}).status_code==200
    assert get(e,pid)['pricing_bases'][-1]['state']=='stale'
    assert e.post(base+'/quotes/calculate').status_code==409
    e.patch(base+'/settings',json={'pricing_date':b['pricing_date'],'reason':'改回也不能恢复审批'})
    assert e.post(base+'/quotes/calculate').status_code==409
    approve(r,pid,'pricing_bases',get(e,pid)['pricing_bases'][-1])
    assert e.post(base+'/quotes/calculate').status_code==200


def test_source_replacement_and_corruption_block_decision_and_quote(clients):
    e,r,pid,comp,q=priced_project(clients);base=f'/api/v1/projects/{pid}'
    p=get(e,pid);eid=p['evidence'][0]['id'];b=submit(e,pid,proposal(e,pid,eid))
    path=store.ROOT/'originals'/p['documents'][0]['hash'];original=path.read_bytes()
    path.write_bytes(b'corrupt')
    approve(r,pid,'pricing_bases',b,expected=409)
    path.write_bytes(original);approve(r,pid,'pricing_bases',b)
    path.unlink()
    assert '日期原始依据' in '；'.join(get(e,pid)['blockers'])
    assert e.post(base+'/quotes/calculate').status_code==409
    path.write_bytes(original)
    upload(e,pid,'新版合成时间依据，招标日期另行核对')
    assert '日期原始依据' in '；'.join(get(e,pid)['blockers'])
    approve(r,pid,'pricing_bases',get(e,pid)['pricing_bases'][-1],expected=409)


def test_historical_date_does_not_select_or_rewrite_rules(clients):
    e,r,pid,comp,old=priced_project(clients);base=f'/api/v1/projects/{pid}'
    p=get(e,pid);eid=p['evidence'][0]['id'];past='2018-05-02'
    b=submit(e,pid,proposal(e,pid,eid,past));approve(r,pid,'pricing_bases',b)
    failure=e.post(base+'/quotes/calculate')
    assert failure.status_code==409 and '有效期' in failure.text
    assert get(e,pid)['rules']==p['rules']
    rules=copy.deepcopy(p['rules'][0]);rules.update(valid_from='2018-01-01',valid_until='2018-12-31',version='test-historical-v1')
    response=e.put(base+'/rules',json=rules)
    assert response.status_code==200,response.text
    approve(r,pid,'rules',response.json())
    current=e.post(base+'/quotes/calculate')
    assert current.status_code==200,current.text
    assert current.json()['snapshot']['pricing_date']==past
    assert get(e,pid)['quotes'][0]==old
