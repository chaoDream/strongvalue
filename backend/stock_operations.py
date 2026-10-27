"""Reviewed, explicitly executed full-bar handovers and custody movements."""
from datetime import date, datetime
from fractions import Fraction
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException
from . import main as core, store, stock_inventory as inv
from .compound_takeoff import text
from .engine import precise
from .stock_cutting import dec

router = APIRouter(prefix='/api/v1')
ISSUE_KINDS={'issue','partial_issue'}
KINDS = {'issue': '整单领料','partial_issue':'分批领料', 'consume': '完整原材投入加工', 'return': '未加工完整原材退回'}
NOTICE = ('完整原材领料仅从当前有效已审预留建立，可首次整单领料或按原分配序号分批领料；提交及复核均不改变库存，编制人显式执行已审单据后才记账。'
          '本次领出的根数从预留转成出库，未领部分继续占用，不增加可用库存；同材料表已领料后不能重新做全量预留；已释放未领余量仅可走独立补料入口。投入加工和退回按已执行领料的逐项未加工余额记录，可分批但不得超量。'
          '投入加工按完整原材根数退出完整根台账，不代表净安装工程量或加工损耗；加工后的余料不能当作完整原材退回。'
          '来源修订、原件失效或审批问题不撤销既有实物记账；新领料阻断，已执行领料的后续实际加工／完整退回可凭当前有效交接原件记录，历史问题另行警示。'
          '已执行单据不可取消或重审；未执行单据可有据取消。余料回用及未加工原段退回须走各自独立入口；完整原材在库盘点须走独立盘点入口；分批领料须预览、独立复核及显式执行；部分领出后可有据释放未领余量，不退回已领数量；未领余量补料另走独立入口，不重开全量需求。余料分批领用使用独立入口；尚不支持在外或已加工盘点、跨项目共享或采购闭环；不改净工程量、排料或报价。')


def execution(p, oid):
    return next((e for e in p['stock_events'] if e['operation'] in KINDS and e['target_id'] == oid), None)


def issued_reservation(p, rid):
    return next((e for e in p['stock_events'] if e['operation'] in ISSUE_KINDS and e.get('reservation_id') == rid), None)



def released_reservation(p,rid):return inv.event_for(p,'release',rid) or inv.event_for(p,'release_remaining',rid)


def reservation_progress(p,reservation):
    issued={i:0 for i in range(1,len(reservation['allocations'])+1)}
    for event in p['stock_events']:
        if event['operation'] in ISSUE_KINDS and event.get('reservation_id')==reservation['id']:
            for a in event['allocations']:
                index=a['allocation_index']
                if index not in issued:inv.fail('领料分配缺少原预留序号')
                issued[index]+=a['quantity']
    rows=[dict(**a,allocation_index=i,issued=issued[i],remaining=a['quantity']-issued[i]) for i,a in enumerate(reservation['allocations'],1)]
    if any(a['remaining']<0 for a in rows):inv.fail('累计领料超出原预留分配数量')
    return rows


def remaining_allocations(p,reservation):
    return [dict(demand_key=a['demand_key'],receipt_id=a['receipt_id'],allocation_index=a['allocation_index'],quantity=a['remaining']) for a in reservation_progress(p,reservation) if a['remaining']]


def frozen_rows(p,reservation):
    rows=[]
    for i,a in enumerate(reservation['allocations'],1):
        receipt=core.find(p,'stock_receipts',a['receipt_id']);raw=receipt['input'];d=next(d for d in reservation['demand']['demands'] if d['key']==a['demand_key'])
        rows.append(dict(**a,allocation_index=i,name=d['name'],lot_code=raw['lot_code'],warehouse=raw['warehouse'],material_spec=raw['material_spec'],stock_length=receipt['normalized']['stock_length'],unit_weight=receipt['normalized']['unit_weight']))
    return rows


def selected_rows(rows,selections):
    if not isinstance(selections,list) or not 1<=len(selections)<=100:raise HTTPException(422,'分批领料须选择1至100条不同原预留分配及正整数本次根数')
    by_index={a['allocation_index']:a for a in rows};seen=set();result=[]
    for a in selections:
        if not isinstance(a,dict) or set(a)!={'allocation_index','quantity'} or type(a['allocation_index']) is not int or a['allocation_index'] not in by_index or a['allocation_index'] in seen or type(a['quantity']) is not int or not 1<=a['quantity']<=1000000:raise HTTPException(422,'分批领料须不同的原预留序号及1至1000000正整数根数')
        seen.add(a['allocation_index']);result.append({**by_index[a['allocation_index']],'quantity':a['quantity']})
    return result


def closed(p, row):
    return execution(p, row['id']) or inv.event_for(p, 'cancel', row['id'])


def reviewable(p, row, actor):
    if row['author'] == actor['username']:
        raise HTTPException(403, '领用单据须由另一独立账号复核')
    if closed(p, row):
        inv.fail('单据已执行或取消，仅供追溯，不能重新复核')


@precise
def totals(rows):
    length = sum((Fraction(r['stock_length'])*r['quantity'] for r in rows), Fraction(0))
    mass = sum((Fraction(r['stock_length'])*Fraction(r['unit_weight'])*r['quantity'] for r in rows), Fraction(0))
    return dict(quantity=sum(r['quantity'] for r in rows), length=dec(length), mass=dec(mass), length_unit='m', mass_unit='kg')


def balances(p):
    """Replay executed movements only. A failed current proof never credits stock back."""
    from . import stock_counts as counts
    from .stock_replenishments import audit
    audit(p)
    stock = {r['id']: dict(issued=0, returned=0, consumed=0,adjustment_gain=0,adjustment_loss=0,count_revision=0) for r in p['stock_receipts']}
    issues = {}; reservations = {}; applied = set(); stopped=set(); released=set()
    ops = {r['id']: r for r in p['stock_operations']}
    for event in p['stock_events']:
        kind = event['operation']
        if kind=='void':stopped.add(event['target_id'])
        if kind in ('release','release_remaining'):
            rid=event['target_id']
            if kind=='release_remaining':
                reservation=core.find(p,'stock_reservations',rid);already=reservations.get(rid,{})
                remaining=[dict(demand_key=a['demand_key'],receipt_id=a['receipt_id'],allocation_index=i,quantity=a['quantity']-already.get(i,0)) for i,a in enumerate(reservation['allocations'],1) if a['quantity']>already.get(i,0)]
                if rid in released or not any(already.values()) or not remaining or event.get('object_hash')!=store.digest(reservation) or event.get('allocations')!=remaining:inv.fail('未领余量释放事件、原预留或执行顺序无法核验')
            elif rid in reservations:inv.fail('已有领料不能通过整单释放覆盖历史')
            released.add(rid)
        if kind in ('stock_count','stock_count_cancel'):
            row=next((r for r in p[counts.COLLECTION] if r['id']==event['target_id']),None)
            if not row or row['id'] in applied or event.get('record_hash')!=store.digest(row):inv.fail('完整原材盘点事件或单据关联无法核验')
            inv.intact(row);inv.intact(event);applied.add(row['id'])
            if kind=='stock_count_cancel':continue
            receipt=next((r for r in p['stock_receipts'] if r['id']==row.get('receipt_id')),None)
            if not receipt or receipt['id'] in stopped:inv.fail('完整原材盘点来源缺失或晚于停用')
            if not inv.approved(p,counts.COLLECTION,row):inv.fail('已执行盘点缺少匹配的独立复核')
            n=stock[receipt['id']];computed=counts.normalize(row['input'],receipt,n)
            if any(row.get(k)!=v for k,v in computed.items()) or any(event.get(k)!=row[k] for k in ('receipt_id','before','after','delta','length_delta','mass_delta')):inv.fail('完整原材实盘、差异或冻结事件无法核验')
            n['adjustment_gain']+=max(0,row['delta']);n['adjustment_loss']+=max(0,-row['delta']);n['count_revision']+=1
            continue
        if kind not in KINDS:
            continue
        row = ops.get(event['target_id'])
        if not row or row['id'] in applied or row['kind'] != kind or event['record_hash'] != store.digest(row) or row['allocations'] != event['allocations'] or row['totals'] != event['totals'] or row['totals'] != totals(event['allocations']):
            inv.fail('领用记账内容或单据关联校验失败，请恢复原始台账')
        if not inv.approved(p, 'stock_operations', row):
            inv.fail('已执行领用单据缺少匹配的独立复核，请恢复原始记录')
        applied.add(row['id'])
        if kind in ISSUE_KINDS:
            rid=row['input']['reference_id'];reservation=core.find(p,'stock_reservations',rid)
            if 'replenishment' in reservation:
                release_id=reservation['replenishment']['release_id']
                if not any(x['id']==release_id for x in p['stock_events'][:p['stock_events'].index(event)]):inv.fail('补料领出早于上游未领余量释放')
            if rid in released or event['reservation_id']!=rid or row['reservation_id']!=rid or not inv.approved(p,'stock_reservations',reservation):inv.fail('领料预留关联、独立复核或释放顺序异常')
            before=reservations.setdefault(rid,{})
            expected=frozen_rows(p,reservation)
            if kind=='issue':
                if any(before.values()) or row['input']['selections']!=[]:inv.fail('整单领料不能重复执行或覆盖既有分批领料')
            else:expected=selected_rows(expected,row['input']['selections'])
            if expected!=row['allocations'] or any(a['receipt_id'] in stopped for a in expected):inv.fail('本次领料分配与原预留不符或晚于批次停用')
            for a in expected:
                index=a['allocation_index'];before[index]=before.get(index,0)+a['quantity']
                if before[index]>reservation['allocations'][index-1]['quantity']:inv.fail('累计领料超过原预留逐项未领余额')
            issues[row['id']]={a['allocation_index']:dict(**a,consumed=0,returned=0) for a in row['allocations']}
        else:
            source = issues.get(row['input']['reference_id'])
            if source is None:
                inv.fail('投入加工或退回缺少先前已执行领料')
        for a in row['allocations']:
            if a['receipt_id'] not in stock or type(a['quantity']) is not int or a['quantity'] <= 0:
                inv.fail('领用记账批次或根数异常')
            if kind not in ISSUE_KINDS:
                parent = source.get(a['allocation_index'])
                if not parent or any(parent[k] != a[k] for k in a if k != 'quantity') or a['quantity'] > parent['quantity']-parent['consumed']-parent['returned']:
                    inv.fail('领用记账超过对应批次未加工余额或分配关联异常')
                parent['consumed' if kind == 'consume' else 'returned'] += a['quantity']
            n=stock[a['receipt_id']];n[{'issue':'issued','partial_issue':'issued','consume':'consumed','return':'returned'}[kind]] += a['quantity']
            receipt=core.find(p,'stock_receipts',a['receipt_id'])
            if receipt['normalized']['quantity']+n['adjustment_gain']-n['adjustment_loss']-n['issued']+n['returned']<0:inv.fail('完整原材出库超出当时实际在库根数，不能用后续盘盈补记')
    for rid, n in stock.items():
        n['in_custody'] = n['issued']-n['returned']-n['consumed']
        count = core.find(p, 'stock_receipts', rid)['normalized']['quantity']+n['adjustment_gain']-n['adjustment_loss']
        n['adjusted_quantity']=count
        n['on_hand'] = count-n['issued']+n['returned']
        if min(n.values()) < 0 or n['on_hand']+n['in_custody']+n['consumed'] != count:
            inv.fail('完整原材库存、在外及加工根数台账不闭合')
    return stock, issues


def reference_rows(p, rid):
    reservation = core.find(p, 'stock_reservations', rid)
    if not inv.approved(p, 'stock_reservations', reservation):
        inv.fail('整单领料须选择当前有效独立复核的预留')
    inv.validate_reservation(p, reservation)
    return reservation,frozen_rows(p,reservation)


def prepare(p, raw):
    expected = {'kind','reference_id','reference_hash','selections','custodian','business_date','basis','evidence_id','confirmed'}
    if not isinstance(raw, dict) or set(raw) != expected or not isinstance(raw['kind'], str) or raw['kind'] not in KINDS:
        raise HTTPException(422, '领用单据须明确类型、来源、分配、交接对象、业务日期、说明和原件及实际范围确认')
    for k in ('reference_id','reference_hash','custodian','basis','evidence_id'):
        text(raw[k], {'custodian':'领用／交接对象','basis':'实物交接或加工说明'}.get(k,k), 2000 if k == 'basis' else 200)
    if raw['confirmed'] is not True:
        raise HTTPException(422, '须据原件确认实际完整原材交接或投入加工，不能把计划当成实物')
    try:
        business_date = date.fromisoformat(raw['business_date'])
        if business_date.isoformat() != raw['business_date'] or business_date > datetime.now(ZoneInfo('Asia/Shanghai')).date():
            raise ValueError()
    except (TypeError, ValueError):
        raise HTTPException(422, '业务日期须为不晚于今天的YYYY-MM-DD实际交接或加工日期') from None
    stock, issued = balances(p)
    if raw['kind'] in ISSUE_KINDS:
        if raw['kind']=='issue' and raw['selections'] != []:
            raise HTTPException(422, '整单领料继承预留全部批次及根数，不接受手改分配')
        source, rows = reference_rows(p, raw['reference_id'])
        if raw['kind']=='issue' and issued_reservation(p,source['id']):inv.fail('已有分批领料，请按原预留未领余额继续使用分批入口')
        if raw['kind']=='partial_issue':
            rows=selected_rows(rows,raw['selections']);left={a['allocation_index']:a['remaining'] for a in reservation_progress(p,source)}
            if any(a['quantity']>left[a['allocation_index']] for a in rows):inv.fail('本次领料超出对应原预留尚未领出的根数')
        for a in rows:
            if stock[a['receipt_id']]['on_hand'] < inv.held(p, a['receipt_id']):
                inv.fail('批次实物在库根数不足以覆盖现有预留')
        context = dict(source_name=source['demand']['source_name'], source_revision=source['demand']['source_revision'], reservation_id=source['id'])
    else:
        source = core.find(p, 'stock_operations', raw['reference_id'])
        event = execution(p, source['id'])
        if source['kind'] not in ISSUE_KINDS or not event or source['id'] not in issued:
            inv.fail('须选择本项目已执行的整单或分批领料记录')
        choices = raw['selections']
        if not isinstance(choices, list) or not 1 <= len(choices) <= 100:
            raise HTTPException(422, '须明确1至100条领料分配及本次正整数根数')
        rows = []; seen = set()
        for s in choices:
            if not isinstance(s, dict) or set(s) != {'allocation_index','quantity'} or type(s['allocation_index']) is not int or type(s['quantity']) is not int or not 1 <= s['quantity'] <= 1000000 or s['allocation_index'] in seen:
                raise HTTPException(422, '本次分配须不重复的领料分配序号及正整数根数')
            seen.add(s['allocation_index']); parent = issued[source['id']].get(s['allocation_index'])
            if not parent or s['quantity'] > parent['quantity']-parent['consumed']-parent['returned']:
                inv.fail('本次根数超过该分配未加工且未退回的完整原材余额')
            rows.append({k:v for k,v in parent.items() if k not in ('consumed','returned')} | {'quantity':s['quantity']})
        context = dict(source_name=source['source_name'], source_revision=source['source_revision'], reservation_id=source['reservation_id'])
    if store.digest(source) != raw['reference_hash']:
        inv.fail('领用来源或复核状态已变化，请刷新重新核查')
    return dict(kind=raw['kind'], allocations=rows, totals=totals(rows), **context)


def validate(p, row):
    inv.intact(row)
    if closed(p, row):
        inv.fail('领用单据已执行或取消，仅供追溯')
    computed = prepare(p, row['input'])
    stamp=inv.proofs(p,[row['input']['evidence_id']])
    if any(row[k] != v for k,v in computed.items()) or stamp != row['proof_stamp'] or (row['kind']=='partial_issue' and row.get('preview_hash')!=preview_hash(row['input'],computed,stamp)):
        inv.fail('领用单据、实物范围或原件已变化，请取消后重新建立')


def status(p):
    _, issues = balances(p); result = []
    for row in p['stock_operations']:
        event = execution(p, row['id']); cancelled = inv.event_for(p, 'cancel', row['id'])
        phase = 'executed' if event else 'cancelled' if cancelled else row['state']
        try:
            if event:
                if inv.proofs(p, [row['input']['evidence_id']]) != row['proof_stamp']:
                    inv.fail('历史领用原件已变化；既有实物记账保持，需另行核查')
            elif not cancelled:
                validate(p, row)
            issue = ''
        except HTTPException as e:
            issue = str(e.detail)
        result.append(dict(record=row, object_hash=store.digest(row), phase=phase, issue=issue,
                           balances=list(issues.get(row['id'], {}).values()) if event and row['kind'] in ISSUE_KINDS else []))
    return result


def preview_hash(raw,result,stamp):return store.digest(dict(input=raw,result=result,proof_stamp=stamp))


@router.post('/projects/{pid}/stock-inventory/operations/preview')
def preview(pid:str,body:dict,a=Depends(core.actor)):
    if not isinstance(body,dict) or set(body)!={'input'}:raise HTTPException(422,'领料预览仅接受原始输入')
    with store.connect() as c:
        p=core.project(c,pid,a);inv.records(p);result=prepare(p,body['input']);stamp=inv.proofs(p,[body['input']['evidence_id']]);return dict(result=result,preview_hash=preview_hash(body['input'],result,stamp),ledger_hash=inv.ledger_hash(p),notice=NOTICE)


@router.post('/projects/{pid}/stock-inventory/operations')
def submit(pid: str, body: dict, a=Depends(core.actor)):
    core.edit(a); inv.fields(body, {'input','preview_hash'} if isinstance(body.get('input'),dict) and body['input'].get('kind')=='partial_issue' else {'input'})
    with store.transaction() as c:
        p = core.project(c, pid, a); old = inv.begin(p, body)
        if old:
            if old not in p['stock_operations']:
                inv.fail('请求标识已用于其他库存操作')
            return old
        result = prepare(p, body['input']); stamp = inv.proofs(p, [body['input']['evidence_id']])
        extra={}
        if result['kind']=='partial_issue':
            value=preview_hash(body['input'],result,stamp)
            if body['preview_hash']!=value:inv.fail('分批领料预览已变化，请重新核对')
            extra['preview_hash']=value
        return inv.commit(c,p,a,'stock_operations',body,dict(input=body['input'],**result,proof_stamp=stamp,state='draft',**extra),'STOCK_OPERATION_SUBMITTED')


@router.post('/projects/{pid}/stock-inventory/operations/{oid}/execute')
def execute(pid: str, oid: str, body: dict, a=Depends(core.actor)):
    core.edit(a); inv.fields(body, {'object_hash'})
    with store.transaction() as c:
        p = core.project(c, pid, a); old = inv.begin(p, body)
        if old:
            if old.get('target_id') != oid or old.get('operation') not in KINDS:
                inv.fail('请求标识已用于另一单据执行')
            return old
        row = core.find(p,'stock_operations',oid)
        if body['object_hash'] != store.digest(row):
            inv.fail('领用单据已变化，请刷新核对')
        if not inv.approved(p,'stock_operations',row):
            inv.fail('领用单据尚未获得有效独立复核，不能记账')
        validate(p,row)
        return inv.commit(c,p,a,'stock_events',body,dict(operation=row['kind'],target_id=oid,record_hash=store.digest(row),reservation_id=row['reservation_id'],
                          allocations=row['allocations'],totals=row['totals']),'STOCK_OPERATION_EXECUTED')
