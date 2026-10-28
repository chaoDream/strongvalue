"""Reviewed partial arrivals; executed acceptances enter a separate original-unit ledger."""
import copy
import hashlib
import io
from decimal import Decimal,localcontext
from typing import Annotated
from fastapi import APIRouter,Depends,HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel,ConfigDict,Field,StringConstraints,field_validator
from . import main as core,store,procurement_orders as orders,procurement_requests as requests,procurement_analyses as analyses,supplier_quotes as offers
from .stock_inventory import approved

router=APIRouter(prefix='/api/v1')
COLLECTION='procurement_receipts';EVENTS='procurement_receipt_events'
Short=Annotated[str,StringConstraints(strict=True,strip_whitespace=True,min_length=1,max_length=200)]
NOTICE='实际到货＝合格＋不合格，数量沿用订单资源原单位。保存及复核不记账，显式执行后合格数量进入独立采购库存，不合格仅列待处置；两者均扣已到货额度，不自动恢复替换额度。采购库存不自动转换为原材根数，不计价、不付款，不自动进入排料；原单位领用另见采购库存领用，不自动计作工程消耗；退回、退货、换算和冲销尚不支持。'


class ReceiptIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    order_id:Short
    order_hash:offers.Hash
    delivery_number:Short
    lot_code:Short
    warehouse:Short
    received_on:Short
    received_quantity:analyses.Quantity
    accepted_quantity:analyses.Quantity
    rejected_quantity:analyses.Quantity
    inspector:Short
    basis:offers.Text
    evidence_id:Short
    received_confirmed:Annotated[bool,Field(strict=True)]

    @field_validator('received_on')
    @classmethod
    def iso_date(cls,v):return requests.RequestIn.iso_date(v)


class PreviewIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    input:ReceiptIn


class SaveIn(PreviewIn):
    request_id:offers.RequestId
    ledger_hash:offers.Hash
    preview_hash:offers.Hash


class MoveIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id:offers.RequestId
    ledger_hash:offers.Hash
    object_hash:offers.Hash


class CancelIn(MoveIn):
    reason:offers.Text
    evidence_id:Short


def intact(row):
    if row.get('content_hash')!=analyses.seal(row)['content_hash']:raise HTTPException(409,'采购收货或执行记录内容校验失败，请恢复核查')


def event_for(p,rid):return next((e for e in p[EVENTS] if e['target_id']==rid),None)
def ledger_hash(p):return store.digest({k:p[k] for k in (COLLECTION,EVENTS)})
def identity(warehouse,lot):return warehouse.strip().casefold(),lot.strip().casefold()


def quantities(raw):
    with localcontext() as ctx:
        ctx.prec=80
        values={k:Decimal(raw[k+'_quantity']) for k in ('received','accepted','rejected')}
        if not 0<values['received']<=Decimal('1e9') or min(values.values())<0 or values['accepted']+values['rejected']!=values['received']:
            raise HTTPException(422,'实际到货须大于0且不超过十亿，合格与不合格须非负且精确相加等于到货量')
        return {k+'_quantity':format(v,'f') for k,v in values.items()}


def records(p):
    orders.records(p);rows={r['id']:r for r in p[COLLECTION]};seen=set();totals={}
    for row in p[COLLECTION]+p[EVENTS]:intact(row)
    for row in p[COLLECTION]:
        if quantities(row['input'])!=row['result']['quantities']:raise HTTPException(409,'采购收货数量与冻结输入不符')
    with localcontext() as ctx:
        ctx.prec=80
        for event in p[EVENTS]:
            row=rows.get(event['target_id'])
            if not row or row['id'] in seen or event['record_hash']!=store.digest(row) or event['created_at']<row['created_at'] or event['kind'] not in ('receive','cancel'):raise HTTPException(409,'采购收货事件重复或关联不符，请恢复核查')
            seen.add(row['id'])
            if event['kind']=='cancel':continue
            source=core.find(p,orders.COLLECTION,row['input']['order_id']);review=next((r for r in reversed(p['reviews']) if r['collection']==COLLECTION and r['object_id']==row['id']),None)
            if source!=row['result']['order'] or store.digest(source)!=row['input']['order_hash'] or not approved(p,orders.COLLECTION,source) or not approved(p,COLLECTION,row) or event['review']!=review or event['quantities']!=row['result']['quantities'] or row['result']['unit']!=source['result']['requisition']['result']['unit'] or row['result']['order_review']!=next((r for r in reversed(p['reviews']) if r['collection']==orders.COLLECTION and r['object_id']==source['id']),None):
                raise HTTPException(409,'已执行采购收货与原订单、数量或独立复核不符')
            family=source['family_id'];totals[family]=totals.get(family,Decimal(0))+Decimal(row['result']['quantities']['received_quantity'])
            limit=Decimal(source['result']['requisition']['result']['quantity'])
            if totals[family]>limit:raise HTTPException(409,'同一订单累计实际到货超过原订单额度，请恢复核查')
    lots=set();deliveries=set()
    for row in p[COLLECTION]:
        event=event_for(p,row['id'])
        if event and event['kind']=='cancel':continue
        key=identity(row['input']['warehouse'],row['input']['lot_code']);delivery=(row['result']['order']['family_id'],row['input']['delivery_number'].casefold())
        if key in lots or delivery in deliveries:raise HTTPException(409,'采购收货批号或同订单送货单号重复，请恢复核查')
        lots.add(key);deliveries.add(delivery)
    return totals


def claimed(p,warehouse,lot,exclude=''):
    records(p);key=identity(warehouse,lot)
    return any(r['id']!=exclude and identity(r['input']['warehouse'],r['input']['lot_code'])==key and not (event_for(p,r['id']) and event_for(p,r['id'])['kind']=='cancel') for r in p[COLLECTION])


def executed_source(p,oid):
    records(p)
    return any(r['input']['order_id']==oid and event_for(p,r['id']) and event_for(p,r['id'])['kind']=='receive' for r in p[COLLECTION])


def remaining(p,order):
    totals=records(p)
    with localcontext() as ctx:
        ctx.prec=80
        total=Decimal(order['result']['requisition']['result']['quantity']);arrived=totals.get(order['family_id'],Decimal(0))
        values=[r['result']['quantities'] for r in p[COLLECTION] if r['result']['order']['family_id']==order['family_id'] and event_for(p,r['id']) and event_for(p,r['id'])['kind']=='receive']
        return dict(ordered_quantity=format(total,'f'),received_quantity=format(arrived,'f'),accepted_quantity=format(sum((Decimal(v['accepted_quantity']) for v in values),Decimal(0)),'f'),rejected_quantity=format(sum((Decimal(v['rejected_quantity']) for v in values),Decimal(0)),'f'),remaining_quantity=format(total-arrived,'f'))


def prepare(p,raw,exclude=''):
    records(p);source=core.find(p,orders.COLLECTION,raw['order_id'])
    if store.digest(source)!=raw['order_hash']:raise HTTPException(409,'收货引用的订单登记已变化，请重新读取')
    orders.validate(p,source)
    if not approved(p,orders.COLLECTION,source):raise HTTPException(409,'收货须从当前有效已独立复核的订单登记建立')
    if not raw['received_confirmed']:raise HTTPException(422,'须据实际到货及验收原件确认数量闭合及合格库位交接')
    if not source['input']['order_date']<=raw['received_on']<=requests.today().isoformat():raise HTTPException(422,'实际收货日期不得早于下单日期或晚于今天')
    values=quantities(raw);balance=remaining(p,source)
    if Decimal(values['received_quantity'])>Decimal(balance['remaining_quantity']):raise HTTPException(409,'本批到货超过订单家族尚未到货额度，已拒收数量也不自动恢复额度')
    if claimed(p,raw['warehouse'],raw['lot_code'],exclude):raise HTTPException(409,'该库位批号已有未取消采购收货，不得重复登记或重复入库')
    if any(r['id']!=exclude and r['result']['order']['family_id']==source['family_id'] and r['input']['delivery_number'].casefold()==raw['delivery_number'].casefold() and not (event_for(p,r['id']) and event_for(p,r['id'])['kind']=='cancel') for r in p[COLLECTION]):raise HTTPException(409,'同一订单送货单号已有未取消收货记录')
    from . import stock_inventory as inv
    key=identity(raw['warehouse'],raw['lot_code'])
    if any(identity(r['input']['warehouse'],r['input']['lot_code'])==key and not inv.event_for(p,'void',r['id']) for r in p['stock_receipts']):raise HTTPException(409,'该批号已用于原材登记，不得跨台账重复入库')
    from .stock_processing_results import balances
    from .stock_offcut_ledger import replay
    _,lots=balances(p);lots=replay(p,lots)['lots']
    if any(identity(r['warehouse'],r['lot_code'])==key for r in lots):raise HTTPException(409,'该批号已用于实物余料，不得跨台账重复入库')
    review=next(r for r in reversed(p['reviews']) if r['collection']==orders.COLLECTION and r['object_id']==source['id'])
    return dict(order=copy.deepcopy(source),order_review=copy.deepcopy(review),quantities=values,unit=source['result']['requisition']['result']['unit'],proof=offers.proof(p,raw['evidence_id']),notice=NOTICE)


def validate(p,row):
    intact(row)
    if event_for(p,row['id']):raise HTTPException(409,'采购收货已执行或取消，仅供追溯')
    # Preserve historical capability copy while revalidating every business field.
    prepared=prepare(p,row['input'],row['id']);prepared['notice']=row['result']['notice']
    if prepared!=row['result']:raise HTTPException(409,'收货输入、订单审核或原件已变化，请取消后重新建立')


def reviewable(p,row,a):
    records(p)
    if row['author']==a['username']:raise HTTPException(403,'采购收货须由非编制人独立复核')
    if event_for(p,row['id']):raise HTTPException(409,'采购收货已执行或取消，不能重审')


def status(p,row):
    records(p);event=event_for(p,row['id']);issue=''
    try:
        if not event:validate(p,row)
        elif event['kind']=='receive':
            if offers.proof(p,row['input']['evidence_id'])!=row['result']['proof'] or offers.proof(p,row['result']['order']['input']['evidence_id'])!=row['result']['order']['result']['proof']:raise HTTPException(409,'历史收货或实际订单原件已变化，实物数量保留且批次待核查')
        elif offers.proof(p,event['evidence_id'])!=event['proof']:raise HTTPException(409,'取消原件已变化，取消事实保留')
    except HTTPException as e:issue=str(e.detail)
    return dict(record=row,object_hash=store.digest(row),phase=event['kind'] if event else row['state'],issue=issue,current=not issue,review_valid=not issue and (not event or event['kind']=='receive') and approved(p,COLLECTION,row),event=event)


def retry(p,body,a,collection,kind='',target=''):
    digest=store.digest(dict(kind=kind,target_id=target,**body.model_dump()))
    for key in (COLLECTION,EVENTS):
        old=next((r for r in p[key] if r['request_id']==body.request_id),None)
        if old:
            if key!=collection or old['request_hash']!=digest or old['author']!=a['username']:raise HTTPException(409,'请求编号已用于其他采购收货操作')
            return old,digest
    if body.ledger_hash!=ledger_hash(p):raise HTTPException(409,'采购收货或复核状态已变化，请刷新后重新核对')
    return None,digest


@router.get('/projects/{pid}/procurement-receipts')
def read(pid:str,a=Depends(core.actor)):
    with store.connect() as c:
        p=core.project(c,pid,a);records(p);items=[status(p,r) for r in reversed(p[COLLECTION])];sources=[];lots=[]
        for order in p[orders.COLLECTION]:
            if orders.latest(p,order['input']['requisition_id'])['id']!=order['id']:continue
            view=orders.status(p,order);sources.append(dict(order=order,object_hash=store.digest(order),eligible=view['review_valid'],issue=view['issue'] or ('' if view['review_valid'] else '订单登记尚未有效独立复核'),balance=remaining(p,order),planning_issue=view['planning_issue']))
        from .procurement_issues import lots as current_lots
        lots=current_lots(p)
        return dict(items=items,sources=sources,lots=lots,ledger_hash=ledger_hash(p),notice=NOTICE)


@router.post('/projects/{pid}/procurement-receipts/preview')
def preview(pid:str,body:PreviewIn,a=Depends(core.actor)):
    core.edit(a)
    with store.connect() as c:
        p=core.project(c,pid,a);raw=body.input.model_dump();result=prepare(p,raw)
        return dict(result=result,balance=remaining(p,result['order']),ledger_hash=ledger_hash(p),preview_hash=store.digest(dict(input=raw,result=result)))


@router.post('/projects/{pid}/procurement-receipts')
def save(pid:str,body:SaveIn,a=Depends(core.actor)):
    core.edit(a)
    with store.transaction() as c:
        p=core.project(c,pid,a);records(p);old,digest=retry(p,body,a,COLLECTION)
        if old:return old
        raw=body.input.model_dump();result=prepare(p,raw)
        if body.preview_hash!=store.digest(dict(input=raw,result=result)):raise HTTPException(409,'采购收货预览已变化，请重新核对')
        row=analyses.seal(dict(id=store.uid(),name='采购收货 '+raw['delivery_number'],input=raw,result=result,state='draft',request_id=body.request_id,request_hash=digest,author=a['username'],created_at=store.now()))
        p[COLLECTION].append(row);store.audit(c,p,a,'PROCUREMENT_RECEIPT_SAVED',dict(id=row['id'],content_hash=row['content_hash']));store.save(c,p);return row


@router.post('/projects/{pid}/procurement-receipts/{rid}/execute')
def execute(pid:str,rid:str,body:MoveIn,a=Depends(core.actor)):
    core.edit(a)
    with store.transaction() as c:
        p=core.project(c,pid,a);records(p);old,digest=retry(p,body,a,EVENTS,'receive',rid)
        if old:return old
        row=core.find(p,COLLECTION,rid)
        if store.digest(row)!=body.object_hash or not approved(p,COLLECTION,row):raise HTTPException(409,'收货摘要已变更或尚未有效独立复核')
        validate(p,row);review=next(r for r in reversed(p['reviews']) if r['collection']==COLLECTION and r['object_id']==rid)
        event=analyses.seal(dict(id=store.uid(),kind='receive',target_id=rid,record_hash=store.digest(row),quantities=row['result']['quantities'],review=copy.deepcopy(review),request_id=body.request_id,request_hash=digest,author=a['username'],created_at=store.now()))
        p[EVENTS].append(event);records(p);store.audit(c,p,a,'PROCUREMENT_RECEIPT_EXECUTED',dict(id=event['id'],receipt_id=rid));store.save(c,p);return event


@router.post('/projects/{pid}/procurement-receipts/{rid}/cancel')
def cancel(pid:str,rid:str,body:CancelIn,a=Depends(core.actor)):
    core.edit(a)
    with store.transaction() as c:
        p=core.project(c,pid,a);records(p);old,digest=retry(p,body,a,EVENTS,'cancel',rid)
        if old:return old
        row=core.find(p,COLLECTION,rid)
        if store.digest(row)!=body.object_hash or event_for(p,rid):raise HTTPException(409,'收货已变更、执行或取消，不能取消')
        event=analyses.seal(dict(id=store.uid(),kind='cancel',target_id=rid,record_hash=store.digest(row),reason=body.reason,evidence_id=body.evidence_id,proof=offers.proof(p,body.evidence_id),request_id=body.request_id,request_hash=digest,author=a['username'],created_at=store.now()))
        p[EVENTS].append(event);store.audit(c,p,a,'PROCUREMENT_RECEIPT_CANCELLED',dict(id=event['id'],receipt_id=rid));store.save(c,p);return event


@router.get('/projects/{pid}/procurement-receipts/{rid}/export')
def export(pid:str,rid:str,a=Depends(core.actor)):
    with store.transaction() as c:
        p=core.project(c,pid,a);row=core.find(p,COLLECTION,rid);view=status(p,row)
        payload=store.canonical(dict(schema='procurement-receipt-v1',project_id=pid,project_name=p['name'],**view,order_balance=remaining(p,row['result']['order']),reviews=[r for r in p['reviews'] if r['collection']==COLLECTION and r['object_id']==rid])).encode();sha=hashlib.sha256(payload).hexdigest();path=store.ROOT/'exports'/sha;path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()!=sha:raise HTTPException(409,'采购收货留档文件校验异常')
        if not path.exists():path.write_bytes(payload)
        artifact=dict(id=store.uid(),purpose='procurement_receipt',receipt_id=rid,format='json',quote_id='',quote_version=None,hash=sha,approved=view['review_valid'],timestamp=store.now(),actor=a['username'],media='application/json',suffix='json')
        c.execute('INSERT INTO artifacts VALUES(?,?,?,?)',(artifact['id'],pid,a['tenant'],store.canonical(artifact)));store.audit(c,p,a,'PROCUREMENT_RECEIPT_EXPORTED',artifact)
    return StreamingResponse(io.BytesIO(payload),media_type='application/json',headers={'Content-Disposition':'attachment; filename="QiangQuote-procurement-receipt.json"','X-Artifact-Hash':sha})
