"""Rectangular developed sheet area, with explicitly evidenced rectangular openings."""
from decimal import Decimal
from .compound_takeoff import text
from .engine import DomainError
from .planar_takeoff import calculate as planar_quantity

NOTICE='板件/网片按独立展开平面尺寸及每组片数计算，矩形开口取并集扣除；单位面积质量依据须匹配此净面积口径。网片微孔不自动扣除，也不得在已含微孔的单位面积质量之外再次扣除。不得用建筑投影或栏杆长度推断展开尺寸或片数；不以密度、厚度推算面密度。'


def calculate(item,prefix):
    openings=item['openings']
    if not isinstance(openings,list) or len(openings)>20:raise DomainError('板件开口须明确为0至20项矩形列表，无开口请填空列表')
    e=item['evidence'];regions=[dict(name='外轮廓',operation='add',parameters=dict(x='0',y='0',length=item['length'],width=item['width']),unit=item['length_unit'],measurement_basis='展开局部原点固定为外矩形左下角(0,0)，长沿X、宽沿Y；'+item['measurement_basis'],evidence=dict(x=e['length'],y=e['width'],length=e['length'],width=e['width'],measurement_basis=e['measurement_basis']))]
    for opening in openings:
        if not isinstance(opening,dict) or set(opening)!={'name','parameters','unit','measurement_basis','evidence'}:raise DomainError('板件开口须明确名称、局部坐标尺寸、单位、说明及逐参数依据')
        text(opening['name'],'板件开口名称',200)
        regions.append({**opening,'operation':'subtract'})
    result=planar_quantity(dict(name=item['name'],parameters={},quantity_type='area',coordinate_basis='每片独立展开局部XY平面，原点为外矩形左下角，长沿X、宽沿Y，与来源栏杆坐标无关。',measurement_basis=item['measurement_basis'],coordinate_evidence_id=e['measurement_basis'],basis_evidence_id=e['measurement_basis'],multiplicity='1',multiplicity_evidence_id=e['quantity'],regions=regions))
    if Decimal(result['planar_summary']['net_area'])<=0:raise DomainError('板件扣除开口后净面积须大于零')
    def namespace(node):
        if isinstance(node,list):return [namespace(n) for n in node]
        if isinstance(node,dict):return {k:prefix+v if k=='dimension' else namespace(v) for k,v in node.items()}
        return node
    result['formula_ast']=namespace(result['formula_ast'])
    result['dimensions']={prefix+k:v for k,v in result['dimensions'].items()}
    result['source_evidence']={prefix+k:v for k,v in result['source_evidence'].items()}
    result['unit_conversions']=[{**v,'parameter':prefix+v['parameter']} for v in result['unit_conversions']]
    return result
