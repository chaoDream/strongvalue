"""Reassign only explicitly released, never-issued joint-supply demand."""
from fastapi import APIRouter,Depends,HTTPException
from . import main as core,store,stock_inventory as inv,stock_mixed_supply as mixed

router=APIRouter(prefix='/api/v1')
NOTICE=('仅联合供应单已有实际领出、所有尚未领出部分均已分别有据释放时，核定本次未领补料。'
        '逐需求合并两侧冻结未领数量，不使用已领、退回、在外或加工数量恢复额度；最初材料表与完整排料须有效且不变。'
        '按原定尺及每米质量重新分配完整原材与已验收余料，可两侧供应，也可仅一侧；不改定尺、不拼接、不追加需求。'
        '预览不占用，保存同一事务建立新关联单及实际有分配侧的独立预留并占用；无供应侧不造预留、不要求空白审核。'
        '所有有供应侧分别独立复核后，按各自实际批单复核并显式领用；原核定、释放和已领事实保持。'
        '新单未领前全部有据释放可从原上游重做，有领出后只能沿本单已释放未领额继续，不能回祖先重复取额，最多20级。'
        '拒绝、待补件或原件失效保持新占用，须分别显式释放；不支持修订或损耗追加、普通单改联合、采购、跨项目或执行冲销。')


def release_basis(p,parent):
    """Historical facts only; validate current file bytes separately."""
    from .stock_operations import reservation_progress,remaining_allocations
    from . import stock_offcut_reuse as reuse
    if not mixed.issued(p,parent):inv.fail('联合补料上游须已有实际领出；未领全单释放请按普通联合入口重做')
    stock,offcut=mixed.children(p,parent);releases=[];counts={}
    for kind,row,collection in (('stock',stock,'stock_reservations'),('offcut',offcut,'stock_offcut_reservations')):
        if row is None:continue
        if not inv.approved(p,collection,row):inv.fail('联合补料上游缺少匹配的独立复核')
        progress=reservation_progress(p,row) if kind=='stock' else reuse.progress(p,row)
        remaining=[a for a in progress if a['remaining']]
        if not remaining:continue
        operation=('release_remaining' if any(a['issued'] for a in progress) else 'release') if kind=='stock' else ('offcut_release_remaining' if any(a['issued'] for a in progress) else 'offcut_release')
        event=inv.event_for(p,operation,row['id'])
        if not event:inv.fail('联合供应尚有未领占用，须两侧全部按实有据释放后才能补料')
        inv.intact(event);hash_key='object_hash' if kind=='stock' else 'record_hash'
        if event.get(hash_key)!=store.digest(row):inv.fail('联合补料上游释放与原预留快照不符')
        if operation=='release_remaining' and event.get('allocations')!=remaining_allocations(p,row):inv.fail('联合补料原材未领释放分配不符')
        if operation=='offcut_release_remaining':
            from .stock_offcut_issues import selection_values
            values,total=selection_values(row,[dict(allocation_index=a['allocation_index'],quantity=a['remaining']) for a in remaining])
            if event.get('allocations')!=values or event.get('totals')!=total:inv.fail('联合补料余料未领释放分配不符')
        releases.append(dict(partition=kind,target_id=row['id'],record_hash=store.digest(row),event_id=event['id'],event_hash=store.digest(event)))
        for a in remaining:counts[a['demand_key']]=counts.get(a['demand_key'],0)+a['remaining']
    if not counts:inv.fail('联合供应没有已释放且尚未领出的原需求，不因退回或加工重开额度')
    return releases,counts


def frozen(p,group,trail=()):
    inv.intact(group)
    if group['id'] in trail or len(trail)>20:inv.fail('联合补料关联循环或超过20级')
    meta=group.get('replenishment')
    if meta is None:return group['demand'],[group['id']]
    if not isinstance(meta,dict) or set(meta)!={'parent_id','parent_hash','releases'}:inv.fail('联合补料上游及释放快照字段不符')
    parent=core.find(p,mixed.COLLECTION,meta['parent_id']);base,chain=frozen(p,parent,trail+(group['id'],))
    releases,counts=release_basis(p,parent)
    if meta['parent_hash']!=store.digest(parent) or meta['releases']!=releases or group['source_id']!=parent['source_id']:inv.fail('联合补料上游、两侧释放或材料表快照不符')
    if group.get('created_at','9999')<max(core.find(p,'stock_events',r['event_id'])['created_at'] for r in releases):inv.fail('联合补料建立早于未领释放')
    needs={d['key']:d for d in base['demands']}
    if any(k not in needs or q>needs[k]['quantity'] for k,q in counts.items()):inv.fail('联合补料不能增加或挪用原需求')
    value={k:v for k,v in base.items() if k not in ('demands','demand_hash')};value['demands']=[{**d,'quantity':counts[d['key']]} for d in base['demands'] if d['key'] in counts]
    return dict(**value,demand_hash=store.digest(value)),chain+[group['id']]


def unused_released(p,group,before):
    if mixed.issued(p,group):return False
    for kind,row in zip(('stock','offcut'),mixed.children(p,group)):
        if row is None:continue
        event=inv.event_for(p,'release' if kind=='stock' else 'offcut_release',row['id'])
        if not event or event['created_at']>before:return False
        inv.intact(event)
        if event.get('object_hash' if kind=='stock' else 'record_hash')!=store.digest(row):inv.fail('联合补料未领重做的整单释放快照不符')
    return True


def audit(p):
    groups={}
    for group in p.get(mixed.COLLECTION,[]):
        if 'replenishment' not in group:continue
        demand,_=frozen(p,group)
        if demand!=group['demand']:inv.fail('联合补料需求与冻结未领释放额度不符')
        raw=group['input'];meta=group['replenishment']
        if raw.get('parent_id')!=meta['parent_id'] or raw.get('parent_hash')!=meta['parent_hash'] or raw.get('releases_hash')!=store.digest(meta['releases']):inv.fail('联合补料原始输入与上游释放关联不符')
        groups.setdefault(meta['parent_id'],[]).append(group)
    for children in groups.values():
        ordered=sorted(children,key=lambda g:(g['created_at'],g['id']))
        for previous,current in zip(ordered,ordered[1:]):
            if not unused_released(p,previous,current['created_at']):inv.fail('同一联合未领额度分叉或重复补料，须沿最近已释放未领单继续')


def check_proofs(p,group):
    _,chain=frozen(p,group)
    for gid in chain:
        item=core.find(p,mixed.COLLECTION,gid)
        if inv.proofs(p,[item['input']['evidence_id']])!=item['proof_stamp']:inv.fail('联合供应上游核定原件已变化')
        for release in item.get('replenishment',{}).get('releases',[]):
            event=core.find(p,'stock_events',release['event_id'])
            if inv.proofs(p,[event['evidence_id']])!=event['proof_stamp']:inv.fail('联合补料上游未领释放原件已变化')


def entitlement(p,parent_id,exclude=''):
    inv.records(p);mixed.audit(p)
    from .stock_operations import balances
    from .stock_processing_results import balances as processing_balances
    from .stock_offcut_ledger import replay
    balances(p);_,lots=processing_balances(p);replay(p,lots)
    parent=core.find(p,mixed.COLLECTION,parent_id);releases,_=release_basis(p,parent)
    meta=dict(parent_id=parent['id'],parent_hash=store.digest(parent),releases=releases)
    provisional=inv.seal(dict(id='new:'+parent['id'],source_id=parent['source_id'],demand=parent['demand'],replenishment=meta));demand,chain=frozen(p,provisional);ancestors=chain[:-1]
    root=core.find(p,mixed.COLLECTION,ancestors[0])
    if inv.demands(p,parent['source_id'])!=root['demand']:inv.fail('最初材料表或完整排料需求已变化，不能按旧联合未领额补料')
    check_proofs(p,parent)
    for r in releases:
        event=core.find(p,'stock_events',r['event_id'])
        if inv.proofs(p,[event['evidence_id']])!=event['proof_stamp']:inv.fail('联合未领释放原件已变化，不能新增补料')
    ignored={rid for gid in ancestors+([exclude] if exclude else []) for rid in mixed.children_ids(p,gid)}
    for collection,op in (('stock_reservations','release'),('stock_offcut_reservations','offcut_release')):
        if any(row['source_id']==parent['source_id'] and row['id'] not in ignored and not inv.event_for(p,op,row['id']) for row in p[collection]):inv.fail('联合未领额度已有补料或后续领用，请沿最近关联单核查，不能回祖先重复取额')
    return parent,demand,meta


def status(p):
    candidates=[]
    for group in p.get(mixed.COLLECTION,[]):
        if not mixed.issued(p,group):continue
        try:
            _,demand,_=entitlement(p,group['id']);issue='';quantity=sum(d['quantity'] for d in demand['demands'])
        except HTTPException as e:issue=str(e.detail);quantity=0
        candidates.append(dict(parent_id=group['id'],source_name=group['demand']['source_name'],quantity=quantity,eligible=not issue,issue=issue,children=[g['id'] for g in p[mixed.COLLECTION] if g.get('replenishment',{}).get('parent_id')==group['id']]))
    return dict(candidates=candidates,notice=NOTICE)


@router.get('/projects/{pid}/stock-inventory/mixed-replenishments/demands')
def demands(pid:str,group_id:str,a=Depends(core.actor)):
    with store.connect() as c:
        p=core.project(c,pid,a);parent,demand,meta=entitlement(p,group_id);return dict(demand=demand,parent_id=parent['id'],parent_hash=meta['parent_hash'],releases=meta['releases'],releases_hash=store.digest(meta['releases']),ledger_hash=inv.ledger_hash(p),notice=NOTICE)


@router.post('/projects/{pid}/stock-inventory/mixed-replenishments/preview')
def preview(pid:str,body:dict,a=Depends(core.actor)):
    if not isinstance(body,dict) or set(body)!={'input'}:raise HTTPException(422,'联合补料预览仅接受原始输入')
    with store.connect() as c:
        p=core.project(c,pid,a);result,stamp=mixed.prepare(p,body['input'],replenishment=True);return dict(result=result,preview_hash=mixed.preview_hash(body['input'],result,stamp),ledger_hash=inv.ledger_hash(p),notice=NOTICE)


@router.post('/projects/{pid}/stock-inventory/mixed-replenishments')
def submit(pid:str,body:dict,a=Depends(core.actor)):
    core.edit(a);inv.fields(body,{'input','preview_hash'})
    with store.transaction() as c:
        p=core.project(c,pid,a);old=inv.begin(p,body)
        if old:
            if old not in p[mixed.COLLECTION] or 'replenishment' not in old:inv.fail('请求标识已用于其他库存操作')
            return old
        result,stamp=mixed.prepare(p,body['input'],replenishment=True)
        if body['preview_hash']!=mixed.preview_hash(body['input'],result,stamp):inv.fail('联合补料预览已变化，请重新核对')
        return mixed.register(c,p,a,body,result,stamp)
