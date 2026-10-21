"""Tenant-private, source-checked price reuse through the normal review workflow."""
import copy
import hashlib
import json
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from . import main as core, store
from .workflows import resources, validate_price_metadata, build_price_change

router = APIRouter(prefix='/api/v1')
PRICE_FIELDS = ['name','type','unit','price','source','license','period','region','tax_mode','valid_from','valid_until','source_type','specification']


def get_entry(c, eid, tenant):
    row = c.execute('SELECT data FROM organization_resources WHERE id=? AND tenant=?', (eid,tenant)).fetchone()
    if not row:
        raise HTTPException(404, '企业价格条目不存在或无权访问')
    return json.loads(row['data'])


def source_project(c, entry, tenant):
    row = c.execute('SELECT data FROM projects WHERE id=? AND tenant=?', (entry['source_project_id'],tenant)).fetchone()
    if not row:
        raise HTTPException(409, '价格原项目不可用')
    p = core.normalize(json.loads(row['data']))
    from .project_access import config
    if config(p)['mode']=='restricted':
        raise HTTPException(409, '受限项目不能作为企业共享价格及原件的来源')
    source = next((r for r in resources(p) if r['key']==entry['source_key']), None)
    b = next((b for b in p['boq'] if b['id']==entry['source_boq_id']), None)
    if not source or not b or b['state']!='approved' or source['boq_hash']!=entry['source_boq_hash'] or source['resource_hash']!=entry['source_resource_hash']:
        raise HTTPException(409, '原清单或资源版本已变化/撤审，请重新发布企业价格')
    r = source['resource']
    if r.get('price_change_id'):
        change = next((x for x in p['price_changes'] if x['id']==r['price_change_id']),None)
        if not change or change['state']!='approved' or change['id'] not in p['applied_price_changes']:
            raise HTTPException(409, '原价格调价审批已失效')
    for eid in [entry['source_evidence_id'],entry['reuse_evidence_id']]:
        evidence=core.require_evidence(p,eid)
        document=core.find(p,'documents',evidence['document_id'])
        if not (store.ROOT/'originals'/document['hash']).is_file():
            raise HTTPException(409, '价格或复用授权原件已缺失')
    return p


def validate_entry(c, entry, tenant, target=None):
    source = source_project(c,entry,tenant)
    metadata = {**entry['resource'],'evidence_id':entry['source_evidence_id']}
    validate_price_metadata(source,metadata)
    if target:
        # Target dates can differ from the source project's effective date.
        validate_price_metadata({**source,'region':target['region'],'pricing_date':core.effective_date(target).isoformat()},metadata)
    return source


@router.get('/organization/resource-library')
def list_entries(q: str='', offset: int=0, a=Depends(core.actor)):
    if len(q)>200 or offset<0:
        raise HTTPException(422, '检索内容或分页参数无效')
    with store.connect() as c:
        entries = [json.loads(row['data']) for row in c.execute('SELECT data FROM organization_resources WHERE tenant=? ORDER BY rowid DESC',(a['tenant'],))]
        from .project_access import allowed
        visible=[]
        for entry in entries:
            row=c.execute('SELECT data FROM projects WHERE id=? AND tenant=?',(entry['source_project_id'],a['tenant'])).fetchone()
            if row and allowed(json.loads(row['data']),a): visible.append(entry)
        entries = [e for e in visible if q.casefold() in (e['resource']['name']+' '+e['resource'].get('specification','')+' '+e['resource']['period']+' '+e['source_project_name']).casefold()]
        result = []
        for entry in entries[offset:offset+100]:
            try:
                validate_entry(c,entry,a['tenant'])
                issue = '' if entry['state']=='approved' else '条目尚未通过企业复核'
            except HTTPException as exc:
                issue = str(exc.detail)
            result.append({'entry':entry,'object_hash':store.digest(entry),'available':not issue,'issue':issue})
    return {'items':result,'total':len(entries),'offset':offset}


@router.post('/projects/{pid}/resource-library/publish')
def publish(pid: str, body: dict, a=Depends(core.actor)):
    core.edit(a)
    if not isinstance(body.get('reuse_basis'),str) or not body['reuse_basis'].strip() or len(body['reuse_basis'])>3000:
        raise HTTPException(422, '须填写允许在本企业其他项目复用的授权范围及条件')
    with store.transaction() as c:
        p = core.project(c,pid,a)
        from .project_access import config
        if config(p)['mode']=='restricted':
            raise HTTPException(409, '受限项目禁止向企业价格库发布原件或价格')
        row = next((r for r in resources(p) if r['key']==body.get('key')),None)
        if not row or row['boq_hash']!=body.get('boq_hash') or row['resource_hash']!=body.get('resource_hash'):
            raise HTTPException(409, '原资源已变化，请重新选择')
        b = core.find(p,'boq',row['boq_id']);r = row['resource']
        if b['state']!='approved' or core.price_blockers({**p,'boq':[b]},p):
            raise HTTPException(409, '只能发布来源完整、价格有效的已审清单资源')
        if r.get('library_ref') or core.require_evidence(p,r['evidence_id']).get('library_origin'):
            raise HTTPException(409, '复用资源不可循环发布；请在原价格来源建立新条目')
        reuse_evidence = core.require_evidence(p,body.get('reuse_evidence_id'))
        resource = {k:r[k] for k in PRICE_FIELDS if k in r}
        if not isinstance(r.get('specification',''),str) or len(r.get('specification',''))>2000:
            raise HTTPException(422, '资源规格须为不超过2000字的说明')
        resource.update(source_type=r.get('source_type') or body.get('source_type'),valid_from=r.get('valid_from') or body.get('valid_from'),specification=r.get('specification',''))
        validate_price_metadata(p,{**resource,'evidence_id':r['evidence_id']})
        entry = {'id':store.uid(),'resource':resource,'source_project_id':pid,'source_project_name':p['name'],
                 'source_boq_id':b['id'],'source_boq_name':b['name'],'source_key':row['key'],'source_boq_hash':row['boq_hash'],
                 'source_resource_hash':row['resource_hash'],'source_evidence_id':r['evidence_id'],'reuse_evidence_id':reuse_evidence['id'],
                 'reuse_basis':body['reuse_basis'],'state':'draft','created_at':store.now(),'author':a['username'],'reviews':[]}
        entry['proofs']=[]
        for kind,eid in [('price',r['evidence_id']),('reuse',reuse_evidence['id'])]:
            ev=core.require_evidence(p,eid);doc=core.find(p,'documents',ev['document_id'])
            entry['proofs'].append({'kind':kind,'text_excerpt':ev['text'][:4000],'excerpt_truncated':len(ev['text'])>4000,
                                    'page':ev['page'],'document_name':doc['name'],'document_hash':doc['hash'],
                                    'document_url':f"/api/v1/projects/{pid}/documents/{doc['id']}/file"})
        c.execute('INSERT INTO organization_resources VALUES(?,?,?)',(entry['id'],a['tenant'],store.canonical(entry)))
        store.audit(c,{'id':'tenant:'+a['tenant'],'tenant':a['tenant']},a,'RESOURCE_LIBRARY_PUBLISHED',entry)
    return {'entry':entry,'object_hash':store.digest(entry)}


@router.post('/organization/resource-library/{eid}/reviews')
def review_entry(eid: str, body: dict, a=Depends(core.actor)):
    if a['role']!='reviewer':
        raise HTTPException(403, '仅独立复核账号可以审定企业价格条目')
    if body.get('decision') not in ('approve','reject','request_more_info') or not isinstance(body.get('reason'),str) or not body['reason'].strip() or len(body['reason'])>3000:
        raise HTTPException(422, '须填写审核决定及许可核查依据')
    with store.transaction() as c:
        entry = get_entry(c,eid,a['tenant'])
        core.project(c,entry['source_project_id'],a)
        if store.digest(entry)!=body.get('object_hash'):
            raise HTTPException(409, '价格条目已变化，请重新核对')
        if body['decision']=='approve':
            validate_entry(c,entry,a['tenant'])
        record = {'id':store.uid(),'reviewer':a['username'],'timestamp':store.now(),'object_hash':body['object_hash'],
                  'decision':body['decision'],'reason':body['reason']}
        entry['reviews'].append(record)
        entry['state']={'approve':'approved','reject':'rejected','request_more_info':'needs_info'}[body['decision']]
        c.execute('UPDATE organization_resources SET data=? WHERE id=? AND tenant=?',(store.canonical(entry),eid,a['tenant']))
        store.audit(c,{'id':'tenant:'+a['tenant'],'tenant':a['tenant']},a,'RESOURCE_LIBRARY_REVIEWED',{'entry_id':eid,**record})
    return {'entry':entry,'object_hash':store.digest(entry)}


def copy_evidence(target, source, entry, eid):
    existing = next((e for e in target['evidence'] if e.get('library_origin',{}).get('entry_id')==entry['id'] and e['library_origin']['evidence_id']==eid),None)
    if existing:
        core.require_evidence(target,existing['id'])
        return existing['id']
    evidence = core.require_evidence(source,eid)
    doc = core.find(source,'documents',evidence['document_id'])
    path = store.ROOT/'originals'/doc['hash']
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=doc['hash']:
        raise HTTPException(409, '价格依据原件缺失或文件校验失败，未采用企业价格')
    origin = {'entry_id':entry['id'],'project_id':source['id'],'document_id':doc['id'],'evidence_id':eid}
    copied_doc = {k:copy.deepcopy(doc[k]) for k in ['hash','size','pages'] if k in doc}
    copied_doc.update(id=store.uid(),name='企业价格依据-'+entry['id'][:8]+'-'+doc['name'],specialty='合同计价',version=1,
                      parse_state='needs_review',parsed=True,uploaded_at=store.now(),library_origin=origin)
    target['documents'].append(copied_doc)
    copied = {**copy.deepcopy(evidence),'id':store.uid(),'document_id':copied_doc['id'],'method':'enterprise_price_evidence','library_origin':origin}
    target['evidence'].append(copied)
    return copied['id']


@router.post('/projects/{pid}/resource-library/{eid}/propose')
def propose_from_library(pid: str, eid: str, body: dict, a=Depends(core.actor)):
    core.edit(a)
    if body.get('confirm_conditions') is not True:
        raise HTTPException(422, '须明确核对复用授权、材质规格、税制及本项目适用条件')
    with store.transaction() as c:
        p = core.project(c,pid,a);entry = get_entry(c,eid,a['tenant'])
        core.project(c,entry['source_project_id'],a)
        if entry['state']!='approved' or store.digest(entry)!=body.get('object_hash'):
            raise HTTPException(409, '企业价格尚未审定或已发生变化')
        source = validate_entry(c,entry,a['tenant'],p)
        if source['id']==pid:
            raise HTTPException(409, '原项目请使用本项目资源调价；企业库复用面向其他项目')
        lookup = {r['key']:r for r in resources(p)}
        items = body.get('entries')
        if not isinstance(items,list) or not 1<=len(items)<=500:
            raise HTTPException(422, '请选择1至500条目标资源')
        for item in items:
            row = lookup.get(item['key']) if isinstance(item,dict) and isinstance(item.get('key'),str) else None
            if not row or any(row['resource'].get(k,'')!=entry['resource'].get(k,'') for k in ['name','type','unit','specification']):
                raise HTTPException(409, '目标资源名称、类别、单位或规格不一致；不进行近似替换')
        price_evidence = copy_evidence(p,source,entry,entry['source_evidence_id'])
        reuse_evidence = copy_evidence(p,source,entry,entry['reuse_evidence_id'])
        change = build_price_change(p,{'name':'采用企业资源价格','reason':body.get('reason'),
                    'metadata':{**entry['resource'],'evidence_id':price_evidence},
                    'entries':[{**item,'price':entry['resource']['price']} for item in items]})
        for item in change['entries']:
            item['after']['library_ref']={'id':eid,'hash':store.digest(entry),'reuse_evidence_id':reuse_evidence}
        change['library_entry_id']=eid
        if not any(x['hash']==store.digest(entry) for x in p['library_snapshots']):
            p['library_snapshots'].append({'entry':copy.deepcopy(entry),'hash':store.digest(entry)})
        store.audit(c,p,a,'RESOURCE_LIBRARY_PRICE_PROPOSED',change)
        store.save(c,p)
    return change


def reference_errors(p):
    errors, cache = [], {}
    with store.connect() as c:
        for row in resources(p):
            r=row['resource'];ref=r.get('library_ref')
            evidence=next((e for e in p['evidence'] if e['id']==r.get('evidence_id')),None)
            if not ref and not (evidence or {}).get('library_origin'):
                continue
            try:
                if r.get('pricing_status') == 'unpriced':
                    raise HTTPException(409,'待询价资源不能视为已采用的企业价格，请补充当前价格依据')
                if not isinstance(ref,dict) or not isinstance(ref.get('id'),str) or not isinstance(ref.get('hash'),str):
                    raise HTTPException(409,'价格来源于企业库，请通过企业价格采用流程重新确认')
                if ref['id'] not in cache:
                    entry=get_entry(c,ref['id'],p['tenant'])
                    validate_entry(c,entry,p['tenant'],p)
                    cache[ref['id']]=entry
                entry=cache[ref['id']]
                if entry['state']!='approved' or store.digest(entry)!=ref.get('hash'):
                    raise HTTPException(409,'企业价格条目已撤审或版本变化')
                if not evidence or evidence.get('library_origin',{}).get('entry_id')!=entry['id'] or evidence['library_origin']['evidence_id']!=entry['source_evidence_id']:
                    raise HTTPException(409,'企业价格原始依据关联不一致')
                reuse=core.require_evidence(p,ref.get('reuse_evidence_id'))
                if reuse.get('library_origin',{}).get('entry_id')!=entry['id'] or reuse['library_origin']['evidence_id']!=entry['reuse_evidence_id']:
                    raise HTTPException(409,'企业复用授权依据关联不一致')
                if any(r.get(k,'')!=entry['resource'].get(k,'') for k in PRICE_FIELDS if k not in ('price',)) or Decimal(r['price'])!=Decimal(entry['resource']['price']):
                    raise HTTPException(409,'采用的价格或适用条件与企业条目不一致')
            except HTTPException as exc:
                errors.append(r.get('name','资源')+'企业价格不可用：'+str(exc.detail))
    return list(dict.fromkeys(errors))
