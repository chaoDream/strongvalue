"""Engineering settlement reconciliation, separate from financial accounts and payment."""
import copy
import hashlib
import io
import json
from fastapi import APIRouter,Depends,HTTPException
from fastapi.responses import StreamingResponse
from . import main as core,store
from .quote_comparison import compare_quotes,validate_quote
from .task_execution import original_bytes

router=APIRouter(prefix='/api/v1')
NOTICE='工程结算差异复核记录，不是竣工财务决算、付款审批或法定签章成果。金额完全取自所选报价，差异说明不改变计量计价结果，也不自动认定合同价款权利。'


def text(body,key,limit=3000):
    v=body.get(key)
    if not isinstance(v,str) or not v.strip() or len(v)>limit:raise HTTPException(422,'请填写完整的结算范围、合同依据及逐项差异说明')
    return v.strip()


def approval(p,qid):
    r=next((r for r in reversed(p['reviews']) if r['collection']=='quotes' and r['object_id']==qid),None)
    if not r or r['decision']!='approve':raise HTTPException(409,'合同基线及结算报价都须有有效的独立复核记录')
    return r


def preview_data(p,left,right):
    a=core.find(p,'quotes',left);b=core.find(p,'quotes',right)
    if a['version']>=b['version']:raise HTTPException(409,'须选择早于结算报价的合同基线版本')
    if a['scope']!=b['scope']:raise HTTPException(409,'两份报价专业范围不同，请先确认同范围基线')
    ar,br=approval(p,left),approval(p,right)
    if right!=p['quotes'][-1]['id'] or right in p['stale_quotes'] or not p['report_approval'] or p['report_approval']['quote_id']!=right:
        raise HTTPException(409,'结算报价须为当前最新且仍有效的已审报价')
    issues=core.blockers(p)
    if issues:raise HTTPException(409,'当前工程尚不具备结算差异复核条件：'+'；'.join(issues))
    report=compare_quotes(p,left,right)
    rows=[{'key':'line:'+x['id'],'kind':'line','boq_id':x['id'],'name':x['name'],'amount_delta':x['amount_delta']} for x in report['lines']]
    rows.extend({'key':'fee:'+str(x['position']),'kind':'fee','boq_id':None,'name':(x['after'] or x['before'])['name'],'amount_delta':x['amount_delta']} for x in report['fee_changes'])
    new_changes=set(b['snapshot'].get('applied_changes',[]))-set(a['snapshot'].get('applied_changes',[]))
    changes=[x for x in p['changes'] if x['id'] in new_changes and x['state']=='approved' and x['id'] in p['applied_changes']]
    context={'comparison':report['hash'],'baseline_review':ar['id'],'target_review':br['id'],'current_boq':p['boq'],'current_rules':p['rules'],
             'pricing_date':core.effective_date(p).isoformat(),'scope':p['scope'],'changes':changes}
    return {'comparison':report,'rows':rows,'changes':changes,'preview_hash':store.digest(context),'baseline_review_id':ar['id'],'target_review_id':br['id'],'notice':NOTICE}


def content_hash(record):return store.digest({k:v for k,v in record.items() if k not in ('state','content_hash')})


def validate(p,record,verify_bytes=True):
    if content_hash(record)!=record['content_hash']:raise HTTPException(409,'结算差异记录内容校验失败')
    if p['engineering_settlements'] and record['id']!=p['engineering_settlements'][-1]['id']:raise HTTPException(409,'已有较新的结算差异记录，请复核最新版本')
    current=preview_data(p,record['baseline_id'],record['target_id'])
    if current['preview_hash']!=record['preview_hash']:raise HTTPException(409,'报价审批、签证或计价输入已变化，请重新建立结算差异记录')
    checked=set()
    for proof in record['proofs']:
        ev=core.require_evidence(p,proof['evidence']['id']);doc=core.find(p,'documents',ev['document_id'])
        if ev!=proof['evidence'] or doc['hash']!=proof['document']['hash'] or ev['document_hash']!=doc['hash']:raise HTTPException(409,'结算依据或原件版本已变化')
        if not (store.ROOT/'originals'/doc['hash']).is_file():raise HTTPException(409,'结算依据原件缺失')
        if verify_bytes and doc['hash'] not in checked:original_bytes(doc);checked.add(doc['hash'])
    return current


@router.post('/projects/{pid}/engineering-settlements/preview')
def preview(pid:str,body:dict,a=Depends(core.actor)):
    with store.connect() as c:return preview_data(core.project(c,pid,a),body.get('baseline_id'),body.get('target_id'))


@router.post('/projects/{pid}/engineering-settlements')
def submit(pid:str,body:dict,a=Depends(core.actor)):
    core.edit(a);name=text(body,'name',200);basis=text(body,'basis');key=text(body,'request_id',100)
    with store.transaction() as c:
        p=core.project(c,pid,a);fingerprint=store.digest(body)
        duplicate=next((x for x in p['engineering_settlements'] if x['request_id']==key),None)
        if duplicate:
            if duplicate['request_hash']!=fingerprint:raise HTTPException(409,'请求标识已用于其他结算差异内容')
            return duplicate
        previous=p['engineering_settlements'][-1] if p['engineering_settlements'] else None
        if body.get('previous_hash','')!=(store.digest(previous) if previous else ''):raise HTTPException(409,'结算差异记录已变化，请刷新后核对')
        current=preview_data(p,body.get('baseline_id'),body.get('target_id'))
        if current['preview_hash']!=body.get('preview_hash'):raise HTTPException(409,'差异预览已变化，请重新核对')
        explanations=body.get('explanations')
        if not isinstance(explanations,list) or len(explanations)!=len(current['rows']) or any(not isinstance(x,dict) or not isinstance(x.get('key'),str) for x in explanations):raise HTTPException(422,'须逐项填写全部清单及费用差异说明')
        lookup={x['key']:x for x in current['rows']};seen=set();normalized=[];proof_ids={text(body,'contract_evidence_id',100)}
        changes={x['id']:x for x in current['changes']}
        for x in explanations:
            row=lookup.get(x['key'])
            if not row or x['key'] in seen:raise HTTPException(422,'差异项重复或不属于当前预览')
            seen.add(x['key']);reason=text(x,'reason');eid=text(x,'evidence_id',100);ids=x.get('change_ids',[])
            if not isinstance(ids,list) or len(ids)>100 or any(not isinstance(cid,str) for cid in ids) or len(set(ids))!=len(ids):raise HTTPException(422,'关联签证须为不重复的已应用记录')
            for cid in ids:
                change=changes.get(cid)
                if not change or row['kind']!='line' or change['boq_id']!=row['boq_id']:raise HTTPException(409,'签证未审、未应用、不属于本次差异或与清单不匹配')
                proof_ids.add(change['evidence_id'])
            proof_ids.add(eid);normalized.append({**row,'reason':reason,'evidence_id':eid,'change_ids':sorted(ids)})
        proofs=[]
        for eid in sorted(proof_ids):
            ev=core.require_evidence(p,eid);doc=core.find(p,'documents',ev['document_id'])
            proofs.append({'evidence':copy.deepcopy(ev),'document':{k:copy.deepcopy(doc[k]) for k in ['id','name','hash','version']}})
        record={'id':store.uid(),'project_name':p['name'],'schema':'engineering-settlement-v1','version':len(p['engineering_settlements'])+1,'name':name,'basis':basis,
                'baseline_id':body['baseline_id'],'target_id':body['target_id'],'baseline_review_id':current['baseline_review_id'],'target_review_id':current['target_review_id'],
                'preview_hash':current['preview_hash'],'comparison':copy.deepcopy(current['comparison']),'explanations':normalized,'changes':copy.deepcopy(current['changes']),
                'contract_evidence_id':body['contract_evidence_id'],'proofs':proofs,'request_id':key,'request_hash':fingerprint,'state':'draft',
                'author':a['username'],'created_at':store.now(),'notice':NOTICE}
        record['content_hash']=content_hash(record)
        # Validate before appending; no quote or business value is changed by this record.
        candidate={**p,'engineering_settlements':p['engineering_settlements']+[record]};validate(candidate,record)
        p['engineering_settlements'].append(record);store.audit(c,p,a,'ENGINEERING_SETTLEMENT_SUBMITTED',record);store.save(c,p)
        return record


@router.get('/projects/{pid}/engineering-settlements/status')
def status(pid:str,a=Depends(core.actor)):
    with store.connect() as c:
        p=core.project(c,pid,a);result=[]
        for x in p['engineering_settlements']:
            try:validate(p,x,False);issue='' if x['state']=='approved' else '结算差异记录尚未通过独立复核'
            except HTTPException as exc:issue=str(exc.detail)
            result.append({'record':x,'object_hash':store.digest(x),'available':not issue,'issue':issue})
        return {'items':result,'previous_hash':store.digest(p['engineering_settlements'][-1]) if p['engineering_settlements'] else ''}


def report_tables(p,record,approved,issue):
    r=record['comparison'];label='工程结算差异已复核（无签章效力）' if approved else '工程结算差异草稿 · 未经有效复核'
    rows=[['项目',record['project_name']],['记录',record['name']+' / V'+str(record['version'])],['状态',label],['合同基线报价','V'+str(r['left_version'])+' / '+r['left_hash']],
          ['结算报价','V'+str(r['right_version'])+' / '+r['right_hash']],['基线总价',r['left_total']],['结算报价总价',r['right_total']],['差额',r['total_delta']],
          ['合同范围及结算依据',record['basis']],['当前警示',issue],['边界',NOTICE],['内容校验值',record['content_hash']]]
    effects=[['数量影响',r['attribution']['quantity']],['综合单价影响',r['attribution']['unit_price']],['修约余额',r['attribution']['rounding']],
             ['新增',r['attribution']['added']],['移除',r['attribution']['removed']],['计量口径变化',r['attribution']['basis_change']],['费用差额',r['fee_delta']],['闭合总差额',r['total_delta']]]
    sections=[('工程结算差异封面',['项目','内容'],rows),('结算差异分解',['差异类型','金额'],effects),
              ('结算差异说明',['差异项目','金额差额','说明及价款依据','原件证据','关联已应用签证'],[[x['name'],x['amount_delta'],x['reason'],x['evidence_id'],', '.join(x['change_ids'])] for x in record['explanations']]),
              ('结算关联签证',['清单','原工程量','变更后','增减量','来源依据'],[[x['name'],x['before_quantity'],x['quantity'],x['delta'],x['evidence_id']] for x in record['changes']]),
              ('结算原始依据',['证据','文件','文件哈希','页码','原文'],[[x['evidence']['id'],x['document']['name'],x['document']['hash'],x['evidence']['page'] or '无固定页码',x['evidence']['text']] for x in record['proofs']]),
              ('结算复核记录',['决定','人员','时间','依据','对象校验值'],[[x['decision'],x['reviewer'],x['timestamp'],x['reason'],x['object_hash']] for x in p['reviews'] if x['collection']=='engineering_settlements' and x['object_id']==record['id']])]
    # Bound printed row sizes using the same continuation convention as quote reports.
    for i,(title,headers,values) in enumerate(sections):
        expanded=[]
        for value in values:
            cells=[str(v).replace('\n','；') for v in value];count=max((len(v)+119)//120 for v in cells)
            for part in range(max(1,count)):
                row=[v[part*120:(part+1)*120] for v in cells]
                if part:row[0]=(row[0] or cells[0][:50])+'（续'+str(part+1)+'）'
                expanded.append(row)
        sections[i]=(title,headers,expanded)
    return label,sections


@router.get('/projects/{pid}/engineering-settlements/{sid}/exports')
def export(pid:str,sid:str,format:str='json',a=Depends(core.actor)):
    if format not in ('json','xlsx','pdf'):raise HTTPException(422,'结算差异记录支持JSON、Excel和PDF')
    with store.transaction() as c:
        p=core.project(c,pid,a);record=core.find(p,'engineering_settlements',sid)
        if content_hash(record)!=record['content_hash']:raise HTTPException(409,'结算差异内容校验失败，不能导出')
        try:validate(p,record);issue=''
        except HTTPException as exc:issue=str(exc.detail)
        approved=not issue and record['state']=='approved';label,sections=report_tables(p,record,approved,issue)
        if format=='json':payload=json.dumps({'status':label,'record':record,'issue':issue,'tables':sections},ensure_ascii=False,indent=2).encode();media='application/json';suffix='json'
        else:
            from .reports import export_report
            q=core.find(p,'quotes',record['target_id']);payload,media,suffix=export_report(p,q,format,False,table_data=(label,sections))
        sha=hashlib.sha256(payload).hexdigest();directory=store.ROOT/'exports';directory.mkdir(exist_ok=True);path=directory/sha
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()!=sha:raise HTTPException(409,'同哈希留档文件异常')
        if not path.exists():path.write_bytes(payload)
        artifact={'id':store.uid(),'purpose':'engineering_settlement','settlement_id':sid,'settlement_version':record['version'],'format':format,
                  'quote_id':record['target_id'],'quote_version':record['comparison']['right_version'],'hash':sha,'approved':approved,'timestamp':store.now(),'actor':a['username'],'media':media,'suffix':suffix}
        c.execute('INSERT INTO artifacts VALUES(?,?,?,?)',(artifact['id'],pid,a['tenant'],store.canonical(artifact)));store.audit(c,p,a,'ENGINEERING_SETTLEMENT_EXPORTED',artifact)
        return StreamingResponse(io.BytesIO(payload),media_type=media,headers={'Content-Disposition':'attachment; filename="QiangQuote-settlement-V'+str(record['version'])+'.'+suffix+'"','X-Artifact-Hash':sha})
