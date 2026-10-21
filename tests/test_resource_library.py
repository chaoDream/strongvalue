import copy
import json
from datetime import date,timedelta
from tests.test_workflow import priced_project,get,approve


def library_source(clients):
    e,r,pid,comp,q=priced_project(clients);base=f'/api/v1/projects/{pid}'
    b=get(e,pid)['boq'][0];quotas=copy.deepcopy(b['quotas']);quotas[0]['resources'][0]['price']='135.678'
    response=e.put(base+'/boq/'+b['id'],json={'quotas':quotas});assert response.status_code==200,response.text
    approve(r,pid,'boq',response.json())
    rows=e.get(base+'/resource-prices').json();row=rows[0]
    body={**{k:row[k] for k in ['key','boq_hash','resource_hash']},'reuse_basis':'合成测试授权：允许本企业内其他测试项目复用，非真实商业许可',
          'reuse_evidence_id':get(e,pid)['evidence'][0]['id'],'source_type':'供应商报价','valid_from':(date.today()-timedelta(days=30)).isoformat()}
    response=e.post(base+'/resource-library/publish',json=body);assert response.status_code==200,response.text
    return e,r,pid,response.json(),body


def review_library(r,item,decision='approve'):
    response=r.post('/api/v1/organization/resource-library/'+item['entry']['id']+'/reviews',json={'object_hash':item['object_hash'],'decision':decision,'reason':'软件测试核查原始价格和企业跨项目复用范围'})
    assert response.status_code==200,response.text
    return response.json()


def adopt_body(e,pid,item):
    rows=e.get(f'/api/v1/projects/{pid}/resource-prices').json()
    return {'object_hash':item['object_hash'],'confirm_conditions':True,'reason':'本项目合成测试资源条件、价格期次和企业授权已核查',
            'entries':[{k:row[k] for k in ['key','boq_hash','resource_hash']} for row in rows]}


def test_library_publish_review_reuse_and_quote_provenance(clients):
    e,r,source_id,item,publish_body=library_source(clients)
    assert not e.get('/api/v1/organization/resource-library').json()['items'][0]['available']
    item=review_library(r,item)
    assert e.get('/api/v1/organization/resource-library').json()['items'][0]['available']
    e,r,pid,comp,old_quote=priced_project(clients);base=f'/api/v1/projects/{pid}'
    before=get(e,pid);body=adopt_body(e,pid,item)
    response=e.post(base+'/resource-library/'+item['entry']['id']+'/propose',json=body)
    assert response.status_code==200,response.text
    change=response.json()
    assert change['entries'][0]['after']['price']=='135.678'
    assert get(e,pid)['boq']==before['boq']
    assert len(get(e,pid)['documents'])>len(before['documents'])
    approve(r,pid,'price_changes',change)
    assert e.post(base+'/price-changes/'+change['id']+'/apply').status_code==200
    b=get(e,pid)['boq'][0];resource=b['quotas'][0]['resources'][0]
    assert resource['consumption']=='1.05' and b['quotas'][0]['coefficient']=='1'
    assert resource['library_ref']['id']==item['entry']['id']
    approve(r,pid,'boq',b)
    response=e.post(base+'/quotes/calculate');assert response.status_code==200,response.text
    quote=response.json();assert quote['snapshot']['library_snapshots'][0]['entry']==item['entry']
    report=e.get(base+'/exports?format=json').json()
    assert any(section[0]=='企业价格复用依据' for section in report['tables'])
    approve(r,pid,'quotes',quote)
    exported=e.get(base+'/exports?format=json');assert '试点范围审核成果' in exported.json()['status']
    artifact=e.get(base+'/artifacts').json()[0]
    old=json.dumps(get(e,pid)['quotes'],sort_keys=True)
    review_library(r,item,'reject')
    assert any('企业价格不可用' in x for x in get(e,pid)['blockers'])
    assert e.post(base+'/quotes/calculate').status_code==409
    assert json.dumps(get(e,pid)['quotes'],sort_keys=True)==old
    assert '未完成成果草稿' in e.get(base+'/exports?format=json').json()['status']
    assert e.get(base+'/artifacts/'+artifact['id']+'/file').content==exported.content


def test_library_source_withdrawal_between_proposal_and_project_review(clients):
    e,r,source_id,item,body=library_source(clients);item=review_library(r,item);eid=item['entry']['id']
    e,r,pid,comp,q=priced_project(clients);base=f'/api/v1/projects/{pid}'
    proposal=e.post(base+'/resource-library/'+eid+'/propose',json=adopt_body(e,pid,item))
    assert proposal.status_code==200,proposal.text
    review_library(r,item,'reject')
    approve(r,pid,'price_changes',proposal.json(),expected=409)
    assert e.post(base+'/price-changes/'+proposal.json()['id']+'/apply').status_code==409


def test_library_role_tenant_hash_and_confirm_boundaries(clients):
    e,r,source_id,item,publish_body=library_source(clients);eid=item['entry']['id']
    assert r.post(f'/api/v1/projects/{source_id}/resource-library/publish',json=publish_body).status_code==403
    assert clients['other'].get('/api/v1/organization/resource-library').json()['total']==0
    assert clients['other'].post('/api/v1/organization/resource-library/'+eid+'/reviews',json={'decision':'approve','object_hash':item['object_hash'],'reason':'cross tenant'}).status_code==403
    assert e.post('/api/v1/organization/resource-library/'+eid+'/reviews',json={'decision':'approve','object_hash':item['object_hash'],'reason':'not reviewer'}).status_code==403
    item=review_library(r,item)
    assert r.post('/api/v1/organization/resource-library/'+eid+'/reviews',json={'decision':'approve','object_hash':'stale','reason':'old snapshot'}).status_code==409
    e,r,pid,comp,q=priced_project(clients);base=f'/api/v1/projects/{pid}';body=adopt_body(e,pid,item)
    assert r.post(base+'/resource-library/'+eid+'/propose',json=body).status_code==403
    assert e.post(base+'/resource-library/'+eid+'/propose',json={**body,'confirm_conditions':False}).status_code==422
    assert e.post(base+'/resource-library/'+eid+'/propose',json={**body,'object_hash':'stale'}).status_code==409
    foreign=clients['other'];foreign_pid=foreign.post('/api/v1/projects',json={'name':'另企业测试','scope':['建筑']}).json()['id']
    assert foreign.post(f'/api/v1/projects/{foreign_pid}/resource-library/{eid}/propose',json=body).status_code==404


def test_library_changed_source_stale_target_and_incompatible_specifications(clients):
    e,r,source_id,item,body=library_source(clients);item=review_library(r,item);eid=item['entry']['id']
    e,r,pid,comp,q=priced_project(clients);base=f'/api/v1/projects/{pid}'
    body=adopt_body(e,pid,item);body['entries'][0]['boq_hash']='stale'
    before=get(e,pid)
    assert e.post(base+'/resource-library/'+eid+'/propose',json=body).status_code==409
    assert get(e,pid)['documents']==before['documents'] and get(e,pid)['price_changes']==before['price_changes']
    b=get(e,pid)['boq'][0];quotas=copy.deepcopy(b['quotas']);quotas[0]['resources'][0]['specification']='不同规格'
    e.put(base+'/boq/'+b['id'],json={'quotas':quotas})
    assert e.post(base+'/resource-library/'+eid+'/propose',json=adopt_body(e,pid,item)).status_code==409
    b=get(e,source_id)['boq'][0]
    e.put(f'/api/v1/projects/{source_id}/boq/'+b['id'],json={'features':'原项目版本已改变'})
    listing=e.get('/api/v1/organization/resource-library').json()['items'][0]
    assert not listing['available'] and '原清单或资源版本' in listing['issue']


def test_library_current_date_and_export_after_source_withdrawal(clients):
    e,r,source_id,item,body=library_source(clients);item=review_library(r,item);eid=item['entry']['id']
    e,r,pid,comp,q=priced_project(clients);base=f'/api/v1/projects/{pid}'
    assert e.patch(base+'/settings',json={'pricing_date':'2040-01-01','reason':'超出库价格适用期测试'}).status_code==200
    assert e.post(base+'/resource-library/'+eid+'/propose',json=adopt_body(e,pid,item)).status_code==409
