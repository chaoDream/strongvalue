from decimal import Decimal
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from . import main as core, store, gateway

router = APIRouter(prefix='/api/v1')


@router.get('/ai/capabilities')
def ai_capabilities(a=Depends(core.actor)):
    return gateway.configuration()


def analyze_job(pid, did, jid, actor):
    from .task_execution import claim,current,validate_source,validate_model,original_bytes,fail,source_evidence
    token=claim(pid,did,jid,actor)
    if token is None:return
    try:
        with store.transaction() as c:
            p = core.task_project(c, pid, actor)
            job = core.find(p, 'jobs', jid)
            if not current(job,token):return
            doc = core.find(p, 'documents', did)
            validate_source(doc,job)
            validate_model(p,doc,job)
            evidence = source_evidence(p,did)
            job['state'] = 'running'
            store.save(c, p)
        result = gateway.request_gateway(doc, original_bytes(doc), evidence)
        with store.transaction() as c:
            p = core.task_project(c, pid, actor)
            current_doc = core.find(p, 'documents', did)
            job = core.find(p, 'jobs', jid)
            if not current(job,token):return
            validate_source(current_doc,job)
            original_bytes(current_doc)
            validate_model(p,current_doc,job)
            candidates, rejected = [], []
            for candidate in result['candidates']:
                source = next((e for e in evidence if (e['page'] == candidate['page'] or e['page'] is None) and candidate['quote'] in e['text']), None)
                # Visual-only candidates require a bounding box and a real PDF page.
                if source is None and not (candidate['bbox'] and doc['name'].lower().endswith('.pdf') and candidate['page'] <= doc.get('pages', 0)):
                    rejected.append({'key': candidate['key'], 'reason': '引文无法与来源匹配且没有可定位图纸证据框'})
                    continue
                e = {'id': store.uid(), 'document_id': did, 'document_hash': doc['hash'], 'page': source['page'] if source else candidate['page'],
                     'text': candidate['quote'], 'bbox': candidate['bbox'], 'method': 'model_text' if source else 'model_visual_unverified',
                     'model': result['model'], 'analysis_id': jid}
                p['evidence'].append(e)
                record = {'id': store.uid(), 'document_id': did, 'evidence_id': e['id'], 'state': 'draft', 'analysis_id': jid, 'method': e['method'],
                          'value': candidate['value'], 'unit': candidate['unit'], 'model': result['model']}
                if candidate['kind'] == 'fact':
                    record.update(key=candidate['key'], label=candidate['label'], raw_text=candidate['quote'])
                    conflict = [x for x in p['facts'] if x['key'] == record['key'] and x['value'] != record['value'] and x['state'] not in ('rejected','superseded')]
                    if conflict:
                        record['state'] = 'conflict'
                        for x in conflict: x['state'] = 'conflict'
                    p['facts'].append(record)
                else:
                    record.update(parameter=candidate['key'], component_name=candidate['component_name'], raw_text=candidate['quote'])
                    conflict = [x for x in p['dimension_candidates'] if x['component_name'] and x['component_name'] == record['component_name'] and x['parameter'] == record['parameter'] and Decimal(x['value']) != Decimal(record['value']) and x['state'] not in ('rejected','superseded')]
                    if conflict:
                        record['state'] = 'conflict'
                        for x in conflict: x['state'] = 'conflict'
                    p['dimension_candidates'].append(record)
                candidates.append(record['id'])
            analysis = {'id': jid, 'document_id': did, 'document_hash': doc['hash'], 'model': result['model'], 'contract': 'qiangquote-extraction-v1',
                        'provider_model': result.get('provider_model', result['model']), 'provider_response_id': result.get('provider_response_id'),
                        'response_hash': store.digest(result), 'result': result, 'accepted_candidates': candidates, 'rejected_candidates': rejected,
                        'timestamp': store.now(), 'notice': '候选仅用于人工核对；视觉证据未自动验证'}
            p['analyses'].append(analysis)
            job.update(state='completed', finished_at=store.now(),message=f'形成{len(candidates)}条待复核候选，拒绝{len(rejected)}条无法定位结果')
            core.invalidate(p, 'facts')
            store.audit(c, p, actor, 'MODEL_ANALYSIS_COMPLETED', {'job_id': jid, 'response_hash': analysis['response_hash'], 'candidates': len(candidates)})
            store.save(c, p)
    except Exception as exc:
        fail(pid,did,jid,actor,token,exc,model=True)


@router.post('/projects/{pid}/documents/{did}/analyze')
def analyze(pid: str, did: str, body: dict, tasks: BackgroundTasks, a=Depends(core.actor)):
    core.edit(a)
    config = gateway.configuration()
    if not config['configured']: raise HTTPException(409, '模型网关未配置，原文提取和人工标注仍可使用')
    if not body.get('consent') or body.get('destination') != config['destination']:
        raise HTTPException(422, '需明确确认将当前文件传给已配置网关；确认的接收地址须与当前配置一致')
    with store.transaction() as c:
        p = core.project(c, pid, a)
        d = core.find(p, 'documents', did)
        if d.get('superseded'): raise HTTPException(409, '不能解析历史替代版作为当前数据')
        from .task_execution import analysis_key,PROMPT_VERSION
        key=analysis_key(p,d,config)
        old = next((j for j in p['jobs'] if j.get('job_key') == key and j['state'] != 'failed'), None)
        if old: return {'duplicate': True, 'job': old}
        job = {'id': store.uid(), 'document_id': did, 'kind': 'model_analysis', 'job_key': key, 'state': 'queued', 'message': '等待模型网关解析',
               'destination': config['destination'], 'model': config['model'], 'protocol':config['protocol'], 'prompt_version':PROMPT_VERSION,'source_hash':d['hash'],'attempts':0}
        p['jobs'].append(job)
        store.audit(c, p, a, 'MODEL_ANALYSIS_CONSENT', {'document_id': did, 'document_hash': d['hash'], 'destination': config['destination'], 'model': config['model']})
        store.save(c, p)
    tasks.add_task(analyze_job, pid, did, job['id'], a)
    return {'duplicate': False, 'job': job}
