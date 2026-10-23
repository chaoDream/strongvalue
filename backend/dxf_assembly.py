"""Explicit ordered assembly of source DXF paths; no snapping or inferred topology."""
import copy
from decimal import Decimal
from typing import Annotated
from fastapi import HTTPException
from pydantic import BaseModel,ConfigDict,Field,StringConstraints
from . import store
from .dxf_geometry import route_body
from .route_takeoff import calculate

VERSION='dxf-wcs-assembly-1.0'
Hash=Annotated[str,StringConstraints(strict=True,pattern=r'^[a-f0-9]{64}$')]


class SourcePart(BaseModel):
    model_config=ConfigDict(extra='forbid')
    candidate_id:Annotated[str,StringConstraints(strict=True,pattern=r'^[a-f0-9]{32}$')]
    object_hash:Hash
    reverse:bool=Field(strict=True)


class AssemblyIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    segments:list[SourcePart]=Field(min_length=2,max_length=20)


def source_ids(candidate):
    try:
        result={x['id'] for x in candidate['source_objects']} if candidate.get('kind')=='ASSEMBLY' else {candidate['id']}
        if not result or any(not isinstance(x,str) or not x for x in result):raise ValueError()
        return result
    except (AttributeError,KeyError,TypeError,ValueError):raise HTTPException(409,'已有CAD采用来源不完整，请先核对原管线记录') from None


def assemble(items,segments):
    selected=AssemblyIn.model_validate({'segments':segments}).model_dump()['segments']
    ids=[s['candidate_id'] for s in selected]
    if len(ids)!=len(set(ids)):raise HTTPException(422,'同一CAD原对象不能在序列中重复使用，反向重复也不允许')
    sources=[];points=[];ranges=[];joins=[]
    by_id={r['id']:r for r in items}
    for index,part in enumerate(selected):
        row=by_id.get(part['candidate_id'])
        if not row or store.digest(row)!=part['object_hash']:raise HTTPException(409,'组装的CAD对象、原件或解析任务已变化，请重新读取')
        if not row['eligible']:raise HTTPException(422,'原对象 '+row['handle']+' 不能组装：'+row['issue'])
        if sources and (row['document_id']!=sources[0]['document_id'] or row['unit']!=sources[0]['unit']):
            raise HTTPException(422,'组装只允许同一DXF原件与同一声明单位')
        oriented=list(reversed(row['points'])) if part['reverse'] else row['points']
        start=len(points) if points else 1
        if points:
            if any(Decimal(points[-1][k])!=Decimal(oriented[0][k]) for k in ('x','y','z')):
                raise HTTPException(422,'第'+str(index)+'与第'+str(index+1)+'个对象的XYZ端点不完全一致；请明确顺序和方向，不自动吸附缺口')
            joins.append({'point_index':len(points),'previous_handle':sources[-1]['handle'],'next_handle':row['handle'],
                          'incoming_end':copy.deepcopy(points[-1]),'outgoing_start':copy.deepcopy(oriented[0]),'unit':row['unit']})
        points.extend(copy.deepcopy(oriented[1:] if points else oriented))
        if len(points)>101:raise HTTPException(422,'组装后超过101个顶点；请按实际计量范围拆分，不截断坐标')
        sources.append(copy.deepcopy(row));ranges.append({'start_point':start,'end_point':len(points),'reverse':part['reverse'],'candidate_id':row['id']})
    first=sources[0]
    row={k:first[k] for k in ('document_id','document_hash','job_id','job_hash','unit')}
    row.update(version=VERSION,kind='ASSEMBLY',ordinal=first['ordinal'],handle=' → '.join(s['handle'] for s in sources),
               layer=first['layer'] if len({s['layer'] for s in sources})==1 else '多图层（逐项见来源）',
               points=points,source_objects=sources,selection=selected,source_ranges=ranges,joins=joins,eligible=True,issue='')
    row['single_length_m']=calculate(route_body(row,'assembly-preview'))['route_summary']['single_length']
    row['id']=store.digest(row)[:32]
    return row


def rebuild(items,candidate):
    try:
        if candidate.get('kind')=='ASSEMBLY':return assemble(items,candidate.get('selection'))
        return next((r for r in items if r['id']==candidate.get('id')),None)
    except (KeyError,TypeError,ValueError,HTTPException):
        raise HTTPException(409,'已保存的DXF组装来源已变化或不完整，请重新核对采用') from None
