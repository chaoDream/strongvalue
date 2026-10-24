"""BOQ mapping choices are distinct from primitive geometry input forms."""
from .engine import GEOMETRIES
from .compound_takeoff import STRUCTURED_KINDS


def profile(key, label, unit):
    return {'id': key, 'label': label, 'unit': unit}


TYPES = {k: {'label': v['label'], 'profiles': [profile('standard', v['label'], v['unit'])]}
         for k, v in GEOMETRIES.items()}
TYPES.update({
    'compound': {'label': '分项合计与逐项扣减', 'profiles': [profile(k, l, u) for k, l, u in
        [('length', '分项净长度', 'm'), ('area', '分项净面积', 'm²'), ('volume', '分项净体积', 'm³'), ('mass', '钢筋表分项净质量', 'kg')]]},
    'planar_regions': {'label': '平面区域并集与扣减', 'profiles': [profile('area', '平面净面积', 'm²'), profile('volume', '等厚平面净实体体积', 'm³')]},
    'polygon_region': {'label': '水平设计多边形与扣孔', 'profiles': [profile('area', '多边形扣孔后净面积', 'm²'), profile('volume', '多边形等厚净实体体积', 'm³')]},
    'spatial_regions': {'label': '空间长方体并集与扣减', 'profiles': [profile('volume', '空间净实体体积', 'm³')]},
    'foundation_horizontal_formwork': {'label':'立柱基础整体顶底模板','profiles':[profile('area','基础已选整体顶面或底面实际接触净面积','m²')]},
    'foundation_void_formwork': {'label':'立柱基础孔壁模板','profiles':[profile('area','基础已选竖直孔壁实际接触净面积','m²')]},
    'foundation_formwork': {'label':'立柱基础外侧模板','profiles':[profile('area','基础已选竖直外侧实际接触净面积','m²')]},
    'post_foundation': {'label':'栏杆立柱独立基础','profiles':[profile('volume','立柱独立基础实体体积（矩形/圆柱/同心台阶）','m³')]},
    'railing_material': {'label':'栏杆材料表','profiles':[profile('mass','栏杆材料表质量','kg')]},
    'polygon_railing': {'label': '设计边线栏杆', 'profiles': [profile('length', '核定原边水平范围栏杆长度', 'm'), profile('spatial_length', '设计坐标线三维栏杆长度', 'm'), profile('horizontal_projection', '设计坐标线水平投影栏杆长度', 'm')]},
    'surface_faces': {'label': '已审实体逐面计量', 'profiles': [profile('formwork', '模板接触面', 'm²'), profile('waterproof', '防水展开面', 'm²')]},
    'design_route': {'label': '设计坐标管线', 'profiles': [profile('length', '设计中心线三维长度', 'm')]},
    'station_trench': {'label': '管网变断面沟槽', 'profiles': [profile('volume', '按水平投影计沟槽开挖体积', 'm³')]},
    'volume_balance': {'label': '已审体积关联回填', 'profiles': [profile('volume', '已审开挖扣占位后的回填体积', 'm³')]},
    'pipe_envelope': {'label': '管道外包占位', 'profiles': [profile('volume', '含管内空腔的外包占位体积', 'm³')]},
    'route_bedding': {'label': '管网分段矩形垫层', 'profiles': [profile('volume', '按水平投影计本层垫层体积', 'm³')]},
    'circular_chamber': {'label': '圆形检查井井筒', 'profiles': [profile('wall_material', '井筒材料体积', 'm³'), profile('outer_envelope', '含井内空腔的井筒外包占位体积', 'm³')]},
    'chamber_plate': {'label': '圆形井底板与盖板', 'profiles': [profile('base', '井底板净实体体积', 'm³'), profile('cover', '井盖板净实体体积', 'm³')]},
})
assert set(TYPES) == set(GEOMETRIES) | set(STRUCTURED_KINDS)


def selected_profile(rule):
    kind = rule.get('kind')
    if not isinstance(kind, str) or kind not in TYPES:
        return None
    key = rule.get('measurement_profile', 'standard' if kind in GEOMETRIES else None)
    return next((p for p in TYPES[kind]['profiles'] if p['id'] == key and p['unit'] == rule.get('unit')), None)


def component_profile(comp, result):
    kind = comp['kind']
    if kind in GEOMETRIES:
        key = 'standard'
    elif kind == 'compound':
        key = {'m': 'length', 'm²': 'area', 'm³': 'volume', 'kg': 'mass'}.get(result['unit'])
    elif kind in ('surface_faces', 'circular_chamber'):
        key = comp.get('purpose')
    elif kind == 'polygon_railing' and result.get('railing_summary',{}).get('source_kind')=='design_route':
        key={'spatial':'spatial_length','horizontal':'horizontal_projection'}.get(comp.get('length_basis'))
    elif kind == 'chamber_plate':
        key = comp.get('plate_role')
    else:
        key = comp.get('quantity_type')
    return selected_profile({'kind': kind, 'measurement_profile': key, 'unit': result['unit']})
