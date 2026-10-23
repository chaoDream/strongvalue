"""Explicit whole-segment selections from original CAD vertices; no interpolation."""
import copy
from fastapi import HTTPException
from . import store
from .dxf_selection import FragmentIn
from .dxf_geometry import route_body
from .route_takeoff import calculate

VERSION='dxf-wcs-fragment-1.0'


def fragment(items,selection):
    selection=FragmentIn.model_validate(selection).model_dump()
    source=next((r for r in items if r['id']==selection['candidate_id']),None)
    if not source or store.digest(source)!=selection['object_hash']:raise HTTPException(409,'CAD原对象、文件或任务已变化，请重新读取')
    if not source['eligible']:raise HTTPException(422,'此CAD原对象不支持取段：'+source['issue'])
    start,end=selection['from_point'],selection['to_point']
    if end>len(source['points']):raise HTTPException(422,'所选原始顶点超出对象范围，不截断或补点')
    indices=list(range(start,end+1))
    if selection['reverse']:indices.reverse()
    row={k:source[k] for k in ('document_id','document_hash','job_id','job_hash','unit','ordinal','handle','layer')}
    row.update(version=VERSION,kind='FRAGMENT',source_object=copy.deepcopy(source),fragment=selection,
               source_point_indices=indices,points=[copy.deepcopy(source['points'][i-1]) for i in indices],eligible=True,issue='')
    row['single_length_m']=calculate(route_body(row,'fragment-preview'))['route_summary']['single_length']
    row['id']=store.digest(row)[:32]
    return row


def source_spans(candidate):
    """Original segment intervals [first vertex, last vertex); touching is disjoint."""
    try:
        if candidate['kind']=='FRAGMENT':
            source=candidate['source_object'];scope=FragmentIn.model_validate(candidate['fragment'])
            if scope.candidate_id!=source['id'] or scope.object_hash!=store.digest(source) or scope.to_point>len(source['points']):raise ValueError()
            return [(source['id'],scope.from_point,scope.to_point)]
        sources=candidate['source_objects'] if candidate['kind']=='ASSEMBLY' else [candidate]
        result=[]
        for source in sources:
            if source.get('kind')=='FRAGMENT':
                result.extend(source_spans(source));continue
            if source.get('kind')=='ASSEMBLY':raise ValueError()
            if not isinstance(source['id'],str) or not source['id'] or not 2<=len(source['points'])<=101:raise ValueError()
            result.append((source['id'],1,len(source['points'])))
        if not result:raise ValueError()
        return result
    except (KeyError,TypeError,ValueError,AttributeError):
        raise HTTPException(409,'已有CAD取段来源不完整，请先核对原管线记录') from None


def overlaps(a,b):
    return any(x==y and max(start,other_start)<min(end,other_end)
               for x,start,end in source_spans(a) for y,other_start,other_end in source_spans(b))
