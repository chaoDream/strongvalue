import copy
from datetime import date,timedelta
from tests.test_workflow import setup_boq,priced_project,get,upload,approve


def mapping_project(clients,priced=False):
    if priced:
        e,r,pid,comp,q=priced_project(clients)
    else:
        e,r,pid,comp,t,b,eid=setup_boq(clients)
    base=f'/api/v1/projects/{pid}'
    upload(e,pid,'测试编码依据（非官方）：TEST-CODE 对应测试底板；TEST-SECOND 对应同类测试项目。仅用于软件回归。',name='test-codes.txt',specialty='合同计价')
    p=get(e,pid)
    for c in p['components']:approve(r,pid,'components',c)
    for t in get(e,pid)['takeoffs']:approve(r,pid,'takeoffs',t)
    assert e.patch(base+'/settings',json={'boq_standard':'测试清单标准','boq_edition':'测试2026','reason':'测试适用标准，非官方编码库'}).status_code==200
    eid=get(e,pid)['evidence'][-1]['id']
    body={'name':'测试底板规则','code':'TEST-CODE','item_name':'测试映射底板','features':'测试编码与对应特征，不具有专业效力',
          'standard':'测试清单标准','edition':'测试2026','license':'仅用于测试','evidence_id':eid,'kind':'slab','unit':'m³',
          'keywords':['测试','底板'],'discipline':'建筑','region':'河南省','valid_from':(date.today()-timedelta(days=30)).isoformat(),
          'valid_until':(date.today()+timedelta(days=365)).isoformat()}
    return e,r,pid,body


def selections(e,pid):
    result=e.get(f'/api/v1/projects/{pid}/boq/mapping-preview').json()
    return [{'boq_id':row['boq']['id'],'boq_hash':row['boq_hash'],'rule_id':row['candidates'][0]['rule']['id'],'rule_hash':row['candidates'][0]['rule_hash']} for row in result['rows'] if row['candidates']]


def test_mapping_source_review_preview_apply_and_report_provenance(clients):
    e,r,pid,body=mapping_project(clients,True);base=f'/api/v1/projects/{pid}'
    assert r.post(base+'/boq-mappings',json=body).status_code==403
    response=e.post(base+'/boq-mappings',json=body);assert response.status_code==200,response.text
    rule=response.json()
    assert e.get(base+'/boq/mapping-preview').json()['rows'][0]['state']=='unmatched'
    approve(r,pid,'boq_mappings',rule)
    rows=e.get(base+'/boq/mapping-preview').json()['rows'];assert rows[0]['state']=='unique'
    before=get(e,pid)['boq'][0]['quantity']
    response=e.post(base+'/boq/apply-mappings',json={'selections':selections(e,pid)})
    assert response.status_code==200,response.text
    b=get(e,pid)['boq'][0]
    assert b['code']=='TEST-CODE' and b['quantity']==before and b['state']=='draft'
    assert b['code_evidence_id']==body['evidence_id']
    assert e.put(base+'/boq/'+b['id'],json={'code':'invented'}).status_code==409
    approve(r,pid,'boq',b)
    q=e.post(base+'/quotes/calculate');assert q.status_code==200,q.text
    assert q.json()['snapshot']['boq_mappings'][0]['id']==rule['id']
    report=e.get(base+'/exports?format=json').json()
    section=next(t for t in report['tables'] if t[0]=='清单编码映射依据')
    assert section[2][0][0]=='TEST-CODE'
    approve(r,pid,'boq_mappings',rule,'reject')
    assert get(e,pid)['boq'][0]['state']=='stale'
    approve(r,pid,'boq',b,expected=409)


def test_mapping_ambiguity_versions_and_atomic_application(clients):
    e,r,pid,body=mapping_project(clients);base=f'/api/v1/projects/{pid}'
    first=e.post(base+'/boq-mappings',json=body).json();approve(r,pid,'boq_mappings',first)
    second=e.post(base+'/boq-mappings',json={**body,'code':'TEST-SECOND','name':'另一个适用条件'}).json();approve(r,pid,'boq_mappings',second)
    assert e.get(base+'/boq/mapping-preview').json()['rows'][0]['state']=='ambiguous'
    b=get(e,pid)['boq'][0]
    assert e.post(base+'/boq/'+b['id']+'/split',json={'parts':[{'name':'拆分一','quantity':'5'},{'name':'拆分二','quantity':'10'}]}).status_code==200
    chosen=selections(e,pid);chosen[-1]['boq_hash']='stale'
    before=copy.deepcopy(get(e,pid)['boq'])
    assert e.post(base+'/boq/apply-mappings',json={'selections':chosen}).status_code==409
    assert get(e,pid)['boq']==before
    assert e.post(base+'/boq/apply-mappings',json={'selections':selections(e,pid)}).status_code==200
    revised=e.post(base+'/boq-mappings',json={**body,'previous_id':first['id'],'features':'测试新版特征'}).json()
    approve(r,pid,'boq_mappings',revised)
    p=get(e,pid)
    assert next(x for x in p['boq_mappings'] if x['id']==first['id'])['state']=='superseded'
    assert all(b['state']=='stale' for b in p['boq'])
    approve(r,pid,'boq_mappings',first,expected=409)


def test_mapping_invalid_proof_scope_units_and_standard_change(clients):
    e,r,pid,body=mapping_project(clients);base=f'/api/v1/projects/{pid}'
    for delta in [{'code':'TEST'}, {'unit':'m²'}, {'discipline':'结构'}, {'edition':'未选择版本'}, {'keywords':[]}]:
        assert e.post(base+'/boq-mappings',json={**body,**delta}).status_code in (409,422)
    assert clients['other'].post(base+'/boq-mappings',json=body).status_code==404
    assert clients['other'].get(base+'/boq/mapping-preview').status_code==404
    rule=e.post(base+'/boq-mappings',json=body).json();approve(r,pid,'boq_mappings',rule)
    assert e.post(base+'/boq/apply-mappings',json={'selections':selections(e,pid)}).status_code==200
    assert e.patch(base+'/settings',json={'boq_standard':body['standard'],'boq_edition':'新版标准','reason':'适用版次变更测试'}).status_code==200
    assert any('映射需重新核对' in issue for issue in get(e,pid)['blockers'])
    assert e.get(base+'/boq/mapping-preview').json()['rows'][0]['state']=='unmatched'
