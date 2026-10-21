"""Single-process task fencing: duplicate/late executions never commit current data."""
import hashlib
from fastapi import HTTPException
from . import store

PROMPT_VERSION='drawing-extraction-1.0'


def source_evidence(p,did):
    return [e for e in p['evidence'] if e['document_id']==did and not e['method'].startswith('model_')]


def analysis_key(p,doc,config):
    evidence=[{k:e[k] for k in ['id','page','text']} for e in source_evidence(p,doc['id'])]
    return store.digest({'hash':doc['hash'],'model':config['model'],'contract':config['contract'],'protocol':config['protocol'],
                         'destination':config['destination'],'source_evidence':evidence,'prompt_version':PROMPT_VERSION})


def validate_model(p,doc,job):
    from . import gateway
    config=gateway.configuration()
    if not config['configured'] or job.get('job_key')!=analysis_key(p,doc,config) or job.get('protocol')!=config['protocol'] or job.get('prompt_version')!=PROMPT_VERSION:
        raise HTTPException(409,'模型配置、提示版本或来源证据已变化，请重新确认接收方后发起解析')


def claim(pid,did,jid,actor):
    from . import main as core
    with store.transaction() as c:
        p=core.project(c,pid,actor);job=core.find(p,'jobs',jid)
        if job['document_id']!=did or job['state']!='queued':return None
        token=store.uid()
        job.update(state='running',execution_id=token,attempts=job.get('attempts',0)+1,started_at=store.now(),finished_at=None,message='正在核对原件及处理输入')
        store.audit(c,p,actor,'JOB_EXECUTION_STARTED',{'job_id':jid,'execution_id':token,'attempt':job['attempts']})
        store.save(c,p)
        return token


def current(job,token):
    return job['state']=='running' and job.get('execution_id')==token


def validate_source(doc,job):
    if doc.get('superseded') or job.get('source_hash',doc['hash'])!=doc['hash']:
        raise HTTPException(409,'任务原件已被替代或版本不一致，旧结果不写入当前数据')


def original_bytes(doc):
    try:
        raw=(store.ROOT/'originals'/doc['hash']).read_bytes()
    except OSError:
        raise HTTPException(409,'任务原件缺失或不可读取，请恢复原件后重试') from None
    if hashlib.sha256(raw).hexdigest()!=doc['hash']:
        raise HTTPException(409,'任务原件内容哈希不一致，请恢复原件后重试')
    return raw


def fail(pid,did,jid,actor,token,exc,model=False):
    from . import main as core
    with store.transaction() as c:
        p=core.project(c,pid,actor);job=core.find(p,'jobs',jid)
        if not current(job,token):return
        message=str(getattr(exc,'detail',exc))[:300]
        job.update(state='failed',message=message,finished_at=store.now())
        if not model:core.find(p,'documents',did)['parse_state']='failed'
        store.audit(c,p,actor,'MODEL_ANALYSIS_FAILED' if model else 'PARSE_FAILED',{'job_id':jid,'document_id':did,'execution_id':token,'reason':message})
        store.save(c,p)
