"""Straight-edged developed panels; mass uses exact unrounded net area."""
from .compound_takeoff import text
from .polygon_takeoff import calculate as polygon_quantity

NOTICE='异形板网片仅按同一展开局部XY平面的一个简单直边外轮廓及最多20个独立孔计算。孔须严格在内，互不接触、重叠或包含；不作孔并集，不从栏杆投影推断展开面。逐点核定原点、方向、单位和顶点顺序；首尾闭合不重复首点。面密度须匹配净展开面积口径，微孔不得重复扣除；不推算折弯、曲线、拼接、损耗或密度。'


def calculate(item,prefix):
    text(item['coordinate_basis'],'板片展开平面、局部原点及XY方向')
    e=item['evidence']
    result=polygon_quantity(dict(name=item['name'],parameters={},quantity_type='area',polygon_rings=item['polygon_rings'],coordinate_basis=item['coordinate_basis'],coordinate_evidence_id=e['coordinate_basis'],measurement_basis=item['measurement_basis'],basis_evidence_id=e['measurement_basis'],multiplicity='1',multiplicity_evidence_id=e['quantity'],rounding_digits=12,rounding_evidence_id=e['measurement_basis'],boundaries_confirmed=True))
    # The geometry kernel exposes both raw and display results. Material multiplication
    # must consume the raw area and raw AST, even when its display rounds to zero.
    result['quantity']=result['polygon_summary']['unrounded_quantity']
    result['formula_ast']=result['formula_ast']['args'][0]
    result['formula']='外轮廓面积 '+result['gross']+' − 独立孔面积 '+result['deduction']+'；单片净展开面积不修约'
    result['polygon_summary'].update(rounding_digits=None,rounding_adjustment='0')
    result['rule_version']='developed-polygon-panel-area-1.0'
    result['rounding_rule']='Decimal 80位；采用逐点坐标计算的未修约单片净展开面积，板片面积不单独修约'
    result['notice']=NOTICE
    def namespace(node):
        if isinstance(node,list):return [namespace(n) for n in node]
        if isinstance(node,dict):return {k:prefix+v if k=='dimension' else namespace(v) for k,v in node.items()}
        return node
    result=namespace(result)
    result['dimensions']={prefix+k:v for k,v in result['dimensions'].items()}
    result['source_evidence']={prefix+k:v for k,v in result['source_evidence'].items()}
    result['unit_conversions']=[{**v,'parameter':prefix+v['parameter']} for v in result['unit_conversions']]
    return result
