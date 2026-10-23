"""Explicit ordered assembly of source DXF paths; no snapping or inferred topology."""
import copy
from decimal import Decimal
from fastapi import HTTPException
from pydantic import BaseModel,ConfigDict,Field
from . import store
from .dxf_selection import AssemblyPart
from .dxf_fragments import fragment,overlaps
from .dxf_geometry import route_body
from .route_takeoff import calculate

VERSION='dxf-wcs-assembly-1.0'
RANGE_VERSION='dxf-wcs-assembly-1.1'


class AssemblyIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    segments:list[AssemblyPart]=Field(min_length=2,max_length=20)


class SelectionIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    segments:list[AssemblyPart]=Field(min_length=1,max_length=20)


def selection(items,segments):
    selected=SelectionIn.model_validate({'segments':segments}).model_dump()['segments']
    if len(selected)>1:return assemble(items,selected)
    part=selected[0]
    if 'from_point' in part:return fragment(items,part)
    row=next((r for r in items if r['id']==part['candidate_id']),None)
    if not row or store.digest(row)!=part['object_hash']:raise HTTPException(409,'所选CAD原对象、原件或任务已变化，请重新读取')
    if not row['eligible']:raise HTTPException(422,row['issue'])
    if part['reverse']:return fragment(items,{**part,'from_point':1,'to_point':len(row['points'])})
    return copy.deepcopy(row)


def assemble(items,segments):
    selected=AssemblyIn.model_validate({'segments':segments}).model_dump()['segments']
    sources=[];points=[];ranges=[];joins=[]
    by_id={r['id']:r for r in items}
    for index,part in enumerate(selected):
        if 'from_point' in part:row=fragment(items,part)
        else:
            row=by_id.get(part['candidate_id'])
            if not row or store.digest(row)!=part['object_hash']:raise HTTPException(409,'组装的CAD对象、原件或解析任务已变化，请重新读取')
        if not row['eligible']:raise HTTPException(422,'原对象 '+row['handle']+' 不能组装：'+row['issue'])
        if any(overlaps(source,row) for source in sources):raise HTTPException(422,'组装序列重复使用了CAD原始管段；共用端点允许，重叠范围或反向重复不允许')
        if sources and (row['document_id']!=sources[0]['document_id'] or row['unit']!=sources[0]['unit']):
            raise HTTPException(422,'组装只允许同一DXF原件与同一声明单位')
        oriented=row['points'] if row['kind']=='FRAGMENT' else list(reversed(row['points'])) if part['reverse'] else row['points']
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
    row.update(version=RANGE_VERSION if any(s['kind']=='FRAGMENT' for s in sources) else VERSION,kind='ASSEMBLY',ordinal=first['ordinal'],handle=' → '.join(s['handle'] for s in sources),
               layer=first['layer'] if len({s['layer'] for s in sources})==1 else '多图层（逐项见来源）',
               points=points,source_objects=sources,selection=selected,source_ranges=ranges,joins=joins,eligible=True,issue='')
    row['single_length_m']=calculate(route_body(row,'assembly-preview'))['route_summary']['single_length']
    row['id']=store.digest(row)[:32]
    return row


def rebuild(items,candidate):
    try:
        if candidate.get('kind')=='FRAGMENT':
            return fragment(items,candidate.get('fragment'))
        if candidate.get('kind')=='ASSEMBLY':return assemble(items,candidate.get('selection'))
        return next((r for r in items if r['id']==candidate.get('id')),None)
    except (KeyError,TypeError,ValueError,HTTPException):
        raise HTTPException(409,'已保存的DXF组装来源已变化或不完整，请重新核对采用') from None
