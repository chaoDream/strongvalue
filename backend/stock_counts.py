"""Evidence-backed physical counts of existing full-bar receipts; additive adjustments only."""
from datetime import date
from fractions import Fraction
from fastapi import APIRouter,Depends,HTTPException
from . import main as core,store,stock_inventory as inv
from .compound_takeoff import text

router=APIRouter(prefix='/api/v1')
COLLECTION='stock_counts'
NOTICE=('仅盘点本项目已独立审定且未停用的现有完整原材批次在库完整根，不盘点余料、领用在外或已加工数量。'
        '按原批号、库位、材质截面、定尺及每米质量现场核对，填写实盘根数（可为0）、当日日期、盘点人、盘点原件及差异核查原件。'
        '预览、保存和独立复核不调整；显式执行才追加盘盈／盘亏或无差异确认事件，保留原登记和领用加工历史。'
        '本批实物流转或另一次盘点后旧快照失效，即使在库根数恢复也须重盘；实盘不能少于未释放预留，不自动改配或释放。'
        '须在同一中国标准时间日期完成现场盘点、复核及执行，过日取消后重新核对；已执行不能取消、重审或冲销。'
        '盘盈仅核查本批同规格完整原材漏记，不是采购、新规格入库或计划找平。历史盘点原件失效保留调整事实，隔离本批及来源余料和后代可用量，不能用新盘点绕过。')
from .stock_offcut_counts import signed,proof_ids,today
COUNTERS=('issued','returned','consumed','adjustment_gain','adjustment_loss','count_revision')


def snapshot(receipt,m):
    return store.digest(dict(receipt_id=receipt['id'],registration=receipt['content_hash'],movements={k:m.get(k,0) for k in COUNTERS}))


def normalize(raw,receipt,m):
    fields={'receipt_id','receipt_snapshot','counted_quantity','business_date','custodian','basis','count_evidence_id','variance_evidence_id','confirmed'}
    if not isinstance(raw,dict) or set(raw)!=fields:raise HTTPException(422,'须填写完整原材批次快照、实盘根数、当日日期、盘点人、差异说明及两项原件和确认')
    for k in ('receipt_id','receipt_snapshot','custodian','basis','count_evidence_id','variance_evidence_id'):text(raw[k],'盘点说明' if k=='basis' else '完整原材来源、盘点人或原件',2000 if k=='basis' else 200)
    if type(raw['counted_quantity']) is not int or not 0<=raw['counted_quantity']<=1000000:raise HTTPException(422,'实盘根数须为0至1000000整数，不接受小数、布尔值或空白')
    if raw['confirmed'] is not True:raise HTTPException(422,'须确认同批号库位、材质截面、定尺及每米质量的在库完整根，差异有据，不含在外或已加工数量')
    try:
        if date.fromisoformat(raw['business_date']).isoformat()!=raw['business_date']:raise ValueError()
    except (ValueError,TypeError):raise HTTPException(422,'盘点日期须为YYYY-MM-DD') from None
    if raw['receipt_id']!=receipt['id'] or raw['receipt_snapshot']!=snapshot(receipt,m):inv.fail('完整原材实物流转或盘点快照已变化，请取消旧单并重新现场核对')
    n=receipt['normalized'];before=n['quantity']+m.get('adjustment_gain',0)-m.get('adjustment_loss',0)-m['issued']+m['returned'];delta=raw['counted_quantity']-before
    return dict(receipt_id=receipt['id'],lot_code=receipt['input']['lot_code'],warehouse=receipt['input']['warehouse'],material_spec=receipt['input']['material_spec'],length=n['stock_length'],unit_weight=n['unit_weight'],before=before,after=raw['counted_quantity'],delta=delta,length_delta=signed(Fraction(n['stock_length'])*delta),mass_delta=signed(Fraction(n['stock_length'])*Fraction(n['unit_weight'])*delta))


def closed(p,row):return inv.event_for(p,'stock_count',row['id']) or inv.event_for(p,'stock_count_cancel',row['id'])


def reviewable(p,row,actor):
    if row['author']==actor['username']:raise HTTPException(403,'完整原材盘点差异须由另一独立账号复核')
    if closed(p,row):inv.fail('盘点已执行或取消，仅供追溯，不能重审')


def prepare(p,raw):
    inv.records(p)
    if not isinstance(raw,dict) or not isinstance(raw.get('receipt_id'),str):raise HTTPException(422,'须选择本项目完整原材批次')
    receipt=core.find(p,'stock_receipts',raw['receipt_id'])
    from .stock_operations import balances
    stock,_=balances(p);result=normalize(raw,receipt,stock[receipt['id']])
    if raw['business_date']!=today():inv.fail('须当日现场盘点并在同一中国标准时间日期完成复核执行，过日请取消后重新核对')
    inv.validate_receipt(p,receipt)
    if not inv.approved(p,'stock_receipts',receipt):inv.fail('完整原材登记须有效独立复核后才能盘点')
    if result['after']<inv.held(p,receipt['id']):inv.fail('实盘根数小于未释放预留，请先据实核查并显式释放或改配，不能覆盖已有占用')
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
        if inv.event_for(p,'stock_count',row['id']):
            try:
                if inv.proofs(p,proof_ids(row['input']))!=row['proof_stamp']:inv.fail('盘点或差异核查原件已变化')
            except HTTPException as e:issues[row['receipt_id']]='完整原材盘点依据待核查，历史调整保留，本批及来源余料可用量隔离：'+str(e.detail)
    return issues


def status(p):
    from .stock_operations import balances
    stock,_=balances(p);issues=proof_issues(p);rows=[];receipts=[]
    for receipt in p['stock_receipts']:
        try:
            inv.validate_receipt(p,receipt)
            if inv.approved(p,'stock_receipts',receipt):receipts.append(dict(receipt_id=receipt['id'],receipt_snapshot=snapshot(receipt,stock[receipt['id']])))
        except HTTPException:pass
    for row in p[COLLECTION]:
        executed=inv.event_for(p,'stock_count',row['id']);cancelled=inv.event_for(p,'stock_count_cancel',row['id'])
        try:
            if executed:issue=issues.get(row['receipt_id'],'')
            elif not cancelled:validate(p,row);issue=''
            else:issue=''
        except HTTPException as e:issue=str(e.detail)
        rows.append(dict(record=row,object_hash=store.digest(row),phase='executed' if executed else 'cancelled' if cancelled else row['state'],issue=issue))
    return dict(records=rows,notice=NOTICE,receipts=receipts)


@router.post('/projects/{pid}/stock-inventory/stock-counts/preview')
def preview(pid:str,body:dict,a=Depends(core.actor)):
    if not isinstance(body,dict) or set(body)!={'input'}:raise HTTPException(422,'盘点预览仅接受原始输入')
    with store.connect() as c:
        p=core.project(c,pid,a);result,stamp=prepare(p,body['input']);return dict(result=result,preview_hash=preview_hash(body['input'],result,stamp),ledger_hash=inv.ledger_hash(p),notice=NOTICE)


@router.post('/projects/{pid}/stock-inventory/stock-counts')
def submit(pid:str,body:dict,a=Depends(core.actor)):
    core.edit(a);inv.fields(body,{'input','preview_hash'})
    with store.transaction() as c:
        p=core.project(c,pid,a);old=inv.begin(p,body)
        if old:
            if old not in p[COLLECTION]:inv.fail('请求标识已用于其他库存操作')
            return old
        result,stamp=prepare(p,body['input'])
        if body['preview_hash']!=preview_hash(body['input'],result,stamp):inv.fail('盘点预览已变化，请重新核对')
        return inv.commit(c,p,a,COLLECTION,body,dict(input=body['input'],**result,proof_stamp=stamp,preview_hash=body['preview_hash'],state='draft'),'STOCK_COUNT_SUBMITTED')


@router.post('/projects/{pid}/stock-inventory/stock-counts/{rid}/{operation}')
def movement(pid:str,rid:str,operation:str,body:dict,a=Depends(core.actor)):
    core.edit(a)
    if operation not in ('execute','cancel'):raise HTTPException(422,'仅支持显式执行盘点或有据取消未执行单')
    inv.fields(body,{'object_hash'} if operation=='execute' else {'object_hash','basis','evidence_id'});kind='stock_count' if operation=='execute' else 'stock_count_cancel'
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
            validate(p,row);data.update(receipt_id=row['receipt_id'],before=row['before'],after=row['after'],delta=row['delta'],length_delta=row['length_delta'],mass_delta=row['mass_delta'])
        else:
            text(body['basis'],'未执行盘点取消原因');data.update(basis=body['basis'],evidence_id=body['evidence_id'],proof_stamp=inv.proofs(p,[body['evidence_id']]))
        return inv.commit(c,p,a,'stock_events',body,data,'STOCK_'+kind.upper())
