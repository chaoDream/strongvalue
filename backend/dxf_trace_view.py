"""Display saved CAD selections in their complete source objects after validation."""
import copy
from . import store
from .dxf_projection import project

VERSION='dxf-adopted-source-view-1.0'


def build(comp,doc,projection):
    candidate=comp['dxf_trace']['candidate']
    entries=list(zip(candidate['source_objects'],candidate['source_ranges'])) if candidate['kind']=='ASSEMBLY' else [(candidate,None)]
    originals={};parts=[]
    for index,(source,scope) in enumerate(entries):
        raw=source['source_object'] if source['kind']=='FRAGMENT' else source
        originals.setdefault(raw['id'],copy.deepcopy(raw))
        if source['kind']=='FRAGMENT':
            indices=source['source_point_indices'];start=source['fragment']['from_point'];end=source['fragment']['to_point'];reverse=source['fragment']['reverse']
        else:
            start,end=1,len(raw['points']);reverse=scope['reverse'] if scope else False
            indices=list(range(start,end+1))
            if reverse:indices.reverse()
        route_start=scope['start_point'] if scope else 1
        parts.append({'index':index+1,'source_candidate_id':raw['id'],'object_hash':store.digest(raw),'handle':raw['handle'],'layer':raw['layer'],
                      'unit':raw['unit'],'from_point':start,'to_point':end,'reverse':reverse,'source_point_indices':copy.deepcopy(indices),
                      'route_point_indices':list(range(route_start,route_start+len(indices))),'single_length_m':source['single_length_m']})
    scene=project(list(originals.values()),projection);shapes={s['candidate_id']:s for s in scene['objects']}
    for part in parts:
        part['points']=[copy.deepcopy(shapes[part['source_candidate_id']]['points'][i-1]) for i in part['source_point_indices']]
        part['collapsed_segments']=[i+1 for i,(a,b) in enumerate(zip(part['points'],part['points'][1:])) if a==b]
    return {'version':VERSION,'display_only':True,'component_id':comp['id'],'component_hash':store.digest(comp),'revision':comp['revision'],
            'name':comp['name'],'state':comp['state'],'source_stamp':comp['source_stamp'],'document_id':doc['id'],'document_hash':doc['hash'],
            'document_name':doc['name'],'scene':scene,'sources':list(originals.values()),'parts':parts,
            'notice':'只显示本管线关联的完整原对象及已采用范围，不代表全图。灰色为原对象背景，彩色为采用范围；坐标以原始XYZ为准，不从投影量距。读取不改变构件、计量或审核状态。'}
