"""Actual machining reconciliation and separately accepted offcut lots; never inferred from plans."""
from datetime import date, datetime
from fractions import Fraction
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException
from . import main as core, store, stock_inventory as inv, stock_operations as ops
from .compound_takeoff import text
from .engine import number, precise
from .measurement_units import LENGTH
from .stock_cutting import dec

router = APIRouter(prefix='/api/v1')
METRICS = ('finished_length', 'kerf_length', 'trim_length', 'scrap_length')
NOTICE = ('加工结果只引用已执行的完整原材投入加工单，按实际加工根数和实测成品、锯缝、切除、报废及余料逐项核对原件。'
          '每项原材总长必须精确等于全部实际去向总长，单位换算后用有理数核对，不按排料预计余料自动入库或用容差补差。'
          '提交、预览及独立复核不入库；显式执行后才登记独立余料批次，并占用对应加工单的已核对根数，禁止重复验收。'
          '余料继承原批次材质截面及每米质量，但每段实测长度、根数、库位和可留用验收均须当前原件。'
          '旧来源失效不撤销既有加工或余料入库；当前入库依据失效时保留数量并标待核查。停用余料不退回已核对加工额度，不等于实物报废或冲销。'
          '余料与完整原材分账，不能当作原长完整根退回或直接在原预留入口使用；暂不支持余料自动回用、再加工、跨项目调拨、采购或已执行结果冲销。')


def execution(p, rid):
    return inv.event_for(p, 'recover', rid)


def closed(p, row):
    return execution(p, row['id']) or inv.event_for(p, 'cancel_processing', row['id'])


def reviewable(p, row, actor):
    if row['author'] == actor['username']:
        raise HTTPException(403, '加工结果及余料验收须由另一独立账号复核')
    if closed(p, row):
        inv.fail('加工结果已执行或取消，仅供追溯，不能重审')


def source(p, raw):
    row = core.find(p, 'stock_operations', raw['consumption_id'])
    if row['kind'] != 'consume' or not ops.execution(p, row['id']):
        inv.fail('加工结果须引用本项目已执行的完整原材投入加工单')
    if store.digest(row) != raw['consumption_hash']:
        inv.fail('来源加工单已变化，请重新读取')
    return row


def integer(value, label):
    if type(value) is not int or not 1 <= value <= 1000000:
        raise HTTPException(422, label+'须为1至1000000正整数')
    return value


def evidence(mapping, keys, ids):
    if not isinstance(mapping, dict) or set(mapping) != set(keys):
        raise HTTPException(422, '实际加工及余料字段须逐项关联原件，不能缺项或夹带字段')
    ids.extend(text(v, '实际加工或余料原件', 200) for v in mapping.values())


@precise
def normalize(p, raw):
    fields = {'consumption_id','consumption_hash','business_date','inspector','basis','evidence_id','confirmed','rows'}
    if not isinstance(raw, dict) or set(raw) != fields:
        raise HTTPException(422, '须填写来源加工单、实际日期、验收人、实际加工及偏差说明、原件、确认及逐项结果')
    for k in ('consumption_id','consumption_hash','inspector','basis','evidence_id'):
        text(raw[k], '实际加工验收说明' if k=='basis' else '加工来源、验收人或原件', 2000 if k=='basis' else 200)
    if raw['confirmed'] is not True:
        raise HTTPException(422, '须据实测及原件确认实际加工和余料可留用，不以计划替代实物')
    try:
        d = date.fromisoformat(raw['business_date'])
        if d.isoformat() != raw['business_date'] or d > datetime.now(ZoneInfo('Asia/Shanghai')).date():
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(422, '实际验收日期须为不晚于今天的YYYY-MM-DD') from None
    src = source(p, raw); parents = {a['allocation_index']:a for a in src['allocations']}
    if not isinstance(raw['rows'], list) or not 1 <= len(raw['rows']) <= 20:
        raise HTTPException(422, '每份加工结果须1至20个不同的来源分配，可分批核对')
    rows = []; refs = [raw['evidence_id']]; seen = set(); lots = set(); lot_total = 0
    sums = {k: Fraction(0) for k in ('input_length',)+METRICS+('offcut_length',)}
    mass = {k.replace('_length','_mass'):Fraction(0) for k in sums}
    for ri, r in enumerate(raw['rows'],1):
        fields = {'allocation_index','quantity','unit','measurement_basis','basis_evidence_id','evidence','offcuts'} | set(METRICS)
        if not isinstance(r, dict) or set(r) != fields or type(r['allocation_index']) is not int or r['allocation_index'] not in parents or r['allocation_index'] in seen:
            raise HTTPException(422, '实际加工分配须来自所选加工单且不重复，结果字段须完整')
        seen.add(r['allocation_index']); parent = parents[r['allocation_index']]
        count = integer(r['quantity'], '本次实际加工原材根数')
        if count > parent['quantity']:
            inv.fail('本次加工结果超过来源已投入加工根数')
        if not isinstance(r['unit'],str) or r['unit'] not in LENGTH:
            raise HTTPException(422, '实际加工长度单位无效')
        text(r['measurement_basis'],'实际范围、去向及与计划偏差说明'); refs.append(text(r['basis_evidence_id'],'本项实测说明原件',200))
        evidence(r['evidence'], ('quantity',)+METRICS, refs)
        factor = Fraction(LENGTH[r['unit']]); actual = {k:Fraction(number(r[k]))*factor for k in METRICS}
        raw_length = Fraction(parent['stock_length']); weight = Fraction(parent['unit_weight'])
        actual['input_length'] = raw_length*count
        if not isinstance(r['offcuts'],list) or len(r['offcuts'])>20:
            raise HTTPException(422,'每项可填0至20组同长度、同库位且同验收口径的实物余料')
        offcuts = []; offcut_length = Fraction(0)
        for oi, o in enumerate(r['offcuts'],1):
            fields = {'lot_code','length','quantity','warehouse','acceptance_basis','evidence'}
            if not isinstance(o,dict) or set(o)!=fields:
                raise HTTPException(422,'余料须编号、实测每段长度、根数、库位、材质质量及可留用验收说明和逐项原件')
            for k in ('lot_code','warehouse','acceptance_basis'):
                text(o[k],'余料编号、库位或实际可留用验收',2000 if k=='acceptance_basis' else 200)
            evidence(o['evidence'], fields-{'evidence'}, refs)
            identity=(o['warehouse'].strip(),o['lot_code'].strip())
            if identity in lots:
                raise HTTPException(422,'同仓库同实物余料编号不能重复')
            lots.add(identity)
            length=Fraction(number(o['length'],True))*factor; quantity=integer(o['quantity'],'实物余料根数')
            if not length < raw_length:
                raise HTTPException(422,'每段余料须短于原材原长，未加工完整根须走完整原材退回')
            offcut_length += length*quantity; lot_total += quantity
            offcuts.append(dict(index=oi,lot_code=o['lot_code'],warehouse=o['warehouse'],length=dec(length),quantity=quantity,total_length=dec(length*quantity),mass=dec(length*quantity*weight),acceptance_basis=o['acceptance_basis']))
        actual['offcut_length']=offcut_length
        if actual['input_length'] != sum((actual[k] for k in METRICS+('offcut_length',)),Fraction(0)):
            inv.fail('实际加工长度不闭合：原材总长须精确等于成品＋锯缝＋切除＋报废＋实物余料；不按容差归零或自动补差')
        for k,v in actual.items():sums[k]+=v;mass[k.replace('_length','_mass')]+=v*weight
        rows.append(dict(index=ri,allocation_index=r['allocation_index'],receipt_id=parent['receipt_id'],name=parent['name'],source_lot_code=parent['lot_code'],material_spec=parent['material_spec'],stock_length=parent['stock_length'],unit_weight=parent['unit_weight'],quantity=count,
                         values={k:dec(v) for k,v in actual.items()},masses={k.replace('_length','_mass'):dec(v*weight) for k,v in actual.items()},balance_difference='0',offcuts=offcuts))
    if sum(len(r['offcuts']) for r in rows)>100 or lot_total>1000000:
        raise HTTPException(422,'每份加工结果最多100组且合计1000000段实物余料')
    total=dict(raw_count=sum(r['quantity'] for r in rows),offcut_count=lot_total,**{k:dec(v) for k,v in sums.items()},**{k:dec(v) for k,v in mass.items()},balance_difference='0',length_unit='m',mass_unit='kg')
    return dict(consumption_id=src['id'],source_name=src['source_name'],source_revision=src['source_revision'],rows=rows,totals=total),refs


def balances(p):
    ops.balances(p)
    accounted={}; lots=[]; used=set(); applied=set(); consumes=set()
    records={r['id']:r for r in p['stock_processing_results']}
    for e in p['stock_events']:
        if e['operation']=='consume':consumes.add(e['target_id'])
        if e['operation']!='recover':continue
        row=records.get(e['target_id'])
        if not row or row['id'] in applied or row['consumption_id'] not in consumes or e['record_hash']!=store.digest(row) or e['rows']!=row['rows'] or e['totals']!=row['totals'] or not inv.approved(p,'stock_processing_results',row):
            inv.fail('加工验收执行事件、来源或独立复核无法核验，请恢复原始台账')
        computed,_=normalize(p,row['input'])
        if any(row[k]!=v for k,v in computed.items()):inv.fail('加工验收归一化结果或长度去向已变化')
        applied.add(row['id']);parent=core.find(p,'stock_operations',row['consumption_id'])
        for r in row['rows']:
            key=(row['consumption_id'],r['allocation_index']);accounted[key]=accounted.get(key,0)+r['quantity']
            limit=next(a['quantity'] for a in parent['allocations'] if a['allocation_index']==r['allocation_index'])
            if accounted[key]>limit:inv.fail('同一投入加工根数被重复验收，请恢复原始台账')
            for o in r['offcuts']:
                identity=(o['warehouse'].strip(),o['lot_code'].strip())
                if identity in used:inv.fail('实物余料编号被重复入库，请恢复原始台账')
                used.add(identity);key_text=row['id']+':'+str(r['index'])+':'+str(o['index'])
                lots.append(dict(**o,lot_key=key_text,result_id=row['id'],row_index=r['index'],offcut_index=o['index'],consumption_id=row['consumption_id'],allocation_index=r['allocation_index'],source_name=row['source_name'],source_lot_code=r['source_lot_code'],material_spec=r['material_spec'],unit_weight=r['unit_weight'],voided=bool(inv.event_for(p,'offcut_void',key_text))))
    stopped=set()
    for e in p['stock_events']:
        if e['operation']!='offcut_void':continue
        lot=next((l for l in lots if l['lot_key']==e['target_id']),None)
        if not lot or e['target_id'] in stopped or e['result_id']!=lot['result_id'] or e['lot_code']!=lot['lot_code'] or e['record_hash']!=store.digest(records[lot['result_id']]):
            inv.fail('余料停用事件或原验收关联无法核验')
        stopped.add(e['target_id'])
    return accounted,lots


def prepare(p,raw):
    inv.records(p);computed,refs=normalize(p,raw);accounted,lots=balances(p)
    src=source(p,raw)
    for r in computed['rows']:
        used=accounted.get((src['id'],r['allocation_index']),0);limit=next(a['quantity'] for a in src['allocations'] if a['allocation_index']==r['allocation_index'])
        if r['quantity']+used>limit:inv.fail('来源投入加工根数已被其他验收占用，请刷新并按未核对根数登记')
        for o in r['offcuts']:
            if any((c['input']['warehouse'].strip(),c['input']['lot_code'].strip())==(o['warehouse'].strip(),o['lot_code'].strip()) for c in p['stock_receipts']):
                inv.fail('余料编号须与原材批号区分，不能重复登记同仓库同编号')
            if any((l['warehouse'].strip(),l['lot_code'].strip())==(o['warehouse'].strip(),o['lot_code'].strip()) for l in lots):
                inv.fail('此实物余料编号已留档入库，停用后也不能重用编号或退回加工验收额度')
    stamp=inv.proofs(p,refs)
    return computed,stamp


def preview_hash(raw,result,stamp):return store.digest(dict(input=raw,result=result,proof_stamp=stamp))


def validate(p,row):
    inv.intact(row)
    if closed(p,row):inv.fail('加工结果已执行或取消，仅供追溯')
    computed,stamp=prepare(p,row['input'])
    if any(row[k]!=v for k,v in computed.items()) or row['proof_stamp']!=stamp or row['preview_hash']!=preview_hash(row['input'],computed,stamp):
        inv.fail('加工实测、原件或预览已变化，请取消后重新核对')


def status(p):
    accounted,lots=balances(p);result=[]
    for row in p['stock_processing_results']:
        event=execution(p,row['id']);cancelled=inv.event_for(p,'cancel_processing',row['id'])
        phase='executed' if event else 'cancelled' if cancelled else row['state']
        try:
            if event:
                _,refs=normalize(p,row['input'])
                if inv.proofs(p,refs)!=row['proof_stamp']:inv.fail('历史加工验收原件已变化，既有入库数量保留，余料待核查')
            elif not cancelled:validate(p,row)
            issue=''
        except HTTPException as e:issue=str(e.detail)
        result.append(dict(record=row,object_hash=store.digest(row),phase=phase,issue=issue))
    issues={r['record']['id']:r['issue'] for r in result}
    for lot in lots:
        lot.update(issue=issues[lot['result_id']],in_stock_quantity=lot['quantity'],adopted_quantity=0 if lot['voided'] or issues[lot['result_id']] else lot['quantity'],verified=not lot['voided'] and not issues[lot['result_id']])
    sources=[]
    for row in p['stock_operations']:
        if row['kind']=='consume' and ops.execution(p,row['id']):
            sources.append(dict(record=row,object_hash=store.digest(row),allocations=[dict(**a,accounted=accounted.get((row['id'],a['allocation_index']),0),unaccounted=a['quantity']-accounted.get((row['id'],a['allocation_index']),0)) for a in row['allocations']]))
    return dict(results=result,lots=lots,sources=sources,notice=NOTICE)


@router.post('/projects/{pid}/stock-inventory/processing-results/preview')
def preview(pid:str,body:dict,a=Depends(core.actor)):
    if set(body)!={'input'}:raise HTTPException(422,'预览仅接受实际加工验收输入')
    with store.connect() as c:
        p=core.project(c,pid,a);result,stamp=prepare(p,body['input'])
        return dict(result=result,preview_hash=preview_hash(body['input'],result,stamp),ledger_hash=inv.ledger_hash(p),notice=NOTICE)


@router.post('/projects/{pid}/stock-inventory/processing-results')
def submit(pid:str,body:dict,a=Depends(core.actor)):
    core.edit(a);inv.fields(body,{'input','preview_hash'})
    with store.transaction() as c:
        p=core.project(c,pid,a);old=inv.begin(p,body)
        if old:
            if old not in p['stock_processing_results']:inv.fail('请求标识已用于其他库存操作')
            return old
        result,stamp=prepare(p,body['input'])
        if body['preview_hash']!=preview_hash(body['input'],result,stamp):inv.fail('实际加工验收预览已变化，请重新预览')
        return inv.commit(c,p,a,'stock_processing_results',body,dict(input=body['input'],**result,proof_stamp=stamp,preview_hash=body['preview_hash'],state='draft'),'STOCK_PROCESSING_RESULT_SUBMITTED')


@router.post('/projects/{pid}/stock-inventory/processing-results/{rid}/execute')
def execute(pid:str,rid:str,body:dict,a=Depends(core.actor)):
    core.edit(a);inv.fields(body,{'object_hash'})
    with store.transaction() as c:
        p=core.project(c,pid,a);old=inv.begin(p,body)
        if old:
            if old.get('operation')!='recover' or old.get('target_id')!=rid:inv.fail('请求标识已用于其他加工验收')
            return old
        row=core.find(p,'stock_processing_results',rid)
        if store.digest(row)!=body['object_hash'] or not inv.approved(p,'stock_processing_results',row):inv.fail('加工验收已变化或缺少有效独立复核')
        validate(p,row)
        return inv.commit(c,p,a,'stock_events',body,dict(operation='recover',target_id=rid,record_hash=store.digest(row),rows=row['rows'],totals=row['totals']),'STOCK_PROCESSING_RESULT_EXECUTED')


@router.post('/projects/{pid}/stock-inventory/processing-results/{rid}/offcuts/{ri}/{oi}/void')
def void(pid:str,rid:str,ri:int,oi:int,body:dict,a=Depends(core.actor)):
    core.edit(a);inv.fields(body,{'object_hash','basis','evidence_id'});key=rid+':'+str(ri)+':'+str(oi)
    with store.transaction() as c:
        p=core.project(c,pid,a);old=inv.begin(p,body)
        if old:
            if old.get('operation')!='offcut_void' or old.get('target_id')!=key:inv.fail('请求标识已用于其他余料操作')
            return old
        row=core.find(p,'stock_processing_results',rid)
        if store.digest(row)!=body['object_hash']:inv.fail('加工验收记录已变化，请刷新核对')
        _,lots=balances(p);lot=next((l for l in lots if l['lot_key']==key),None)
        if not lot:raise HTTPException(404,'已入库余料不存在')
        if lot['voided']:inv.fail('余料登记已停用')
        text(body['basis'],'余料停用核查原因');stamp=inv.proofs(p,[body['evidence_id']])
        return inv.commit(c,p,a,'stock_events',body,dict(operation='offcut_void',target_id=key,result_id=rid,record_hash=store.digest(row),lot_code=lot['lot_code'],basis=body['basis'],evidence_id=body['evidence_id'],proof_stamp=stamp),'STOCK_OFFCUT_VOIDED')
