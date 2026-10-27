"""Atomic, exact-length joint supply; separate reviewed holds and custody ledgers."""
import copy
from datetime import date,datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter,Depends,HTTPException
from . import main as core,store,stock_inventory as inv
from .compound_takeoff import text

router=APIRouter(prefix='/api/v1')
COLLECTION='stock_mixed_reservations'
FIELDS={'source_id','demand_hash','stock_allocations','offcut_allocations','business_date','custodian','basis','evidence_id','compatibility_confirmed'}
NOTICE=('按本项目当前已审排料定尺，联合核定完整原材和已验收余料供应，逐需求两侧根数之和必须恰好等于原需求；两侧均须有分配。'
        '段长和每米质量精确相等，同材质截面及余料质量合格须据原件确认；不改定尺、不拼接、不过量、不自动优化排料。'
        '预览不占用；保存同一事务建立关联的两侧预留并立即分别占用，任一侧失败全部不保存。'
        '两份预留须分别由另一账号独立复核，两侧均审定后才可办理实际领用；任一侧领出后两侧预留均不可重审。'
        '原材按独立领料单整单或分批，余料必须另建实际批单，分别复核并显式执行；核定日期和拟交接对象不表示实际交接。'
        '拒绝、待补件或来源失效保留占用，两侧未领部分须分别有据释放，不自动释放另一侧；任一侧已领或退回均不重开全量需求。'
        '同一材料表不并行普通全量或其他全量联合预留；已全部释放未领补料须走联合补料入口，不重开全量需求；不支持修订追加、改定尺、损耗追加、采购、跨项目或已执行冲销。')


def portion(demand,allocations,key,allow_empty=False):
    if not isinstance(allocations,list) or not (0 if allow_empty else 1)<=len(allocations)<=100:raise HTTPException(422,'联合供应两侧均须填写正整数分配')
    needs={d['key']:d for d in demand['demands']};counts={}
    for a in allocations:
        if not isinstance(a,dict) or set(a)!={'demand_key',key,'quantity'} or type(a['quantity']) is not int or not 1<=a['quantity']<=1000000 or not isinstance(a['demand_key'],str) or a['demand_key'] not in needs or not isinstance(a[key],str):raise HTTPException(422,'联合供应分配须为本次需求、对应账实物标识及1至1000000正整数根数')
        counts[a['demand_key']]=counts.get(a['demand_key'],0)+a['quantity']
    if any(q>needs[k]['quantity'] for k,q in counts.items()):inv.fail('联合供应单侧数量已超出原需求')
    value={k:v for k,v in demand.items() if k not in ('demands','demand_hash')};value['demands']=[{**d,'quantity':counts[d['key']]} for d in demand['demands'] if d['key'] in counts]
    return dict(**value,demand_hash=store.digest(value))


def parts(demand,raw,allow_single=False):
    stock=portion(demand,raw['stock_allocations'],'receipt_id',allow_single);offcut=portion(demand,raw['offcut_allocations'],'lot_key',allow_single)
    if len(raw['stock_allocations'])+len(raw['offcut_allocations'])>100:raise HTTPException(422,'联合供应两侧合计最多100条分配')
    counts={d['key']:0 for d in demand['demands']}
    for part in (stock,offcut):
        for d in part['demands']:counts[d['key']]+=d['quantity']
    if counts!={d['key']:d['quantity'] for d in demand['demands']}:inv.fail('原材与余料逐需求合计须恰好覆盖全部原需求，不得遗漏、超量或互借需求')
    return stock,offcut


def children(p,group):
    return (core.find(p,'stock_reservations',group['stock_id']) if group['stock_id'] else None,core.find(p,'stock_offcut_reservations',group['offcut_id']) if group['offcut_id'] else None)


def group_for(p,row):return core.find(p,COLLECTION,row['mixed_supply']['group_id'])


def audit(p):
    for group in p.get(COLLECTION,[]):
        inv.intact(group);raw=group['input'];stock,offcut=children(p,group);sd,od=parts(group['demand'],raw,allow_single='replenishment' in group)
        if group['state']!='registered':inv.fail('联合供应关联单不能冒充已审定预留')
        if group['source_id']!=raw['source_id'] or group['demand']['source_id']!=raw['source_id'] or raw['demand_hash']!=group['demand']['demand_hash']:inv.fail('联合供应材料表及完整需求关联无法核验')
        for row,kind,demand,allocations in ((stock,'stock',sd,raw['stock_allocations']),(offcut,'offcut',od,group['offcut']['allocations'])):
            if not demand['demands']:
                from .stock_operations import totals
                if row is not None or group[kind]['allocations'] or group[kind]['totals']!=totals([]):inv.fail('联合补料无供应侧不能虚构预留或数量')
                continue
            if row is None:inv.fail('联合供应有分配侧缺少预留')
            inv.intact(row)
            if row.get('mixed_supply')!={'group_id':group['id'],'partition':kind} or row['author']!=group['author'] or row['source_id']!=group['source_id'] or row['demand']!=demand or row['allocations']!=allocations or row['proof_stamp']!=group['proof_stamp']:inv.fail('联合供应两侧预留、作者或需求快照不符')
        if (stock and (stock['basis']!=raw['basis'] or stock['evidence_id']!=raw['evidence_id'])) or (offcut and (offcut['input']!=offcut_input(raw,od) or offcut['totals']!=group['offcut']['totals'])) or group['stock']['demand']!=sd or group['offcut']['demand']!=od:inv.fail('联合供应输入及两侧冻结分配不符')
        if [{k:a[k] for k in ('demand_key','lot_key','quantity')} for a in group['offcut']['allocations']]!=raw['offcut_allocations']:inv.fail('联合供应余料原始分配与规范快照不符')
        result={k:group[k] for k in ('source_id','demand','stock','offcut','totals')}
        if 'replenishment' in group:result['replenishment']=group['replenishment']
        if group['preview_hash']!=preview_hash(raw,result,group['proof_stamp']):inv.fail('联合供应预览及冻结快照关联无法核验')
    from .stock_mixed_replenishments import audit as audit_replenishments
    audit_replenishments(p)
    for collection,kind in (('stock_reservations','stock'),('stock_offcut_reservations','offcut')):
        for row in p[collection]:
            if 'mixed_supply' not in row:continue
            meta=row['mixed_supply']
            if not isinstance(meta,dict) or set(meta)!={'group_id','partition'} or meta['partition']!=kind:inv.fail('联合供应预留类型无法核验')
            group=group_for(p,row)
            if group[kind+'_id']!=row['id']:inv.fail('联合供应预留关联缺失或分叉')


def issued(p,group):
    from .stock_operations import issued_reservation
    from .stock_offcut_reuse import issued as offcut_issued
    return (issued_reservation(p,group['stock_id']) if group['stock_id'] else None) or (offcut_issued(p,group['offcut_id']) if group['offcut_id'] else None)


def reviewable(p,row):
    if 'mixed_supply' in row and issued(p,group_for(p,row)):inv.fail('联合供应任一侧已领出，两侧预留均只供追溯，不能重审')


def source_free(p,source_id,exclude=''):
    ignored=set(children_ids(p,exclude)) if exclude else set()
    for collection,op in (('stock_reservations','release'),('stock_offcut_reservations','offcut_release')):
        if any(r['source_id']==source_id and r['id'] not in ignored and not inv.event_for(p,op,r['id']) for r in p[collection]):inv.fail('材料表已有普通或联合预留／领用记录，须核查旧范围，不能重复全量供应')


def children_ids(p,gid):
    group=core.find(p,COLLECTION,gid);return tuple(rid for rid in (group['stock_id'],group['offcut_id']) if rid)


def offcut_input(raw,demand):
    return dict(source_id=raw['source_id'],demand_hash=demand['demand_hash'],allocations=raw['offcut_allocations'],business_date=raw['business_date'],custodian=raw['custodian'],basis=raw['basis'],evidence_id=raw['evidence_id'],compatibility_confirmed=True)


def prepare(p,raw,exclude='',*,replenishment=False):
    expected=FIELDS|{'parent_id','parent_hash','releases_hash'} if replenishment else FIELDS
    if not isinstance(raw,dict) or set(raw)!=expected:raise HTTPException(422,'须填写完整需求、两账分配、核定日期、拟交接对象、兼容原件及确认')
    for k in ('source_id','demand_hash','custodian','basis','evidence_id')+(('parent_id','parent_hash','releases_hash') if replenishment else ()):text(raw[k],'联合供应兼容性核查说明' if k=='basis' else '联合供应来源、对象或原件',2000 if k=='basis' else 200)
    if raw['compatibility_confirmed'] is not True:raise HTTPException(422,'须据原件确认同材质截面、余料质量合格、定尺与每米质量一致且未重复占用')
    try:
        d=date.fromisoformat(raw['business_date'])
        if d.isoformat()!=raw['business_date'] or d>datetime.now(ZoneInfo('Asia/Shanghai')).date():raise ValueError()
    except (TypeError,ValueError):raise HTTPException(422,'联合供应核定日期须为不晚于今天的YYYY-MM-DD') from None
    inv.records(p);audit(p);meta=None
    if replenishment:
        from .stock_mixed_replenishments import entitlement
        parent,demand,meta=entitlement(p,raw['parent_id'],exclude)
        if raw['source_id']!=parent['source_id'] or raw['parent_hash']!=meta['parent_hash'] or raw['releases_hash']!=store.digest(meta['releases']):inv.fail('联合补料上游或两侧释放快照已变化，请刷新')
        if raw['business_date']<parent['input']['business_date']:inv.fail('联合补料核定日期不得早于上游核定日期')
    else:source_free(p,raw['source_id'],exclude);demand=inv.demands(p,raw['source_id'])
    if demand['demand_hash']!=raw['demand_hash']:inv.fail('联合供应本次排料需求已变化，请刷新')
    sd,od=parts(demand,raw,allow_single=replenishment)
    from .stock_operations import frozen_rows,totals,balances,reservation_progress,released_reservation
    from .stock_processing_results import status as processing_status
    from . import stock_offcut_reuse as reuse
    if sd['demands']:inv.allocation_check(p,sd,raw['stock_allocations'],availability=not exclude)
    stock_rows=frozen_rows(p,dict(demand=sd,allocations=raw['stock_allocations']))
    stock_rows=[dict(a,total_length=totals([a])['length'],mass=totals([a])['mass']) for a in stock_rows]
    lots=processing_status(p)['lots'];offcut_rows,offcut_totals=reuse.allocation_values(od,raw['offcut_allocations'],lots) if od['demands'] else ([],totals([]))
    stock_used={};offcut_used={}
    if exclude:
        group=core.find(p,COLLECTION,exclude);sr,orr=children(p,group)
        sa=[] if sr is None or released_reservation(p,sr['id']) else reservation_progress(p,sr)
        oa=[] if orr is None or reuse.released(p,orr['id']) else reuse.progress(p,orr)
    else:sa=stock_rows;oa=offcut_rows
    for a in sa:
        q=a['remaining'] if exclude else a['quantity'];stock_used[a['receipt_id']]=stock_used.get(a['receipt_id'],0)+q
    for a in oa:
        q=a['remaining'] if exclude else a['quantity'];offcut_used[a['lot_key']]=offcut_used.get(a['lot_key'],0)+q
    stock_balances,_=balances(p)
    for key,q in stock_used.items():
        if inv.held(p,key)+(0 if exclude else q)>stock_balances[key]['on_hand']:inv.fail('联合供应原材可用不足')
    for key,q in offcut_used.items():
        lot=next(l for l in lots if l['lot_key']==key)
        if q and not lot['verified']:inv.fail('联合供应余料已停用或验收及盘点依据失效')
        if reuse.held(p,key)+(0 if exclude else q)>lot['in_stock_quantity']:inv.fail('联合供应余料可用不足')
    result=dict(source_id=raw['source_id'],demand=demand,stock=dict(demand=sd,allocations=stock_rows,totals=totals(stock_rows)),offcut=dict(demand=od,allocations=offcut_rows,totals=offcut_totals),totals=totals(stock_rows+offcut_rows))
    if meta is not None:result['replenishment']=meta
    return result,inv.proofs(p,[raw['evidence_id']])


def preview_hash(raw,result,stamp):return store.digest(dict(input=raw,result=result,proof_stamp=stamp))


def validate_partition(p,row):
    group=group_for(p,row);result,stamp=prepare(p,group['input'],group['id'],replenishment='replenishment' in group)
    if any(group[k]!=v for k,v in result.items()) or group['proof_stamp']!=stamp:inv.fail('联合供应来源、原件或两侧快照已变化，未领占用保持，请核查后分别释放')
    return row['demand']


def reviewed(p,row,event=None):
    group=group_for(p,row);stock,offcut=children(p,group)
    if (stock is not None and not inv.approved(p,'stock_reservations',stock)) or (offcut is not None and not inv.approved(p,'stock_offcut_reservations',offcut)):inv.fail('联合供应所有有分配侧预留须分别有效独立复核后才能领用')

    if event is not None and 'replenishment' in group:
        prior={e['id'] for e in p['stock_events'][:p['stock_events'].index(event)]}
        if any(r['event_id'] not in prior for r in group['replenishment']['releases']):inv.fail('联合补料领用早于上游未领释放')


def current_demand(p,row):
    group=group_for(p,row)
    from .stock_mixed_replenishments import frozen
    _,chain=frozen(p,group);root=core.find(p,COLLECTION,chain[0])
    if inv.demands(p,row['source_id'])!=root['demand']:inv.fail('联合供应最初材料表或完整需求已变化，历史数量保持')
    return row['demand']


def proof_issue(p,row):
    group=group_for(p,row)
    try:
        from .stock_mixed_replenishments import check_proofs
        check_proofs(p,group)
        return ''
    except HTTPException as e:return '联合供应依据待核查，历史数量保持：'+str(e.detail)


def status(p):
    audit(p);rows=[]
    from .stock_operations import reservation_progress,released_reservation
    from . import stock_offcut_reuse as reuse
    for group in p.get(COLLECTION,[]):
        stock,offcut=children(p,group);sp=reservation_progress(p,stock) if stock else [];op=reuse.progress(p,offcut) if offcut else []
        count=sum(a['issued'] for a in sp+op);held=sum(a['remaining'] for a in sp if not released_reservation(p,stock['id']))+sum(a['remaining'] for a in op if not reuse.released(p,offcut['id']))
        try:
            if held:validate_partition(p,stock or offcut);reviewed(p,stock or offcut)
            elif count:
                current_demand(p,stock or offcut)
                problem=proof_issue(p,stock or offcut)
                if problem:inv.fail(problem)
            problem=''
        except HTTPException as e:problem=str(e.detail)
        phase='released' if not count and not held else 'issued' if count==group['totals']['quantity'] else 'remaining_released' if not held else 'partially_issued' if count else 'reserved'
        rows.append(dict(record=group,stock=stock,offcut=offcut,issued_quantity=count,held_quantity=held,phase=phase,issue=problem))
    return dict(records=rows,notice=NOTICE)


@router.post('/projects/{pid}/stock-inventory/mixed-supply/preview')
def preview(pid:str,body:dict,a=Depends(core.actor)):
    if not isinstance(body,dict) or set(body)!={'input'}:raise HTTPException(422,'联合供应预览仅接受原始输入')
    with store.connect() as c:
        p=core.project(c,pid,a);result,stamp=prepare(p,body['input']);return dict(result=result,preview_hash=preview_hash(body['input'],result,stamp),ledger_hash=inv.ledger_hash(p),notice=NOTICE)


@router.post('/projects/{pid}/stock-inventory/mixed-supply')
def submit(pid:str,body:dict,a=Depends(core.actor)):
    core.edit(a);inv.fields(body,{'input','preview_hash'})
    with store.transaction() as c:
        p=core.project(c,pid,a);old=inv.begin(p,body)
        if old:
            if old not in p[COLLECTION]:inv.fail('请求标识已用于其他库存操作')
            return old
        result,stamp=prepare(p,body['input']);raw=body['input']
        if body['preview_hash']!=preview_hash(raw,result,stamp):inv.fail('联合供应预览已变化，请重新核对')
        return register(c,p,a,body,result,stamp)


def register(c,p,a,body,result,stamp):
    raw=body['input'];gid=store.uid();sid=store.uid() if result['stock']['demand']['demands'] else '';oid=store.uid() if result['offcut']['demand']['demands'] else '';now=store.now()
    common=dict(source_id=result['source_id'],proof_stamp=stamp,state='draft')
    stock=dict(**common,demand=result['stock']['demand'],allocations=raw['stock_allocations'],basis=raw['basis'],evidence_id=raw['evidence_id'],mixed_supply=dict(group_id=gid,partition='stock'))
    offcut=dict(**common,input=offcut_input(raw,result['offcut']['demand']),demand=result['offcut']['demand'],allocations=result['offcut']['allocations'],totals=result['offcut']['totals'],mixed_supply=dict(group_id=gid,partition='offcut'))
    group=dict(**result,input=raw,stock_id=sid,offcut_id=oid,proof_stamp=stamp,preview_hash=body['preview_hash'],state='registered')
    for collection,rid,data,suffix in (('stock_reservations',sid,stock,':stock'),('stock_offcut_reservations',oid,offcut,':offcut'),(COLLECTION,gid,group,'')):
        if not rid:continue
        row=inv.seal(dict(id=rid,**copy.deepcopy(data),request_id=body['request_id']+suffix,request_hash=store.digest(body),author=a['username'],created_at=now));p[collection].append(row);store.audit(c,p,a,'STOCK_MIXED_SUPPLY_REGISTERED',row)
    audit(p);store.save(c,p);return row
