"""Actual, reviewed returns of unchanged accepted offcuts to their original lot."""
from datetime import date, datetime
from fractions import Fraction
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException
from . import main as core, store, stock_inventory as inv, stock_offcut_reuse as reuse
from .compound_takeoff import text
from .engine import precise
from .stock_cutting import dec

router=APIRouter(prefix='/api/v1')
COLLECTION='stock_offcut_returns'
NOTICE=('仅引用本项目已执行的余料领用单，按逐分配在外余额登记未加工原段退回；须保持原长、同材质截面和质量合格，实际交接日期、接收人、验收说明及当前原件必填。'
        '切短、加工、损坏或遗失不能按原段退回；本流程不代表再加工、报废或冲销。提交、预览及独立复核不增加库存，显式执行后才按实际根数回到原余料编号及原库位。'
        '来源排料或旧交接依据失效不回滚已领用数量，实际退回可凭新有效原件记录；原验收或已执行退回验收依据失效时保留实物数量并整组待核查，禁止新增领用。'
        '已执行退回不能取消或重审；未执行可有据取消。退回不释放原领用材料表的再次领用额度，不改工程量、排料、报价、完整原材账或原加工核对额度。')


def closed(p,row):return inv.event_for(p,'offcut_return',row['id']) or inv.event_for(p,'offcut_return_cancel',row['id'])


def reviewable(p,row,actor):
    if row['author']==actor['username']:raise HTTPException(403,'余料原段退回须由另一独立账号复核')
    if closed(p,row):inv.fail('余料退回已执行或取消，仅供追溯，不能重审')


@precise
def normalize(p,raw):
    fields={'reference_id','reference_hash','selections','business_date','custodian','basis','evidence_id','confirmed'}
    if not isinstance(raw,dict) or set(raw)!=fields:raise HTTPException(422,'退回须完整填写已执行余料领用单、逐项根数、实际日期、接收人、验收说明、原件及原段确认')
    for k in ('reference_id','reference_hash','custodian','basis','evidence_id'):text(raw[k],'未加工原段退回验收说明' if k=='basis' else '退回来源、接收人或原件',2000 if k=='basis' else 200)
    if raw['confirmed'] is not True:raise HTTPException(422,'须据实确认未加工、保持原长、同材质截面且质量合格，不能将短料或损坏料按原段退回')
    try:
        d=date.fromisoformat(raw['business_date'])
        if d.isoformat()!=raw['business_date'] or d>datetime.now(ZoneInfo('Asia/Shanghai')).date():raise ValueError()
    except (TypeError,ValueError):raise HTTPException(422,'实际退回日期须为不晚于今天的YYYY-MM-DD') from None
    source=core.find(p,reuse.COLLECTION,raw['reference_id'])
    if not inv.event_for(p,'offcut_issue',source['id']):inv.fail('退回须引用已执行余料领用，预留或仅已审单不能退回')
    if store.digest(source)!=raw['reference_hash']:inv.fail('原余料领用单已变化，请刷新核对')
    if raw['business_date']<source['input']['business_date']:inv.fail('实际退回日期不得早于来源余料领用交接日期')
    selections=raw['selections']
    if not isinstance(selections,list) or not 1<=len(selections)<=100:raise HTTPException(422,'退回须选择1至100条不同领用分配及正整数根数')
    rows=[];seen=set();length=Fraction(0);mass=Fraction(0)
    for a in selections:
        if not isinstance(a,dict) or set(a)!={'allocation_index','quantity'} or type(a['allocation_index']) is not int or not 1<=a['allocation_index']<=len(source['allocations']) or a['allocation_index'] in seen or type(a['quantity']) is not int or not 1<=a['quantity']<=1000000:
            raise HTTPException(422,'须选择本单不同分配序号，根数为1至1000000正整数')
        seen.add(a['allocation_index']);parent=source['allocations'][a['allocation_index']-1]
        if a['quantity']>parent['quantity']:inv.fail('本次退回超过来源分配原领用根数')
        size=Fraction(parent['stock_length'])*a['quantity'];weight=size*Fraction(parent['unit_weight']);length+=size;mass+=weight
        rows.append(dict(**{k:v for k,v in parent.items() if k not in ('quantity','total_length','mass')},**a,total_length=dec(size),mass=dec(weight)))
    return dict(reference_id=source['id'],source_name=source['demand']['source_name'],source_revision=source['demand']['source_revision'],allocations=rows,
                totals=dict(quantity=sum(a['quantity'] for a in rows),length=dec(length),mass=dec(mass),length_unit='m',mass_unit='kg'))


def prepare(p,raw):
    inv.records(p);result=normalize(p,raw)
    from .stock_processing_results import balances as processing_balances
    _,lots=processing_balances(p);_,issues=reuse.balances(p,lots,with_issues=True)
    source=issues.get(result['reference_id'])
    if not source:inv.fail('来源余料领用执行关联无效')
    for a in result['allocations']:
        parent=source[a['allocation_index']]
        if a['quantity']>parent['quantity']-parent['returned']-parent['consumed']:inv.fail('退回超过该领用分配尚在外的原段余额，请刷新核对')
    return result,inv.proofs(p,[raw['evidence_id']])


def preview_hash(raw,result,stamp):return store.digest(dict(input=raw,result=result,proof_stamp=stamp))


def validate(p,row):
    inv.intact(row)
    if closed(p,row):inv.fail('余料退回已执行或取消，仅供追溯')
    result,stamp=prepare(p,row['input'])
    if any(row[k]!=v for k,v in result.items()) or row['proof_stamp']!=stamp or row['preview_hash']!=preview_hash(row['input'],result,stamp):inv.fail('退回输入、原件或预览已变化，请取消后重新核定')


def proof_issues(p):
    result={}
    for row in p[COLLECTION]:
        if not inv.event_for(p,'offcut_return',row['id']):continue
        try:
            if inv.proofs(p,[row['input']['evidence_id']])!=row['proof_stamp']:inv.fail('已执行退回验收原件已变化')
        except HTTPException:
            for a in row['allocations']:result[a['lot_key']]='已执行原段退回验收依据失效，数量保留，整组余料待核查，禁止新增领用'
    return result


def status(p):
    rows=[]
    for row in p[COLLECTION]:
        executed=inv.event_for(p,'offcut_return',row['id']);cancelled=inv.event_for(p,'offcut_return_cancel',row['id'])
        try:
            if executed:
                if inv.proofs(p,[row['input']['evidence_id']])!=row['proof_stamp']:inv.fail('历史退回原件已变化，实物数量保留待核查')
            elif not cancelled:validate(p,row)
            issue=''
        except HTTPException as e:issue=str(e.detail)
        rows.append(dict(record=row,object_hash=store.digest(row),phase='executed' if executed else 'cancelled' if cancelled else row['state'],issue=issue))
    return dict(records=rows,notice=NOTICE)


@router.post('/projects/{pid}/stock-inventory/offcut-returns/preview')
def preview(pid:str,body:dict,a=Depends(core.actor)):
    if not isinstance(body,dict) or set(body)!={'input'}:raise HTTPException(422,'退回预览仅接受原始退回输入')
    with store.connect() as c:
        p=core.project(c,pid,a);result,stamp=prepare(p,body['input'])
        return dict(result=result,preview_hash=preview_hash(body['input'],result,stamp),ledger_hash=inv.ledger_hash(p),notice=NOTICE)


@router.post('/projects/{pid}/stock-inventory/offcut-returns')
def submit(pid:str,body:dict,a=Depends(core.actor)):
    core.edit(a);inv.fields(body,{'input','preview_hash'})
    with store.transaction() as c:
        p=core.project(c,pid,a);old=inv.begin(p,body)
        if old:
            if old not in p[COLLECTION]:inv.fail('请求标识已用于其他库存操作')
            return old
        result,stamp=prepare(p,body['input'])
        if body['preview_hash']!=preview_hash(body['input'],result,stamp):inv.fail('退回预览已变化，请重新预览')
        return inv.commit(c,p,a,COLLECTION,body,dict(input=body['input'],**result,proof_stamp=stamp,preview_hash=body['preview_hash'],state='draft'),'STOCK_OFFCUT_RETURN_SUBMITTED')


@router.post('/projects/{pid}/stock-inventory/offcut-returns/{rid}/{operation}')
def movement(pid:str,rid:str,operation:str,body:dict,a=Depends(core.actor)):
    core.edit(a)
    if operation not in ('execute','cancel'):raise HTTPException(422,'仅支持执行已审退回或有据取消未执行退回')
    inv.fields(body,{'object_hash'} if operation=='execute' else {'object_hash','basis','evidence_id'})
    kind='offcut_return' if operation=='execute' else 'offcut_return_cancel'
    with store.transaction() as c:
        p=core.project(c,pid,a);old=inv.begin(p,body)
        if old:
            if old.get('operation')!=kind or old.get('target_id')!=rid:inv.fail('请求标识已用于其他余料退回操作')
            return old
        row=core.find(p,COLLECTION,rid)
        if body['object_hash']!=store.digest(row):inv.fail('余料退回单已变化，请刷新核对')
        if closed(p,row):inv.fail('余料退回已执行或取消，不能重复记账、取消或冲销')
        data=dict(operation=kind,target_id=rid,record_hash=store.digest(row))
        if operation=='execute':
            if not inv.approved(p,COLLECTION,row):inv.fail('须有效独立复核后才能执行余料退回')
            validate(p,row);data.update(reference_id=row['reference_id'],allocations=row['allocations'],totals=row['totals'])
        else:
            text(body['basis'],'未执行退回取消原因');data.update(basis=body['basis'],evidence_id=body['evidence_id'],proof_stamp=inv.proofs(p,[body['evidence_id']]))
        return inv.commit(c,p,a,'stock_events',body,data,'STOCK_'+kind.upper())
