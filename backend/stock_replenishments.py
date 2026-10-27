"""Reassign only explicitly released, never-issued full-bar demand within a frozen chain."""
from fastapi import APIRouter,Depends,HTTPException
from . import main as core,store,stock_inventory as inv
from .compound_takeoff import text

router=APIRouter(prefix='/api/v1')
FIELDS={'parent_id','parent_hash','release_id','release_hash','demand_hash','allocations','basis','evidence_id','compatibility_confirmed'}
NOTICE=('仅完整原材已分批领出后显式释放的未领余量，可在原材料表及排料需求保持当前有效且未变化时重新核定供应。'
        '不使用已领、已退、在外或已加工数量补额度，不增原需求、不覆盖旧预留；不支持改定尺、跨账混配、修订后补量或损耗追加。'
        '核对上游预留、余量释放及原件，选当前有效已审批次精确覆盖本次未领需求，另附材质截面兼容性原件。'
        '预览不占用，保存立即形成独立补料预留并占用；拒绝、待补件或来源失效仍占用，须有据释放。'
        '另一账号独立复核后，按新预留整单或分批领料，后续实际加工／退回只引用对应新领用批次。'
        '同一上游余量同一时点只能有一份补料预留；新预留未领前整单释放后可重做，有领出后须沿新预留自己的未领释放继续核定，不能回上级重复取额。最多20级。')


def frozen(p,row,trail=(),*,offcut=False):
    """Structural entitlement only; lost current files must not roll back historical facts."""
    inv.intact(row)
    if row['id'] in trail or len(trail)>20:inv.fail('补料关联循环或超过20级，请核查原始供应链')
    meta=row.get('replenishment')
    if meta is None:return row['demand'],[row['id']]
    if not isinstance(meta,dict) or set(meta)!={'parent_id','parent_hash','release_id','release_hash'}:inv.fail('补料原预留及释放快照字段无法核验')
    collection='stock_offcut_reservations' if offcut else 'stock_reservations'
    operation='offcut_release_remaining' if offcut else 'release_remaining'
    hash_key='record_hash' if offcut else 'object_hash'
    parent=core.find(p,collection,meta['parent_id']);base,ancestors=frozen(p,parent,trail+(row['id'],),offcut=offcut)
    event=next((e for e in p['stock_events'] if e['id']==meta['release_id']),None)
    if not event or event['operation']!=operation or event['target_id']!=parent['id'] or event.get(hash_key)!=store.digest(parent) or meta['parent_hash']!=store.digest(parent) or meta['release_hash']!=store.digest(event):inv.fail('补料关联的上游预留或未领余量释放快照不符')
    inv.intact(event)
    if not inv.approved(p,collection,parent):inv.fail('补料上游缺少匹配的独立复核')
    if row['source_id']!=parent['source_id'] or row.get('created_at',event['created_at'])<event['created_at']:inv.fail('补料来源跨材料表或早于释放事实')
    by_key={d['key']:d for d in base['demands']};counts={};seen=set()
    if not isinstance(event.get('allocations'),list) or not event['allocations']:inv.fail('未领释放没有冻结分配')
    for a in event['allocations']:
        index=a.get('allocation_index')
        if type(index) is not int or not 1<=index<=len(parent['allocations']) or index in seen or type(a.get('quantity')) is not int or not 1<=a['quantity']<=1000000:inv.fail('未领释放的原分配序号或根数无效')
        seen.add(index);original=parent['allocations'][index-1]
        if offcut:
            from .stock_offcut_issues import selection_values
            expected,_=selection_values(parent,[dict(allocation_index=index,quantity=a['quantity'])])
            same=a==expected[0]
        else:same=set(a)=={'allocation_index','demand_key','receipt_id','quantity'} and all(a[k]==original[k] for k in ('demand_key','receipt_id'))
        if not same or a['quantity']>original['quantity'] or a['demand_key'] not in by_key:inv.fail('未领释放分配不能替换或超过上游原预留')
        counts[a['demand_key']]=counts.get(a['demand_key'],0)+a['quantity']
    if any(n>by_key[k]['quantity'] for k,n in counts.items()):inv.fail('补料不能增加原定尺需求')
    value={k:v for k,v in base.items() if k not in ('demands','demand_hash')}
    value['demands']=[{**d,'quantity':counts[d['key']]} for d in base['demands'] if d['key'] in counts]
    return {**value,'demand_hash':store.digest(value)},ancestors+[row['id']]


def audit(p,*,offcut=False):
    groups={}
    for row in p['stock_offcut_reservations' if offcut else 'stock_reservations']:
        if 'replenishment' not in row:continue
        demand,_=frozen(p,row,offcut=offcut)
        if row['demand']!=demand:inv.fail('补料需求与冻结未领释放额度不符')
        groups.setdefault(row['replenishment']['parent_id'],[]).append(row)
    for rows in groups.values():
        ordered=sorted(rows,key=lambda r:(r['created_at'],r['id']))
        for previous,current in zip(ordered,ordered[1:]):
            event=inv.event_for(p,'offcut_release' if offcut else 'release',previous['id'])
            if not event or event['created_at']>current['created_at']:inv.fail('同一未领余量重复分配补料预留，或先后顺序无法核验')


def entitlement(p,parent_id,exclude=''):
    inv.records(p)
    from .stock_operations import balances
    balances(p)
    parent=core.find(p,'stock_reservations',parent_id)
    if 'mixed_supply' in parent:inv.fail('联合供应须走联合未领补料入口，不能单侧重新取额')
    event=inv.event_for(p,'release_remaining',parent_id)
    if not event:inv.fail('须选择本项目已分批领出并显式释放未领余量的完整原材预留')
    meta=dict(parent_id=parent['id'],parent_hash=store.digest(parent),release_id=event['id'],release_hash=store.digest(event))
    provisional=dict(id='new:'+parent['id'],source_id=parent['source_id'],demand=parent['demand'],replenishment=meta)
    provisional=inv.seal(provisional);demand,chain=frozen(p,provisional);ancestors=chain[:-1]
    root=core.find(p,'stock_reservations',ancestors[0])
    if inv.demands(p,parent['source_id'])!=root['demand']:inv.fail('原材料表或完整排料需求已变化，不能按旧未领额补料，须另行核定范围')
    if inv.proofs(p,[event['evidence_id']])!=event['proof_stamp']:inv.fail('未领余量释放依据已变化，不能据此新增补料')
    for row in p['stock_reservations']:
        if row['id']==exclude or row['id'] in ancestors or row['source_id']!=parent['source_id'] or inv.event_for(p,'release',row['id']):continue
        inv.fail('本材料表未领额度已有补料预留或后续领用，请沿最近一份预留核查，不能重复取额')
    if any(r['source_id']==parent['source_id'] and not inv.event_for(p,'offcut_release',r['id']) for r in p['stock_offcut_reservations']):inv.fail('已有余料预留或领用，不能跨账补料')
    return parent,event,demand,meta


def prepare(p,raw,exclude=''):
    if not isinstance(raw,dict) or set(raw)!=FIELDS:raise HTTPException(422,'须填写上游预留及释放快照、本次需求、全部新批次分配、兼容说明原件及确认')
    for k in ('parent_id','parent_hash','release_id','release_hash','demand_hash','basis','evidence_id'):text(raw[k],'补料兼容性与核查说明' if k=='basis' else '补料来源或原件',2000 if k=='basis' else 200)
    if raw['compatibility_confirmed'] is not True:raise HTTPException(422,'须据原件确认新供应与原需求同材质截面、定尺及每米质量，且未重复占用')
    parent,event,demand,meta=entitlement(p,raw['parent_id'],exclude)
    if any(raw[k]!=v for k,v in meta.items()) or raw['demand_hash']!=demand['demand_hash']:inv.fail('补料上游、未领释放或需求快照已变化，请刷新核对')
    inv.allocation_check(p,demand,raw['allocations'],availability=not exclude)
    from .stock_operations import frozen_rows,totals
    supply_rows=frozen_rows(p,dict(demand=demand,allocations=raw['allocations']))
    supply_rows=[dict(a,total_length=totals([a])['length'],mass=totals([a])['mass']) for a in supply_rows]
    result=dict(source_id=parent['source_id'],demand=demand,allocations=raw['allocations'],basis=raw['basis'],evidence_id=raw['evidence_id'],replenishment=meta,supply=dict(allocations=supply_rows,totals=totals(supply_rows)))
    return result,inv.proofs(p,[raw['evidence_id']])


def preview_hash(raw,result,stamp):return store.digest(dict(input=raw,result=result,proof_stamp=stamp))


def validate(p,row):
    result,stamp=prepare(p,row.get('replenishment_input'),row['id'])
    if any(row.get(k)!=v for k,v in result.items()) or row['proof_stamp']!=stamp or row.get('replenishment_preview_hash')!=preview_hash(row['replenishment_input'],result,stamp):inv.fail('补料输入、原件、需求或预览关联已变化，请有据释放后重新核定')
    return result['demand']


def status(p):
    candidates=[]
    for row in p['stock_reservations']:
        event=inv.event_for(p,'release_remaining',row['id'])
        if not event:continue
        try:
            _,_,demand,meta=entitlement(p,row['id']);issue='';quantity=sum(d['quantity'] for d in demand['demands'])
        except HTTPException as e:issue=str(e.detail);quantity=sum(a['quantity'] for a in event.get('allocations',[]))
        candidates.append(dict(parent_id=row['id'],source_name=row['demand']['source_name'],release_id=event['id'],quantity=quantity,eligible=not issue,issue=issue,children=[r['id'] for r in p['stock_reservations'] if r.get('replenishment',{}).get('parent_id')==row['id']]))
    return dict(candidates=candidates,notice=NOTICE)


@router.get('/projects/{pid}/stock-inventory/replenishments/demands')
def demands(pid:str,reservation_id:str,a=Depends(core.actor)):
    with store.connect() as c:
        p=core.project(c,pid,a);_,_,demand,meta=entitlement(p,reservation_id);return dict(demand=demand,**meta,ledger_hash=inv.ledger_hash(p),notice=NOTICE)


@router.post('/projects/{pid}/stock-inventory/replenishments/preview')
def preview(pid:str,body:dict,a=Depends(core.actor)):
    if not isinstance(body,dict) or set(body)!={'input'}:raise HTTPException(422,'补料预览仅接受原始输入')
    with store.connect() as c:
        p=core.project(c,pid,a);result,stamp=prepare(p,body['input']);return dict(result=result,preview_hash=preview_hash(body['input'],result,stamp),ledger_hash=inv.ledger_hash(p),notice=NOTICE)


@router.post('/projects/{pid}/stock-inventory/replenishments')
def submit(pid:str,body:dict,a=Depends(core.actor)):
    core.edit(a);inv.fields(body,{'input','preview_hash'})
    with store.transaction() as c:
        p=core.project(c,pid,a);old=inv.begin(p,body)
        if old:
            if old not in p['stock_reservations'] or 'replenishment' not in old:inv.fail('请求标识已用于其他库存操作')
            return old
        result,stamp=prepare(p,body['input'])
        if body['preview_hash']!=preview_hash(body['input'],result,stamp):inv.fail('补料预览已变化，请重新核对')
        return inv.commit(c,p,a,'stock_reservations',body,dict(**result,proof_stamp=stamp,replenishment_input=body['input'],replenishment_preview_hash=body['preview_hash'],state='draft'),'STOCK_REPLENISHMENT_RESERVED')
