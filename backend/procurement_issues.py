"""Actual original-unit withdrawals from accepted procurement receipt lots."""
import copy
import hashlib
import io
from decimal import Decimal, localcontext
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from . import main as core, store, procurement_receipts as receipts, procurement_analyses as analyses, supplier_quotes as offers
from .stock_inventory import approved

router=APIRouter(prefix='/api/v1')
COLLECTION='procurement_issues'
EVENTS='procurement_issue_events'
NOTICE='仅从已执行验收的合格采购批次按原资源单位领用。保存及复核不扣库存，显式执行后扣本批在库并累计已领；已领仅表示实际交接，不等于安装、消耗或加工。不改订单已到／未到额度，不计价、不付款、不转原材根数。退回、退货、移库、换算、加工接入及冲销尚不支持。'


class IssueIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    receipt_id:receipts.Short
    receipt_hash:offers.Hash
    issue_number:receipts.Short
    issued_on:receipts.Short
    quantity:analyses.Quantity
    destination:receipts.Short
    receiver:receipts.Short
    basis:offers.Text
    evidence_id:receipts.Short
    handover_confirmed:Annotated[bool,Field(strict=True)]

    @field_validator('issued_on')
    @classmethod
    def iso_date(cls,v):return receipts.requests.RequestIn.iso_date(v)


class PreviewIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    input:IssueIn


class SaveIn(PreviewIn):
    request_id:offers.RequestId
    ledger_hash:offers.Hash
    preview_hash:offers.Hash


def intact(row):
    if row.get('content_hash')!=analyses.seal(row)['content_hash']:raise HTTPException(409,'采购领用记录或事件内容校验失败，请恢复核查')


def event_for(p,rid):return next((e for e in p[EVENTS] if e['target_id']==rid),None)
def ledger_hash(p):return store.digest({k:p[k] for k in (receipts.COLLECTION,receipts.EVENTS,COLLECTION,EVENTS)})


def quantity(raw):
    value=Decimal(raw['quantity'])
    if not Decimal(0)<value<=Decimal('1e9'):raise HTTPException(422,'领用量须大于0且不超过十亿，沿用收货原单位')
    return format(value,'f')


def records(p):
    receipts.records(p);rows={r['id']:r for r in p[COLLECTION]};totals={};seen=set();numbers=set()
    for row in p[COLLECTION]+p[EVENTS]:intact(row)
    for row in p[COLLECTION]:
        if quantity(row['input'])!=row['result']['quantity']:raise HTTPException(409,'采购领用冻结数量不符')
        if not event_for(p,row['id']) or event_for(p,row['id'])['kind']!='cancel':
            number=row['input']['issue_number'].casefold()
            if number in numbers:raise HTTPException(409,'采购领用单号重复，请恢复核查')
            numbers.add(number)
    with localcontext() as ctx:
        ctx.prec=80
        for event in p[EVENTS]:
            row=rows.get(event['target_id'])
            if not row or row['id'] in seen or event['record_hash']!=store.digest(row) or event['created_at']<row['created_at'] or event['kind'] not in ('issue','cancel'):raise HTTPException(409,'采购领用事件重复或关联不符')
            seen.add(row['id'])
            if event['kind']=='cancel':continue
            source=core.find(p,receipts.COLLECTION,row['input']['receipt_id']);parent=receipts.event_for(p,source['id'])
            review=next((r for r in reversed(p['reviews']) if r['collection']==COLLECTION and r['object_id']==row['id']),None)
            if not parent or parent['kind']!='receive' or source!=row['result']['receipt'] or store.digest(source)!=row['input']['receipt_hash'] or parent!=row['result']['receipt_execution'] or event['created_at']<parent['created_at'] or row['result']['unit']!=source['result']['unit'] or not approved(p,COLLECTION,row) or event['review']!=review or event['quantity']!=row['result']['quantity']:raise HTTPException(409,'已执行采购领用与验收入库、数量或独立复核不符')
            rid=source['id'];totals[rid]=totals.get(rid,Decimal(0))+Decimal(event['quantity'])
            if totals[rid]>Decimal(source['result']['quantities']['accepted_quantity']):raise HTTPException(409,'采购累计领用超过合格入库量，请恢复核查')
    return totals


def source_issue(p,source):
    view=receipts.status(p,source)
    return view['issue'] or ('' if view['phase']=='receive' and view['review_valid'] else '来源须已执行有效验收入库')


def history_issue(p,row):
    source=row['result']['receipt'];issue=source_issue(p,source)
    if issue:return issue
    try:
        if offers.proof(p,row['input']['evidence_id'])!=row['result']['proof']:return '历史采购领用原件变化，已领及在库数量保留待核查'
    except HTTPException as e:return str(e.detail)
    return ''


def lots(p):
    totals=records(p);result=[]
    with localcontext() as ctx:
        ctx.prec=80
        for source in p[receipts.COLLECTION]:
            event=receipts.event_for(p,source['id'])
            if not event or event['kind']!='receive' or Decimal(source['result']['quantities']['accepted_quantity'])<=0:continue
            received=Decimal(source['result']['quantities']['accepted_quantity']);issued=totals.get(source['id'],Decimal(0));issue=source_issue(p,source)
            for row in p[COLLECTION]:
                operation=event_for(p,row['id'])
                if row['input']['receipt_id']==source['id'] and operation and operation['kind']=='issue':issue=issue or history_issue(p,row)
            result.append(dict(receipt_id=source['id'],receipt_hash=store.digest(source),receipt=source,warehouse=source['input']['warehouse'],lot_code=source['input']['lot_code'],resource=source['result']['order']['result']['requisition']['result']['target']['resource'],unit=source['result']['unit'],accepted_quantity=format(received,'f'),issued_quantity=format(issued,'f'),in_stock_quantity=format(received-issued,'f'),verified=not issue,issue=issue))
    return result


def prepare(p,raw,exclude=''):
    records(p);source=core.find(p,receipts.COLLECTION,raw['receipt_id'])
    if store.digest(source)!=raw['receipt_hash']:raise HTTPException(409,'采购领用引用的收货摘要变化，请重新核对')
    issue=source_issue(p,source)
    if issue:raise HTTPException(409,issue)
    if not raw['handover_confirmed']:raise HTTPException(422,'须据实际领用交接原件确认原单位数量及接收人、去向')
    if not source['input']['received_on']<=raw['issued_on']<=receipts.requests.today().isoformat():raise HTTPException(422,'领用日期不得早于收货日期或晚于今天')
    value=quantity(raw);lot=next((l for l in lots(p) if l['receipt_id']==source['id']),None)
    if not lot or not lot['verified']:raise HTTPException(409,lot['issue'] if lot else '该收货无合格采购库存')
    if Decimal(value)>Decimal(lot['in_stock_quantity']):raise HTTPException(409,'领用量超过本批尚在库量，请刷新核查')
    if any(r['id']!=exclude and r['input']['issue_number'].casefold()==raw['issue_number'].casefold() and not (event_for(p,r['id']) and event_for(p,r['id'])['kind']=='cancel') for r in p[COLLECTION]):raise HTTPException(409,'采购领用单号已有未取消记录')
    return dict(receipt=copy.deepcopy(source),receipt_execution=copy.deepcopy(receipts.event_for(p,source['id'])),quantity=value,unit=source['result']['unit'],proof=offers.proof(p,raw['evidence_id']),notice=NOTICE)


def validate(p,row):
    intact(row)
    if event_for(p,row['id']):raise HTTPException(409,'采购领用已执行或取消，仅供追溯')
    if prepare(p,row['input'],row['id'])!=row['result']:raise HTTPException(409,'采购领用来源、输入或原件变化，请取消后重建')


def reviewable(p,row,a):
    records(p)
    if row['author']==a['username']:raise HTTPException(403,'采购领用须非编制人独立复核')
    if event_for(p,row['id']):raise HTTPException(409,'采购领用已执行或取消，不能重审')


def status(p,row):
    records(p);event=event_for(p,row['id']);issue=''
    try:
        if not event:validate(p,row)
        elif event['kind']=='issue':issue=history_issue(p,row)
        elif offers.proof(p,event['evidence_id'])!=event['proof']:issue='取消原件变化，取消事实保留'
    except HTTPException as e:issue=str(e.detail)
    return dict(record=row,object_hash=store.digest(row),phase=event['kind'] if event else row['state'],issue=issue,current=not issue,event=event,review_valid=not issue and (not event or event['kind']=='issue') and approved(p,COLLECTION,row))


def retry(p,body,a,collection,kind='',target=''):
    digest=store.digest(dict(kind=kind,target_id=target,**body.model_dump()))
    for key in (COLLECTION,EVENTS):
        old=next((r for r in p[key] if r['request_id']==body.request_id),None)
        if old:
            if key!=collection or old['request_hash']!=digest or old['author']!=a['username']:raise HTTPException(409,'请求编号已用于其他采购领用操作')
            return old,digest
    if body.ledger_hash!=ledger_hash(p):raise HTTPException(409,'采购收货、领用或审核状态已变化，请刷新重新核对')
    return None,digest


@router.get('/projects/{pid}/procurement-issues')
def read(pid:str,a=Depends(core.actor)):
    with store.connect() as c:
        p=core.project(c,pid,a);return dict(items=[status(p,r) for r in reversed(p[COLLECTION])],lots=lots(p),ledger_hash=ledger_hash(p),notice=NOTICE)


@router.post('/projects/{pid}/procurement-issues/preview')
def preview(pid:str,body:PreviewIn,a=Depends(core.actor)):
    core.edit(a)
    with store.connect() as c:
        p=core.project(c,pid,a);raw=body.input.model_dump();result=prepare(p,raw)
        return dict(result=result,balance=next(l for l in lots(p) if l['receipt_id']==raw['receipt_id']),ledger_hash=ledger_hash(p),preview_hash=store.digest(dict(input=raw,result=result)))


@router.post('/projects/{pid}/procurement-issues')
def save(pid:str,body:SaveIn,a=Depends(core.actor)):
    core.edit(a)
    with store.transaction() as c:
        p=core.project(c,pid,a);records(p);old,digest=retry(p,body,a,COLLECTION)
        if old:return old
        raw=body.input.model_dump();result=prepare(p,raw)
        if body.preview_hash!=store.digest(dict(input=raw,result=result)):raise HTTPException(409,'采购领用预览变化，请重新核对')
        row=analyses.seal(dict(id=store.uid(),name='采购领用 '+raw['issue_number'],input=raw,result=result,state='draft',request_id=body.request_id,request_hash=digest,author=a['username'],created_at=store.now()))
        p[COLLECTION].append(row);store.audit(c,p,a,'PROCUREMENT_ISSUE_SAVED',dict(id=row['id'],content_hash=row['content_hash']));store.save(c,p);return row


@router.post('/projects/{pid}/procurement-issues/{rid}/execute')
def execute(pid:str,rid:str,body:receipts.MoveIn,a=Depends(core.actor)):
    core.edit(a)
    with store.transaction() as c:
        p=core.project(c,pid,a);records(p);old,digest=retry(p,body,a,EVENTS,'issue',rid)
        if old:return old
        row=core.find(p,COLLECTION,rid)
        if store.digest(row)!=body.object_hash or not approved(p,COLLECTION,row):raise HTTPException(409,'采购领用摘要变化或尚未有效独立复核')
        validate(p,row);review=next(r for r in reversed(p['reviews']) if r['collection']==COLLECTION and r['object_id']==rid)
        event=analyses.seal(dict(id=store.uid(),kind='issue',target_id=rid,record_hash=store.digest(row),quantity=row['result']['quantity'],review=copy.deepcopy(review),request_id=body.request_id,request_hash=digest,author=a['username'],created_at=store.now()))
        p[EVENTS].append(event);records(p);store.audit(c,p,a,'PROCUREMENT_ISSUE_EXECUTED',dict(id=event['id'],issue_id=rid));store.save(c,p);return event


@router.post('/projects/{pid}/procurement-issues/{rid}/cancel')
def cancel(pid:str,rid:str,body:receipts.CancelIn,a=Depends(core.actor)):
    core.edit(a)
    with store.transaction() as c:
        p=core.project(c,pid,a);records(p);old,digest=retry(p,body,a,EVENTS,'cancel',rid)
        if old:return old
        row=core.find(p,COLLECTION,rid)
        if store.digest(row)!=body.object_hash or event_for(p,rid):raise HTTPException(409,'采购领用已变更、执行或取消，不能取消')
        event=analyses.seal(dict(id=store.uid(),kind='cancel',target_id=rid,record_hash=store.digest(row),reason=body.reason,evidence_id=body.evidence_id,proof=offers.proof(p,body.evidence_id),request_id=body.request_id,request_hash=digest,author=a['username'],created_at=store.now()))
        p[EVENTS].append(event);store.audit(c,p,a,'PROCUREMENT_ISSUE_CANCELLED',dict(id=event['id'],issue_id=rid));store.save(c,p);return event


@router.get('/projects/{pid}/procurement-issues/{rid}/export')
def export(pid:str,rid:str,a=Depends(core.actor)):
    with store.transaction() as c:
        p=core.project(c,pid,a);row=core.find(p,COLLECTION,rid);view=status(p,row)
        payload=store.canonical(dict(schema='procurement-issue-v1',project_id=pid,project_name=p['name'],**view,lot_balance=next(l for l in lots(p) if l['receipt_id']==row['input']['receipt_id']),reviews=[r for r in p['reviews'] if r['collection']==COLLECTION and r['object_id']==rid])).encode();sha=hashlib.sha256(payload).hexdigest();path=store.ROOT/'exports'/sha;path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()!=sha:raise HTTPException(409,'采购领用留档文件校验异常')
        if not path.exists():path.write_bytes(payload)
        artifact=dict(id=store.uid(),purpose='procurement_issue',issue_id=rid,format='json',quote_id='',quote_version=None,hash=sha,approved=view['review_valid'],timestamp=store.now(),actor=a['username'],media='application/json',suffix='json')
        c.execute('INSERT INTO artifacts VALUES(?,?,?,?)',(artifact['id'],pid,a['tenant'],store.canonical(artifact)));store.audit(c,p,a,'PROCUREMENT_ISSUE_EXPORTED',artifact)
    return StreamingResponse(io.BytesIO(payload),media_type='application/json',headers={'Content-Disposition':'attachment; filename="QiangQuote-procurement-issue.json"','X-Artifact-Hash':sha})
