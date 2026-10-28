"""Internal purchase requisitions from reviewed cost scenarios; no supplier transmission."""
import copy
import hashlib
import io
from datetime import date,datetime
from typing import Annotated
from zoneinfo import ZoneInfo
from fastapi import APIRouter,Depends,HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel,ConfigDict,Field,field_validator
from . import main as core,store,procurement_analyses as analyses,supplier_quotes as offers
from .stock_inventory import approved

router=APIRouter(prefix='/api/v1')
COLLECTION='procurement_requests'
EVENTS='procurement_request_events'
NOTICE='仅内部采购申请，人工选择一个已审测算方案，不向供应商发送，不形成采购订单、付款或入库。审定只代表内部申请复核；对外采购须另行办理。不同测算之间不自动去重，须人工核查采购范围。'


def today():return datetime.now(ZoneInfo('Asia/Shanghai')).date()


class RequestIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name:offers.Text
    analysis_id:offers.Text
    analysis_hash:offers.Hash
    quote_id:offers.Text
    request_date:offers.Text
    required_by:offers.Text
    destination:offers.Text
    reason:offers.Text
    evidence_id:offers.Text
    selection_confirmed:Annotated[bool,Field(strict=True)]

    @field_validator('request_date','required_by')
    @classmethod
    def iso_date(cls,value):
        try:
            if date.fromisoformat(value).isoformat()!=value:raise ValueError()
        except ValueError:raise ValueError('申请及需求日期须为YYYY-MM-DD') from None
        return value


class PreviewIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    input:RequestIn


class SaveIn(PreviewIn):
    request_id:offers.RequestId
    preview_hash:offers.Hash


class CancelIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id:offers.RequestId
    object_hash:offers.Hash
    reason:offers.Text
    evidence_id:offers.Text
    not_ordered_confirmed:Annotated[bool,Field(strict=True)]


def intact(row):
    if row.get('content_hash')!=analyses.seal(row)['content_hash']:raise HTTPException(409,'内部采购申请或取消记录内容校验失败，请恢复核查')


def cancelled(p,rid):
    return next((e for e in p[EVENTS] if e['target_id']==rid),None)


def records(p):
    for row in p[COLLECTION]+p[EVENTS]:intact(row)
    seen=set();rows={r['id']:r for r in p[COLLECTION]}
    for event in p[EVENTS]:
        row=rows.get(event['target_id'])
        if not row or row['id'] in seen or event.get('operation')!='cancel' or event.get('record_hash')!=store.digest(row) or event['created_at']<row['created_at']:
            raise HTTPException(409,'采购申请取消关联或快照无法核验，请恢复核查')
        seen.add(row['id'])


def prepare(p,raw,exclude=''):
    records(p)
    parent=core.find(p,analyses.COLLECTION,raw['analysis_id'])
    if store.digest(parent)!=raw['analysis_hash']:raise HTTPException(409,'采购测算摘要已变化，请重新读取选择')
    if not approved(p,analyses.COLLECTION,parent):raise HTTPException(409,'采购申请须引用有效独立复核的成本测算')
    analyses.validate(p,parent)
    scenario=next((s for s in parent['result']['scenarios'] if s['quote']['id']==raw['quote_id']),None)
    if not scenario:raise HTTPException(422,'须明确选择原测算中的一份供应商方案')
    if not raw['selection_confirmed']:raise HTTPException(422,'须确认人工选定方案及采购范围，仅建立内部申请')
    m=scenario['quote']['metadata'];request_date=raw['request_date']
    if request_date>today().isoformat() or request_date<parent['result']['analysis_date'] or raw['required_by']<request_date:
        raise HTTPException(422,'申请日期不得晚于今天或早于测算日期，需求日期不得早于申请日期')
    if not m['valid_from']<=request_date<=m['valid_until'] or not m['valid_from']<=today().isoformat()<=m['valid_until']:
        raise HTTPException(409,'所选报价须同时适用于申请日期及当前日期，过期报价须重新核查')
    if any(r['id']!=exclude and r['input']['analysis_id']==parent['id'] and not cancelled(p,r['id']) for r in p[COLLECTION]):
        raise HTTPException(409,'同一测算已有未取消内部采购申请，请先核查原申请，不能重复取用或同时选多家')
    review=next(r for r in reversed(p['reviews']) if r['collection']==analyses.COLLECTION and r['object_id']==parent['id'])
    return dict(analysis_id=parent['id'],analysis_hash=store.digest(parent),analysis_name=parent['name'],analysis_review=copy.deepcopy(review),target=copy.deepcopy(parent['result']['target']),quantity=parent['result']['quantity'],unit=parent['result']['unit'],currency='CNY',tax_mode=parent['result']['tax_mode'],scenario=copy.deepcopy(scenario),analysis_basis=parent['input']['basis'],analysis_evidence_id=parent['input']['evidence_id'],proof=offers.proof(p,raw['evidence_id']),notice=NOTICE)


def validate(p,row):
    records(p)
    if cancelled(p,row['id']):raise HTTPException(409,'内部采购申请已取消，仅供追溯，不能重新复核')
    if prepare(p,row['input'],row['id'])!=row['result']:raise HTTPException(409,'采购申请来源、审核或原件已变化，请核查并另建，不覆盖旧申请')


def reviewable(p,row,a):
    records(p)
    if row['author']==a['username']:raise HTTPException(403,'内部采购申请须由非编制人独立复核')
    if cancelled(p,row['id']):raise HTTPException(409,'内部采购申请已取消，仅供追溯，不能重审')
    from .procurement_orders import summary
    if summary(p,row['id']):raise HTTPException(409,'申请已有已下单订单登记，仅供追溯，不能重审')


def status(p,row):
    records(p);event=cancelled(p,row['id']);cancel_issue=''
    try:validate(p,row);issue=''
    except HTTPException as e:issue=str(e.detail)
    if event:
        try:
            if offers.proof(p,event['evidence_id'])!=event['proof']:raise HTTPException(409,'取消依据已变化；取消事实保留，须另行核查')
        except HTTPException as e:cancel_issue=str(e.detail)
    from .procurement_orders import summary
    order=summary(p,row['id'])
    return dict(record=row,object_hash=store.digest(row),order=order,phase='order_registered' if order else 'cancelled' if event else row['state'],current=not issue,issue=issue,review_valid=not issue and approved(p,COLLECTION,row),cancellation=event,cancellation_issue=cancel_issue,overdue=not event and row['input']['required_by']<today().isoformat())


def retry(p,request_id,digest,actor,collection):
    for key in (COLLECTION,EVENTS):
        old=next((r for r in p[key] if r['request_id']==request_id),None)
        if old:
            intact(old)
            if key!=collection or old['request_hash']!=digest or old['author']!=actor['username']:raise HTTPException(409,'请求编号已用于其他采购申请操作，不重复保存')
            return old


@router.get('/projects/{pid}/procurement-requests/options')
def options(pid:str,a=Depends(core.actor)):
    with store.connect() as c:
        p=core.project(c,pid,a);records(p);items=[]
        for row in reversed(p[analyses.COLLECTION]):
            view=analyses.status(p,row)
            active=next((r for r in p[COLLECTION] if r['input']['analysis_id']==row['id'] and not cancelled(p,r['id'])),None)
            choices=[dict(quote_id=s['quote']['id'],eligible=s['quote']['metadata']['valid_from']<=today().isoformat()<=s['quote']['metadata']['valid_until']) for s in row['result']['scenarios']]
            issue=view['issue'] or ('测算尚未有效独立复核' if not view['review_valid'] else '') or ('已有未取消申请 '+active['number'] if active else '') or ('各方案报价均不适用于当前日期，请重新核查' if not any(x['eligible'] for x in choices) else '')
            items.append(dict(analysis=row,object_hash=store.digest(row),eligible=not issue,issue=issue,scenarios=choices))
        return dict(items=items,notice=NOTICE)


@router.get('/projects/{pid}/procurement-requests')
def read(pid:str,a=Depends(core.actor)):
    with store.connect() as c:
        p=core.project(c,pid,a);records(p)
        return dict(items=[status(p,r) for r in reversed(p[COLLECTION])],notice=NOTICE)


@router.post('/projects/{pid}/procurement-requests/preview')
def preview(pid:str,body:PreviewIn,a=Depends(core.actor)):
    core.edit(a)
    with store.connect() as c:
        p=core.project(c,pid,a);raw=body.input.model_dump();result=prepare(p,raw)
        return dict(result=result,preview_hash=store.digest(dict(input=raw,result=result)))


@router.post('/projects/{pid}/procurement-requests')
def save(pid:str,body:SaveIn,a=Depends(core.actor)):
    core.edit(a)
    with store.transaction() as c:
        p=core.project(c,pid,a);records(p);digest=store.digest(body.model_dump());old=retry(p,body.request_id,digest,a,COLLECTION)
        if old:return old
        raw=body.input.model_dump();result=prepare(p,raw)
        if body.preview_hash!=store.digest(dict(input=raw,result=result)):raise HTTPException(409,'采购申请预览已变化，请重新读取核对')
        rid=store.uid();row=analyses.seal(dict(id=rid,number='PR-'+rid[:16].upper(),request_id=body.request_id,request_hash=digest,name=raw['name'],input=raw,result=result,author=a['username'],created_at=store.now(),state='draft'))
        p[COLLECTION].append(row);store.audit(c,p,a,'PROCUREMENT_REQUEST_SAVED',dict(id=rid,number=row['number'],content_hash=row['content_hash']));store.save(c,p)
        return row


@router.post('/projects/{pid}/procurement-requests/{rid}/cancel')
def cancel(pid:str,rid:str,body:CancelIn,a=Depends(core.actor)):
    core.edit(a)
    with store.transaction() as c:
        p=core.project(c,pid,a);records(p);digest=store.digest(dict(target_id=rid,**body.model_dump()));old=retry(p,body.request_id,digest,a,EVENTS)
        if old:return old
        row=core.find(p,COLLECTION,rid)
        from .procurement_orders import summary
        if summary(p,rid):raise HTTPException(409,'申请已有已下单订单登记，不能通过内部取消撤销外部事实')
        if not body.not_ordered_confirmed:raise HTTPException(422,'须据原件确认尚未对外下单，内部取消不能撤销外部订单或收货')
        if store.digest(row)!=body.object_hash or cancelled(p,rid):raise HTTPException(409,'申请已变化或已取消，请重新读取核对')
        event=analyses.seal(dict(id=store.uid(),request_id=body.request_id,request_hash=digest,operation='cancel',target_id=rid,record_hash=store.digest(row),reason=body.reason,evidence_id=body.evidence_id,proof=offers.proof(p,body.evidence_id),not_ordered_confirmed=True,author=a['username'],created_at=store.now()))
        p[EVENTS].append(event);store.audit(c,p,a,'PROCUREMENT_REQUEST_CANCELLED',dict(id=event['id'],application_id=rid));store.save(c,p)
        return event


@router.get('/projects/{pid}/procurement-requests/{rid}/export')
def export(pid:str,rid:str,a=Depends(core.actor)):
    with store.transaction() as c:
        p=core.project(c,pid,a);row=core.find(p,COLLECTION,rid);view=status(p,row)
        payload=store.canonical(dict(schema='internal-procurement-request-v1',project_id=pid,project_name=p['name'],**view,reviews=[r for r in p['reviews'] if r['collection']==COLLECTION and r['object_id']==rid])).encode()
        sha=hashlib.sha256(payload).hexdigest();path=store.ROOT/'exports'/sha;path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()!=sha:raise HTTPException(409,'采购申请历史文件校验异常，请恢复核查')
        if not path.exists():path.write_bytes(payload)
        artifact=dict(id=store.uid(),purpose='procurement_request',requisition_id=rid,format='json',quote_id='',quote_version=None,hash=sha,approved=view['review_valid'],timestamp=store.now(),actor=a['username'],media='application/json',suffix='json')
        c.execute('INSERT INTO artifacts VALUES(?,?,?,?)',(artifact['id'],pid,a['tenant'],store.canonical(artifact)));store.audit(c,p,a,'PROCUREMENT_REQUEST_EXPORTED',artifact)
    return StreamingResponse(io.BytesIO(payload),media_type='application/json',headers={'Content-Disposition':'attachment; filename="QiangQuote-internal-procurement-request.json"','X-Artifact-Hash':sha})
