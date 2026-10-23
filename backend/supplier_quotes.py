"""Received supplier offers for missing prices; no outbound requests or automatic award."""
import copy
from datetime import date
from typing import Annotated, Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from . import main as core, store
from .engine import number
from .workflows import resources, validate_price_metadata
from .task_execution import original_bytes

router=APIRouter(prefix='/api/v1')
Text=Annotated[str,StringConstraints(strict=True,strip_whitespace=True,min_length=1,max_length=2000)]
Hash=Annotated[str,StringConstraints(pattern=r'^[a-f0-9]{64}$')]
RequestId=Annotated[str,StringConstraints(pattern=r'^[a-zA-Z0-9-]{16,64}$')]
IDENTITY=['name','type','unit','resource_code','specification','fee_allocation']
PRICE=['price','source','license','period','region','tax_mode','valid_from','valid_until','evidence_id','source_type','pricing_status']


class OfferIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id:RequestId
    key:Text
    boq_hash:Hash
    resource_hash:Hash
    supplier:Text
    reference:Text
    received_on:Text
    terms:Text
    reason:Text
    price:Annotated[str,StringConstraints(strict=True,pattern=r'^\d+(?:\.\d{1,18})?$')]
    license:Text
    period:Text
    region:Text
    tax_mode:Literal['含税','不含税']
    valid_from:Text
    valid_until:Text
    evidence_id:Text
    revision_of:Text|None=None
    revision_hash:Hash|None=None

    @field_validator('received_on','valid_from','valid_until')
    @classmethod
    def iso_date(cls,value):
        try:
            if date.fromisoformat(value).isoformat()!=value:raise ValueError()
        except ValueError:raise ValueError('日期须为YYYY-MM-DD') from None
        return value


class AdoptIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    object_hash:Hash
    reason:Text
    confirm_conditions:Annotated[bool,Field(strict=True)]


class ReopenIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id:RequestId
    object_hash:Hash
    boq_hash:Hash
    resource_hash:Hash
    evidence_id:Text
    reason:Text
    confirm_reopen:Annotated[bool,Field(strict=True)]


def proof(p,eid):
    ev=core.require_evidence(p,eid);doc=core.find(p,'documents',ev['document_id'])
    if ev.get('document_hash')!=doc['hash']:raise HTTPException(409,'报价证据与原件版本不一致')
    original_bytes(doc)
    return {'evidence_id':eid,'evidence_hash':store.digest(ev),'document_id':doc['id'],'document_hash':doc['hash']}


def pending_target(p,target):
    row=next((r for r in resources(p) if r['key']==target['key']),None)
    if not row or row['boq_hash']!=target['boq_hash'] or row['resource_hash']!=target['resource_hash']:
        raise HTTPException(409,'询价资源或清单版本已变化，请核对后重新登记报价')
    if row['resource'].get('pricing_status')!='unpriced':raise HTTPException(409,'此入口仅为待询价资源补价，已有价格请使用调价流程')
    validate_inquiry_ref(p,row['resource'])
    return row


def family(offer):
    return offer.get('family_id',offer['id'])


def latest(p,offer):
    return max((o for o in p['supplier_quotes'] if family(o)==family(offer)),key=lambda o:o.get('revision',1))


def validate_inquiry_ref(p,resource):
    if 'supplier_inquiry_ref' not in resource:return
    ref=resource['supplier_inquiry_ref']
    if not isinstance(ref,dict):raise HTTPException(409,'重新询价来源关联无效')
    record=core.find(p,'supplier_inquiry_reopenings',ref.get('id'))
    if store.digest(record)!=ref.get('hash') or {k:v for k,v in resource.items() if k!='supplier_inquiry_ref'}!=record['after']:
        raise HTTPException(409,'重新询价记录或目标资源已变化，请重新核对')
    if proof(p,record['evidence_id'])!=record['proof']:raise HTTPException(409,'重新询价依据已变化，请重新核对原件')


def validate(p,offer,*,target=True):
    if latest(p,offer)['id']!=offer['id']:raise HTTPException(409,'此报价已有修订版本，历史版本不再用于新的审核或采用')
    if proof(p,offer['evidence_id'])!=offer['proof']:raise HTTPException(409,'报价原始证据已变化，请重新登记并复核')
    validate_inquiry_ref(p,offer['target']['resource'])
    validate_price_metadata(p,offer['metadata'])
    number(offer['metadata']['price'])
    if target:return pending_target(p,offer['target'])


def validate_reference(p,row):
    r=row['resource'];ref=r.get('supplier_quote_ref')
    if not isinstance(ref,dict):raise HTTPException(409,'供应商报价采用关联无效')
    offer=core.find(p,'supplier_quotes',ref.get('quote_id'))
    adoption=core.find(p,'supplier_quote_adoptions',ref.get('adoption_id'))
    if offer['state']!='approved' or store.digest(offer)!=adoption['quote_hash'] or adoption['quote_id']!=offer['id']:
        raise HTTPException(409,'所用供应商报价已撤审或发生变化')
    if adoption['target_key']!=row['key'] or ref.get('quote_hash')!=adoption['quote_hash']:
        raise HTTPException(409,'报价采用的目标资源关联不一致')
    if any(r.get(k)!=adoption['after'].get(k) for k in IDENTITY+PRICE):
        raise HTTPException(409,'所用报价的规格、单位或价格依据已改变，请重新核对来源')
    validate(p,offer,target=False)


def reference_errors(p):
    errors=[]
    for row in resources(p):
        if 'supplier_inquiry_ref' in row['resource']:
            try:validate_inquiry_ref(p,row['resource'])
            except HTTPException as e:errors.append(row['resource']['name']+'：'+str(e.detail))
        if 'supplier_quote_ref' not in row['resource']:continue
        try:validate_reference(p,row)
        except HTTPException as e:errors.append(row['resource']['name']+'：'+str(e.detail))
    return errors


def selected_target(p,offer,adoption):
    if not adoption:raise HTTPException(409,'此报价没有采用记录')
    row=next((r for r in resources(p) if r['key']==adoption['target_key']),None)
    if not row:raise HTTPException(409,'原采用资源已不在当前清单中')
    r=row['resource'];ref=r.get('supplier_quote_ref',{})
    if (not isinstance(ref,dict) or ref.get('quote_id')!=offer['id'] or ref.get('adoption_id')!=adoption['id']
        or ref.get('quote_hash')!=adoption['quote_hash'] or adoption['quote_id']!=offer['id']
        or any(r.get(k)!=adoption['after'].get(k) for k in IDENTITY+PRICE)):
        raise HTTPException(409,'当前资源已不再使用此报价的原采用版本，请刷新核对')
    return row


@router.get('/projects/{pid}/supplier-quotes')
def read(pid:str,key:str=Query('',max_length=200),offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=200),a=Depends(core.actor)):
    with store.connect() as c:
        p=core.project(c,pid,a)
        offers=[o for o in reversed(p['supplier_quotes']) if not key or o['target']['key']==key]
        items=[]
        for offer in offers[offset:offset+limit]:
            adoption=next((x for x in p['supplier_quote_adoptions'] if x['quote_id']==offer['id']),None)
            try:
                row=selected_target(p,offer,adoption)
                reopen_target={k:row[k] for k in ('boq_hash','resource_hash')}
            except HTTPException:reopen_target=None
            row=next((r for r in resources(p) if r['key']==offer['target']['key']),None)
            can_revise=bool(row and row['resource'].get('pricing_status')=='unpriced' and latest(p,offer)['id']==offer['id']
                            and all(row['resource'].get(k)==offer['target']['resource'].get(k) for k in IDENTITY))
            try:
                validate(p,offer,target=adoption is None)
                issue='' if offer['state']=='approved' else '报价尚未通过独立复核'
                if adoption:
                    row=next((r for r in resources(p) if r['key']==adoption['target_key']),None)
                    if not row or row['resource'].get('supplier_quote_ref',{}).get('adoption_id')!=adoption['id']:
                        issue='历史采用；当前资源已改用其他价格或版本'
                    else:validate_reference(p,row)
            except HTTPException as e:issue=str(e.detail)
            items.append({'offer':offer,'object_hash':store.digest(offer),'issue':issue,'available':not issue and adoption is None,'adoption':adoption,
                          'latest_revision':latest(p,offer)['id']==offer['id'],'reopen_target':reopen_target,'can_revise':can_revise})
        return {'items':items,'total':len(offers),'offset':offset,'limit':limit,
                'notice':'仅登记已收到的报价，不发送询价或采购订单。不同税制及供货条件不可直接比低价；请独立复核并明确选择，不自动采用最低价。'}


@router.post('/projects/{pid}/supplier-quotes')
def register(pid:str,body:OfferIn,a=Depends(core.actor)):
    core.edit(a)
    with store.transaction() as c:
        p=core.project(c,pid,a);payload=body.model_dump(exclude_none=True);digest=store.digest(payload)
        existing=next((x for x in p['supplier_quotes'] if x['request_id']==body.request_id),None)
        if existing:
            if existing['request_hash']!=digest or existing['author']!=a['username']:raise HTTPException(409,'请求标识已用于其他报价，未重复登记')
            return {'offer':existing,'duplicate':True}
        target={k:payload[k] for k in ('key','boq_hash','resource_hash')}
        row=pending_target(p,target)
        predecessor=None
        if body.revision_of is not None or body.revision_hash is not None:
            if body.revision_of is None or body.revision_hash is None:raise HTTPException(422,'报价修订须同时提交原报价及读取时的版本校验值')
            predecessor=core.find(p,'supplier_quotes',body.revision_of)
            if store.digest(predecessor)!=body.revision_hash or latest(p,predecessor)['id']!=predecessor['id']:
                raise HTTPException(409,'原报价已变化或已有后续修订，请读取最新版本')
            if predecessor['supplier']!=body.supplier or predecessor['target']['key']!=body.key or any(predecessor['target']['resource'].get(k)!=row['resource'].get(k) for k in IDENTITY):
                raise HTTPException(409,'报价修订须为同一供应商和同一资源规格；其他供应商或规格请另行登记')
        if any(not isinstance(row['resource'].get(k),str) or not row['resource'][k].strip() for k in IDENTITY):
            raise HTTPException(409,'请先补齐待询价资源编码、规格、单位和费用归属')
        if body.valid_from>body.valid_until:raise HTTPException(422,'报价有效期起止顺序不正确')
        metadata={k:payload[k] for k in ('license','period','region','tax_mode','valid_from','valid_until','evidence_id')}
        metadata.update(price=format(number(body.price),'f'),source=body.supplier+' / '+body.reference,source_type='供应商报价',pricing_status='priced')
        if len(metadata['source'])>2000:raise HTTPException(422,'供应商与报价单名称合计不得超过1997字')
        offer={'id':store.uid(),'state':'draft','author':a['username'],'created_at':store.now(),
               'request_id':body.request_id,'request_hash':digest,'target':{**target,'boq_id':row['boq_id'],'boq_name':row['boq_name'],'resource':copy.deepcopy(row['resource'])},
               **{k:payload[k] for k in ('supplier','reference','received_on','terms','reason','evidence_id')},
               'metadata':metadata,'proof':proof(p,body.evidence_id)}
        offer.update(family_id=family(predecessor) if predecessor else offer['id'],revision=predecessor.get('revision',1)+1 if predecessor else 1,
                     previous_quote_id=predecessor['id'] if predecessor else None,previous_quote_hash=store.digest(predecessor) if predecessor else None)
        p['supplier_quotes'].append(offer)
        store.audit(c,p,a,'SUPPLIER_QUOTE_REGISTERED',offer);store.save(c,p)
        return {'offer':offer,'duplicate':False}


@router.post('/projects/{pid}/supplier-quotes/{qid}/reopen-inquiry')
def reopen(pid:str,qid:str,body:ReopenIn,a=Depends(core.actor)):
    core.edit(a)
    if body.confirm_reopen is not True:raise HTTPException(422,'须确认撤出当前价格并重新询价；清单与当前报价须重新审核')
    with store.transaction() as c:
        p=core.project(c,pid,a);digest=store.digest({'quote_id':qid,**body.model_dump()})
        previous=next((x for x in p['supplier_inquiry_reopenings'] if x['request_id']==body.request_id),None)
        if previous:
            if previous['request_hash']!=digest or previous['author']!=a['username']:raise HTTPException(409,'请求标识已用于其他重新询价操作')
            return {'reopening':previous,'duplicate':True}
        offer=core.find(p,'supplier_quotes',qid)
        if store.digest(offer)!=body.object_hash:raise HTTPException(409,'原报价状态或内容已变化，请刷新核对')
        adoption=next((x for x in p['supplier_quote_adoptions'] if x['quote_id']==qid),None)
        row=selected_target(p,offer,adoption)
        if row['boq_hash']!=body.boq_hash or row['resource_hash']!=body.resource_hash:raise HTTPException(409,'当前清单或资源已变化，未重新询价')
        # Recovery may use new evidence even when the previous price source is unavailable.
        evidence_proof=proof(p,body.evidence_id)
        after={k:copy.deepcopy(v) for k,v in row['resource'].items() if k not in PRICE+['supplier_quote_ref','supplier_inquiry_ref','library_ref','price_change_id']}
        after.update(price=None,pricing_status='unpriced',evidence_id=body.evidence_id)
        record={'id':store.uid(),'quote_id':qid,'quote_hash':store.digest(offer),'adoption_id':adoption['id'],'target_key':row['key'],
                'boq_id':row['boq_id'],'boq_name':row['boq_name'],'boq_hash':row['boq_hash'],'resource_hash':row['resource_hash'],
                'before':copy.deepcopy(row['resource']),'after':copy.deepcopy(after),'evidence_id':body.evidence_id,'proof':evidence_proof,
                'reason':body.reason,'author':a['username'],'created_at':store.now(),'request_id':body.request_id,'request_hash':digest}
        after['supplier_inquiry_ref']={'id':record['id'],'hash':store.digest(record)}
        b=core.find(p,'boq',row['boq_id']);p.setdefault('boq_history',[]).append(copy.deepcopy(b))
        b['quotas'][row['quota_index']]['resources'][row['resource_index']]=after;b['state']='draft'
        p['supplier_inquiry_reopenings'].append(record);core.invalidate(p,'quotes');p['status']='BOQ_DRAFT'
        store.audit(c,p,a,'SUPPLIER_INQUIRY_REOPENED',record);store.save(c,p)
        return {'reopening':record,'duplicate':False}


@router.post('/projects/{pid}/supplier-quotes/{qid}/adopt')
def adopt(pid:str,qid:str,body:AdoptIn,a=Depends(core.actor)):
    core.edit(a)
    if body.confirm_conditions is not True:raise HTTPException(422,'须确认已核对规格、单位、税制、供货条件、许可及合同适用性')
    with store.transaction() as c:
        p=core.project(c,pid,a);offer=core.find(p,'supplier_quotes',qid)
        if offer['state']!='approved' or body.object_hash!=store.digest(offer):raise HTTPException(409,'报价尚未审定或已经变化，请刷新核对')
        old=next((x for x in p['supplier_quote_adoptions'] if x['quote_id']==qid),None)
        if old:
            row=next((r for r in resources(p) if r['key']==old['target_key']),None)
            if not row or old['reason']!=body.reason or old['author']!=a['username'] or row['resource'].get('supplier_quote_ref',{}).get('adoption_id')!=old['id']:
                raise HTTPException(409,'此报价已采用，不能重复用于其他版本或改写原选择依据')
            validate_reference(p,row)
            return {'adoption':old,'duplicate':True}
        row=validate(p,offer);b=core.find(p,'boq',row['boq_id'])
        # No zero baseline or synthetic difference for a previously missing price.
        after={**copy.deepcopy(row['resource']),**offer['metadata']}
        for k in ('library_ref','price_change_id','supplier_quote_ref','supplier_inquiry_ref'):after.pop(k,None)
        adoption={'id':store.uid(),'quote_id':qid,'quote_hash':store.digest(offer),'target_key':row['key'],
                  'boq_id':b['id'],'reason':body.reason,'author':a['username'],'created_at':store.now(),
                  'before':copy.deepcopy(row['resource']),'after':copy.deepcopy(after),'boq_hash':row['boq_hash']}
        after['supplier_quote_ref']={'quote_id':qid,'quote_hash':adoption['quote_hash'],'adoption_id':adoption['id']}
        p.setdefault('boq_history',[]).append(copy.deepcopy(b))
        b['quotas'][row['quota_index']]['resources'][row['resource_index']]=after
        b['state']='draft';p['supplier_quote_adoptions'].append(adoption)
        core.invalidate(p,'quotes');p['status']='BOQ_DRAFT'
        store.audit(c,p,a,'SUPPLIER_QUOTE_ADOPTED',adoption);store.save(c,p)
        return {'adoption':adoption,'duplicate':False}
