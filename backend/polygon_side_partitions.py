"""Evidence-backed rectangles in a polygon edge's local S / relative Z plane."""
from decimal import Decimal, localcontext
from . import store
from .compound_takeoff import op, text
from .engine import DomainError, precise, quantity_number
from .measurement_units import LENGTH, VERSION as UNIT_VERSION
from .planar_takeoff import linear

VERSION = 'polygon-boundary-1.1'
NOTICE = '多边形外侧和孔内侧可在原边展开面分区：S为从原边起点沿输入方向的距离，Z为距局部底面0的高度，均非共同设计XY或绝对高程。上下扣孔净面仍整体核定，不支持其局部分区。分割只划分原有边界，各子面须明确计入或排除；不推断施工接触、搭接、翻边或其他实体相交。'
ROUNDING = 'Decimal 80位计算；局部S上限采用来源80位边长平方根，逐子面按80位乘法计算，不另行修约。用200位中间校验确认有限十进制展开边界恰好铺满原面，保存子面合计与原面80位面积的精度差，不调平子面；计入子面合计后仅乘一次来源重复数量'


def canonical(value):
    return '0' if not value else format(value.normalize(), 'f')


@precise
def partition(source, subdivisions, result):
    originals = {f['face_id']:f for f in result['faces']}
    configs, records = {}, []
    for i, group in enumerate(subdivisions,1):
        if not isinstance(group,dict) or set(group)!={'face_id','u_cuts','v_cuts'} or not isinstance(group['face_id'],str):
            raise DomainError('侧面分区须明确原面编号、沿边S及局部Z分割坐标')
        fid=group['face_id']
        if fid not in originals or fid in configs:
            raise DomainError('分区原始面不存在、已过期或重复')
        f=originals[fid]
        if f['geometry_type']!='polygon_side':
            raise DomainError('上下多边形净面暂不支持局部分区；只能选择外侧或孔内侧展开面')
        bounds={}
        for axis, label, end, expr in [('u','s',f['edge_length'],f['formula_ast']['args'][0]),
                                       ('v','z',f['height'],f['formula_ast']['args'][1])]:
            cuts=group[axis+'_cuts']
            if not isinstance(cuts,list) or len(cuts)>8:
                raise DomainError('每个原侧面每轴最多8条分割线')
            values={Decimal(0):{'constant':'0'},Decimal(end):expr}
            for j,cut in enumerate(cuts,1):
                if not isinstance(cut,dict) or set(cut)!={'value','unit','evidence_id'}:
                    raise DomainError('分割坐标须保留原值、长度单位和原始依据')
                value=linear(cut['value'],cut['unit'])
                if not 0<value<Decimal(end) or value in values:
                    raise DomainError('局部S/Z分割坐标须严格在原边长/厚度内部，不能重复、落边或越界')
                eid=text(cut['evidence_id'],'侧面分割原始依据',200)
                key=f'cut.{i}.{axis}.{j}'
                result['dimensions'][key]=str(cut['value']);result['source_evidence'][key]=eid
                conversion={'parameter':key,'original_value':str(cut['value']),'source_unit':cut['unit'],
                            'factor':LENGTH[cut['unit']],'canonical_value':canonical(value),'canonical_unit':'m','rule_version':UNIT_VERSION}
                result['unit_conversions'].append(conversion)
                records.append({**conversion,'parent_face_id':fid,'parent_index':f['index'],'axis':axis,
                                'coordinate_axis':label,'coordinate_system':'edge_local','evidence_id':eid})
                values[value]=op('multiply',{'dimension':key},{'constant':LENGTH[cut['unit']]})
            bounds[axis]=values
        if not group['u_cuts'] and not group['v_cuts']:
            raise DomainError('分区至少需要一条分割线；不分区请移除配置')
        configs[fid]=bounds
    count=len(result['faces'])+sum((len(b['u'])-1)*(len(b['v'])-1)-1 for b in configs.values())
    if count>1000:
        raise DomainError('分区后超过1000个计量面，请拆分专业计量范围')
    faces, checks = [], []
    for f in result['faces']:
        if f['face_id'] not in configs:
            faces.append(dict(f));continue
        b=configs[f['face_id']];us,vs=sorted(b['u']),sorted(b['v']);children=[];exact_areas=[]
        for u0,u1 in zip(us,us[1:]):
            for v0,v1 in zip(vs,vs[1:]):
                index=len(children)+1
                with localcontext() as ctx:
                    ctx.prec=200;exact_areas.append((u1-u0)*(v1-v0))
                area=quantity_number((u1-u0)*(v1-v0))
                local={'u_from':canonical(u0),'u_to':canonical(u1),'v_from':canonical(v0),'v_to':canonical(v1)}
                formula=op('multiply',op('subtract',b['u'][u1],b['u'][u0]),op('subtract',b['v'][v1],b['v'][v0]))
                children.append({**f,**local,'face_id':store.digest({'parent':f['face_id'],**local}),
                                 'parent_face_id':f['face_id'],'parent_index':f['index'],'sub_index':index,
                                 'label':f['label']+f' · 子{index}',
                                 'position':f'原边XY ({f["from_xy"]["x"]},{f["from_xy"]["y"]})→({f["to_xy"]["x"]},{f["to_xy"]["y"]})m；沿原边S {canonical(u0)}→{canonical(u1)}m；局部Z {canonical(v0)}→{canonical(v1)}m',
                                 'calculation':f'({canonical(u1)}−{canonical(u0)}) × ({canonical(v1)}−{canonical(v0)})',
                                 'area':str(area),'formula_ast':formula})
        with localcontext() as ctx:
            ctx.prec=200
            exact_parent=Decimal(f['edge_length'])*Decimal(f['height'])
            if sum(exact_areas,Decimal(0))!=exact_parent:
                raise DomainError('侧面分区不能完整闭合，请核对原边及坐标精度')
            children_area=sum((Decimal(c['area']) for c in children),Decimal(0))
            difference=children_area-Decimal(f['area'])
            checks.append({'parent_face_id':f['face_id'],'parent_index':f['index'],'child_count':len(children),
                           'parent_area':f['area'],'children_area':str(children_area),'area_difference':str(difference),
                           'finite_decimal_tiling':True})
        faces.extend(children)
    totals={k:Decimal(0) for k in result['normal_totals']}
    for f in faces:totals[f['normal']]+=Decimal(f['area'])
    result.update(faces=[{**f,'index':i+1} for i,f in enumerate(faces)],surface_subdivisions=records,
                  surface_partition_checks=checks,total_area=str(sum((Decimal(f['area']) for f in faces),Decimal(0))),
                  normal_totals={k:str(v) for k,v in totals.items()})
    return result
