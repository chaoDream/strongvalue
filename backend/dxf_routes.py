"""Source-bound DXF path adoption after independent reference-length calibration."""
import copy
from decimal import Decimal
from typing import Annotated, Literal
from fastapi import APIRouter,Depends,HTTPException,Query
from pydantic import BaseModel,ConfigDict,Field,StringConstraints
from . import main as core,store
from .engine import precise
from .dxf_geometry import parse,route_body,VERSION
from .planar_takeoff import linear
from .task_execution import original_bytes
from .dxf_assembly import SourcePart,AssemblyIn,assemble,rebuild,source_ids

router=APIRouter(prefix='/api/v1')
Text=Annotated[str,StringConstraints(strict=True,strip_whitespace=True,min_length=1,max_length=2000)]
Hash=Annotated[str,StringConstraints(pattern=r'^[a-f0-9]{64}$')]
SCOPE=['coordinate_basis','measurement_basis','basis_evidence_id','multiplicity','rounding_digits']


class Calibration(BaseModel):
    model_config=ConfigDict(extra='forbid')
    from_point:int=Field(strict=True,ge=1,le=101)
    to_point:int=Field(strict=True,ge=1,le=101)
    distance:Text
    unit:Literal['m','mm','cm','dm','km']
    tolerance:Text


class PreviewIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id:Annotated[str,StringConstraints(pattern=r'^[a-zA-Z0-9-]{16,64}$')]
    object_hash:Hash
    name:Text
    coordinate_basis:Text
    measurement_basis:Text
    basis_evidence_id:Text
    calibration_evidence_id:Text
    calibration:Calibration
    multiplicity:Text
    rounding_digits:int=Field(strict=True,ge=0,le=12)
    reason:Text
    source_confirmed:Annotated[bool,Field(strict=True)]
    component_id:Text|None=None
    component_hash:Hash|None=None
    segments:list[SourcePart]|None=Field(default=None,min_length=2,max_length=20)


class AdoptIn(PreviewIn):
    preview_hash:Hash


def request_payload(body):
    # Preserve pre-assembly request hashes for retries of v0.49 operations.
    return body.model_dump(exclude={'segments'} if body.segments is None else set())


def document(p,did):
    doc=core.find(p,'documents',did)
    if not doc['name'].lower().endswith('.dxf'):raise HTTPException(422,'此入口只读取ASCII DXF原件')
    if doc.get('superseded') or not doc.get('parsed'):raise HTTPException(409,'DXF版本已被替代或尚未完成本地解析')
    job=next((j for j in reversed(p['jobs']) if j['document_id']==did and j.get('kind')=='local_parse'),None)
    if not job or job['state']!='completed' or job.get('source_hash')!=doc['hash']:raise HTTPException(409,'DXF本地解析任务来源已变化')
    raw=original_bytes(doc)
    return doc,job,raw


def rows(p,did):
    doc,job,raw=document(p,did);data=parse(raw);result=[]
    for row in data['objects']:
        item={**row,'version':VERSION,'document_id':did,'document_hash':doc['hash'],'job_id':job['id'],'job_hash':store.digest(job)}
        item['id']=store.digest(item)[:32];result.append(item)
    return doc,data,result


def evidence_proof(p,eid):
    e=core.require_evidence(p,eid);d=core.find(p,'documents',e['document_id'])
    if e.get('document_hash')!=d['hash']:raise HTTPException(409,'DXF校核依据的文档关联失效')
    original_bytes(d)
    return {'evidence_id':eid,'evidence_hash':store.digest(e),'document_id':d['id'],'document_hash':d['hash']}


@precise
def calibration(row,body):
    points=row['points'];start,end=body['from_point']-1,body['to_point']-1
    if start==end or start>=len(points) or end>=len(points):raise HTTPException(422,'校核须选择此对象中两个不同的有效顶点')
    a,b=points[start],points[end]
    observed=sum(((linear(a[k],row['unit'])-linear(b[k],row['unit']))**2 for k in ('x','y','z')),Decimal(0)).sqrt()
    known=linear(body['distance'],body['unit'],True);allowed=linear(body['tolerance'],body['unit'])
    if allowed<0:raise HTTPException(422,'校核容差不得为负值')
    delta=observed-known
    return {**body,'cad_distance_m':str(observed),'known_distance_m':str(known),'tolerance_m':str(allowed),'delta_m':str(delta),'passed':abs(delta)<=allowed,
            'notice':'校核为两点三维直线距离，不是折线总长；只比较已声明单位，不自动缩放、平移、旋转或补高程。'}


def evaluate(p,did,cid,body):
    doc,data,items=rows(p,did)
    row=assemble(items,body['segments']) if body.get('segments') is not None else next((r for r in items if r['id']==cid),None)
    if not row or row['id']!=cid or store.digest(row)!=body['object_hash']:raise HTTPException(409,'DXF候选、任务或原件已变化，请重新读取')
    if not row['eligible']:raise HTTPException(422,row['issue'])
    proof={k:evidence_proof(p,body[k+'_evidence_id']) for k in ('basis','calibration')}
    calibration_source=core.require_evidence(p,body['calibration_evidence_id'])
    if calibration_source['document_id']==did or calibration_source.get('method')=='dxf_geometry':
        raise HTTPException(422,'已知长度须引用独立于当前CAD坐标的原始依据，不能用待校核坐标证明自身单位')
    check=calibration(row,body['calibration'])
    from .route_takeoff import calculate
    result=calculate(route_body(row,'dxf-candidate-preview',**body))
    if bool(body.get('component_id'))!=bool(body.get('component_hash')):raise HTTPException(422,'修订须同时选择原DXF管线及其版本校验值')
    if body.get('component_id'):
        old=core.find(p,'components',body['component_id'])
        if old['kind']!='design_route' or not old.get('dxf_trace') or store.digest(old)!=body['component_hash']:
            raise HTTPException(409,'原DXF管线已变化或不属于此类来源，请重新选择')
    return {'candidate':row,'calibration':check,'proofs':proof,'result':result,'request':body,'document_name':doc['name']}


@router.get('/projects/{pid}/documents/{did}/dxf-route-candidates')
def read(pid:str,did:str,layer:str=Query('',max_length=2000),offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=200),projection:Literal['xy','xz','yz']='xz',a=Depends(core.actor)):
    with store.connect() as conn:
        p=core.project(conn,pid,a);doc,data,items=rows(p,did)
        layers=sorted({r['layer'] for r in items});items=[r for r in items if not layer or r['layer']==layer]
        from .dxf_projection import project
        batch=items[offset:offset+limit]
        return {'scene':project(batch,projection),'document_name':doc['name'],'unit':data['unit'],'insunits':data['insunits'],'layers':layers,'ignored':data['ignored'],'notice':data['notice'],
                'total':len(items),'offset':offset,'limit':limit,'candidates':[{**r,'object_hash':store.digest(r)} for r in batch]}


@router.post('/projects/{pid}/documents/{did}/dxf-route-assembly')
def assembly(pid:str,did:str,body:AssemblyIn,a=Depends(core.actor)):
    with store.connect() as conn:
        p=core.project(conn,pid,a);doc,data,items=rows(p,did)
        row=assemble(items,body.model_dump()['segments'])
        return {'candidate':{**row,'object_hash':store.digest(row)},'document_name':doc['name'],
                'notice':'按明确顺序与方向组成候选；仅合并XYZ精确相同的连接端点，不自动吸附、补段或增加工程量。'}


@router.post('/projects/{pid}/documents/{did}/dxf-route-candidates/{cid}/preview')
def preview(pid:str,did:str,cid:str,body:PreviewIn,a=Depends(core.actor)):
    with store.connect() as conn:
        result=evaluate(core.project(conn,pid,a),did,cid,request_payload(body))
        return {**result,'preview_hash':store.digest(result)}


@router.post('/projects/{pid}/documents/{did}/dxf-route-candidates/{cid}/adopt')
def adopt(pid:str,did:str,cid:str,body:AdoptIn,a=Depends(core.actor)):
    core.edit(a)
    if body.source_confirmed is not True:raise HTTPException(422,'须明确确认原CAD对象为设计中心线，单位、WCS及高程基准已核对')
    payload=request_payload(body);request_hash=store.digest({'document_id':did,'candidate_id':cid,**payload})
    with store.transaction() as conn:
        p=core.project(conn,pid,a)
        duplicate=next((r for r in p['dxf_route_adoptions'] if r['request_id']==body.request_id),None)
        if duplicate:
            if duplicate['request_hash']!=request_hash or duplicate['author']!=a['username']:raise HTTPException(409,'请求标识已用于其他DXF采用操作')
            return {'adoption':duplicate,'component':core.find(p,'components',duplicate['component_id']),'duplicate':True}
        result=evaluate(p,did,cid,{k:v for k,v in payload.items() if k!='preview_hash'})
        if store.digest(result)!=body.preview_hash:raise HTTPException(409,'DXF校核预览或依据已变化，请重新预览')
        if not result['calibration']['passed']:raise HTTPException(409,'CAD两点距离与已知长度不符，禁止采用或自动缩放')
        row=result['candidate'];old=core.find(p,'components',body.component_id) if body.component_id else None
        if any(source_ids(c['dxf_trace']['candidate']) & source_ids(row) and c['id']!=body.component_id for c in p['components'] if c.get('dxf_trace')):
            raise HTTPException(409,'此CAD对象已有管线登记，请选择原管线修订，不能重复计长')
        eid=store.uid();trace={'version':VERSION,'candidate':row,'scope':{k:payload[k] for k in SCOPE},'calibration':result['calibration'],'calibration_input':body.calibration.model_dump(),
                              'proofs':result['proofs'],'source_confirmed':True,'reason':body.reason,'author':a['username'],'created_at':store.now(),'evidence_id':eid}
        ev={'id':eid,'document_id':did,'document_hash':row['document_hash'],'page':None,'bbox':None,'method':'dxf_geometry',
            'text':evidence_text(row),
            'dxf_geometry':copy.deepcopy(row)}
        p['evidence'].append(ev)
        comp=route_body(row,eid,**payload);comp['dxf_trace']=trace
        comp.update(id=old['id'] if old else store.uid(),revision=old['revision']+1 if old else 1)
        record={'id':store.uid(),'component_id':comp['id'],'component_name':comp['name'],'component_revision':comp['revision'],'trace':copy.deepcopy(trace),
                'previous_component_hash':body.component_hash,'request_id':body.request_id,'request_hash':request_hash,'author':a['username']}
        p['dxf_route_adoptions'].append(record)
        from .route_takeoff import validate
        calculated,stamp=validate(p,comp)
        comp.update(state='draft',preview=calculated,
                    evidence=calculated['source_evidence'],source_stamp=stamp)
        core.invalidate(p,'components')
        if old:
            core.invalidate_component_dependents(p,old['id']);p.setdefault('component_history',[]).append(copy.deepcopy(old))
            p['components'][p['components'].index(old)]=comp
            for t in p['takeoffs']:
                if t.get('component_id')==old['id']:t['state']='stale'
        else:p['components'].append(comp)
        p['status']='TAKEOFF_DRAFT'
        store.audit(conn,p,a,'DXF_ROUTE_ADOPTED',record);store.save(conn,p)
        return {'adoption':record,'component':comp,'duplicate':False}


def evidence_text(row):
    return 'DXF '+row['kind']+' / 句柄 '+row['handle']+' / 图层 '+row['layer']+' / WCS '+store.canonical(row['points'])+' '+row['unit']


def validate_geometry_evidence(p,e):
    candidate=e.get('dxf_geometry')
    if not isinstance(candidate,dict):raise HTTPException(409,'DXF坐标证据缺少原对象来源')
    doc,data,items=rows(p,e.get('document_id'))
    row=rebuild(items,candidate)
    if (not row or row!=candidate or e.get('document_hash')!=row['document_hash'] or e.get('method')!='dxf_geometry'
        or e.get('page') is not None or e.get('bbox') is not None or e.get('text')!=evidence_text(row)):
        raise HTTPException(409,'DXF坐标证据或原件、任务、原对象关联已变化')


def manual_body(p,body,old=None):
    if not body.get('detach_dxf') and not (old and old.get('dxf_trace') and not body.get('dxf_trace')):return body,None
    reason=body.get('detach_reason')
    if (body.get('detach_dxf') is not True or not old or not old.get('dxf_trace') or old['kind']!='design_route'
        or body.get('object_hash')!=store.digest(old) or not isinstance(reason,str) or not reason.strip() or len(reason)>2000):
        raise HTTPException(409,'DXF来源不能静默移除；须明确转为人工坐标，填写原因并核对原管线版本')
    copied=dict(body);copied.pop('dxf_trace',None)
    return copied,reason.strip()


def validate_trace(p,comp):
    try:_validate_trace(p,comp)
    except (KeyError,TypeError,ValueError):raise HTTPException(409,'DXF来源或校核记录不完整，请重新核对采用') from None


def _validate_trace(p,comp):
    trace=comp.get('dxf_trace')
    if not isinstance(trace,dict) or trace.get('version')!=VERSION:raise HTTPException(409,'DXF管线来源记录无效')
    if trace.get('source_confirmed') is not True or not any(x.get('trace')==trace for x in p.get('dxf_route_adoptions',[])):
        raise HTTPException(409,'DXF采用记录已变化或缺失，请重新校核采用')
    candidate=trace.get('candidate',{})
    if not isinstance(candidate,dict):raise HTTPException(409,'DXF原对象来源无效')
    Calibration.model_validate(trace['calibration_input'])
    did=candidate.get('document_id')
    doc,data,items=rows(p,did)
    row=rebuild(items,candidate)
    if not row or row!=candidate or not row['eligible']:raise HTTPException(409,'DXF原对象、任务或原件已变化，请重新校核采用')
    if calibration(row,trace['calibration_input'])!=trace['calibration'] or not trace['calibration']['passed']:
        raise HTTPException(409,'DXF单位校核记录无效')
    for kind in ('basis','calibration'):
        saved=trace['proofs'][kind]
        if evidence_proof(p,saved['evidence_id'])!=saved:raise HTTPException(409,'DXF校核依据已变化，请重新采用')
    if trace.get('scope')!={k:comp[k] for k in SCOPE}:raise HTTPException(409,'DXF计量或坐标口径已变化，请重新采用或转为人工坐标')
    expected=route_body(row,trace['evidence_id'],**trace['scope'])
    if any(comp.get(k)!=v for k,v in expected.items() if k!='name'):raise HTTPException(409,'CAD坐标与原对象不一致；请重新采用或明确转为人工坐标')
    e=core.require_evidence(p,trace['evidence_id'])
    if e.get('dxf_geometry')!=row or e['document_id']!=did or e['document_hash']!=row['document_hash'] or e['method']!='dxf_geometry':
        raise HTTPException(409,'DXF坐标证据关联失效')
