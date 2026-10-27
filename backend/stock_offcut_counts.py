"""Evidence-backed physical counts of existing offcut groups; additive adjustments only."""
from datetime import date,datetime
from fractions import Fraction
from zoneinfo import ZoneInfo
from fastapi import APIRouter,Depends,HTTPException
from . import main as core,store,stock_inventory as inv
from .compound_takeoff import text
from .stock_cutting import dec
from .engine import precise

router=APIRouter(prefix='/api/v1')
COLLECTION='stock_offcut_counts'
NOTICE=('仅盘点本项目已验收且未停用的现有余料组在库原段，不盘点完整原材、领用在外、在加工或已加工数量。'
        '按原编号、位置、材质截面、每段长度及每米质量现场核对，填写实盘根数（可为0）、当日日期、盘点人、盘点原件及差异核查原件。'
        '预览、保存和独立复核不调整；显式执行才追加盘盈／盘亏或无差异确认事件，保留原始验收和全部领用加工历史。'
        '该组实物流转或另一次盘点发生后，旧快照不得执行；实盘根数不得少于未释放预留，不自动释放或改配。'
        '盘点及复核执行须同一中国标准时间日期，过日须取消并重新现场核对。已执行不能重审、取消或冲销。'
        '盘盈仅核定同组现有原段，不是采购、新规格入库或无依据找平。历史依据失效保留调整事实，并隔离该组及后代余料的可用量，不能用新盘点绕过来源问题。')
@precise
def signed(value):return ('-' if value<0 else '')+dec(abs(value))


COUNTERS=('issued_quantity','returned_quantity','processed_quantity','processing_quantity','adjustment_gain','adjustment_loss','count_revision')


def today():return datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()


def snapshot(lot,m):
    return store.digest(dict(lot={k:lot[k] for k in ('lot_key','lot_code','warehouse','material_spec','length','unit_weight','quantity')},movements={k:m.get(k,0) for k in COUNTERS}))


def normalize(raw,lot,m):
    fields={'lot_key','lot_snapshot','counted_quantity','business_date','custodian','basis','count_evidence_id','variance_evidence_id','confirmed'}
    if not isinstance(raw,dict) or set(raw)!=fields:raise HTTPException(422,'须填写余料组快照、实盘根数、当日日期、盘点人、差异核查说明及两项原件和确认')
    for k in ('lot_key','lot_snapshot','custodian','basis','count_evidence_id','variance_evidence_id'):text(raw[k],'盘点说明' if k=='basis' else '盘点来源、盘点人或原件',2000 if k=='basis' else 200)
    if type(raw['counted_quantity']) is not int or not 0<=raw['counted_quantity']<=1000000:raise HTTPException(422,'实盘根数须为0至1000000整数，不接受小数、布尔值或空白')
    if raw['confirmed'] is not True:raise HTTPException(422,'须确认按实物核对现有同编号、位置、材质截面、段长及质量的在库原段，差异有据')
    try:
        if date.fromisoformat(raw['business_date']).isoformat()!=raw['business_date']:raise ValueError()
    except (ValueError,TypeError):raise HTTPException(422,'盘点日期须为YYYY-MM-DD') from None
    if raw['lot_key']!=lot['lot_key'] or raw['lot_snapshot']!=snapshot(lot,m):inv.fail('余料实物流转或盘点快照已变化，请取消旧单并重新现场核对')
    before=lot['quantity']+m.get('adjustment_gain',0)-m.get('adjustment_loss',0)-m['issued_quantity']+m['returned_quantity']
    delta=raw['counted_quantity']-before
    return dict(lot_key=lot['lot_key'],lot_code=lot['lot_code'],warehouse=lot['warehouse'],material_spec=lot['material_spec'],length=lot['length'],unit_weight=lot['unit_weight'],before=before,after=raw['counted_quantity'],delta=delta,length_delta=signed(Fraction(lot['length'])*delta),mass_delta=signed(Fraction(lot['length'])*Fraction(lot['unit_weight'])*delta))


def closed(p,row):return inv.event_for(p,'offcut_count',row['id']) or inv.event_for(p,'offcut_count_cancel',row['id'])


def reviewable(p,row,actor):
    if row['author']==actor['username']:raise HTTPException(403,'余料盘点差异须由另一独立账号复核')
    if closed(p,row):inv.fail('盘点已执行或取消，仅供追溯，不能重审')


def proof_ids(raw):return [raw['count_evidence_id'],raw['variance_evidence_id']]


def prepare(p,raw):
    inv.records(p)
    if not isinstance(raw,dict) or not isinstance(raw.get('lot_key'),str):raise HTTPException(422,'须选择本项目余料组')
    from .stock_processing_results import status as stock_status
    lots=stock_status(p)['lots'];lot=next((l for l in lots if l['lot_key']==raw['lot_key']),None)
    if not lot:inv.fail('余料组不属于本项目或尚未验收入库')
    result=normalize(raw,lot,lot)
    if raw['business_date']!=today():inv.fail('须当日现场盘点并在同一中国标准时间日期完成复核执行，过日请取消后重新核对')
    if not lot['verified']:inv.fail('余料已停用或历史依据待核查，不能通过盘点恢复可用量')
    if result['after']<lot['reserved']:inv.fail('实盘根数小于未释放预留，请先据实核查并显式释放或改配，不能覆盖已有占用')
    return result,inv.proofs(p,proof_ids(raw))


def preview_hash(raw,result,stamp):return store.digest(dict(input=raw,result=result,proof_stamp=stamp))


def validate(p,row):
    inv.intact(row)
    if closed(p,row):inv.fail('盘点已执行或取消，仅供追溯')
    result,stamp=prepare(p,row['input'])
    if any(row[k]!=v for k,v in result.items()) or row['proof_stamp']!=stamp or row['preview_hash']!=preview_hash(row['input'],result,stamp):inv.fail('盘点、原件或预览已变化，请取消后重新现场核对')


def proof_issues(p):
    issues={}
    for row in p[COLLECTION]:
        if inv.event_for(p,'offcut_count',row['id']):
            try:
                if inv.proofs(p,proof_ids(row['input']))!=row['proof_stamp']:inv.fail('盘点或差异核查原件已变化')
            except HTTPException as e:issues[row['lot_key']]='盘点依据待核查，历史调整保留，可用量隔离：'+str(e.detail)
    return issues


def status(p):
    from .stock_processing_results import status as stock_status
    lots=stock_status(p)['lots'];issues=proof_issues(p);rows=[]
    for row in p[COLLECTION]:
        executed=inv.event_for(p,'offcut_count',row['id']);cancelled=inv.event_for(p,'offcut_count_cancel',row['id'])
        try:
            if executed:issue=issues.get(row['lot_key'],'')
            elif not cancelled:validate(p,row);issue=''
            else:issue=''
        except HTTPException as e:issue=str(e.detail)
        rows.append(dict(record=row,object_hash=store.digest(row),phase='executed' if executed else 'cancelled' if cancelled else row['state'],issue=issue))
    return dict(records=rows,notice=NOTICE,lots=[dict(lot_key=l['lot_key'],lot_snapshot=snapshot(l,l)) for l in lots if l['verified']])


@router.post('/projects/{pid}/stock-inventory/offcut-counts/preview')
def preview(pid:str,body:dict,a=Depends(core.actor)):
    if not isinstance(body,dict) or set(body)!={'input'}:raise HTTPException(422,'盘点预览仅接受原始输入')
    with store.connect() as c:
        p=core.project(c,pid,a);result,stamp=prepare(p,body['input']);return dict(result=result,preview_hash=preview_hash(body['input'],result,stamp),ledger_hash=inv.ledger_hash(p),notice=NOTICE)


@router.post('/projects/{pid}/stock-inventory/offcut-counts')
def submit(pid:str,body:dict,a=Depends(core.actor)):
    core.edit(a);inv.fields(body,{'input','preview_hash'})
    with store.transaction() as c:
        p=core.project(c,pid,a);old=inv.begin(p,body)
        if old:
            if old not in p[COLLECTION]:inv.fail('请求标识已用于其他库存操作')
            return old
        result,stamp=prepare(p,body['input'])
        if body['preview_hash']!=preview_hash(body['input'],result,stamp):inv.fail('盘点预览已变化，请重新核对')
        return inv.commit(c,p,a,COLLECTION,body,dict(input=body['input'],**result,proof_stamp=stamp,preview_hash=body['preview_hash'],state='draft'),'STOCK_OFFCUT_COUNT_SUBMITTED')


@router.post('/projects/{pid}/stock-inventory/offcut-counts/{rid}/{operation}')
def movement(pid:str,rid:str,operation:str,body:dict,a=Depends(core.actor)):
    core.edit(a)
    if operation not in ('execute','cancel'):raise HTTPException(422,'仅支持显式执行盘点或有据取消未执行单')
    inv.fields(body,{'object_hash'} if operation=='execute' else {'object_hash','basis','evidence_id'});kind='offcut_count' if operation=='execute' else 'offcut_count_cancel'
    with store.transaction() as c:
        p=core.project(c,pid,a);old=inv.begin(p,body)
        if old:
            if old.get('operation')!=kind or old.get('target_id')!=rid:inv.fail('请求标识已用于其他盘点操作')
            return old
        row=core.find(p,COLLECTION,rid)
        if store.digest(row)!=body['object_hash']:inv.fail('盘点单已变化，请刷新核对')
        if closed(p,row):inv.fail('盘点已执行或取消，不能重复执行、取消或冲销')
        data=dict(operation=kind,target_id=rid,record_hash=store.digest(row))
        if operation=='execute':
            if not inv.approved(p,COLLECTION,row):inv.fail('须有效独立复核后才能执行盘点调整')
            validate(p,row);data.update(lot_key=row['lot_key'],before=row['before'],after=row['after'],delta=row['delta'],length_delta=row['length_delta'],mass_delta=row['mass_delta'])
        else:
            text(body['basis'],'未执行盘点取消原因');data.update(basis=body['basis'],evidence_id=body['evidence_id'],proof_stamp=inv.proofs(p,[body['evidence_id']]))
        return inv.commit(c,p,a,'stock_events',body,data,'STOCK_'+kind.upper())
