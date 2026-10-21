import copy
from fastapi.testclient import TestClient
from backend import store, main as core
from tests.test_workflow import create, get, upload, approve, priced_project
from tests.test_resource_library import library_source, review_library, adopt_body


def users(admin):
    return {m['username']:m for m in admin.get('/api/v1/organization/members').json()}


def restrict(admin,pid,ids):
    base=f'/api/v1/projects/{pid}/access';target=admin.get(base).json()
    response=admin.put(base,json={'mode':'restricted','member_ids':ids,'object_hash':target['object_hash'],'reason':'合成项目最小访问范围测试'})
    assert response.status_code==200,response.text
    return response.json()


def manager(admin):
    response=admin.post('/api/v1/organization/members',json={'username':'test_manager','role':'manager','password':'Test-Only-Password-2026'})
    assert response.status_code==200,response.text
    c=TestClient(core.app);assert c.post('/api/v1/auth/login',json={'username':'test_manager','password':'Test-Only-Password-2026'}).status_code==200
    return c,response.json()


def test_restricted_project_hides_data_files_quotes_and_denied_audit(clients):
    e,r,pid,comp,q=priced_project(clients);admin=clients['admin'];base=f'/api/v1/projects/{pid}'
    export=e.get(base+'/exports?format=json');artifact=e.get(base+'/artifacts').json()[0]
    p=get(e,pid);restrict(admin,pid,[])
    assert pid not in [p['id'] for p in e.get('/api/v1/projects').json()]
    assert pid in [p['id'] for p in admin.get('/api/v1/projects').json()]
    urls=['','/access','/hashes','/audit','/exports?format=json','/artifacts','/delivery-check',
          '/documents/'+p['documents'][0]['id']+'/file','/artifacts/'+artifact['id']+'/file',
          '/review-target?collection=quotes&object_id='+q['id'],'/quantity-change-targets']
    for suffix in urls:
        response=e.get(base+suffix)
        assert response.status_code==404,response.text
        assert response.json()==e.get('/api/v1/projects/unknown-project').json()
    assert clients['other'].get(base).status_code==404
    assert admin.get(base).json()['quotes'][0]==q
    events=admin.get(base+'/audit').json()
    assert any(v['action']=='PROJECT_ACCESS_DENIED' and v['actor']=='estimator' for v in events)
    assert events[-1]['hash']==store.digest({k:v for k,v in events[-1].items() if k!='hash'})
    restrict(admin,pid,[users(admin)['estimator']['id']])
    assert e.get(base+'/artifacts/'+artifact['id']+'/file').content==export.content


def test_restriction_is_project_scoped_and_existing_cookie_rechecks_membership(clients):
    e=clients['estimator'];admin=clients['admin'];first=create(e)['id'];second=create(e)['id'];uid=users(admin)['estimator']['id']
    restrict(admin,first,[])
    assert e.get(f'/api/v1/projects/{first}').status_code==404
    assert e.get(f'/api/v1/projects/{second}').status_code==200
    restrict(admin,first,[uid])
    assert e.get(f'/api/v1/projects/{first}').status_code==200
    restrict(admin,first,[])
    assert e.get(f'/api/v1/projects/{first}').status_code==404
    assert e.get('/api/v1/auth/me').status_code==200


def test_roster_does_not_grant_edit_or_review_roles(clients):
    e,r,pid,comp,q=priced_project(clients);admin=clients['admin'];roster=users(admin);base=f'/api/v1/projects/{pid}'
    restrict(admin,pid,[roster['estimator']['id'],roster['reviewer']['id']])
    approve(e,pid,'quotes',q,expected=403)
    assert r.post(base+'/components',json={}).status_code==403
    for client in [e,r]:
        target=client.get(base+'/access').json()
        assert not target['can_manage'] and target['members']==[]
        assert client.put(base+'/access',json={}).status_code==403


def test_manager_can_maintain_assigned_roster_but_cannot_switch_scope_or_lock_self_out(clients):
    e=clients['estimator'];admin=clients['admin'];pid=create(e)['id'];m,user=manager(admin);base=f'/api/v1/projects/{pid}'
    restrict(admin,pid,[user['id']]);target=m.get(base+'/access').json()
    assert target['can_manage']
    data={'object_hash':target['object_hash'],'mode':'restricted','member_ids':[user['id'],users(admin)['estimator']['id']],'reason':'合成项目成员分配'}
    assert m.put(base+'/access',json=data).status_code==200
    target=m.get(base+'/access').json();data.update(object_hash=target['object_hash'],member_ids=[])
    assert m.put(base+'/access',json=data).status_code==409
    data.update(mode='tenant',member_ids=[])
    assert m.put(base+'/access',json=data).status_code==403
    restrict(admin,pid,[])
    assert m.get(base+'/access').status_code==404
    assert m.put(base+'/access',json=data).status_code==404
    m.close()


def test_stale_roster_hash_and_invalid_members_never_partially_update(clients):
    e=clients['estimator'];admin=clients['admin'];pid=create(e)['id'];base=f'/api/v1/projects/{pid}';target=admin.get(base+'/access').json()
    uid=users(admin)['estimator']['id']
    with store.connect() as c: foreign=c.execute("SELECT id FROM users WHERE username='other'").fetchone()['id']
    data={'mode':'restricted','member_ids':[uid],'object_hash':target['object_hash'],'reason':'测试范围'}
    assert admin.put(base+'/access',json=data).status_code==200
    assert admin.put(base+'/access',json=data).status_code==409
    target=admin.get(base+'/access').json();before=get(admin,pid);data['object_hash']=target['object_hash']
    for ids in [[foreign],[uid,uid],['missing-user']]:
        data['member_ids']=ids
        assert admin.put(base+'/access',json=data).status_code==422
        assert get(admin,pid)==before
    data.update(mode='tenant',member_ids=[uid])
    assert admin.put(base+'/access',json=data).status_code==422
    data.update(mode='restricted',member_ids=[],reason='')
    assert admin.put(base+'/access',json=data).status_code==422


def test_disabled_member_cannot_be_added_and_existing_membership_does_not_bypass_sessions(clients):
    e=clients['estimator'];admin=clients['admin'];pid=create(e)['id'];uid=users(admin)['estimator']['id'];restrict(admin,pid,[uid])
    assert admin.patch('/api/v1/organization/members/'+uid,json={'role':'estimator','active':False,'reason':'测试停用'}).status_code==200
    assert e.get(f'/api/v1/projects/{pid}').status_code==401
    target=admin.get(f'/api/v1/projects/{pid}/access').json()
    res=admin.put(f'/api/v1/projects/{pid}/access',json={'mode':'restricted','member_ids':[uid],'object_hash':target['object_hash'],'reason':'测试失效成员'})
    assert res.status_code==422


def test_member_changes_preserve_business_snapshot_and_reviews(clients):
    e,r,pid,comp,q=priced_project(clients);approve(r,pid,'quotes',q);admin=clients['admin'];p=get(admin,pid)
    restrict(admin,pid,[]);after=get(admin,pid)
    for key in ['quotes','reviews','boq','takeoffs','report_approval','stale_quotes']:
        assert after[key]==p[key]
    target=admin.get(f'/api/v1/projects/{pid}/access').json()
    res=admin.put(f'/api/v1/projects/{pid}/access',json={'mode':'tenant','member_ids':[],'object_hash':target['object_hash'],'reason':'恢复合成企业共享'})
    assert res.status_code==200
    assert get(e,pid)['quotes'][0]==q


def test_previously_authorized_parse_finishes_as_service_after_membership_removal(clients):
    from tests.test_task_execution import queued,ACTOR
    e,pid,d,j=queued(clients);admin=clients['admin'];restrict(admin,pid,[])
    assert e.get(f'/api/v1/projects/{pid}').status_code==404
    core.parse_job(pid,d['id'],j['id'],ACTOR)
    p=get(admin,pid)
    assert p['jobs'][0]['state']=='completed' and p['documents'][0]['parsed']
    assert len(p['evidence'])==1


def test_private_library_source_is_hidden_and_cannot_publish_or_reuse(clients):
    e,r,source,item,publish_body=library_source(clients);item=review_library(r,item);admin=clients['admin']
    e,r,pid,comp,q=priced_project(clients);base=f'/api/v1/projects/{pid}';proposal=adopt_body(e,pid,item)
    restrict(admin,source,[])
    assert e.get('/api/v1/organization/resource-library').json()['total']==0
    assert r.get('/api/v1/organization/resource-library').json()['items']==[]
    assert e.post(base+'/resource-library/'+item['entry']['id']+'/propose',json=proposal).status_code==404
    assert r.post('/api/v1/organization/resource-library/'+item['entry']['id']+'/reviews',json={'object_hash':item['object_hash'],'decision':'reject','reason':'测试未授权复核'}).status_code==404
    restrict(admin,source,[users(admin)['estimator']['id']])
    items=e.get('/api/v1/organization/resource-library').json()['items']
    assert len(items)==1 and not items[0]['available']
    assert e.post(base+'/resource-library/'+item['entry']['id']+'/propose',json=proposal).status_code==409
    assert e.post(f'/api/v1/projects/{source}/resource-library/publish',json=publish_body).status_code==409


def test_existing_library_copies_remain_but_restricted_source_blocks_new_pricing(clients):
    e,r,source,item,publish_body=library_source(clients);item=review_library(r,item)
    e,r,pid,comp,q=priced_project(clients);base=f'/api/v1/projects/{pid}'
    change=e.post(base+'/resource-library/'+item['entry']['id']+'/propose',json=adopt_body(e,pid,item)).json()
    approve(r,pid,'price_changes',change)
    assert e.post(base+'/price-changes/'+change['id']+'/apply').status_code==200
    approve(r,pid,'boq',get(e,pid)['boq'][0])
    before=get(e,pid);restrict(clients['admin'],source,[])
    p=get(e,pid)
    assert p['documents']==before['documents'] and p['quotes']==before['quotes']
    assert any('受限项目' in issue for issue in p['blockers'])
    assert e.post(base+'/quotes/calculate').status_code==409
