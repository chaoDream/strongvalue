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
          '申请含实际交接日期、领用人及原件，独立复核后显式整单领用才减少在库并增加在外待加工数量。已领用不能释放或重审；依据失效不回滚实物数量。'
          '同材料表与完整原材预留互斥，不支持两账混配、部分领用、领后再领、再加工结果、冲销及跨项目调拨；未加工原段退回须走独立退回入口；不改变工程量、排料、报价或既有加工核对额度。')


def closed(p, row):
    return inv.event_for(p, 'offcut_issue', row['id']) or inv.event_for(p, 'offcut_release', row['id'])


def active(p, row):
    return not closed(p, row)


def source_free(p, cid, exclude=''):
    if any(r['source_id']==cid and r['id']!=exclude and not inv.event_for(p,'offcut_release',r['id']) for r in p[COLLECTION]):
        inv.fail('此材料表已有余料回用预留或领用记录，不能二次占用')
    if any(r['source_id']==cid and not inv.event_for(p,'release',r['id']) for r in p['stock_reservations']):
        inv.fail('此材料表已有完整原材预留或领料记录，不能同时回用余料')


def held(p, key, exclude=''):
    return sum(a['quantity'] for r in p[COLLECTION] if r['id']!=exclude and active(p,r) for a in r['allocations'] if a['lot_key']==key)


def balances(p, lots, *, with_issues=False):
    """Replay issues and actual unchanged-segment returns without current proof gates."""
    from . import stock_offcut_returns as returns
    by_key={l['lot_key']:l for l in lots}; issued={k:0 for k in by_key}; returned={k:0 for k in by_key}
    records={r['id']:r for r in p[COLLECTION]}; return_records={r['id']:r for r in p[returns.COLLECTION]}
    admitted=set(); applied=set(); stopped=set(); issues={}
    for e in p['stock_events']:
        kind=e['operation']
        if kind=='recover':admitted.add(e['target_id'])
        if kind=='offcut_void':stopped.add(e['target_id'])
        if kind not in ('offcut_issue','offcut_release','offcut_return','offcut_return_cancel'):continue
        row=(return_records if kind.startswith('offcut_return') else records).get(e['target_id'])
        if not row or row['id'] in applied or e.get('record_hash')!=store.digest(row):inv.fail('余料回用或退回事件、原单或历史关联无法核验')
        applied.add(row['id'])
        if kind in ('offcut_release','offcut_return_cancel'):continue
        collection=returns.COLLECTION if kind=='offcut_return' else COLLECTION
        if not inv.approved(p,collection,row) or e.get('allocations')!=row['allocations'] or e.get('totals')!=row['totals']:inv.fail('余料领用或退回事件与独立复核或分配不一致')
        if kind=='offcut_return':
            computed=returns.normalize(p,row['input'])
            if any(row[k]!=v for k,v in computed.items()) or e.get('reference_id')!=row['reference_id']:inv.fail('退回原段规格、根数、总长或领用关联无法核验')
            source=issues.get(row['reference_id'])
            if not source:inv.fail('余料退回缺少先前已执行领用')
            for a in row['allocations']:
                parent=source.get(a['allocation_index'])
                if not parent or a['quantity']>parent['quantity']-parent['returned']:inv.fail('原段退回超过对应领用分配在外余额')
                parent['returned']+=a['quantity'];returned[a['lot_key']]+=a['quantity']
        else:
            normalized,totals=allocation_values(row['demand'],row['input']['allocations'],lots)
            if normalized!=row['allocations'] or totals!=row['totals']:inv.fail('余料回用规格、数量或总长无法核验')
            issues[row['id']]={i:dict(**a,returned=0) for i,a in enumerate(row['allocations'],1)}
            for a in row['allocations']:
                lot=by_key.get(a['lot_key'])
                if not lot or lot['result_id'] not in admitted or a['lot_key'] in stopped:inv.fail('余料领用早于实际入库或晚于停用')
                issued[a['lot_key']]+=a['quantity']
                if issued[a['lot_key']]-returned[a['lot_key']]>lot['quantity']:inv.fail('余料领用超出实物当前在库数量')
    result={}
    for key,lot in by_key.items():
        custody=issued[key]-returned[key];on_hand=lot['quantity']-custody;reserved=held(p,key)
        if min(custody,on_hand)<0 or reserved>on_hand:inv.fail('余料在库、在外或预留台账不闭合，请核查原始台账')
        result[key]=dict(in_stock_quantity=on_hand,in_custody=custody,issued_quantity=issued[key],returned_quantity=returned[key],reserved=reserved,remaining=on_hand-reserved)
    return (result,issues) if with_issues else result


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


def prepare(p, raw, exclude=''):
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
    for a in rows:used[a['lot_key']]=used.get(a['lot_key'],0)+a['quantity']
    for key,count in used.items():
        lot=next(l for l in lots if l['lot_key']==key)
        if not lot['verified']:inv.fail('余料已停用或验收原件失效，不能新增回用或执行领用')
        if count+held(p,key,exclude)>lot['in_stock_quantity']:inv.fail('余料可用根数不足，已有预留或已领用，请刷新核对')
    return dict(source_id=raw['source_id'],demand=demand,allocations=rows,totals=totals),inv.proofs(p,[raw['evidence_id']])


def preview_hash(raw, result, stamp):return store.digest(dict(input=raw,result=result,proof_stamp=stamp))


def validate(p,row):
    inv.records(p)
    if closed(p,row):inv.fail('余料回用已领用或释放，仅供追溯')
    result,stamp=prepare(p,row['input'],row['id'])
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
        issued=inv.event_for(p,'offcut_issue',row['id']);released=inv.event_for(p,'offcut_release',row['id'])
        try:
            if issued:
                if inv.proofs(p,[row['input']['evidence_id']])!=row['proof_stamp']:inv.fail('历史回用交接原件已变化，已领用数量保留待核查')
                if inv.demands(p,row['source_id'])!=row['demand']:inv.fail('历史回用排料来源已变化，已领用数量保留待核查')
            elif not released:validate(p,row)
            issue=''
        except HTTPException as e:issue=str(e.detail)
        rows.append(dict(record=row,object_hash=store.digest(row),active=not issued and not released,phase='issued' if issued else 'released' if released else row['state'],issue=issue,balances=[dict(**a,allocation_index=i,outstanding=a['quantity']-a['returned']) for i,a in allocations.get(row['id'],{}).items()]))
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
    if operation not in ('execute','release'):raise HTTPException(422,'仅支持执行整单余料领用或释放未领用申请')
    inv.fields(body,{'object_hash'} if operation=='execute' else {'object_hash','basis','evidence_id'})
    kind='offcut_issue' if operation=='execute' else 'offcut_release'
    with store.transaction() as c:
        p=core.project(c,pid,a);old=inv.begin(p,body)
        if old:
            if old.get('operation')!=kind or old.get('target_id')!=rid:inv.fail('请求标识已用于其他余料操作')
            return old
        row=core.find(p,COLLECTION,rid)
        if body['object_hash']!=store.digest(row):inv.fail('余料回用申请已变化，请刷新核对')
        if closed(p,row):inv.fail('余料回用已领用或释放，不能再次执行或退回库存')
        data=dict(operation=kind,target_id=rid,record_hash=store.digest(row))
        if operation=='execute':
            if not inv.approved(p,COLLECTION,row):inv.fail('须有效独立复核后才能执行余料领用')
            validate(p,row);data.update(allocations=row['allocations'],totals=row['totals'])
        else:
            text(body['basis'],'余料释放核查原因');data.update(basis=body['basis'],evidence_id=body['evidence_id'],proof_stamp=inv.proofs(p,[body['evidence_id']]))
        return inv.commit(c,p,a,'stock_events',body,data,'STOCK_'+kind.upper())
