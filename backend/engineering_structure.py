"""Reviewed project/single/unit engineering structure; project-level fees stay explicit."""
import copy
import re
from decimal import Decimal, localcontext
from fastapi import APIRouter, Depends, HTTPException
from . import main as core, store
from .engine import money, DomainError
from .task_execution import original_bytes

router=APIRouter(prefix='/api/v1')


def latest(p):
    return p.get('engineering_structures',[])[-1] if p.get('engineering_structures') else None


def boq_source(p):
    # Quantities/prices and approval states do not change the declared location of a BOQ.
    return sorted([{k:b.get(k,'') for k in ['id','code','name','unit','features','node']} for b in p['boq']],key=lambda b:b['id'])


def source_hash(p):
    return store.digest(boq_source(p))


def validate_shape(p,s):
    nodes=s.get('nodes');assignments=s.get('assignments')
    if not isinstance(nodes,list) or not 2<=len(nodes)<=200 or any(not isinstance(n,dict) or set(n)!={'id','kind','name','parent_id'} for n in nodes):
        raise HTTPException(422,'须填写2至200个单项/单位工程节点，仅支持项目下两级结构')
    for n in nodes:
        if not isinstance(n['id'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',n['id']) or n['id']=='project':
            raise HTTPException(422,'工程节点编号无效或使用了项目保留编号')
        if not isinstance(n['name'],str) or not n['name'].strip() or len(n['name'])>150 or n['kind'] not in ('single','unit'):
            raise HTTPException(422,'请填写150字以内的单项/单位工程名称及有效层级')
    lookup={n['id']:n for n in nodes}
    if len(lookup)!=len(nodes):raise HTTPException(422,'工程节点编号不能重复')
    names=set()
    for n in nodes:
        if n['kind']=='single' and n['parent_id'] is not None:
            raise HTTPException(422,'单项工程直属建设项目，不能设置其他上级')
        if n['kind']=='unit' and (not isinstance(n['parent_id'],str) or lookup.get(n['parent_id'],{}).get('kind')!='single'):
            raise HTTPException(422,'单位工程必须归属于本结构内的一个单项工程，不能孤立或循环')
        key=(n['parent_id'],n['name'].strip().casefold())
        if key in names:raise HTTPException(422,'同一上级下工程名称不能重复')
        names.add(key)
    singles=[n for n in nodes if n['kind']=='single'];units=[n for n in nodes if n['kind']=='unit']
    if not singles or not units or any(not any(u['parent_id']==n['id'] for u in units) for n in singles):
        raise HTTPException(422,'每个单项工程至少需要一个单位工程')
    if not isinstance(assignments,dict) or set(assignments)!={b['id'] for b in p['boq']}:
        raise HTTPException(409,'须将每条当前清单完整且唯一归属到单位工程，不能遗漏或夹带历史清单')
    if any(not isinstance(uid,str) or lookup.get(uid,{}).get('kind')!='unit' for uid in assignments.values()):
        raise HTTPException(422,'每条清单只能归属单位工程，不能直接归属单项工程或建设项目')


def validate(p,s,verify_bytes=True):
    if latest(p) and latest(p)['id']!=s['id']:
        raise HTTPException(409,'历史工程结构仅供查阅，请复核当前最新版本')
    validate_shape(p,s)
    if s['source_hash']!=source_hash(p):
        raise HTTPException(409,'清单名称、编码、单位、特征或范围已变化，请修订工程结构并重新复核')
    ev=core.require_evidence(p,s['evidence_id']);doc=core.find(p,'documents',ev['document_id'])
    if ev['document_hash']!=doc['hash']:
        raise HTTPException(409,'工程归属依据的原件关联已失效')
    if not (store.ROOT/'originals'/doc['hash']).is_file():
        raise HTTPException(409,'工程归属依据原件缺失，请恢复原件后重新核验')
    if verify_bytes:original_bytes(doc)


def issues(p):
    s=latest(p)
    if not s:return []
    result=[]
    if s['state']!='approved':result.append('当前工程层级与清单归属尚未复核通过')
    try:validate(p,s,False)
    except HTTPException as e:result.append('工程层级与清单归属无效：'+str(e.detail))
    return result


def rollup(p,result):
    s=latest(p)
    if not s:return None
    lookup={n['id']:n for n in s['nodes']};amounts={uid:Decimal(0) for uid in lookup};counts={uid:0 for uid in lookup}
    rows=[]
    with localcontext() as ctx:
        ctx.prec=80
        if set(s['assignments'])!={b['id'] for b in result['lines']}:
            raise DomainError('工程归属与报价清单不一致，不能生成分级汇总')
        for b in result['lines']:
            uid=s['assignments'][b['id']];unit=lookup[uid];single=lookup[unit['parent_id']]
            amounts[uid]+=Decimal(b['amount']);counts[uid]+=1
            rows.append({'boq_id':b['id'],'boq_name':b['name'],'unit_id':uid,'unit_name':unit['name'],
                         'single_id':single['id'],'single_name':single['name'],'amount':b['amount']})
        for n in s['nodes']:
            if n['kind']=='unit':amounts[n['parent_id']]+=amounts[n['id']];counts[n['parent_id']]+=counts[n['id']]
        subtotal=sum((amounts[n['id']] for n in s['nodes'] if n['kind']=='single'),Decimal(0))
        fees=sum((Decimal(f['amount']) for f in result['fees']),Decimal(0))
        if subtotal!=Decimal(result['subtotal']) or subtotal+fees!=Decimal(result['total']):
            raise DomainError('逐级汇总、分部分项和项目费用不闭合')
        return {'schema':'engineering-summary-v1','structure_id':s['id'],'structure_version':s['version'],'structure_hash':store.digest(s),
                'nodes':[{**n,'subtotal':money(amounts[n['id']]),'line_count':counts[n['id']]} for n in s['nodes']],
                'assignments':rows,'subtotal':money(subtotal),'project_fees':money(fees),'total':result['total'],'closed':True,
                'fee_policy':'单位工程清单合价汇总至单项工程，再汇总建设项目；现有规则费用为项目级单列，不按比例摊入单项或单位工程。空单位工程金额为0，不代表已验证无漏项。'}


@router.get('/projects/{pid}/engineering-structure-targets')
def targets(pid: str,a=Depends(core.actor)):
    with store.connect() as c:
        p=core.project(c,pid,a);s=latest(p)
        return {'nodes':copy.deepcopy(s['nodes']) if s else [],'assignments':copy.deepcopy(s['assignments']) if s else {},
                'source_hash':source_hash(p),'base_hash':store.digest(s) if s else '', 'boq':boq_source(p)}


@router.post('/projects/{pid}/engineering-structures')
def create(pid: str,body: dict,a=Depends(core.actor)):
    core.edit(a)
    for key in ['name','reason']:
        if not isinstance(body.get(key),str) or not body[key].strip() or len(body[key])>(150 if key=='name' else 2000):
            raise HTTPException(422,'请填写结构版本名称及工程划分/清单归属依据')
    request_id=body.get('request_id')
    if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}',request_id):
        raise HTTPException(422,'缺少有效提交标识，请重新读取工程结构')
    fingerprint=store.digest({k:v for k,v in body.items() if k!='request_id'})
    with store.transaction() as c:
        p=core.project(c,pid,a);s=latest(p)
        duplicate=next((v for v in p['engineering_structures'] if v['request_id']==request_id),None)
        if duplicate:
            if duplicate['request_hash']!=fingerprint:raise HTTPException(409,'同一提交标识的工程结构内容不一致')
            return {'structure':duplicate,'duplicate':True}
        if body.get('base_hash')!=(store.digest(s) if s else '') or body.get('source_hash')!=source_hash(p):
            raise HTTPException(409,'工程结构或清单已变化，请重新读取后核对')
        record={'id':store.uid(),'version':len(p['engineering_structures'])+1,'name':body['name'].strip(),'reason':body['reason'].strip(),
                'nodes':copy.deepcopy(body.get('nodes')),'assignments':copy.deepcopy(body.get('assignments')),
                'source_hash':source_hash(p),'evidence_id':body.get('evidence_id'),'state':'draft','created_at':store.now(),
                'request_id':request_id,'request_hash':fingerprint,'author':a['username']}
        # Validate against the new latest version without mutating the stored source on failure.
        proposed={**p,'engineering_structures':p['engineering_structures']+[record]}
        validate(proposed,record)
        p['engineering_structures'].append(record);core.invalidate(p,'quotes')
        store.audit(c,p,a,'ENGINEERING_STRUCTURE_PROPOSED',record);store.save(c,p)
    return {'structure':record,'duplicate':False}
