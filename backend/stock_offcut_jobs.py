"""Actual offcut machining starts and quantity-based work-in-progress tracking."""
from datetime import date,datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter,Depends,HTTPException
from . import main as core,store,stock_inventory as inv,stock_offcut_reuse as reuse
from .compound_takeoff import text
from .stock_operations import totals

router=APIRouter(prefix='/api/v1')
COLLECTION='stock_offcut_jobs'
NOTICE=('按已执行余料领用单逐分配未加工且未退回余额，登记实际投入加工根数、日期、地点、接收人或班组、说明及当前原件。'
        '预览、保存和独立复核不记账，显式执行后才将未加工原段转为在加工待验收；不自动生成成品、损耗或新余料。'
        '后续在专门结果入口引用已执行投入单，可分批实测验收，仅结转本单已投入且尚未验收数量，不再次扣减未加工余额。'
        '已投入不能再按未加工原段退回，不能通过取消、释放或来源失效放回数量。原件失效保留在加工数量并提示待核查；实际后续结果须新的有效原件，关联新余料的采用仍检查投入依据。'
        '未执行可有据取消，已执行及已取消不能重审；当前按数量显示待验收、部分验收及已验收，不跟踪设备、工时或自动百分比，不支持执行冲销、跨项目调拨或采购闭环。')


def closed(p,row):return inv.event_for(p,'offcut_start',row['id']) or inv.event_for(p,'offcut_start_cancel',row['id'])


def reviewable(p,row,actor):
    if row['author']==actor['username']:raise HTTPException(403,'实际余料投入加工须由另一独立账号复核')
    if closed(p,row):inv.fail('余料投入加工已执行或取消，仅供追溯，不能重审')


def normalize(p,raw):
    fields={'reference_id','reference_hash','selections','business_date','custodian','workshop','basis','evidence_id','confirmed'}
    if not isinstance(raw,dict) or set(raw)!=fields:raise HTTPException(422,'须填写已执行领用、逐项根数、实际投入日期、加工地点、接收人、实际投入说明及原件和确认')
    for k in ('reference_id','reference_hash','custodian','workshop','basis','evidence_id'):text(raw[k],'实际投入加工范围说明' if k=='basis' else '投入来源、地点、接收人或原件',2000 if k=='basis' else 200)
    if raw['confirmed'] is not True:raise HTTPException(422,'须据原件确认原段实际已投入加工，不以计划或预留替代实物')
    try:
        d=date.fromisoformat(raw['business_date'])
        if d.isoformat()!=raw['business_date'] or d>datetime.now(ZoneInfo('Asia/Shanghai')).date():raise ValueError()
    except (TypeError,ValueError):raise HTTPException(422,'实际投入日期须为不晚于今天的YYYY-MM-DD') from None
    source=reuse.issue_source(p,raw['reference_id'])
    if store.digest(source)!=raw['reference_hash']:inv.fail('余料领用快照已变化，请刷新核对')
    if raw['business_date']<source['input']['business_date']:inv.fail('实际投入日期不得早于余料领用交接日期')
    selections=raw['selections']
    if not isinstance(selections,list) or not 1<=len(selections)<=100:raise HTTPException(422,'每单须1至100条不同来源分配和正整数投入根数')
    rows=[];seen=set()
    for a in selections:
        if not isinstance(a,dict) or set(a)!={'allocation_index','quantity'} or type(a['allocation_index']) is not int or a['allocation_index'] not in reuse.issue_allocations(source) or a['allocation_index'] in seen or type(a['quantity']) is not int or not 1<=a['quantity']<=1000000:raise HTTPException(422,'投入须选择不同的本单分配序号，根数为1至1000000正整数')
        seen.add(a['allocation_index']);parent=reuse.issue_allocations(source)[a['allocation_index']]
        if a['quantity']>parent['quantity']:inv.fail('实际投入超过原领用分配根数')
        item={**parent,**a};measure=totals([item]);item.update(total_length=measure['length'],mass=measure['mass']);rows.append(item)
    return dict(reference_id=source['id'],source_name=source['demand']['source_name'],source_revision=source['demand']['source_revision'],allocations=rows,totals=totals(rows))


def prepare(p,raw):
    inv.records(p);result=normalize(p,raw)
    from .stock_processing_results import balances
    from .stock_offcut_ledger import replay
    _,lots=balances(p);source=replay(p,lots)['issues'].get(result['reference_id'])
    if not source:inv.fail('来源领用缺少可核验的实际执行')
    for a in result['allocations']:
        parent=source[a['allocation_index']]
        if a['quantity']>parent['quantity']-parent['returned']-parent['consumed']-parent['processing']:inv.fail('实际投入超过该分配尚未加工且未退回余额，请刷新核对')
    return result,inv.proofs(p,[raw['evidence_id']])


def preview_hash(raw,result,stamp):return store.digest(dict(input=raw,result=result,proof_stamp=stamp))


def validate(p,row):
    inv.intact(row)
    if closed(p,row):inv.fail('余料投入加工已执行或取消，仅供追溯')
    result,stamp=prepare(p,row['input'])
    if any(row[k]!=v for k,v in result.items()) or row['proof_stamp']!=stamp or row['preview_hash']!=preview_hash(row['input'],result,stamp):inv.fail('实际投入、原件或预览已变化，请取消后重新核定')


def proof_issue(p,row):
    try:
        if inv.proofs(p,[row['input']['evidence_id']])!=row['proof_stamp']:inv.fail('投入加工原件已变化')
        return reuse.batch_proof_issue(p,row['reference_id'])
    except HTTPException as e:return '实际投入依据待核查，在加工及已验收数量保留：'+str(e.detail)


def status(p):
    from .stock_processing_results import balances
    from .stock_offcut_ledger import replay
    _,lots=balances(p);jobs=replay(p,lots)['jobs'];result=[]
    for row in p[COLLECTION]:
        executed=inv.event_for(p,'offcut_start',row['id']);cancelled=inv.event_for(p,'offcut_start_cancel',row['id'])
        try:
            if executed:issue=proof_issue(p,row)
            elif not cancelled:validate(p,row);issue=''
            else:issue=''
        except HTTPException as e:issue=str(e.detail)
        allocations=[dict(**a,remaining=a['quantity']-a['accounted']) for a in jobs.get(row['id'],{}).values()]
        remaining=sum(a['remaining'] for a in allocations);accounted=sum(a['accounted'] for a in allocations)
        result.append(dict(record=row,object_hash=store.digest(row),phase='executed' if executed else 'cancelled' if cancelled else row['state'],issue=issue,allocations=allocations,remaining=remaining,accounted=accounted,progress='complete' if executed and not remaining else 'partial' if accounted else 'pending'))
    return dict(records=result,notice=NOTICE)


@router.post('/projects/{pid}/stock-inventory/offcut-jobs/preview')
def preview(pid:str,body:dict,a=Depends(core.actor)):
    if not isinstance(body,dict) or set(body)!={'input'}:raise HTTPException(422,'实际投入预览仅接受原始输入')
    with store.connect() as c:
        p=core.project(c,pid,a);result,stamp=prepare(p,body['input']);return dict(result=result,preview_hash=preview_hash(body['input'],result,stamp),ledger_hash=inv.ledger_hash(p),notice=NOTICE)


@router.post('/projects/{pid}/stock-inventory/offcut-jobs')
def submit(pid:str,body:dict,a=Depends(core.actor)):
    core.edit(a);inv.fields(body,{'input','preview_hash'})
    with store.transaction() as c:
        p=core.project(c,pid,a);old=inv.begin(p,body)
        if old:
            if old not in p[COLLECTION]:inv.fail('请求标识已用于其他库存操作')
            return old
        result,stamp=prepare(p,body['input'])
        if body['preview_hash']!=preview_hash(body['input'],result,stamp):inv.fail('实际投入预览已变化，请重新核对')
        return inv.commit(c,p,a,COLLECTION,body,dict(input=body['input'],**result,proof_stamp=stamp,preview_hash=body['preview_hash'],state='draft'),'STOCK_OFFCUT_JOB_SUBMITTED')


@router.post('/projects/{pid}/stock-inventory/offcut-jobs/{rid}/{operation}')
def movement(pid:str,rid:str,operation:str,body:dict,a=Depends(core.actor)):
    core.edit(a)
    if operation not in ('execute','cancel'):raise HTTPException(422,'仅支持执行实际投入或有据取消未执行单')
    inv.fields(body,{'object_hash'} if operation=='execute' else {'object_hash','basis','evidence_id'});kind='offcut_start' if operation=='execute' else 'offcut_start_cancel'
    with store.transaction() as c:
        p=core.project(c,pid,a);old=inv.begin(p,body)
        if old:
            if old.get('operation')!=kind or old.get('target_id')!=rid:inv.fail('请求标识已用于其他投入加工操作')
            return old
        row=core.find(p,COLLECTION,rid)
        if store.digest(row)!=body['object_hash']:inv.fail('实际投入单已变化，请刷新核对')
        if closed(p,row):inv.fail('实际投入单已执行或取消，不能再次执行、取消或冲销')
        data=dict(operation=kind,target_id=rid,record_hash=store.digest(row))
        if operation=='execute':
            if not inv.approved(p,COLLECTION,row):inv.fail('须有效独立复核后才能执行实际投入加工')
            validate(p,row);data.update(reference_id=row['reference_id'],allocations=row['allocations'],totals=row['totals'])
        else:
            text(body['basis'],'未执行投入取消原因');data.update(basis=body['basis'],evidence_id=body['evidence_id'],proof_stamp=inv.proofs(p,[body['evidence_id']]))
        return inv.commit(c,p,a,'stock_events',body,data,'STOCK_'+kind.upper())
