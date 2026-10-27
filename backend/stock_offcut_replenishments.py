"""Reassign explicitly released, never-issued demand to verified offcut lots."""
from datetime import date,datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter,Depends,HTTPException
from . import main as core,store,stock_inventory as inv,stock_offcut_reuse as reuse
from .compound_takeoff import text
from .stock_replenishments import frozen,FIELDS as STOCK_FIELDS

router=APIRouter(prefix='/api/v1')
FIELDS=STOCK_FIELDS|{'business_date','custodian'}
NOTICE=('仅余料预留分批领用后已显式释放的未领余量，可在最初材料表及完整排料需求有效且不变时重新核定余料供应。'
        '只取释放事件冻结的逐需求未领根数，不使用已领、原段退回、在外或加工数量增加额度。'
        '逐项同材质截面、质量合格、实测段长和每米质量；不改定尺、不拼接或按总长折算，不与完整原材混配。'
        '预览不占用，保存另建补料预留立即占用当前有效已验收余料，另一账号独立复核；实际交接须另建余料批单、复核并显式领用。'
        '拒绝、待补件或来源失效保留未领占用，须有据释放。未领子单整单释放后可重做；有领出后只能沿该子单自己的已释放未领额继续，不能回祖先重复取额，最多20级。'
        '核定日期及拟交接对象不表示实际交接，原预留及批单保持；不覆盖修订或损耗追加、采购、跨项目或已执行冲销。')


def current_demand(p,row):
    """Compare the original full plan, then return this row's frozen reduced demand."""
    if 'mixed_supply' in row:
        from .stock_mixed_supply import current_demand
        return current_demand(p,row)
    if 'replenishment' not in row:return inv.demands(p,row['source_id'])
    demand,chain=frozen(p,row,offcut=True);root=core.find(p,reuse.COLLECTION,chain[0])
    if inv.demands(p,row['source_id'])!=root['demand']:inv.fail('原材料表或完整排料需求已变化，不能按旧余量补料')
    return demand


def release_proof_issue(p,row):
    if 'replenishment' not in row:return ''
    try:
        _,chain=frozen(p,row,offcut=True)
        for rid in chain[1:]:
            child=core.find(p,reuse.COLLECTION,rid);event=core.find(p,'stock_events',child['replenishment']['release_id'])
            if inv.proofs(p,[event['evidence_id']])!=event['proof_stamp']:inv.fail('上游未领释放原件已变化')
        return ''
    except HTTPException as e:return '余料补料上游释放依据待核查，历史数量保持：'+str(e.detail)


def entitlement(p,parent_id,exclude=''):
    inv.records(p)
    from .stock_processing_results import balances
    from .stock_offcut_ledger import replay
    _,lots=balances(p);replay(p,lots)
    parent=core.find(p,reuse.COLLECTION,parent_id)
    if 'mixed_supply' in parent:inv.fail('联合供应预留暂不支持补料，不能单侧重新取额')
    event=inv.event_for(p,'offcut_release_remaining',parent_id)
    if not event:inv.fail('须选择本项目已分批领用并显式释放未领余量的余料预留')
    meta=dict(parent_id=parent['id'],parent_hash=store.digest(parent),release_id=event['id'],release_hash=store.digest(event))
    provisional=inv.seal(dict(id='new:'+parent['id'],source_id=parent['source_id'],demand=parent['demand'],replenishment=meta))
    demand,chain=frozen(p,provisional,offcut=True);root=core.find(p,reuse.COLLECTION,chain[0])
    problem=release_proof_issue(p,parent)
    if problem:inv.fail(problem)
    if inv.demands(p,parent['source_id'])!=root['demand']:inv.fail('原材料表或完整排料需求已变化，不能按旧余量补料')
    if inv.proofs(p,[event['evidence_id']])!=event['proof_stamp']:inv.fail('余料未领余量释放原件已变化，不能新增补料')
    for row in p[reuse.COLLECTION]:
        if row['id']==exclude or row['id'] in chain[:-1] or row['source_id']!=parent['source_id'] or inv.event_for(p,'offcut_release',row['id']):continue
        inv.fail('此材料表未领额度已有余料补料或后续领用，请沿最近预留核对，不能重复取额')
    if any(r['source_id']==parent['source_id'] and not inv.event_for(p,'release',r['id']) for r in p['stock_reservations']):inv.fail('已有完整原材预留或领用，不能跨账补料')
    return parent,event,demand,meta


def prepare(p,raw,exclude='',remaining=False):
    if not isinstance(raw,dict) or set(raw)!=FIELDS:raise HTTPException(422,'须填写上游预留及释放快照、未领需求、新余料分配、核定日期及对象、兼容原件和确认')
    for k in FIELDS-{'allocations','compatibility_confirmed','business_date'}:text(raw[k],'余料补料兼容核查说明' if k=='basis' else '余料补料来源、对象或原件',2000 if k=='basis' else 200)
    if raw['compatibility_confirmed'] is not True:raise HTTPException(422,'须据原件确认同材质截面、质量合格、段长和每米质量一致，未重复占用')
    try:
        d=date.fromisoformat(raw['business_date'])
        if d.isoformat()!=raw['business_date'] or d>datetime.now(ZoneInfo('Asia/Shanghai')).date():raise ValueError()
    except (ValueError,TypeError):raise HTTPException(422,'补料核定日期须为不晚于今天的YYYY-MM-DD') from None
    parent,event,demand,meta=entitlement(p,raw['parent_id'],exclude)
    if raw['business_date']<parent['input']['business_date']:inv.fail('补料核定日期不得早于上游预留核定日期')
    if any(raw[k]!=v for k,v in meta.items()) or raw['demand_hash']!=demand['demand_hash']:inv.fail('余料补料上游、释放或未领需求快照已变化，请刷新')
    from .stock_processing_results import status
    lots=status(p)['lots'];rows,totals=reuse.allocation_values(demand,raw['allocations'],lots)
    amounts=reuse.progress(p,core.find(p,reuse.COLLECTION,exclude)) if remaining else rows;used={}
    for a in amounts:
        count=a['remaining'] if remaining else a['quantity']
        if count:used[a['lot_key']]=used.get(a['lot_key'],0)+count
    for key,count in used.items():
        lot=next(l for l in lots if l['lot_key']==key)
        if not lot['verified']:inv.fail('新余料停用或来源验收及盘点原件失效，不能新增补料或领用')
        if count+reuse.held(p,key,exclude)>lot['in_stock_quantity']:inv.fail('新余料可用不足，已有预留或已领用，请刷新核对')
    return dict(source_id=parent['source_id'],demand=demand,allocations=rows,totals=totals,replenishment=meta),inv.proofs(p,[raw['evidence_id']])


def preview_hash(raw,result,stamp):return store.digest(dict(input=raw,result=result,proof_stamp=stamp))


def validate(p,row,remaining=False):
    result,stamp=prepare(p,row['input'],row['id'],remaining)
    if any(row.get(k)!=v for k,v in result.items()) or row['proof_stamp']!=stamp or row['preview_hash']!=preview_hash(row['input'],result,stamp):inv.fail('余料补料输入、原件、规范分配或预览关联已变化，请有据释放后重做')


def status(p):
    candidates=[]
    for row in p[reuse.COLLECTION]:
        event=inv.event_for(p,'offcut_release_remaining',row['id'])
        if not event:continue
        try:
            _,_,demand,_=entitlement(p,row['id']);issue='';quantity=sum(d['quantity'] for d in demand['demands'])
        except HTTPException as e:issue=str(e.detail);quantity=sum(a['quantity'] for a in event.get('allocations',[]))
        candidates.append(dict(parent_id=row['id'],source_name=row['demand']['source_name'],release_id=event['id'],quantity=quantity,eligible=not issue,issue=issue,children=[r['id'] for r in p[reuse.COLLECTION] if r.get('replenishment',{}).get('parent_id')==row['id']]))
    return dict(candidates=candidates,notice=NOTICE)


@router.get('/projects/{pid}/stock-inventory/offcut-replenishments/demands')
def demands(pid:str,reservation_id:str,a=Depends(core.actor)):
    with store.connect() as c:
        p=core.project(c,pid,a);_,_,demand,meta=entitlement(p,reservation_id);return dict(demand=demand,**meta,ledger_hash=inv.ledger_hash(p),notice=NOTICE)


@router.post('/projects/{pid}/stock-inventory/offcut-replenishments/preview')
def preview(pid:str,body:dict,a=Depends(core.actor)):
    if not isinstance(body,dict) or set(body)!={'input'}:raise HTTPException(422,'余料补料预览仅接受原始输入')
    with store.connect() as c:
        p=core.project(c,pid,a);result,stamp=prepare(p,body['input']);return dict(result=result,preview_hash=preview_hash(body['input'],result,stamp),ledger_hash=inv.ledger_hash(p),notice=NOTICE)


@router.post('/projects/{pid}/stock-inventory/offcut-replenishments')
def submit(pid:str,body:dict,a=Depends(core.actor)):
    core.edit(a);inv.fields(body,{'input','preview_hash'})
    with store.transaction() as c:
        p=core.project(c,pid,a);old=inv.begin(p,body)
        if old:
            if old not in p[reuse.COLLECTION] or 'replenishment' not in old:inv.fail('请求标识已用于其他库存操作')
            return old
        result,stamp=prepare(p,body['input'])
        if body['preview_hash']!=preview_hash(body['input'],result,stamp):inv.fail('余料补料预览已变化，请重新核对')
        return inv.commit(c,p,a,reuse.COLLECTION,body,dict(input=body['input'],**result,proof_stamp=stamp,preview_hash=body['preview_hash'],state='draft'),'STOCK_OFFCUT_REPLENISHMENT_RESERVED')
