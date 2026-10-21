"""Project-local, reviewed composition candidates. No inferred codes or public quota data."""
import copy
from datetime import date
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from . import main as core, store
from .engine import calculate, DomainError, money, validate_pricing_inputs

router = APIRouter(prefix='/api/v1')


def text(body, key, limit=3000):
    value=body.get(key)
    if not isinstance(value,str) or not value.strip() or len(value)>limit:
        raise HTTPException(422,'请填写完整的候选适用条件、版本、许可及采用理由')
    return value.strip()


def proof_ids(item):
    ids={item['reuse_evidence_id']}
    for q in item['quotas']:
        ids.add(q['evidence_id'])
        if q.get('conversion'):ids.add(q['conversion']['evidence_id'])
        ids.update(r['evidence_id'] for r in q['resources'])
    return sorted(ids)


def validate(p,item,verify_bytes=False):
    # Older records remain viewable, but cannot be adopted without a new scoped submission.
    if item.get('schema')!='quota-catalog-v2':
        raise HTTPException(409,'旧候选未登记专业、适用期和复用条件，请从已审原清单重新保存候选')
    rule=p['rules'][0] if p['rules'] else {}
    if item['region']!=p['region'] or item['discipline'] not in p['scope'] or item['edition']!=rule.get('version') or item['standard']!=rule.get('standard'):
        raise HTTPException(409,'候选地区、专业或计价规则版本已不适用')
    if not date.fromisoformat(item['valid_from'])<=core.effective_date(p)<=date.fromisoformat(item['valid_until']):
        raise HTTPException(409,'候选不在当前计价日期适用期间')
    source=core.find(p,'boq',item['boq_id'])
    if source['state']!='approved' or store.digest(source)!=item['source_hash']:
        raise HTTPException(409,'原清单已修改或撤审，请重新保存并复核候选')
    if any(q['edition']!=item['quota_edition'] for q in item['quotas']):
        raise HTTPException(409,'定额年度版本不一致')
    validate_pricing_inputs([{**item,'quantity':'1'}])
    issues=core.price_blockers({**p,'boq':[source]},p)
    if issues:raise HTTPException(409,'；'.join(issues))
    from .task_execution import original_bytes
    checked=set()
    for eid in proof_ids(item):
        ev=core.require_evidence(p,eid);doc=core.find(p,'documents',ev['document_id'])
        if ev['document_hash']!=doc['hash']:raise HTTPException(409,'候选依据版本不一致')
        if not (store.ROOT/'originals'/doc['hash']).is_file():raise HTTPException(409,'候选依据原件缺失')
        if verify_bytes and doc['hash'] not in checked:
            original_bytes(doc);checked.add(doc['hash'])


def reference_errors(p,items=None,verify_bytes=False):
    errors=[]
    for b in (p['boq'] if items is None else items):
        ref=b.get('catalog_ref')
        if not ref:continue
        try:
            item=core.find(p,'catalog',ref['id'])
            if item['state']!='approved' or store.digest(item)!=ref.get('hash'):raise HTTPException(409,'候选审批或版本已变化')
            validate(p,item,verify_bytes)
        except HTTPException as exc:errors.append(b['name']+'定额候选不可用：'+str(exc.detail))
    return list(dict.fromkeys(errors))


def create(p,body,a):
    b=core.find(p,'boq',body.get('boq_id'))
    if body.get('object_hash')!=store.digest(b):raise HTTPException(409,'原清单已变化，请刷新后核对')
    if b.get('catalog_ref'):raise HTTPException(409,'已采用的候选不能循环发布；请使用原清单')
    if b['state']!='approved' or not b['quotas'] or core.price_blockers({**p,'boq':[b]},p):raise HTTPException(409,'只能保存来源完整的已审组价')
    for key in ['discipline','quota_edition','applicability','reuse_basis','reuse_evidence_id']:text(body,key)
    try:start,end=date.fromisoformat(body['valid_from']),date.fromisoformat(body['valid_until'])
    except (KeyError,TypeError,ValueError):raise HTTPException(422,'候选有效期格式无效') from None
    if start>end:raise HTTPException(422,'候选生效日期不得晚于截止日期')
    if not p['rules']:raise HTTPException(409,'请先登记本项目计价规则')
    item={k:copy.deepcopy(b[k]) for k in ['name','features','code','unit','quotas']}
    item.update({k:body[k].strip() for k in ['discipline','quota_edition','applicability','reuse_basis','reuse_evidence_id','valid_from','valid_until']})
    rule=p['rules'][0]
    item.update(id=store.uid(),schema='quota-catalog-v2',region=p['region'],standard=rule['standard'],edition=rule['version'],
                state='draft',created_at=store.now(),author=a['username'],boq_id=b['id'],source_hash=store.digest(b),evidence_id=b['quotas'][0]['evidence_id'])
    validate(p,item,True)
    return item


def candidates(p,b,discipline='',quota_edition=''):
    result=[]
    for item in p['catalog']:
        if item['state']!='approved' or item['region']!=p['region'] or item['unit']!=b['unit'] or item['edition']!=(p['rules'][0]['version'] if p['rules'] else ''):continue
        if discipline and item.get('discipline')!=discipline or quota_edition and item.get('quota_edition')!=quota_edition:continue
        try:
            if item.get('schema')=='quota-catalog-v2':validate(p,item)
            elif core.price_blockers({**p,'boq':[item]},p):continue
        except (HTTPException,DomainError):continue
        exact_code=bool(b.get('code')) and item['code']==b['code'];exact_name=item['name']==b['name']
        overlap=len(set(b['name']+b['features']) & set(item['name']+item['features']))
        score=(100 if exact_code else 0)+(30 if exact_name else 0)+overlap
        if score:result.append({**item,'object_hash':store.digest(item),'score':score,'adoptable':item.get('schema')=='quota-catalog-v2' and item['boq_id']!=b['id'],
                               'match_reason':f'编码{"相同" if exact_code else "不同"}；名称{"相同" if exact_name else "不同"}；特征文字交集{overlap}字。仅文字检索，须核对构造、工法、换算及许可，不代表专业匹配结论。'})
    return sorted(result,key=lambda x:(-x['score'],x['id']))[:5]


def prepare(p,bid,eid,body):
    b=core.find(p,'boq',bid);item=core.find(p,'catalog',eid)
    if body.get('boq_hash')!=store.digest(b) or body.get('object_hash')!=store.digest(item):raise HTTPException(409,'目标清单或候选已变化，请重新试算')
    if item['state']!='approved':raise HTTPException(409,'候选尚未通过独立复核')
    validate(p,item,True)
    if bid==item['boq_id']:raise HTTPException(409,'原清单无需采用自己的候选')
    if b['unit']!=item['unit'] or body.get('discipline')!=item['discipline'] or body.get('quota_edition')!=item['quota_edition']:
        raise HTTPException(409,'目标单位、所选专业或定额年度版本与候选不一致')
    context={'target':store.digest(b),'candidate':store.digest(item),'rule':p['rules'],'date':core.effective_date(p).isoformat(),
             'region':p['region'],'scope':p['scope'],'source':store.digest(core.find(p,'boq',item['boq_id']))}
    return b,item,store.digest(context)


@router.get('/projects/{pid}/catalog-status')
def status(pid:str,a=Depends(core.actor)):
    with store.connect() as c:
        p=core.project(c,pid,a);items=[]
        for item in p['catalog']:
            try:validate(p,item);issue='' if item['state']=='approved' else '候选尚未审定'
            except (HTTPException,DomainError) as exc:issue=str(exc.detail if isinstance(exc,HTTPException) else exc)
            items.append({'item':item,'object_hash':store.digest(item),'available':not issue,'issue':issue})
        return {'items':items,'boq_hashes':{b['id']:store.digest(b) for b in p['boq']}}


@router.post('/projects/{pid}/boq/{bid}/quota-candidates/{eid}/preview')
def preview(pid:str,bid:str,eid:str,body:dict,a=Depends(core.actor)):
    with store.connect() as c:
        p=core.project(c,pid,a);b,item,context=prepare(p,bid,eid,body)
        rules={**(p['rules'][0] if p['rules'] else {}),'fees':[],'node_fees':[]}
        after=calculate([{**b,'quotas':item['quotas']}],rules)
        try:before=calculate([b],rules) if b['quotas'] and not core.price_blockers({**p,'boq':[b]},p) else None
        except DomainError:before=None
        return {'preview_hash':context,'before':before,'after':after,'delta':money(Decimal(after['total'])-Decimal(before['total'])) if before else None,
                'notice':'仅本清单综合单价与合价对比，不含任何单位、单项或项目级取费。未计价清单不按零价计算差额；采用后须重新复核清单并生成报价。'}


@router.post('/projects/{pid}/boq/{bid}/quota-candidates/{eid}/adopt')
def adopt(pid:str,bid:str,eid:str,body:dict,a=Depends(core.actor)):
    core.edit(a);reason=text(body,'reason');key=text(body,'request_id',100)
    if body.get('confirm_conditions') is not True:raise HTTPException(422,'须明确核对专业、工法、单位、价格和复用许可')
    with store.transaction() as c:
        p=core.project(c,pid,a);fingerprint=store.digest({'bid':bid,'eid':eid,'body':body})
        previous=next((x for x in p.get('quota_adoptions',[]) if x['request_id']==key),None)
        if previous:
            if previous['request_hash']!=fingerprint:raise HTTPException(409,'请求标识已用于其他采用内容')
            return previous
        b,item,context=prepare(p,bid,eid,body)
        if body.get('preview_hash')!=context:raise HTTPException(409,'采用前试算已过期，请重新核对')
        record={'id':store.uid(),'request_id':key,'request_hash':fingerprint,'boq_id':bid,'boq_name':b['name'],'catalog_id':eid,
                'candidate_hash':store.digest(item),'candidate':copy.deepcopy(item),'before_quotas':copy.deepcopy(b['quotas']),
                'preview_hash':context,'reason':reason,'author':a['username'],'created_at':store.now()}
        core.invalidate(p,'quotes')
        b.update(quotas=copy.deepcopy(item['quotas']),catalog_ref={'id':eid,'hash':store.digest(item),'adoption_id':record['id']},state='draft')
        p['status']='BOQ_DRAFT'
        p.setdefault('quota_adoptions',[]).append(record)
        store.audit(c,p,a,'QUOTA_CANDIDATE_ADOPTED',record);store.save(c,p)
        return record
