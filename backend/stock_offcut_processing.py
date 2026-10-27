"""Actual measured reprocessing of issued offcuts; child lots require explicit admission."""
from datetime import date,datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter,Depends,HTTPException
from . import main as core,store,stock_inventory as inv,stock_offcut_reuse as reuse
from .compound_takeoff import text
from .stock_processing_results import actual_rows

router=APIRouter(prefix='/api/v1')
COLLECTION='stock_offcut_processing'
NOTICE=('未单独登记投入且已有完整结果时引用已执行余料领用；已实际投入的结果须引用对应已执行投入单，按本单尚未验收余额登记本次验收原段根数、成品、锯缝、切除、报废及新余料逐项实测和原件。'
        '原段总长须精确等于全部实际去向，不按计划生成库存或用容差补差。预览、保存和独立复核不记账，显式执行后才增加已验收加工根数并入库新余料；直接模式扣未加工余额，引用投入单时仅结转本单在加工待验收数量，不重复扣未加工余额。'
        '新余料须短于来源原段、独立编号及库位和质量验收，继承材质截面及每米质量，保留父余料与原领用关联，可再按核定定尺独立回用。'
        '已投入或已加工验收原段不能再按未加工原段退回；待执行加工和退回竞争同一余额，执行时重查。上游失效不回滚历史实物记账，实际结果须当前有效实测凭据。'
        '再加工或祖先验收依据失效时保留数量、新余料待核查，禁止新增领用。已执行结果不可取消、改数或重审；未执行可有据取消。不改变工程净量、排料、报价、完整原材或最初加工核对额度，现有余料在库盘点使用独立盘点入口；不支持冲销、在外及在加工盘点调整、自动替换排料及跨项目调拨。')


def closed(p,row):return inv.event_for(p,'offcut_process',row['id']) or inv.event_for(p,'offcut_process_cancel',row['id'])


def reviewable(p,row,actor):
    if row['author']==actor['username']:raise HTTPException(403,'余料再加工结果须由另一独立账号复核')
    if closed(p,row):inv.fail('余料再加工已执行或取消，仅供追溯，不能重审')


def normalize(p,raw):
    fields={'reference_id','reference_hash','business_date','inspector','basis','evidence_id','confirmed','rows'}
    if isinstance(raw,dict) and ('job_id' in raw or 'job_hash' in raw):fields|={'job_id','job_hash'}
    if not isinstance(raw,dict) or set(raw)!=fields:raise HTTPException(422,'须完整填写已执行余料领用、实际日期、验收人、实测偏差说明、原件、确认及逐项加工去向')
    for k in ('reference_id','reference_hash','inspector','basis','evidence_id'):text(raw[k],'实际再加工范围及偏差说明' if k=='basis' else '再加工来源、验收人或原件',2000 if k=='basis' else 200)
    if raw['confirmed'] is not True:raise HTTPException(422,'须据实测及原件确认实际再加工和新余料质量，不以计划替代实物')
    try:
        d=date.fromisoformat(raw['business_date'])
        if d.isoformat()!=raw['business_date'] or d>datetime.now(ZoneInfo('Asia/Shanghai')).date():raise ValueError()
    except (TypeError,ValueError):raise HTTPException(422,'实际再加工日期须为不晚于今天的YYYY-MM-DD') from None
    source=reuse.issue_source(p,raw['reference_id'])
    if store.digest(source)!=raw['reference_hash']:inv.fail('来源余料领用快照已变化，请刷新核对')
    if raw['business_date']<source['input']['business_date']:inv.fail('再加工实际日期不得早于来源余料领用交接日期')
    job=None
    if 'job_id' in raw:
        text(raw['job_id'],'已投入加工单',200);text(raw['job_hash'],'已投入快照',200)
        job=core.find(p,'stock_offcut_jobs',raw['job_id'])
        if not inv.event_for(p,'offcut_start',job['id']) or job['reference_id']!=source['id'] or store.digest(job)!=raw['job_hash']:inv.fail('结果须引用本领用单已执行的实际投入单及当前冻结快照')
        if raw['business_date']<job['input']['business_date']:inv.fail('结果实际验收日期不得早于本单实际投入日期')
    parents={a['allocation_index']:a for a in job['allocations']} if job else reuse.issue_allocations(source)
    rows,totals,refs=actual_rows(raw,parents)
    return dict(**({'job_id':job['id'],'job_hash':store.digest(job)} if job else {}),reference_id=source['id'],source_name=source['demand']['source_name'],source_revision=source['demand']['source_revision'],rows=rows,totals=totals),refs


def child_lots(row,actual):
    for o in actual['offcuts']:
        yield dict(**o,lot_key=row['id']+':'+str(actual['index'])+':'+str(o['index']),result_id=row['id'],row_index=actual['index'],offcut_index=o['index'],origin='offcut_processing',reference_id=row['reference_id'],parent_lot_key=actual['parent_lot_key'],allocation_index=actual['allocation_index'],source_name=row['source_name'],source_lot_code=actual['source_lot_code'],material_spec=actual['material_spec'],unit_weight=actual['unit_weight'],voided=False)


def prepare(p,raw):
    inv.records(p);computed,refs=normalize(p,raw)
    from .stock_processing_results import balances as raw_balances
    from .stock_offcut_ledger import replay
    _,lots=raw_balances(p);ledger=replay(p,lots);source=ledger['issues'].get(computed['reference_id'])
    if not source:inv.fail('来源余料领用缺少可核验实际执行')
    existing={(l['warehouse'].strip(),l['lot_code'].strip()) for l in ledger['lots']}|{(r['input']['warehouse'].strip(),r['input']['lot_code'].strip()) for r in p['stock_receipts']}
    for r in computed['rows']:
        parent=source[r['allocation_index']]
        if computed.get('job_id'):
            job=ledger['jobs'].get(computed['job_id'],{}).get(r['allocation_index'])
            if not job or r['quantity']>job['quantity']-job['accounted']:inv.fail('本次结果超过本投入单已投入且尚未验收余额')
        elif r['quantity']>parent['quantity']-parent['returned']-parent['consumed']-parent['processing']:inv.fail('本次再加工超过对应领用分配尚未加工且未退回余额')
        for o in r['offcuts']:
            if (o['warehouse'].strip(),o['lot_code'].strip()) in existing:inv.fail('新余料编号已被完整原材或其他代余料使用，停用后也不能复用')
    return computed,inv.proofs(p,refs)


def preview_hash(raw,result,stamp):return store.digest(dict(input=raw,result=result,proof_stamp=stamp))


def validate(p,row):
    inv.intact(row)
    if closed(p,row):inv.fail('余料再加工已执行或取消，仅供追溯')
    result,stamp=prepare(p,row['input'])
    if any(row[k]!=v for k,v in result.items()) or stamp!=row['proof_stamp'] or row['preview_hash']!=preview_hash(row['input'],result,stamp):inv.fail('再加工实测、原件或预览已变化，请有据取消后重新核定')


def execution_issues(p):
    issues={}
    for row in p[COLLECTION]:
        if not inv.event_for(p,'offcut_process',row['id']):continue
        try:
            _,refs=normalize(p,row['input'])
            if inv.proofs(p,refs)!=row['proof_stamp']:inv.fail('再加工实测验收原件已变化')
            if row.get('job_id'):
                from .stock_offcut_jobs import proof_issue
                issue=proof_issue(p,core.find(p,'stock_offcut_jobs',row['job_id']))
            else:issue=''
            issue='；'.join(dict.fromkeys(v for v in (issue,reuse.batch_proof_issue(p,row['reference_id'])) if v))
        except HTTPException as e:issue='再加工验收依据待核查，已加工和新余料数量保留：'+str(e.detail)
        issues[row['id']]=issue
    return issues


def status(p):
    problems=execution_issues(p);result=[]
    for row in p[COLLECTION]:
        executed=inv.event_for(p,'offcut_process',row['id']);cancelled=inv.event_for(p,'offcut_process_cancel',row['id'])
        try:
            if not executed and not cancelled:validate(p,row)
            issue=problems.get(row['id'],'')
        except HTTPException as e:issue=str(e.detail)
        result.append(dict(record=row,object_hash=store.digest(row),phase='executed' if executed else 'cancelled' if cancelled else row['state'],issue=issue))
    return dict(results=result,notice=NOTICE)


@router.post('/projects/{pid}/stock-inventory/offcut-processing/preview')
def preview(pid:str,body:dict,a=Depends(core.actor)):
    if not isinstance(body,dict) or set(body)!={'input'}:raise HTTPException(422,'再加工预览仅接受实际实测输入')
    with store.connect() as c:
        p=core.project(c,pid,a);result,stamp=prepare(p,body['input'])
        return dict(result=result,preview_hash=preview_hash(body['input'],result,stamp),ledger_hash=inv.ledger_hash(p),notice=NOTICE)


@router.post('/projects/{pid}/stock-inventory/offcut-processing')
def submit(pid:str,body:dict,a=Depends(core.actor)):
    core.edit(a);inv.fields(body,{'input','preview_hash'})
    with store.transaction() as c:
        p=core.project(c,pid,a);old=inv.begin(p,body)
        if old:
            if old not in p[COLLECTION]:inv.fail('请求标识已用于其他库存操作')
            return old
        result,stamp=prepare(p,body['input'])
        if body['preview_hash']!=preview_hash(body['input'],result,stamp):inv.fail('再加工预览已变化，请重新核对')
        return inv.commit(c,p,a,COLLECTION,body,dict(input=body['input'],**result,proof_stamp=stamp,preview_hash=body['preview_hash'],state='draft'),'STOCK_OFFCUT_PROCESSING_SUBMITTED')


@router.post('/projects/{pid}/stock-inventory/offcut-processing/{rid}/{operation}')
def movement(pid:str,rid:str,operation:str,body:dict,a=Depends(core.actor)):
    core.edit(a)
    if operation not in ('execute','cancel'):raise HTTPException(422,'仅支持执行已审再加工或有据取消未执行结果')
    inv.fields(body,{'object_hash'} if operation=='execute' else {'object_hash','basis','evidence_id'});kind='offcut_process' if operation=='execute' else 'offcut_process_cancel'
    with store.transaction() as c:
        p=core.project(c,pid,a);old=inv.begin(p,body)
        if old:
            if old.get('operation')!=kind or old.get('target_id')!=rid:inv.fail('请求标识已用于其他再加工操作')
            return old
        row=core.find(p,COLLECTION,rid)
        if store.digest(row)!=body['object_hash']:inv.fail('再加工结果已变化，请刷新核对')
        if closed(p,row):inv.fail('再加工已执行或取消，不能重复记账或冲销')
        data=dict(operation=kind,target_id=rid,record_hash=store.digest(row))
        if operation=='execute':
            if not inv.approved(p,COLLECTION,row):inv.fail('须有效独立复核后才能执行再加工和新余料入库')
            validate(p,row);data.update(reference_id=row['reference_id'],rows=row['rows'],totals=row['totals'])
        else:
            text(body['basis'],'未执行再加工取消原因');data.update(basis=body['basis'],evidence_id=body['evidence_id'],proof_stamp=inv.proofs(p,[body['evidence_id']]))
        return inv.commit(c,p,a,'stock_events',body,data,'STOCK_'+kind.upper())
