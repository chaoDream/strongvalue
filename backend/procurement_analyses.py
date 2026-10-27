"""Evidence-backed supplier cost scenarios; no order, award, payment or stock movement."""
import copy
import hashlib
import io
from datetime import date, datetime
from decimal import Decimal, localcontext, ROUND_HALF_UP
from typing import Annotated
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, StringConstraints, Field, field_validator
from . import main as core, store, supplier_quotes as offers
from .stock_inventory import approved
from .workflows import resources

router=APIRouter(prefix='/api/v1')
COLLECTION='procurement_analyses'
NOTICE='仅以人工核定采购数量及已审报价计算人民币货款、运费和其他费用；同一资源同一税制分别列方案，不合计不同供应商、不自动排序或选商。不含自动税额换算、付款、采购订单、入库或工程报价变更。'
Quantity=Annotated[str,StringConstraints(strict=True,pattern=r'^\d{1,10}(?:\.\d{1,18})?$')]
Fee=Annotated[str,StringConstraints(strict=True,pattern=r'^\d{1,13}(?:\.\d{1,2})?$')]


class ScenarioIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    quote_id:offers.Text
    quote_hash:offers.Hash
    freight:Fee
    other_charges:Fee
    adjustment_basis:offers.Text


class AnalysisIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name:offers.Text
    key:offers.Text
    boq_hash:offers.Hash
    resource_hash:offers.Hash
    quantity:Quantity
    analysis_date:offers.Text
    basis:offers.Text
    evidence_id:offers.Text
    conditions_confirmed:Annotated[bool,Field(strict=True)]
    scenarios:list[ScenarioIn]=Field(min_length=1,max_length=10)

    @field_validator('analysis_date')
    @classmethod
    def valid_date(cls,v):
        try:
            d=date.fromisoformat(v)
            if d.isoformat()!=v or d>datetime.now(ZoneInfo('Asia/Shanghai')).date():raise ValueError()
        except ValueError:raise ValueError('测算日期须为不晚于今天的YYYY-MM-DD') from None
        return v


class PreviewIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    input:AnalysisIn


class SaveIn(PreviewIn):
    request_id:offers.RequestId
    preview_hash:offers.Hash


def seal(row):
    return {**row,'content_hash':store.digest({k:v for k,v in row.items() if k not in ('state','content_hash')})}


def intact(row):
    if row.get('content_hash')!=seal(row)['content_hash']:raise HTTPException(409,'采购测算内容校验失败，请恢复核查原记录')


def usable(p,offer,row):
    if not approved(p,'supplier_quotes',offer):raise HTTPException(409,'报价须有匹配且非登记人签署的有效独立复核')
    offers.validate(p,offer,target=False)
    if offer['target']['key']!=row['key'] or any(offer['target']['resource'].get(k)!=row['resource'].get(k) for k in offers.IDENTITY+['consumption','supplier_inquiry_ref']):
        raise HTTPException(409,'报价资源规格、消耗或询价轮次与当前清单不符')
    return next(r for r in reversed(p['reviews']) if r['collection']=='supplier_quotes' and r['object_id']==offer['id'])


def prepare(p,raw):
    row=next((x for x in resources(p) if x['key']==raw['key']),None)
    if not row or row['boq_hash']!=raw['boq_hash'] or row['resource_hash']!=raw['resource_hash']:raise HTTPException(409,'测算资源或清单已变化，请重新读取当前目标')
    if row['resource']['type']!='材料':raise HTTPException(422,'当前采购测算仅支持材料资源')
    if not raw['conditions_confirmed']:raise HTTPException(422,'须逐方案确认费用与原报价不重复、税制一致且供货条件可解释')
    if not Decimal(0)<Decimal(raw['quantity'])<=Decimal('1e9'):raise HTTPException(422,'采购数量须大于0且不超过十亿，单位沿用材料资源')
    if len({s['quote_id'] for s in raw['scenarios']})!=len(raw['scenarios']):raise HTTPException(422,'同一报价不能重复列入本次测算')
    proof=offers.proof(p,raw['evidence_id']);scenarios=[];tax=None
    with localcontext() as ctx:
        ctx.prec=80
        for selected in raw['scenarios']:
            offer=core.find(p,'supplier_quotes',selected['quote_id'])
            if store.digest(offer)!=selected['quote_hash']:raise HTTPException(409,'所选报价摘要已变化，请重新读取核对')
            review=usable(p,offer,row);m=offer['metadata']
            if not m['valid_from']<=raw['analysis_date']<=m['valid_until'] or offer['received_on']>raw['analysis_date']:raise HTTPException(409,'测算日期须在报价有效期内且不早于收到报价日期')
            if tax is not None and tax!=m['tax_mode']:raise HTTPException(422,'不同税制须分开测算，当前不自动换税或混合比较')
            tax=m['tax_mode'];freight=Decimal(selected['freight']);other=Decimal(selected['other_charges'])
            if max(freight,other)>Decimal('1e12'):raise HTTPException(422,'单项附加费用不得超过一万亿元')
            exact=Decimal(raw['quantity'])*Decimal(m['price'])
            goods=exact.quantize(Decimal('.01'),rounding=ROUND_HALF_UP);total=goods+freight+other
            if total>Decimal('1e18'):raise HTTPException(422,'方案金额超出当前测算范围')
            scenarios.append(dict(quote=copy.deepcopy(offer),quote_review=copy.deepcopy(review),goods_exact=format(exact,'f'),goods_amount=format(goods,'.2f'),freight=format(freight,'.2f'),other_charges=format(other,'.2f'),total=format(total,'.2f'),adjustment_basis=selected['adjustment_basis']))
    return dict(target=copy.deepcopy(row),quantity=raw['quantity'],unit=row['resource']['unit'],currency='CNY',tax_mode=tax,analysis_date=raw['analysis_date'],pricing_date=core.effective_date(p).isoformat(),proof=proof,scenarios=scenarios,notice=NOTICE)


def validate(p,row):
    intact(row)
    if prepare(p,row['input'])!=row['result']:raise HTTPException(409,'测算来源、复核记录、日期口径或原件已变化，请另建当前测算，历史结果保留')


def reviewable(p,row,a):
    intact(row)
    if a['username']==row['author']:raise HTTPException(403,'采购测算须由非编制人独立复核')


def status(p,row):
    intact(row)
    try:validate(p,row);issue=''
    except HTTPException as e:issue=str(e.detail)
    return dict(record=row,object_hash=store.digest(row),current=not issue,issue=issue,review_valid=not issue and approved(p,COLLECTION,row))


@router.get('/projects/{pid}/procurement-analyses/options')
def options(pid:str,a=Depends(core.actor)):
    with store.connect() as c:
        p=core.project(c,pid,a);groups=[]
        for row in resources(p):
            if row['resource']['type']!='材料':continue
            candidates=[]
            for offer in p['supplier_quotes']:
                if offer['target']['key']!=row['key'] or offers.latest(p,offer)['id']!=offer['id']:continue
                try:usable(p,offer,row);issue=''
                except HTTPException as e:issue=str(e.detail)
                candidates.append(dict(offer=offer,object_hash=store.digest(offer),eligible=not issue,issue=issue))
            if candidates:groups.append(dict(target=row,offers=candidates))
        return dict(groups=groups,notice=NOTICE)


@router.get('/projects/{pid}/procurement-analyses')
def read(pid:str,a=Depends(core.actor)):
    with store.connect() as c:
        p=core.project(c,pid,a)
        return dict(items=[status(p,row) for row in reversed(p[COLLECTION])],notice=NOTICE)


@router.post('/projects/{pid}/procurement-analyses/preview')
def preview(pid:str,body:PreviewIn,a=Depends(core.actor)):
    core.edit(a)
    with store.connect() as c:
        p=core.project(c,pid,a);raw=body.input.model_dump();result=prepare(p,raw)
        return dict(result=result,preview_hash=store.digest(dict(input=raw,result=result)))


@router.post('/projects/{pid}/procurement-analyses')
def save(pid:str,body:SaveIn,a=Depends(core.actor)):
    core.edit(a)
    with store.transaction() as c:
        p=core.project(c,pid,a);request_hash=store.digest(body.model_dump());old=next((r for r in p[COLLECTION] if r['request_id']==body.request_id),None)
        if old:
            intact(old)
            if old['request_hash']!=request_hash or old['author']!=a['username']:raise HTTPException(409,'请求编号已用于其他采购测算，不重复保存')
            return old
        raw=body.input.model_dump();result=prepare(p,raw)
        if body.preview_hash!=store.digest(dict(input=raw,result=result)):raise HTTPException(409,'测算预览已变化，请重新核对后保存')
        row=seal(dict(id=store.uid(),request_id=body.request_id,request_hash=request_hash,name=raw['name'],input=raw,result=result,author=a['username'],created_at=store.now(),state='draft'))
        p[COLLECTION].append(row);store.audit(c,p,a,'PROCUREMENT_ANALYSIS_SAVED',dict(id=row['id'],content_hash=row['content_hash']));store.save(c,p)
        return row


@router.get('/projects/{pid}/procurement-analyses/{rid}/export')
def export(pid:str,rid:str,a=Depends(core.actor)):
    with store.transaction() as c:
        p=core.project(c,pid,a);row=core.find(p,COLLECTION,rid);view=status(p,row)
        payload=store.canonical(dict(schema='procurement-analysis-v1',project_id=pid,project_name=p['name'],**view,reviews=[r for r in p['reviews'] if r['collection']==COLLECTION and r['object_id']==rid])).encode()
        sha=hashlib.sha256(payload).hexdigest();path=store.ROOT/'exports'/sha;path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()!=sha:raise HTTPException(409,'历史留档文件校验异常，请恢复核查')
        if not path.exists():path.write_bytes(payload)
        artifact=dict(id=store.uid(),purpose='procurement_analysis',analysis_id=rid,format='json',quote_id='',quote_version=None,hash=sha,approved=view['review_valid'],timestamp=store.now(),actor=a['username'],media='application/json',suffix='json')
        c.execute('INSERT INTO artifacts VALUES(?,?,?,?)',(artifact['id'],pid,a['tenant'],store.canonical(artifact)));store.audit(c,p,a,'PROCUREMENT_ANALYSIS_EXPORTED',artifact)
    return StreamingResponse(io.BytesIO(payload),media_type='application/json',headers={'Content-Disposition':'attachment; filename="QiangQuote-procurement-analysis.json"','X-Artifact-Hash':sha})
