"""Evidence-backed reuse of accepted offcuts at the exact approved stock length."""
from datetime import date, datetime
from fractions import Fraction
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException
from . import main as core, store, stock_inventory as inv
from .compound_takeoff import text
from .stock_cutting import dec
from .engine import precise

router = APIRouter(prefix='/api/v1')
COLLECTION = 'stock_offcut_reservations'
NOTICE = ('仅本项目已验收入库且依据有效的余料，按当前已审材料表的全部定尺需求回用；每段长度和每米质量必须完全相等，材质截面及质量另据原件确认。'
          '须先以实物余料长度核定排料并独立审定，不按总长度折算、拼接或自动替换原排料。保存申请立即预留；拒绝、待补件和来源失效仍占用，须有据释放。'
          '申请含实际交接日期、领用人及原件，独立复核后可首次整单或按独立批单分批领用；本次执行才减少在库和未领占用并增加对应批单在外数量。首次领出后原预留不能重审或整单释放，未领继续占用，可有据释放未领余量；依据失效不回滚实物数量。'
          '普通回用与完整原材预留互斥，按定尺联合供应须走独立联合核定入口，不支持用普通申请拼配、同材料表重新全量预留再领、冲销及跨项目调拨；仅已释放未领额可走独立余料补料入口。未加工原段退回及余料实际再加工须走各自独立入口；不改变工程量、排料、报价或既有加工核对额度。')


def issued(p,rid):
    return inv.event_for(p,'offcut_issue',rid) or next((e for e in p['stock_events'] if e['operation']=='offcut_partial_issue' and e.get('reservation_id')==rid),None)


def released(p,rid):return inv.event_for(p,'offcut_release',rid) or inv.event_for(p,'offcut_release_remaining',rid)


def closed(p,row):return issued(p,row['id']) or released(p,row['id'])


def progress(p,row):
    taken={i:0 for i in range(1,len(row['allocations'])+1)}
    for e in p['stock_events']:
        if e['operation']=='offcut_issue' and e['target_id']==row['id']:
            for i,a in enumerate(e['allocations'],1):taken[i]+=a['quantity']
        elif e['operation']=='offcut_partial_issue' and e.get('reservation_id')==row['id']:
            for a in e['allocations']:
                if a['allocation_index'] not in taken:inv.fail('余料批单缺少原预留分配关联')
                taken[a['allocation_index']]+=a['quantity']
    result=[dict(**a,allocation_index=i,issued=taken[i],remaining=a['quantity']-taken[i]) for i,a in enumerate(row['allocations'],1)]
    if any(a['remaining']<0 for a in result):inv.fail('余料累计领出超出原预留逐项数量')
    return result


def active(p,row):return not released(p,row['id']) and any(a['remaining'] for a in progress(p,row))


def issue_source(p,rid):
    row=next((r for r in p[COLLECTION] if r['id']==rid),None)
    if row:
        if not inv.event_for(p,'offcut_issue',rid):inv.fail('须引用已执行余料领用批次，部分领用的原预留不是已领批单')
        return row
    row=core.find(p,'stock_offcut_issues',rid)
    if not inv.event_for(p,'offcut_partial_issue',rid):inv.fail('须引用本项目已执行余料分批领用单')
    return row


def batch_proof_issue(p,rid):
    row=next((r for r in p['stock_offcut_issues'] if r['id']==rid),None)
    problems=[]
    if row:
        from .stock_offcut_issues import proof_issue
        problems.append(proof_issue(p,row))
        reservation=core.find(p,COLLECTION,row['reservation_id'])
    else:reservation=core.find(p,COLLECTION,rid)
    if 'replenishment' in reservation:
        from .stock_offcut_replenishments import release_proof_issue
        problems.append(release_proof_issue(p,reservation))
    if 'mixed_supply' in reservation:
        from .stock_mixed_supply import proof_issue
        problems.append(proof_issue(p,reservation))
    return '；'.join(v for v in problems if v)


def issue_allocations(row):return {a.get('allocation_index',i):a for i,a in enumerate(row['allocations'],1)}


def source_free(p, cid, exclude=''):
    if any(r['source_id']==cid and r['id']!=exclude and not inv.event_for(p,'offcut_release',r['id']) for r in p[COLLECTION]):
        inv.fail('此材料表已有余料回用预留或领用记录，不能二次占用')
    if any(r['source_id']==cid and not inv.event_for(p,'release',r['id']) for r in p['stock_reservations']):
        inv.fail('此材料表已有完整原材预留或领料记录，不能同时回用余料')


def held(p, key, exclude=''):
    return sum(a['remaining'] for r in p[COLLECTION] if r['id']!=exclude and not released(p,r['id']) for a in progress(p,r) if a['lot_key']==key)


def balances(p, lots, *, with_issues=False):
    from .stock_offcut_ledger import replay
    result=replay(p,lots)
    return (result['movements'],result['issues']) if with_issues else result['movements']


@precise
def allocation_values(demand, allocations, lots):
    if not isinstance(allocations,list) or not 1<=len(allocations)<=100:
        raise HTTPException(422,'余料回用须填写1至100条分配，完整覆盖所选排料需求')
    by_key={l['lot_key']:l for l in lots}; needs={d['key']:d for d in demand['demands']}; seen=set(); totals={}; rows=[]
    length=Fraction(0); mass=Fraction(0)
    for a in allocations:
        if not isinstance(a,dict) or set(a)!={'demand_key','lot_key','quantity'} or type(a['quantity']) is not int or not 1<=a['quantity']<=1000000 or not isinstance(a['demand_key'],str) or not isinstance(a['lot_key'],str):
            raise HTTPException(422,'余料分配须为需求标识、已入库余料标识及正整数根数')
        key=(a['demand_key'],a['lot_key'])
        if key in seen:raise HTTPException(422,'同需求同余料批次不能重复分配')
        seen.add(key); lot=by_key.get(a['lot_key']); need=needs.get(a['demand_key'])
        if not lot or not need:inv.fail('余料批次或排料需求不属于当前项目')
        if Fraction(lot['length'])!=Fraction(need['stock_length']) or Fraction(lot['unit_weight'])!=Fraction(need['unit_weight']):
            inv.fail('余料每段实测长度及每米质量须与已审排料定尺完全相等，不能按总长折算或拼接')
        totals[a['demand_key']]=totals.get(a['demand_key'],0)+a['quantity']
        size=Fraction(lot['length'])*a['quantity']; weight=size*Fraction(lot['unit_weight']);length+=size;mass+=weight
        rows.append(dict(**a,name=need['name'],lot_code=lot['lot_code'],warehouse=lot['warehouse'],material_spec=lot['material_spec'],stock_length=lot['length'],unit_weight=lot['unit_weight'],result_id=lot['result_id'],total_length=dec(size),mass=dec(weight)))
    if totals!={k:d['quantity'] for k,d in needs.items()}:inv.fail('余料分配须恰好覆盖材料表全部已审排料需求，不得遗漏或超量')
    return rows,dict(quantity=sum(a['quantity'] for a in rows),length=dec(length),mass=dec(mass),length_unit='m',mass_unit='kg')


def prepare(p, raw, exclude='',remaining=False):
    fields={'source_id','demand_hash','allocations','basis','evidence_id','confirmed','custodian','business_date'}
    if not isinstance(raw,dict) or set(raw)!=fields:raise HTTPException(422,'须填写排料来源、全部余料分配、实物兼容及交接说明、领用人、实际日期、原件及确认')
    for k in ('source_id','demand_hash','basis','evidence_id','custodian'):text(raw[k],'回用兼容及实际交接说明' if k=='basis' else '回用来源、领用人或原件',2000 if k=='basis' else 200)
    if raw['confirmed'] is not True:raise HTTPException(422,'须据原件确认同材质截面、质量合格、定尺相等及实际交接，不以每米质量代替材质确认')
    try:
        d=date.fromisoformat(raw['business_date'])
        if d.isoformat()!=raw['business_date'] or d>datetime.now(ZoneInfo('Asia/Shanghai')).date():raise ValueError()
    except (TypeError,ValueError):raise HTTPException(422,'实际交接日期须为不晚于今天的YYYY-MM-DD') from None
    inv.records(p);source_free(p,raw['source_id'],exclude);demand=inv.demands(p,raw['source_id'])
    if demand['demand_hash']!=raw['demand_hash']:inv.fail('排料需求已变化，请重新读取并分配余料')
    from .stock_processing_results import status as processing_status
    lots=processing_status(p)['lots']; rows,totals=allocation_values(demand,raw['allocations'],lots)
    used={}
    amounts=progress(p,core.find(p,COLLECTION,exclude)) if remaining else rows
    for a in amounts:
        count=a['remaining'] if remaining else a['quantity']
        if count:used[a['lot_key']]=used.get(a['lot_key'],0)+count
    for key,count in used.items():
        lot=next(l for l in lots if l['lot_key']==key)
        if not lot['verified']:inv.fail('余料已停用或验收原件失效，不能新增回用或执行领用')
        if count+held(p,key,exclude)>lot['in_stock_quantity']:inv.fail('余料可用根数不足，已有预留或已领用，请刷新核对')
    return dict(source_id=raw['source_id'],demand=demand,allocations=rows,totals=totals),inv.proofs(p,[raw['evidence_id']])


def preview_hash(raw, result, stamp):return store.digest(dict(input=raw,result=result,proof_stamp=stamp))


def validate(p,row,allow_partial=False):
    inv.records(p)
    if (closed(p,row) and not allow_partial) or released(p,row['id']) or not active(p,row):inv.fail('余料回用已领用或释放，不能继续领用或重审')
    if 'mixed_supply' in row:
        from .stock_mixed_supply import validate_partition
        validate_partition(p,row);return
    if 'replenishment' in row:
        from .stock_offcut_replenishments import validate as validate_replenishment
        validate_replenishment(p,row,remaining=allow_partial);return
    result,stamp=prepare(p,row['input'],row['id'],remaining=allow_partial)
    if any(row[k]!=v for k,v in result.items()) or row['proof_stamp']!=stamp or row['preview_hash']!=preview_hash(row['input'],result,stamp):
        inv.fail('余料回用来源、原件或预览已变化，请有据释放后重新核定')


def reviewable(p,row,actor):
    if row['author']==actor['username']:raise HTTPException(403,'余料回用须由另一独立账号复核')
    if closed(p,row):inv.fail('余料回用已领用或释放，不能重审')


def status(p):
    from .stock_processing_results import balances as processing_balances
    _,lots=processing_balances(p);_,allocations=balances(p,lots,with_issues=True)
    rows=[]
    for row in p[COLLECTION]:
        did_issue=issued(p,row['id']);did_release=released(p,row['id']);details=progress(p,row);sent=sum(a['issued'] for a in details);left=sum(a['remaining'] for a in details)
        try:
            if did_issue:
                if inv.proofs(p,[row['input']['evidence_id']])!=row['proof_stamp']:inv.fail('历史回用交接原件已变化，已领用数量保留待核查')
                from .stock_offcut_replenishments import current_demand,release_proof_issue
                problem=release_proof_issue(p,row)
                if problem:inv.fail(problem)
                if current_demand(p,row)!=row['demand']:inv.fail('历史回用排料来源已变化，已领用数量保留待核查')
            elif not did_release:validate(p,row)
            issue=''
        except HTTPException as e:issue=str(e.detail)
        rows.append(dict(record=row,object_hash=store.digest(row),active=not did_release and left>0,phase='remaining_released' if did_release and did_issue else 'partially_issued' if did_issue and left else 'issued' if did_issue else 'released' if did_release else row['state'],progress=details,issued_quantity=sent,unissued_quantity=left,held_quantity=0 if did_release else left,issue=issue,balances=[dict(**a,allocation_index=i,outstanding=a['quantity']-a['returned']-a['consumed']-a['processing']) for i,a in allocations.get(row['id'],{}).items()]))
    return dict(reservations=rows,notice=NOTICE)


@router.post('/projects/{pid}/stock-inventory/offcut-reuse/preview')
def preview(pid:str,body:dict,a=Depends(core.actor)):
    if not isinstance(body,dict) or set(body)!={'input'}:raise HTTPException(422,'余料回用预览仅接受申请输入')
    with store.connect() as c:
        p=core.project(c,pid,a);result,stamp=prepare(p,body['input'])
        return dict(result=result,preview_hash=preview_hash(body['input'],result,stamp),ledger_hash=inv.ledger_hash(p),notice=NOTICE)


@router.post('/projects/{pid}/stock-inventory/offcut-reuse')
def submit(pid:str,body:dict,a=Depends(core.actor)):
    core.edit(a);inv.fields(body,{'input','preview_hash'})
    with store.transaction() as c:
        p=core.project(c,pid,a);old=inv.begin(p,body)
        if old:
            if old not in p[COLLECTION]:inv.fail('请求标识已用于其他库存操作')
            return old
        result,stamp=prepare(p,body['input'])
        if body['preview_hash']!=preview_hash(body['input'],result,stamp):inv.fail('余料回用预览已变化，请重新预览')
        return inv.commit(c,p,a,COLLECTION,body,dict(input=body['input'],**result,proof_stamp=stamp,preview_hash=body['preview_hash'],state='draft'),'STOCK_OFFCUT_RESERVED')


@router.post('/projects/{pid}/stock-inventory/offcut-reuse/{rid}/{operation}')
def movement(pid:str,rid:str,operation:str,body:dict,a=Depends(core.actor)):
    core.edit(a)
    if operation not in ('execute','release','release-remaining'):raise HTTPException(422,'仅支持执行整单余料领用或释放未领用申请')
    inv.fields(body,{'object_hash'} if operation=='execute' else {'object_hash','basis','evidence_id'})
    kind='offcut_issue' if operation=='execute' else 'offcut_release_remaining' if operation=='release-remaining' else 'offcut_release'
    with store.transaction() as c:
        p=core.project(c,pid,a);old=inv.begin(p,body)
        if old:
            if old.get('operation')!=kind or old.get('target_id')!=rid:inv.fail('请求标识已用于其他余料操作')
            return old
        row=core.find(p,COLLECTION,rid)
        if body['object_hash']!=store.digest(row):inv.fail('余料回用申请已变化，请刷新核对')
        if operation=='execute' and ('replenishment' in row or 'mixed_supply' in row):inv.fail('补料核定不代表实际交接，须另建余料分批领用单并独立复核后执行')
        if operation!='release-remaining' and closed(p,row):inv.fail('余料回用已领用或释放，不能再次整单执行或释放')
        if operation=='release-remaining':
            from .stock_processing_results import balances as raw_balances
            _,lots=raw_balances(p);balances(p,lots)
            if released(p,rid) or not issued(p,rid) or not active(p,row):inv.fail('仅可释放已部分领用且尚未关闭的未领余量')
        data=dict(operation=kind,target_id=rid,record_hash=store.digest(row))
        if operation=='execute':
            if not inv.approved(p,COLLECTION,row):inv.fail('须有效独立复核后才能执行余料领用')
            validate(p,row);data.update(allocations=row['allocations'],totals=row['totals'])
        else:
            if operation=='release-remaining':
                from .stock_offcut_issues import selection_values
                selected=[dict(allocation_index=a['allocation_index'],quantity=a['remaining']) for a in progress(p,row) if a['remaining']]
                allocations,totals=selection_values(row,selected);data.update(allocations=allocations,totals=totals)
            text(body['basis'],'余料释放核查原因');data.update(basis=body['basis'],evidence_id=body['evidence_id'],proof_stamp=inv.proofs(p,[body['evidence_id']]))
        return inv.commit(c,p,a,'stock_events',body,data,'STOCK_'+kind.upper())
