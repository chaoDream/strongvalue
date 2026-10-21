"""Tenant-private composition sharing, with explicit permission and no price carry-over."""
import copy
import json
from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from . import main as core,store
from .quota_catalog import text,proof_ids,validate as validate_catalog
from .engine import resource_pricing_state,validate_pricing_inputs
from .project_access import config
from .task_execution import original_bytes

router=APIRouter(prefix='/api/v1')


def get_entry(c,eid,tenant):
    row=c.execute('SELECT data FROM organization_quotas WHERE id=? AND tenant=?',(eid,tenant)).fetchone()
    if not row:raise HTTPException(404,'企业定额候选不存在或无权访问')
    return json.loads(row['data'])


def composition_hash(quotas):
    result=[]
    for q in quotas:
        item={k:q.get(k) for k in ['code','edition','coefficient','source','license']}
        if q.get('conversion'):item['conversion']={k:v for k,v in q['conversion'].items() if k!='evidence_id'}
        item['resources']=[{k:r.get(k) for k in ['name','type','resource_code','specification','unit','consumption','fee_allocation']} for r in q['resources']]
        result.append(item)
    return store.digest(result)


def pending_quotas(item):
    quotas=copy.deepcopy(item['quotas'])
    for q in quotas:
        for r in q['resources']:
            for k in ['price_change_id','substitution_id','library_ref','source_type','source','license','period','valid_from','valid_until']:
                r.pop(k,None)
            r.update(price=None,pricing_status='unpriced')
            resource_pricing_state(r)
    validate_pricing_inputs([{'id':'validation','quantity':'1','quotas':quotas}])
    return quotas


def approval_ids(p,item):
    return {collection:next((r['id'] for r in reversed(p['reviews']) if r['collection']==collection and r['object_id']==oid),None)
            for collection,oid in [('boq',item['boq_id']),('catalog',item['id'])]}


def validate_entry(c,entry,tenant,target=None,verify_bytes=False):
    row=c.execute('SELECT data FROM projects WHERE id=? AND tenant=?',(entry['source_project_id'],tenant)).fetchone()
    if not row:raise HTTPException(409,'企业候选原项目不可用')
    source=core.normalize(json.loads(row['data']))
    if config(source)['mode']!='tenant':raise HTTPException(409,'原项目已限制共享，企业候选不可继续采用')
    item=core.find(source,'catalog',entry['catalog_id'])
    if item['state']!='approved' or store.digest(item)!=entry['catalog_hash']:raise HTTPException(409,'原候选已修改或撤审，请重新发布')
    if entry.get('source_approval_ids')!=approval_ids(source,item) or entry.get('source_access_hash')!=store.digest(config(source)):
        raise HTTPException(409,'原清单/候选审批或源共享版本已变化，请重新发布企业候选')
    validate_catalog(source,item,verify_bytes)
    ev=core.require_evidence(source,entry['reuse_evidence_id']);doc=core.find(source,'documents',ev['document_id'])
    if ev['document_hash']!=doc['hash'] or not (store.ROOT/'originals'/doc['hash']).is_file():raise HTTPException(409,'跨项目复用许可原件失效')
    if verify_bytes:original_bytes(doc)
    if target:
        rule=target['rules'][0] if target['rules'] else {}
        if target['id']==source['id']:raise HTTPException(409,'原项目请使用本项目定额候选流程')
        if item['region']!=target['region'] or item['discipline'] not in target['scope'] or item['standard']!=rule.get('standard') or item['edition']!=rule.get('version'):
            raise HTTPException(409,'目标地区、专业或计价标准/规则版本不一致')
        if not date.fromisoformat(item['valid_from'])<=core.effective_date(target)<=date.fromisoformat(item['valid_until']):raise HTTPException(409,'候选不在目标项目计价日期适用期间')
    pending_quotas(item)
    return source,item


@router.post('/projects/{pid}/quota-library/publish')
def publish(pid:str,body:dict,a=Depends(core.actor)):
    core.edit(a);basis=text(body,'reuse_basis');eid=text(body,'reuse_evidence_id')
    if body.get('confirm_share_originals') is not True:raise HTTPException(422,'请确认共享候选及相关全部依据原件的企业内跨项目许可')
    with store.transaction() as c:
        p=core.project(c,pid,a)
        if config(p)['mode']!='tenant':raise HTTPException(409,'受限项目不能发布企业定额候选及原件')
        item=core.find(p,'catalog',body.get('catalog_id'))
        if item['state']!='approved' or store.digest(item)!=body.get('object_hash'):raise HTTPException(409,'本项目候选未审定或已变化')
        validate_catalog(p,item,True);core.require_evidence(p,eid)
        for proof_id in set(proof_ids(item))|{eid}:
            ev=core.require_evidence(p,proof_id);doc=core.find(p,'documents',ev['document_id'])
            if ev.get('quota_library_origin') or doc.get('quota_library_origin'):raise HTTPException(409,'复用的企业候选或原件不能循环发布，请在原项目维护')
        entry={'id':store.uid(),'source_project_id':pid,'source_project_name':p['name'],'catalog_id':item['id'],'catalog_hash':store.digest(item),
               'candidate':copy.deepcopy(item),'source_approval_ids':approval_ids(p,item),'source_access_hash':store.digest(config(p)),'reuse_basis':basis,'reuse_evidence_id':eid,'author':a['username'],'created_at':store.now(),'state':'draft','reviews':[]}
        validate_entry(c,entry,a['tenant'],verify_bytes=True)
        entry['proofs']=[]
        for proof_id in sorted(set(proof_ids(item))|{eid}):
            ev=core.require_evidence(p,proof_id);doc=core.find(p,'documents',ev['document_id'])
            entry['proofs'].append({'evidence_id':proof_id,'document_name':doc['name'],'document_hash':doc['hash'],'page':ev['page'],
                                    'text_excerpt':ev['text'][:4000],'excerpt_truncated':len(ev['text'])>4000,'document_url':f"/api/v1/projects/{pid}/documents/{doc['id']}/file"})
        c.execute('INSERT INTO organization_quotas VALUES(?,?,?)',(entry['id'],a['tenant'],store.canonical(entry)))
        store.audit(c,{'id':'tenant:'+a['tenant'],'tenant':a['tenant']},a,'QUOTA_LIBRARY_PUBLISHED',entry)
        return {'entry':entry,'object_hash':store.digest(entry)}


@router.get('/organization/quota-library')
def listing(q:str='',offset:int=0,a=Depends(core.actor)):
    if len(q)>200 or offset<0:raise HTTPException(422,'检索或分页参数无效')
    with store.connect() as c:
        rows=[]
        for row in c.execute('SELECT data FROM organization_quotas WHERE tenant=? ORDER BY rowid DESC',(a['tenant'],)):
            entry=json.loads(row['data']);source=c.execute('SELECT data FROM projects WHERE id=? AND tenant=?',(entry['source_project_id'],a['tenant'])).fetchone()
            if not source or config(json.loads(source['data']))['mode']!='tenant':continue
            item=entry['candidate']
            if q.casefold() not in (' '.join([item['name'],item['code'],item['features'],item['quota_edition'],entry['source_project_name']])).casefold():continue
            rows.append(entry)
        result=[]
        for entry in rows[offset:offset+100]:
            try:validate_entry(c,entry,a['tenant']);issue='' if entry['state']=='approved' else '企业共享许可尚未审定'
            except HTTPException as exc:issue=str(exc.detail)
            result.append({'entry':entry,'object_hash':store.digest(entry),'available':not issue,'issue':issue})
        return {'items':result,'total':len(rows),'offset':offset}


@router.post('/organization/quota-library/{eid}/reviews')
def review(eid:str,body:dict,a=Depends(core.actor)):
    if a['role']!='reviewer':raise HTTPException(403,'仅独立复核账号可以审定企业候选共享许可')
    reason=text(body,'reason')
    if body.get('decision') not in ('approve','reject','request_more_info'):raise HTTPException(422,'审核决定无效')
    with store.transaction() as c:
        entry=get_entry(c,eid,a['tenant']);core.project(c,entry['source_project_id'],a)
        if store.digest(entry)!=body.get('object_hash'):raise HTTPException(409,'企业候选已变化，请重新核对')
        if body['decision']=='approve':validate_entry(c,entry,a['tenant'],verify_bytes=True)
        record={'id':store.uid(),'reviewer':a['username'],'timestamp':store.now(),'decision':body['decision'],'reason':reason,'object_hash':body['object_hash']}
        entry['reviews'].append(record);entry['state']={'approve':'approved','reject':'rejected','request_more_info':'needs_info'}[body['decision']]
        c.execute('UPDATE organization_quotas SET data=? WHERE id=? AND tenant=?',(store.canonical(entry),eid,a['tenant']))
        store.audit(c,{'id':'tenant:'+a['tenant'],'tenant':a['tenant']},a,'QUOTA_LIBRARY_REVIEWED',{'entry_id':eid,**record})
        return {'entry':entry,'object_hash':store.digest(entry)}


@router.get('/projects/{pid}/boq/{bid}/enterprise-quota-candidates')
def candidates(pid:str,bid:str,discipline:str,quota_edition:str,a=Depends(core.actor)):
    if len(discipline)>100 or len(quota_edition)>200 or not discipline or not quota_edition:raise HTTPException(422,'请明确专业和定额年度')
    with store.connect() as c:
        p=core.project(c,pid,a);b=core.find(p,'boq',bid);result=[]
        for row in c.execute('SELECT data FROM organization_quotas WHERE tenant=?',(a['tenant'],)):
            entry=json.loads(row['data']);item=entry['candidate']
            if entry['state']!='approved' or item['discipline']!=discipline or item['quota_edition']!=quota_edition or item['unit']!=b['unit']:continue
            try:validate_entry(c,entry,a['tenant'],p)
            except HTTPException:continue
            same_code=bool(b.get('code')) and b['code']==item['code'];same_name=b['name']==item['name'];overlap=len(set(b['name']+b['features'])&set(item['name']+item['features']))
            score=100*same_code+30*same_name+overlap
            if score:result.append({'entry':entry,'object_hash':store.digest(entry),'boq_hash':store.digest(b),'score':score,
                                    'match_reason':f'编码{"相同" if same_code else "不同"}；名称{"相同" if same_name else "不同"}；文字交集{overlap}字。仅文字检索，须专业核对；采用后全部资源待询价。'})
        return sorted(result,key=lambda x:(-x['score'],x['entry']['id']))[:5]


def prepare(c,p,bid,eid,body,tenant):
    b=core.find(p,'boq',bid);entry=get_entry(c,eid,tenant)
    if store.digest(b)!=body.get('boq_hash') or store.digest(entry)!=body.get('object_hash') or entry['state']!='approved':raise HTTPException(409,'目标清单、企业候选或共享审批已变化')
    source,item=validate_entry(c,entry,tenant,p,True)
    if b['unit']!=item['unit'] or body.get('discipline')!=item['discipline'] or body.get('quota_edition')!=item['quota_edition']:raise HTTPException(409,'目标单位、专业或定额年度不一致')
    context=store.digest({'boq':b,'entry':entry,'rules':p['rules'],'region':p['region'],'scope':p['scope'],'date':core.effective_date(p).isoformat(),
                          'source_access':config(source),'source_candidate':item,'source_boq':core.find(source,'boq',item['boq_id'])})
    return b,entry,source,item,context


@router.post('/projects/{pid}/boq/{bid}/enterprise-quota-candidates/{eid}/preview')
def preview(pid:str,bid:str,eid:str,body:dict,a=Depends(core.actor)):
    with store.connect() as c:
        p=core.project(c,pid,a);b,entry,source,item,context=prepare(c,p,bid,eid,body,a['tenant'])
        return {'preview_hash':context,'quotas':pending_quotas(item),'before_quotas':b['quotas'],'quantity':b['quantity'],'unit':b['unit'],
                'notice':'仅复用定额与原始消耗组成，目标全部资源转为待询价。不计算无价合价或金额差额；补齐本项目价格并独立复核后重新计价。'}


def copy_proofs(p,source,entry,item):
    mapping={};docs={};verified=set()
    for eid in sorted(set(proof_ids(item))|{entry['reuse_evidence_id']}):
        ev=core.require_evidence(source,eid);doc=core.find(source,'documents',ev['document_id'])
        if doc['hash'] not in verified:original_bytes(doc);verified.add(doc['hash'])
        if doc['id'] not in docs:
            copied=copy.deepcopy(doc);copied.update(id=store.uid(),name='企业定额依据-'+entry['id'][:8]+'-'+doc['name'],version=1,uploaded_at=store.now(),
                parse_state='needs_review',quota_library_origin={'entry_id':entry['id'],'project_id':source['id'],'document_id':doc['id']})
            copied.pop('library_origin',None)
            p['documents'].append(copied);docs[doc['id']]=copied['id']
        copied=copy.deepcopy(ev);copied.update(id=store.uid(),document_id=docs[doc['id']],method='enterprise_quota_evidence',
            quota_library_origin={'entry_id':entry['id'],'project_id':source['id'],'evidence_id':eid})
        copied.pop('library_origin',None)
        p['evidence'].append(copied);mapping[eid]=copied['id']
    return mapping


@router.post('/projects/{pid}/boq/{bid}/enterprise-quota-candidates/{eid}/adopt')
def adopt(pid:str,bid:str,eid:str,body:dict,a=Depends(core.actor)):
    core.edit(a);reason=text(body,'reason');key=text(body,'request_id',100)
    if body.get('confirm_conditions') is not True:raise HTTPException(422,'请确认跨项目许可、构造工法和全部资源重新询价')
    with store.transaction() as c:
        p=core.project(c,pid,a);fingerprint=store.digest({'bid':bid,'eid':eid,'body':body})
        previous=next((x for x in p['enterprise_quota_adoptions'] if x['request_id']==key),None)
        if previous:
            if previous['request_hash']!=fingerprint:raise HTTPException(409,'请求标识已用于其他采用内容')
            return previous
        b,entry,source,item,context=prepare(c,p,bid,eid,body,a['tenant'])
        if body.get('preview_hash')!=context:raise HTTPException(409,'采用前核对已过期，请重新预览')
        mapping=copy_proofs(p,source,entry,item);quotas=pending_quotas(item)
        for q in quotas:
            q['evidence_id']=mapping[q['evidence_id']]
            if q.get('conversion'):q['conversion']['evidence_id']=mapping[q['conversion']['evidence_id']]
            for r in q['resources']:r['evidence_id']=mapping[r['evidence_id']]
        record={'id':store.uid(),'request_id':key,'request_hash':fingerprint,'boq_id':bid,'boq_name':b['name'],'entry':copy.deepcopy(entry),
                'entry_hash':store.digest(entry),'proof_map':mapping,'composition_hash':composition_hash(quotas),'before_quotas':copy.deepcopy(b['quotas']),
                'reason':reason,'author':a['username'],'created_at':store.now(),'preview_hash':context}
        b.pop('catalog_ref',None);b.update(quotas=quotas,enterprise_quota_ref={'id':eid,'hash':store.digest(entry),'adoption_id':record['id']},state='draft')
        p['enterprise_quota_adoptions'].append(record);core.invalidate(p,'quotes');p['status']='BOQ_DRAFT'
        store.audit(c,p,a,'ENTERPRISE_QUOTA_ADOPTED',record);store.save(c,p)
        return record


def reference_errors(p,items=None,verify_bytes=False):
    errors=[];verified=set()
    targets=[b for b in (p['boq'] if items is None else items) if b.get('enterprise_quota_ref')]
    if not targets:return []
    with store.connect() as c:
        for b in targets:
            ref=b.get('enterprise_quota_ref')
            if not ref:continue
            try:
                entry=get_entry(c,ref['id'],p['tenant'])
                if entry['state']!='approved' or store.digest(entry)!=ref['hash']:raise HTTPException(409,'企业共享许可已撤审或版本变化')
                validate_entry(c,entry,p['tenant'],p,verify_bytes)
                record=next((x for x in p['enterprise_quota_adoptions'] if x['id']==ref['adoption_id']),None)
                if not record or record['entry_hash']!=ref['hash'] or record['composition_hash']!=composition_hash(b['quotas']):raise HTTPException(409,'采用组成已变化，请重新采用或有据转为人工编制')
                for source_id,target_id in record['proof_map'].items():
                    ev=core.require_evidence(p,target_id);doc=core.find(p,'documents',ev['document_id'])
                    if ev.get('quota_library_origin',{}).get('evidence_id')!=source_id or ev['quota_library_origin'].get('entry_id')!=entry['id'] or ev['document_hash']!=doc['hash'] or not (store.ROOT/'originals'/doc['hash']).is_file():raise HTTPException(409,'复制的定额及共享许可依据失效')
                    if verify_bytes and doc['hash'] not in verified:original_bytes(doc);verified.add(doc['hash'])
            except HTTPException as exc:errors.append(b['name']+'企业定额候选不可用：'+str(exc.detail))
    return list(dict.fromkeys(errors))
