from datetime import date
from backend import store
from tests.test_workflow import create, get, upload, priced_project, approve


def task_body(**changes):
    return {'name':'测试图纸补充核对','detail':'补充同一单体剖面及材料说明，仅合成测试',
            'specialty':'建筑','assignee':'estimator','due_date':date.today().isoformat(),'blocking':True,**changes}


def new_task(e,pid,**changes):
    response=e.post(f'/api/v1/projects/{pid}/document-requests',json=task_body(**changes))
    assert response.status_code==200,response.text
    return response.json()


def act(e,pid,task,action,expected=200,**changes):
    response=e.post(f'/api/v1/projects/{pid}/document-requests/{task["id"]}/actions',json={
        'object_hash':store.digest(task),'action':action,'reason':'仅合成软件流程验证',**changes})
    assert response.status_code==expected,response.text
    return response.json()


def test_request_submission_independent_closure_snapshot_report_and_approval(clients):
    e,r,pid,_,old=priced_project(clients);base=f'/api/v1/projects/{pid}'
    task=new_task(e,pid)
    assert any('补件任务未关闭' in b for b in get(e,pid)['blockers'])
    assert old['id'] in get(e,pid)['stale_quotes']
    approve(r,pid,'document_requests',task,expected=409)
    task=act(e,pid,task,'start');assert task['state']=='in_progress'
    eid=get(e,pid)['evidence'][0]['id']
    task=act(e,pid,task,'submit',evidence_ids=[eid],note='已核对当前原始图纸，仅软件测试')
    assert task['state']=='submitted'
    approve(e,pid,'document_requests',task,expected=403)
    approve(r,pid,'document_requests',task)
    p=get(e,pid);closed=p['document_requests'][0]
    assert closed['state']=='approved' and closed['version']==4
    assert not any('补件任务' in b for b in p['blockers'])
    assert p['coverage']['建筑']['state']=='approved'
    q=e.post(base+'/quotes/calculate').json()
    assert q['snapshot']['document_requests'][0]==closed
    approve(r,pid,'quotes',q)
    report=e.get(base+'/exports?format=json').json()
    assert any(table[0]=='补件任务台账' for table in report['tables'])
    assert '试点范围审核成果' in report['status']
    assert get(e,pid)['quotes'][0]==old


def test_closure_does_not_approve_professional_coverage(clients):
    e,r=clients['estimator'],clients['reviewer'];pid=create(e)['id'];upload(e,pid)
    task=new_task(e,pid);eid=get(e,pid)['evidence'][0]['id']
    task=act(e,pid,task,'submit',evidence_ids=[eid],note='仅补齐此项资料')
    approve(r,pid,'document_requests',task)
    p=get(e,pid)
    assert p['coverage']=={} and any('建筑资料尚未复核完整' in i for i in p['blockers'])


def test_rejected_submission_retries_and_superseded_evidence_reblocks(clients):
    e,r=clients['estimator'],clients['reviewer'];pid=create(e)['id'];upload(e,pid)
    task=new_task(e,pid);eid=get(e,pid)['evidence'][0]['id']
    task=act(e,pid,task,'submit',evidence_ids=[eid],note='第一次合成补件')
    approve(r,pid,'document_requests',task,decision='request_more_info')
    task=get(e,pid)['document_requests'][0];assert task['state']=='needs_info'
    task=act(e,pid,task,'submit',evidence_ids=[eid],note='补充核查第二次说明')
    approve(r,pid,'document_requests',task)
    assert len(get(e,pid)['document_requests'][0]['submissions'])==2
    upload(e,pid,'新的测试图纸版本',name='fixture.txt')
    assert any('已关闭补件依据失效' in i for i in get(e,pid)['blockers'])
    task=get(e,pid)['document_requests'][0]
    approve(r,pid,'document_requests',task,expected=409)
    act(e,pid,task,'reopen',expected=403)
    reopened=act(clients['admin'],pid,task,'reopen')
    assert reopened['state']=='draft' and reopened['evidence_ids']==[] and len(reopened['submissions'])==2


def test_task_roles_tenants_assignees_dates_and_hashes(clients):
    e,r,a=clients['estimator'],clients['reviewer'],clients['admin'];pid=create(e)['id'];base=f'/api/v1/projects/{pid}'
    assert r.post(base+'/document-requests',json=task_body()).status_code==403
    for changes in [{'assignee':'other'},{'assignee':'reviewer'},{'due_date':'bad'},{'blocking':'true'},{'name':''}]:
        assert e.post(base+'/document-requests',json=task_body(**changes)).status_code==422
    task=new_task(e,pid)
    assert {m['username'] for m in e.get(base+'/document-request-assignees').json()}=={'admin','estimator'}
    assert clients['other'].get(base+'/document-request-assignees').status_code==404
    assert clients['other'].post(base+'/document-requests',json=task_body()).status_code==404
    assert clients['other'].post(base+'/document-requests/'+task['id']+'/actions',json={'reason':'foreign'}).status_code==404
    otherpid=create(e)['id'];act(e,otherpid,task,'start',expected=404)
    act(r,pid,task,'start',expected=403)
    act(e,pid,task,'update',expected=403,assignee='admin',due_date=date.today().isoformat(),blocking=False)
    updated=act(a,pid,task,'update',assignee='admin',due_date=date.today().isoformat(),blocking=False)
    act(e,pid,task,'start',expected=409)
    act(e,pid,updated,'start',expected=403)


def test_cancellation_reopen_history_and_out_of_scope_tasks(clients):
    e,r=clients['estimator'],clients['reviewer'];pid=create(e)['id']
    task=new_task(e,pid,specialty='电气')
    assert not any('补件任务' in i for i in get(e,pid)['blockers'])
    act(e,pid,task,'cancel',expected=403)
    cancelled=act(clients['admin'],pid,task,'cancel')
    approve(r,pid,'document_requests',cancelled,expected=409)
    reopened=act(clients['admin'],pid,cancelled,'reopen')
    assert reopened['state']=='draft'
    events=e.get(f'/api/v1/projects/{pid}/audit').json()
    assert any(x['action']=='DOCUMENT_REQUEST_CANCEL' and x['detail']['reason'] for x in events)
    task2=new_task(e,pid,blocking=False)
    assert not any('补件任务' in i for i in get(e,pid)['blockers'])
    assert task2['blocking'] is False


def test_invalid_submission_atomicity_and_source_recheck(clients):
    e,r=clients['estimator'],clients['reviewer'];pid=create(e)['id'];base=f'/api/v1/projects/{pid}'
    upload(e,pid);upload(e,pid,'测试结构配筋资料',name='struct.txt',specialty='结构')
    p=get(e,pid);task=new_task(e,pid)
    eid=p['evidence'][0]['id'];wrong=next(v['id'] for v in p['evidence'] if v['id']!=eid)
    before=get(e,pid);logs=e.get(base+'/audit').json()
    act(e,pid,task,'submit',expected=409,evidence_ids=[wrong],note='专业不符')
    act(e,pid,task,'submit',expected=422,evidence_ids=[eid,eid],note='重复依据')
    assert get(e,pid)==before and e.get(base+'/audit').json()==logs
    submitted=act(e,pid,task,'submit',evidence_ids=[eid],note='当前有效依据')
    doc=next(d for d in p['documents'] if d['id']==p['evidence'][0]['document_id'])
    (store.ROOT/'originals'/doc['hash']).unlink()
    approve(r,pid,'document_requests',submitted,expected=409)


def test_batch_closure_rolls_back_if_one_source_is_missing(clients):
    e,r=clients['estimator'],clients['reviewer'];pid=create(e)['id'];base=f'/api/v1/projects/{pid}'
    upload(e,pid);upload(e,pid,'第二份合成建筑资料',name='second.txt')
    p=get(e,pid);tasks=[]
    for i,ev in enumerate(p['evidence']):
        t=new_task(e,pid,name='批次补件'+str(i))
        tasks.append(act(e,pid,t,'submit',evidence_ids=[ev['id']],note='批次软件验证'))
    rows=r.get(base+'/review-batch-targets?collection=document_requests').json()['targets']
    assert len(rows)==2
    doc=next(d for d in p['documents'] if d['id']==p['evidence'][1]['document_id'])
    (store.ROOT/'originals'/doc['hash']).unlink()
    before=get(e,pid);logs=e.get(base+'/audit').json()
    response=r.post(base+'/reviews/batch',json={'collection':'document_requests','targets':[{'object_id':x['object']['id'],'object_hash':x['object_hash']} for x in rows],
        'decision':'approve','reason':'批量补件复核软件验证'})
    assert response.status_code==409,response.text
    assert get(e,pid)==before and e.get(base+'/audit').json()==logs


def test_scope_change_enables_task_gate_and_cancelled_tasks_not_in_batch(clients):
    e=clients['estimator'];pid=create(e)['id'];base=f'/api/v1/projects/{pid}'
    t=new_task(e,pid,specialty='电气')
    assert not any('补件任务' in x for x in get(e,pid)['blockers'])
    response=e.patch(base+'/settings',json={'scope':['建筑','电气'],'reason':'扩展合成试点范围'})
    assert response.status_code==200,response.text
    assert any('电气补件任务未关闭' in x for x in get(e,pid)['blockers'])
    act(clients['admin'],pid,t,'cancel')
    assert clients['reviewer'].get(base+'/review-batch-targets?collection=document_requests').json()['targets']==[]
