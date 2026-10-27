"""Reviewed partial offcut handovers with independent batch custody sources."""
from datetime import date,datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter,Depends,HTTPException
from . import main as core,store,stock_inventory as inv,stock_offcut_reuse as reuse
from .compound_takeoff import text
from .stock_operations import totals

router=APIRouter(prefix='/api/v1')
COLLECTION='stock_offcut_issues'
NOTICE=('仅从本项目当前有效已审余料预留，按原分配序号登记本次实际交接根数、日期、接收人和当前原件。'
        '预览、保存和独立复核不出库、不新增占用；显式执行只将本次数量从原预留未领占用转为本批在外数量，未领部分继续占用。'
        '每批原段退回、本批领用及再加工须引用本批单及原分配序号，不借用其他批次或未领余额。'
        '已领批单不能取消或重审；原预留首次领出后不能重审、整单领用或整单释放，可有据释放未领余量，不退回已领数量，也不重开全量需求；已释放未领额补料须走独立余料补料入口。'
        '来源失效保留已领事实及未领占用，新领用阻断；历史交接原件失效提示待核查，实际后续交接仍须有效新原件。'
        '仅完整原段按核定定尺交接，不自动替换排料、混配完整原材、采购或跨项目调拨。')


def closed(p,row):return inv.event_for(p,'offcut_partial_issue',row['id']) or inv.event_for(p,'offcut_partial_cancel',row['id'])


def reviewable(p,row,actor):
    if row['author']==actor['username']:raise HTTPException(403,'余料分批领用须由另一独立账号复核')
    if closed(p,row):inv.fail('余料分批领用已执行或取消，仅供追溯，不能重审')


def selection_values(source,selections):
    if not isinstance(selections,list) or not 1<=len(selections)<=100:raise HTTPException(422,'须选择1至100条不同原余料预留分配及正整数根数')
    rows=[];seen=set()
    for a in selections:
        if not isinstance(a,dict) or set(a)!={'allocation_index','quantity'} or type(a['allocation_index']) is not int or not 1<=a['allocation_index']<=len(source['allocations']) or a['allocation_index'] in seen or type(a['quantity']) is not int or not 1<=a['quantity']<=1000000:raise HTTPException(422,'须选择不同的原预留分配序号，根数为1至1000000正整数')
        seen.add(a['allocation_index']);parent=source['allocations'][a['allocation_index']-1]
        if a['quantity']>parent['quantity']:inv.fail('本批根数超过原预留分配')
        item={**parent,**a};measure=totals([item]);item.update(total_length=measure['length'],mass=measure['mass']);rows.append(item)
    return rows,totals(rows)


def normalize(p,raw):
    fields={'reservation_id','reservation_hash','selections','business_date','custodian','basis','evidence_id','confirmed'}
    if not isinstance(raw,dict) or set(raw)!=fields:raise HTTPException(422,'须填写原余料预留、逐项实际根数、交接日期、接收人、说明及原件和确认')
    for k in ('reservation_id','reservation_hash','custodian','basis','evidence_id'):text(raw[k],'本批实际交接说明' if k=='basis' else '领用来源、接收人或原件',2000 if k=='basis' else 200)
    if raw['confirmed'] is not True:raise HTTPException(422,'须据原件确认本批完整余料原段实际交接，不以计划或预留替代')
    try:
        d=date.fromisoformat(raw['business_date'])
        if d.isoformat()!=raw['business_date'] or d>datetime.now(ZoneInfo('Asia/Shanghai')).date():raise ValueError()
    except (TypeError,ValueError):raise HTTPException(422,'实际交接日期须为不晚于今天的YYYY-MM-DD') from None
    source=core.find(p,reuse.COLLECTION,raw['reservation_id'])
    if store.digest(source)!=raw['reservation_hash']:inv.fail('原余料预留已变化，请刷新核对')
    if raw['business_date']<source['input']['business_date']:inv.fail('分批交接日期不得早于原预留核定日期')
    rows,measures=selection_values(source,raw['selections'])
    return dict(reservation_id=source['id'],source_id=source['source_id'],demand=source['demand'],allocations=rows,totals=measures)


def prepare(p,raw):
    inv.records(p);result=normalize(p,raw);source=core.find(p,reuse.COLLECTION,result['reservation_id'])
    if not inv.approved(p,reuse.COLLECTION,source):inv.fail('原余料预留须有效独立复核后才能分批领用')
    reuse.validate(p,source,allow_partial=True)
    left={a['allocation_index']:a['remaining'] for a in reuse.progress(p,source)}
    if any(a['quantity']>left[a['allocation_index']] for a in result['allocations']):inv.fail('本批根数超过对应原余料预留未领余额')
    return result,inv.proofs(p,[raw['evidence_id']])


def preview_hash(raw,result,stamp):return store.digest(dict(input=raw,result=result,proof_stamp=stamp))


def validate(p,row):
    inv.intact(row)
    if closed(p,row):inv.fail('余料分批领用已执行或取消，仅供追溯')
    result,stamp=prepare(p,row['input'])
    if any(row[k]!=v for k,v in result.items()) or row['proof_stamp']!=stamp or row['preview_hash']!=preview_hash(row['input'],result,stamp):inv.fail('本批领用、原件或预览已变化，请取消后重新核定')


def proof_issue(p,row):
    try:
        if inv.proofs(p,[row['input']['evidence_id']])!=row['proof_stamp']:inv.fail('本批交接原件已变化')
        return ''
    except HTTPException as e:return '本批交接依据待核查，已领及后续数量保留：'+str(e.detail)


def status(p):
    from .stock_processing_results import balances
    from .stock_offcut_ledger import replay
    _,lots=balances(p);issues=replay(p,lots)['issues'];result=[]
    for row in p[COLLECTION]:
        executed=inv.event_for(p,'offcut_partial_issue',row['id']);cancelled=inv.event_for(p,'offcut_partial_cancel',row['id'])
        try:
            if executed:issue=proof_issue(p,row)
            elif not cancelled:validate(p,row);issue=''
            else:issue=''
        except HTTPException as e:issue=str(e.detail)
        allocations=[{**a,'allocation_index':i,'outstanding':a['quantity']-a['returned']-a['consumed']-a['processing']} for i,a in issues.get(row['id'],{}).items()]
        result.append(dict(record=row,object_hash=store.digest(row),phase='issued' if executed else 'cancelled' if cancelled else row['state'],issue=issue,balances=allocations))
    return dict(records=result,notice=NOTICE)


@router.post('/projects/{pid}/stock-inventory/offcut-issues/preview')
def preview(pid:str,body:dict,a=Depends(core.actor)):
    if not isinstance(body,dict) or set(body)!={'input'}:raise HTTPException(422,'本批领用预览仅接受原始输入')
    with store.connect() as c:
        p=core.project(c,pid,a);result,stamp=prepare(p,body['input']);return dict(result=result,preview_hash=preview_hash(body['input'],result,stamp),ledger_hash=inv.ledger_hash(p),notice=NOTICE)


@router.post('/projects/{pid}/stock-inventory/offcut-issues')
def submit(pid:str,body:dict,a=Depends(core.actor)):
    core.edit(a);inv.fields(body,{'input','preview_hash'})
    with store.transaction() as c:
        p=core.project(c,pid,a);old=inv.begin(p,body)
        if old:
            if old not in p[COLLECTION]:inv.fail('请求标识已用于其他库存操作')
            return old
        result,stamp=prepare(p,body['input'])
        if body['preview_hash']!=preview_hash(body['input'],result,stamp):inv.fail('本批领用预览已变化，请重新核对')
        return inv.commit(c,p,a,COLLECTION,body,dict(input=body['input'],**result,proof_stamp=stamp,preview_hash=body['preview_hash'],state='draft'),'STOCK_OFFCUT_ISSUE_SUBMITTED')


@router.post('/projects/{pid}/stock-inventory/offcut-issues/{rid}/{operation}')
def movement(pid:str,rid:str,operation:str,body:dict,a=Depends(core.actor)):
    core.edit(a)
    if operation not in ('execute','cancel'):raise HTTPException(422,'仅支持执行本批领用或有据取消未执行单')
    inv.fields(body,{'object_hash'} if operation=='execute' else {'object_hash','basis','evidence_id'});kind='offcut_partial_issue' if operation=='execute' else 'offcut_partial_cancel'
    with store.transaction() as c:
        p=core.project(c,pid,a);old=inv.begin(p,body)
        if old:
            if old.get('operation')!=kind or old.get('target_id')!=rid:inv.fail('请求标识已用于其他分批领用操作')
            return old
        row=core.find(p,COLLECTION,rid)
        if store.digest(row)!=body['object_hash']:inv.fail('本批领用单已变化，请刷新核对')
        if closed(p,row):inv.fail('本批领用单已执行或取消，不能再次执行、取消或冲销')
        data=dict(operation=kind,target_id=rid,record_hash=store.digest(row))
        if operation=='execute':
            if not inv.approved(p,COLLECTION,row):inv.fail('须有效独立复核后才能执行余料分批领用')
            validate(p,row);data.update(reservation_id=row['reservation_id'],allocations=row['allocations'],totals=row['totals'])
        else:
            text(body['basis'],'未执行分批领用取消原因');data.update(basis=body['basis'],evidence_id=body['evidence_id'],proof_stamp=inv.proofs(p,[body['evidence_id']]))
        return inv.commit(c,p,a,'stock_events',body,data,'STOCK_'+kind.upper())
