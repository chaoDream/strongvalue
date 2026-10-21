import threading
from backend import store,main as core,gateway
from backend.recovery import recover_interrupted_jobs
from backend.task_execution import fail,analysis_key,PROMPT_VERSION
from tests.test_workflow import create,upload,get

ACTOR={'tenant':'local','username':'estimator','role':'estimator'}


def queued(clients):
    e=clients['estimator'];pid=create(e)['id'];upload(e,pid,'旧版：工期45日历天。')
    with store.transaction() as c:
        p=core.project(c,pid,ACTOR);d=p['documents'][0];j=p['jobs'][0]
        d['parsed']=False;j['state']='queued';p['evidence']=[];p['facts']=[];p['dimension_candidates']=[];store.save(c,p)
    return e,pid,d,j


def test_duplicate_running_execution_is_claimed_once(clients,monkeypatch):
    e,pid,d,j=queued(clients);entered=threading.Event();release=threading.Event();calls=[];original=core.extract
    def slow(ext,raw):
        calls.append(raw);entered.set();assert release.wait(5);return original(ext,raw)
    monkeypatch.setattr(core,'extract',slow)
    worker=threading.Thread(target=core.parse_job,args=(pid,d['id'],j['id'],ACTOR));worker.start()
    try:
        assert entered.wait(5)
        core.parse_job(pid,d['id'],j['id'],ACTOR)
        assert e.post(f'/api/v1/projects/{pid}/jobs/{j["id"]}/retry').status_code==409
    finally:release.set();worker.join(5)
    assert not worker.is_alive() and len(calls)==1
    p=get(e,pid);job=p['jobs'][0]
    assert job['state']=='completed' and job['attempts']==2 and job['started_at'] and job['finished_at']
    assert len(p['evidence'])==1


def test_recovery_retry_fences_old_completion_and_late_failure(clients,monkeypatch):
    e,pid,d,j=queued(clients);entered=threading.Event();release=threading.Event();calls=[];original=core.extract
    def slow_first(ext,raw):
        calls.append(raw)
        if len(calls)==1:entered.set();assert release.wait(5)
        return original(ext,raw)
    monkeypatch.setattr(core,'extract',slow_first)
    worker=threading.Thread(target=core.parse_job,args=(pid,d['id'],j['id'],ACTOR));worker.start()
    try:
        assert entered.wait(5);old_token=get(e,pid)['jobs'][0]['execution_id']
        recover_interrupted_jobs()
        assert e.post(f'/api/v1/projects/{pid}/jobs/{j["id"]}/retry').status_code==200
        before=get(e,pid);assert before['jobs'][0]['state']=='completed'
        assert before['jobs'][0]['execution_id']!=old_token
    finally:release.set();worker.join(5)
    assert get(e,pid)==before and len(before['evidence'])==1
    logs=e.get(f'/api/v1/projects/{pid}/audit').json()
    fail(pid,d['id'],j['id'],ACTOR,old_token,RuntimeError('late failure'))
    assert get(e,pid)==before and e.get(f'/api/v1/projects/{pid}/audit').json()==logs


def test_document_replaced_during_parse_cannot_publish_old_candidates(clients,monkeypatch):
    e,pid,d,j=queued(clients);original=core.extract
    def replace(ext,raw):
        if '旧版'.encode() in raw:upload(e,pid,'新版：工期60日历天。',name='fixture.txt')
        return original(ext,raw)
    monkeypatch.setattr(core,'extract',replace)
    core.parse_job(pid,d['id'],j['id'],ACTOR)
    p=get(e,pid);assert p['jobs'][0]['state']=='failed'
    assert len(p['evidence'])==1 and '新版' in p['evidence'][0]['text']
    assert not any(f['value']=='45' for f in p['facts'])
    assert e.post(f'/api/v1/projects/{pid}/jobs/{j["id"]}/retry').status_code==409


def test_original_corruption_stops_before_parser_and_can_retry_after_restore(clients,monkeypatch):
    e,pid,d,j=queued(clients);path=store.ROOT/'originals'/d['hash'];old=path.read_bytes();path.write_bytes(b'corrupted')
    calls=[];original=core.extract
    monkeypatch.setattr(core,'extract',lambda ext,raw:(calls.append(raw),original(ext,raw))[1])
    core.parse_job(pid,d['id'],j['id'],ACTOR)
    assert calls==[] and get(e,pid)['jobs'][0]['state']=='failed'
    path.write_bytes(old)
    assert e.post(f'/api/v1/projects/{pid}/jobs/{j["id"]}/retry').status_code==200
    assert len(calls)==1 and len(get(e,pid)['evidence'])==1


def model_job(clients,monkeypatch):
    e=clients['estimator'];pid=create(e)['id'];upload(e,pid,'规模500m³/日')
    monkeypatch.setenv('QIANG_AI_GATEWAY_URL','http://localhost:9999/extract');monkeypatch.setenv('QIANG_AI_MODEL','mock-only')
    with store.transaction() as c:
        p=core.project(c,pid,ACTOR);d=p['documents'][0];config=gateway.configuration()
        j={'id':store.uid(),'document_id':d['id'],'kind':'model_analysis','state':'failed','job_key':analysis_key(p,d,config),
           'destination':config['destination'],'model':config['model'],'protocol':config['protocol'],'prompt_version':PROMPT_VERSION,'source_hash':d['hash']}
        p['jobs'].append(j);store.save(c,p)
    return e,pid,d,j


def test_model_retry_requires_same_protocol_prompt_and_source_evidence(clients,monkeypatch):
    e,pid,d,j=model_job(clients,monkeypatch);url=f'/api/v1/projects/{pid}/jobs/{j["id"]}/retry';calls=[]
    monkeypatch.setattr(gateway,'request_gateway',lambda *args:calls.append(args))
    monkeypatch.setenv('QIANG_AI_PROTOCOL','openai_responses')
    assert e.post(url).status_code==409 and calls==[]
    monkeypatch.delenv('QIANG_AI_PROTOCOL')
    assert e.post(f'/api/v1/projects/{pid}/evidence',json={'document_id':d['id'],'page':1,'text':'新的来源证据'}).status_code==200
    assert e.post(url).status_code==409 and calls==[]
    assert clients['other'].post(url).status_code==404
    assert clients['reviewer'].post(url).status_code==403


def test_model_result_ignored_after_execution_recovers(clients,monkeypatch):
    from backend.analysis_routes import analyze_job
    e,pid,d,j=model_job(clients,monkeypatch)
    with store.transaction() as c:
        p=core.project(c,pid,ACTOR);p['jobs'][-1]['state']='queued';store.save(c,p)
    def interrupted(*args):
        recover_interrupted_jobs()
        return {'model':'mock-only','warnings':[],'candidates':[]}
    monkeypatch.setattr(gateway,'request_gateway',interrupted)
    analyze_job(pid,d['id'],j['id'],ACTOR)
    p=get(e,pid);assert p['jobs'][-1]['state']=='failed' and p['analyses']==[]
