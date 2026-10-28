"""Register already-issued external orders; never transmit, pay or receive goods."""
import copy
import hashlib
import io
from datetime import date,datetime
from typing import Annotated
from zoneinfo import ZoneInfo
from fastapi import APIRouter,Depends,HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel,ConfigDict,Field,StringConstraints,field_validator
from . import main as core,store,procurement_analyses as analyses,procurement_requests as requests,supplier_quotes as offers
from .stock_inventory import approved

router=APIRouter(prefix='/api/v1')
COLLECTION='procurement_orders'
NOTICE='仅登记已有下单原件的采购订单，不代发订单、签合同、付款或入库。供应商、规格、数量、税制、单价和附加费用沿用已审申请，须人工与实际订单核对。登记即锁定原申请取消和重审；修订保留历史，不撤销已下单事实。'


class OrderIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    requisition_id:offers.Text
    requisition_hash:offers.Hash
    order_number:Annotated[str,StringConstraints(strict=True,strip_whitespace=True,min_length=1,max_length=200)]
    order_date:offers.Text
    delivery_due:offers.Text
    basis:offers.Text
    evidence_id:offers.Text
    placed_confirmed:Annotated[bool,Field(strict=True)]
    revision_of:offers.Text|None=None
    revision_hash:offers.Hash|None=None

    @field_validator('order_date','delivery_due')
    @classmethod
    def iso_date(cls,v):
        try:
            if date.fromisoformat(v).isoformat()!=v:raise ValueError()
        except ValueError:raise ValueError('下单及交货日期须为YYYY-MM-DD') from None
        return v


class PreviewIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    input:OrderIn


class SaveIn(PreviewIn):
    request_id:offers.RequestId
    preview_hash:offers.Hash


def intact(row):
    if row.get('content_hash')!=analyses.seal(row)['content_hash']:raise HTTPException(409,'采购订单登记内容校验失败，请恢复核查')


def latest(p,rid):
    rows=[r for r in p[COLLECTION] if r['input']['requisition_id']==rid]
    return max(rows,key=lambda r:r['revision']) if rows else None


def source_review(p,source):
    if not approved(p,requests.COLLECTION,source):raise HTTPException(409,'订单登记须有匹配的申请独立复核记录')
    return next(r for r in reversed(p['reviews']) if r['collection']==requests.COLLECTION and r['object_id']==source['id'])


def dates(raw,source,review):
    first=max(source['input']['request_date'],datetime.fromisoformat(review['timestamp']).astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat())
    m=source['result']['scenario']['quote']['metadata']
    if raw['order_date']<first or raw['order_date']>requests.today().isoformat() or raw['delivery_due']<raw['order_date']:
        raise HTTPException(422,'下单日期须不早于申请及其复核日期、不晚于今天；交货日期不得早于下单日期')
    if not m['valid_from']<=raw['order_date']<=m['valid_until']:raise HTTPException(409,'实际下单日期不在所选报价有效期内，请另行核查差异订单')


def records(p):
    requests.records(p);groups={}
    for row in p[COLLECTION]:
        intact(row);raw=row['input'];source=core.find(p,requests.COLLECTION,raw['requisition_id']);frozen=row['result']['requisition'];review=row['result']['requisition_review']
        if source!=frozen or store.digest(source)!=raw['requisition_hash'] or source_review(p,source)!=review or requests.cancelled(p,source['id']) or row['created_at']<review['timestamp']:
            raise HTTPException(409,'订单登记与冻结申请、独立复核或取消事实不符，请恢复核查')
        if not raw['placed_confirmed']:raise HTTPException(409,'订单登记缺少实际下单确认')
        dates(raw,source,review)
        groups.setdefault(source['id'],[]).append(row)
    identifiers=set()
    for rid,rows in groups.items():
        rows.sort(key=lambda r:r['revision']);first=rows[0]
        for index,row in enumerate(rows,1):
            previous=rows[index-2] if index>1 else None
            if row['revision']!=index or row['family_id']!=first['id'] or row['previous_id']!=(previous['id'] if previous else '') or row['previous_hash']!=(store.digest(previous) if previous else '') or row['input']['revision_of']!=(previous['id'] if previous else None) or row['input']['revision_hash']!=(store.digest(previous) if previous else None) or (previous and row['created_at']<previous['created_at']):
                raise HTTPException(409,'订单登记修订链不连续或历史快照不符，请恢复核查')
        current=rows[-1];key=(current['result']['requisition']['result']['scenario']['quote']['supplier'].strip().casefold(),current['input']['order_number'].strip().casefold())
        if key in identifiers:raise HTTPException(409,'当前同供应商订单号重复登记，请核查原件')
        identifiers.add(key)


def prepare(p,raw):
    records(p);source=core.find(p,requests.COLLECTION,raw['requisition_id'])
    if store.digest(source)!=raw['requisition_hash']:raise HTTPException(409,'所选采购申请已变化，请重新读取')
    previous=latest(p,source['id'])
    if raw['revision_of'] is not None or raw['revision_hash'] is not None:
        if not previous or raw['revision_of']!=previous['id'] or raw['revision_hash']!=store.digest(previous):raise HTTPException(409,'订单登记须从最新修订及其读取时摘要追加，不能从历史分叉')
    else:
        if previous:raise HTTPException(409,'该申请已有订单登记，请核对或追加修订，不能重复下单登记')
        requests.validate(p,source)
    review=source_review(p,source)
    if not raw['placed_confirmed']:raise HTTPException(422,'须据实际下单原件确认已对外下单且数量价格与已审申请一致')
    dates(raw,source,review)
    key=(source['result']['scenario']['quote']['supplier'].strip().casefold(),raw['order_number'].strip().casefold())
    for row in p[COLLECTION]:
        if row['input']['requisition_id']==source['id'] or latest(p,row['input']['requisition_id'])['id']!=row['id']:continue
        other=(row['result']['requisition']['result']['scenario']['quote']['supplier'].strip().casefold(),row['input']['order_number'].strip().casefold())
        if key==other:raise HTTPException(409,'同一供应商的当前订单号已登记，请核对，不能跨申请重复登记')
    return dict(requisition=copy.deepcopy(source),requisition_review=copy.deepcopy(review),proof=offers.proof(p,raw['evidence_id']),notice=NOTICE),previous


def validate(p,row):
    records(p)
    if latest(p,row['input']['requisition_id'])['id']!=row['id']:raise HTTPException(409,'此订单登记已有新修订，历史版本仅供追溯')
    if offers.proof(p,row['input']['evidence_id'])!=row['result']['proof']:raise HTTPException(409,'已下单原件已变化，请核查并追加有据修订；原登记事实保留')


def reviewable(p,row,a):
    records(p)
    if row['author']==a['username']:raise HTTPException(403,'采购订单登记须由非登记人独立复核')
    from .procurement_receipts import executed_source
    if executed_source(p,row['id']):raise HTTPException(409,'该订单版本已有实际收货，不能重审；登记信息纠正须追加修订')
    if latest(p,row['input']['requisition_id'])['id']!=row['id']:raise HTTPException(409,'历史订单修订不能重新复核，请核对最新版本')


def summary(p,rid):
    records(p);row=latest(p,rid)
    return dict(id=row['id'],order_number=row['input']['order_number'],revision=row['revision'],state=row['state']) if row else None


def status(p,row):
    records(p);is_latest=latest(p,row['input']['requisition_id'])['id']==row['id']
    try:validate(p,row);issue=''
    except HTTPException as e:issue=str(e.detail)
    try:requests.validate(p,row['result']['requisition']);planning_issue=''
    except HTTPException as e:planning_issue=str(e.detail)
    return dict(record=row,object_hash=store.digest(row),latest=is_latest,current=not issue,issue=issue,review_valid=not issue and approved(p,COLLECTION,row),planning_issue=planning_issue,overdue=is_latest and row['input']['delivery_due']<requests.today().isoformat())


@router.get('/projects/{pid}/procurement-orders/options')
def options(pid:str,a=Depends(core.actor)):
    with store.connect() as c:
        p=core.project(c,pid,a);records(p);items=[]
        for source in reversed(p[requests.COLLECTION]):
            try:
                requests.validate(p,source);source_review(p,source)
                if latest(p,source['id']):raise HTTPException(409,'已有订单登记，请在记录中追加修订')
                issue=''
            except HTTPException as e:issue=str(e.detail)
            items.append(dict(requisition=source,object_hash=store.digest(source),eligible=not issue,issue=issue))
        return dict(items=items,notice=NOTICE)


@router.get('/projects/{pid}/procurement-orders')
def read(pid:str,a=Depends(core.actor)):
    with store.connect() as c:
        p=core.project(c,pid,a);records(p)
        return dict(items=[status(p,row) for row in reversed(p[COLLECTION])],notice=NOTICE)


@router.post('/projects/{pid}/procurement-orders/preview')
def preview(pid:str,body:PreviewIn,a=Depends(core.actor)):
    core.edit(a)
    with store.connect() as c:
        p=core.project(c,pid,a);raw=body.input.model_dump();result,previous=prepare(p,raw)
        return dict(result=result,revision=previous['revision']+1 if previous else 1,preview_hash=store.digest(dict(input=raw,result=result)))


@router.post('/projects/{pid}/procurement-orders')
def save(pid:str,body:SaveIn,a=Depends(core.actor)):
    core.edit(a)
    with store.transaction() as c:
        p=core.project(c,pid,a);records(p);digest=store.digest(body.model_dump());old=next((r for r in p[COLLECTION] if r['request_id']==body.request_id),None)
        if old:
            if old['request_hash']!=digest or old['author']!=a['username']:raise HTTPException(409,'请求编号已用于其他订单登记，不重复保存')
            return old
        raw=body.input.model_dump();result,previous=prepare(p,raw)
        if body.preview_hash!=store.digest(dict(input=raw,result=result)):raise HTTPException(409,'订单登记预览已变化，请重新读取核对')
        rid=store.uid();row=analyses.seal(dict(id=rid,name='已下单订单 '+raw['order_number'],family_id=previous['family_id'] if previous else rid,revision=previous['revision']+1 if previous else 1,previous_id=previous['id'] if previous else '',previous_hash=store.digest(previous) if previous else '',request_id=body.request_id,request_hash=digest,input=raw,result=result,author=a['username'],created_at=store.now(),state='draft'))
        p[COLLECTION].append(row);store.audit(c,p,a,'PROCUREMENT_ORDER_REGISTERED',dict(id=rid,revision=row['revision'],requisition_id=raw['requisition_id'],content_hash=row['content_hash']));store.save(c,p)
        return row


@router.get('/projects/{pid}/procurement-orders/{oid}/export')
def export(pid:str,oid:str,a=Depends(core.actor)):
    with store.transaction() as c:
        p=core.project(c,pid,a);row=core.find(p,COLLECTION,oid);view=status(p,row)
        payload=store.canonical(dict(schema='issued-procurement-order-v1',project_id=pid,project_name=p['name'],**view,reviews=[r for r in p['reviews'] if r['collection']==COLLECTION and r['object_id']==oid])).encode()
        sha=hashlib.sha256(payload).hexdigest();path=store.ROOT/'exports'/sha;path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()!=sha:raise HTTPException(409,'订单登记历史文件校验异常，请恢复核查')
        if not path.exists():path.write_bytes(payload)
        artifact=dict(id=store.uid(),purpose='procurement_order',order_id=oid,order_revision=row['revision'],format='json',quote_id='',quote_version=None,hash=sha,approved=view['review_valid'],timestamp=store.now(),actor=a['username'],media='application/json',suffix='json')
        c.execute('INSERT INTO artifacts VALUES(?,?,?,?)',(artifact['id'],pid,a['tenant'],store.canonical(artifact)));store.audit(c,p,a,'PROCUREMENT_ORDER_EXPORTED',artifact)
    return StreamingResponse(io.BytesIO(payload),media_type='application/json',headers={'Content-Disposition':'attachment; filename="QiangQuote-issued-procurement-order.json"','X-Artifact-Hash':sha})
