"""Evidence-backed simple horizontal polygons with strictly contained disjoint holes."""
from decimal import Decimal, ROUND_HALF_UP
from fastapi import HTTPException
from . import store
from .engine import DomainError, number, precise, quantity_number
from .compound_takeoff import op, text
from .planar_takeoff import linear
from .measurement_units import LENGTH, VERSION as UNIT_VERSION

VERSION = 'horizontal-polygon-1.0'
NOTICE = '仅计算已确认同一水平设计平面的直边外轮廓与贯通孔，首尾按输入顺序闭合；不从像素测量，不自动合并多块区域，不推断曲线、坡面、变厚、扣孔阈值或施工附加量。孔须严格位于外轮廓内，彼此不得接触、交叠或包含。'


def cross(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])


def on_segment(a, b, p):
    return cross(a, b, p) == 0 and all(min(a[k], b[k]) <= p[k] <= max(a[k], b[k]) for k in (0, 1))


def intersects(a, b, c, d):
    x, y, z, w = cross(a, b, c), cross(a, b, d), cross(c, d, a), cross(c, d, b)
    return (min(x, y) < 0 < max(x, y) and min(z, w) < 0 < max(z, w)) or any(
        on_segment(u, v, p) for u, v, p in ((a, b, c), (a, b, d), (c, d, a), (c, d, b)))


def edges(points):
    return list(zip(points, points[1:]+points[:1]))


def inside(point, polygon):
    """Winding test with exact orientation; 0 boundary, 1 inside, -1 outside."""
    winding = 0
    for a, b in edges(polygon):
        if on_segment(a, b, point): return 0
        if a[1] <= point[1] < b[1] and cross(a, b, point) > 0: winding += 1
        elif b[1] <= point[1] < a[1] and cross(a, b, point) < 0: winding -= 1
    return 1 if winding else -1


def simple(points):
    if len(set(points)) != len(points):
        raise DomainError('轮廓顶点坐标不得重复；末点无需重复填写首点')
    lines = edges(points)
    for i, (a, b) in enumerate(lines):
        c = points[(i+2) % len(points)]
        if cross(a, b, c) == 0 and sum((b[k]-a[k])*(c[k]-b[k]) for k in (0, 1)) <= 0:
            raise DomainError('轮廓有折返或重叠边，请核对原始顶点顺序')
        for j, (c, d) in enumerate(lines[:i]):
            if j == i-1 or (j == 0 and i == len(lines)-1): continue
            if intersects(a, b, c, d):
                raise DomainError('轮廓边存在相交、重叠或非相邻接触，请核对原始顶点顺序')


@precise
def calculate(comp):
    rings = comp.get('polygon_rings'); mode = comp.get('quantity_type')
    if comp.get('parameters') != {} or mode not in ('area', 'volume') or not isinstance(rings, list) or not 1 <= len(rings) <= 21:
        raise DomainError('多边形须明确面积或等厚体积，顶层参数为空，包含一个外轮廓及最多20个孔')
    if comp.get('boundaries_confirmed') is not True:
        raise DomainError('须确认同一水平设计平面、顶点顺序、首尾闭合与逐孔计量边界')
    for k, label in [('name', '构件名称'), ('coordinate_basis', '共同水平平面、原点及XY方向'), ('measurement_basis', '计量与扣孔依据')]: text(comp.get(k), label)
    refs = {k: text(comp.get(k+'_evidence_id'), '多边形原始依据', 200) for k in ('coordinate', 'basis', 'multiplicity', 'rounding')}
    count = number(comp.get('multiplicity'), True); digits = comp.get('rounding_digits')
    if count != count.to_integral_value() or count > 1000000: raise DomainError('整组重复数量须为1至1000000的整数')
    if type(digits) is not int or not 0 <= digits <= 12: raise DomainError('总量修约位数须为0至12的整数')
    if any(not isinstance(r, dict) or not isinstance(r.get('points'), list) for r in rings): raise DomainError('轮廓须包含明确的顶点列表')
    if sum(len(r['points']) for r in rings) > 200: raise DomainError('全部轮廓合计最多200个顶点，请按实际计量边界拆分')
    dimensions = {'multiplicity': str(comp['multiplicity'])}; conversions = []; rows = []; ring_names = set(); shapes = []
    for ri, ring in enumerate(rings, 1):
        if set(ring) != {'name', 'role', 'points', 'measurement_basis', 'evidence_id'}:
            raise DomainError('每个轮廓须仅包含名称、外轮廓或孔、顶点、计量说明及原始依据')
        name = text(ring['name'], '轮廓名称', 200)
        if name in ring_names: raise DomainError('轮廓名称不能重复')
        ring_names.add(name)
        if ring['role'] != ('outer' if ri == 1 else 'hole'): raise DomainError('第一项必须是唯一外轮廓，后续各项必须是扣孔')
        if not 3 <= len(ring['points']) <= 100: raise DomainError('每个轮廓须有3至100个有序不同顶点')
        text(ring['measurement_basis'], '轮廓计量说明'); refs[f'{ri}.basis'] = text(ring['evidence_id'], '轮廓计量原始依据', 200)
        points = []; point_rows = []; nodes = []; point_names = set()
        for pi, point in enumerate(ring['points'], 1):
            if not isinstance(point, dict) or set(point) != {'name', 'parameters', 'unit', 'evidence'}:
                raise DomainError('顶点须明确名称、XY坐标、原始单位及逐坐标依据')
            point_name = text(point['name'], '顶点名称', 200)
            if point_name in point_names: raise DomainError('同一轮廓内顶点名称不能重复')
            point_names.add(point_name)
            if not isinstance(point['parameters'], dict) or set(point['parameters']) != {'x', 'y'} or not isinstance(point['evidence'], dict) or set(point['evidence']) != {'x', 'y'}:
                raise DomainError('顶点须完整填写XY及各自原始依据，不能混入高程或其他参数')
            values = {k: linear(point['parameters'][k], point['unit']) for k in ('x', 'y')}; point_nodes = {}
            for k in ('x', 'y'):
                key = f'{ri}.{pi}.{k}'; dimensions[key] = str(point['parameters'][k]); refs[key] = text(point['evidence'][k], '顶点原始依据', 200)
                point_nodes[k] = op('multiply', {'dimension': key}, {'constant': LENGTH[point['unit']]})
                conversions.append({'parameter': key, 'original_value': dimensions[key], 'source_unit': point['unit'], 'factor': LENGTH[point['unit']],
                                    'canonical_value': format(values[k], 'f'), 'canonical_unit': 'm', 'rule_version': UNIT_VERSION})
            points.append(tuple(values.values())); nodes.append(point_nodes)
            point_rows.append({**point, 'index': pi, 'normalized_parameters': {k: format(v, 'f') for k, v in values.items()}})
        simple(points)
        terms = []; edge_rows = []; twice = Decimal(0)
        for i, (a, b) in enumerate(edges(points)):
            j = (i+1) % len(points); value = cross(points[0], a, b); twice += value
            def relative(index, axis): return op('subtract', nodes[index][axis], nodes[0][axis])
            term = op('subtract', op('multiply', relative(i, 'x'), relative(j, 'y')), op('multiply', relative(i, 'y'), relative(j, 'x')))
            terms.append(term)
            edge_rows.append({'index': i+1, 'from_index': i+1, 'to_index': j+1, 'cross_product': str(value), 'formula_ast': term})
        if twice == 0: raise DomainError('轮廓面积为零，请核对顶点及计量范围')
        area = quantity_number(abs(twice)/2); ast = op('multiply', op('add', *terms), {'constant': '0.5' if twice > 0 else '-0.5'})
        rows.append({'index': ri, 'name': name, 'role': ring['role'], 'measurement_basis': ring['measurement_basis'], 'evidence_id': ring['evidence_id'],
                     'points': point_rows, 'edges': edge_rows, 'orientation': 'counterclockwise' if twice > 0 else 'clockwise',
                     'signed_double_area': str(twice), 'area': str(area), 'formula_ast': ast})
        shapes.append(points)
    checks = []
    for i, hole in enumerate(shapes[1:], 1):
        if any(intersects(a, b, c, d) for a, b in edges(shapes[0]) for c, d in edges(hole)) or any(inside(point, shapes[0]) != 1 for point in hole):
            raise DomainError('扣孔 '+rows[i]['name']+' 须严格位于外轮廓内，不能接触或跨越外边界')
        checks.append({'left': rows[0]['name'], 'right': rows[i]['name'], 'relation': 'strictly_inside', 'passed': True})
        for j, other in enumerate(shapes[1:i], 1):
            if any(intersects(a, b, c, d) for a, b in edges(other) for c, d in edges(hole)) or inside(hole[0], other) != -1 or inside(other[0], hole) != -1:
                raise DomainError('扣孔之间不能接触、交叠或相互包含，请核定逐孔边界')
            checks.append({'left': rows[j]['name'], 'right': rows[i]['name'], 'relation': 'disjoint', 'passed': True})
    outer = Decimal(rows[0]['area']); holes = sum((Decimal(r['area']) for r in rows[1:]), Decimal(0)); net = outer-holes
    thickness = Decimal(1); thickness_node = {'constant': '1'}
    if mode == 'volume':
        thickness = linear(comp.get('thickness'), comp.get('thickness_unit'), True); dimensions['thickness'] = str(comp['thickness'])
        refs['thickness'] = text(comp.get('thickness_evidence_id'), '统一竖向厚度依据', 200)
        thickness_node = op('multiply', {'dimension': 'thickness'}, {'constant': LENGTH[comp['thickness_unit']]})
        conversions.append({'parameter': 'thickness', 'original_value': dimensions['thickness'], 'source_unit': comp['thickness_unit'], 'factor': LENGTH[comp['thickness_unit']],
                            'canonical_value': str(thickness), 'canonical_unit': 'm', 'rule_version': UNIT_VERSION})
    elif any(comp.get(k) not in (None, '') for k in ('thickness', 'thickness_unit', 'thickness_evidence_id')): raise DomainError('面积模式不得携带厚度，请清空或明确选择等厚体积')
    gross = quantity_number(outer*thickness*count); deduction = quantity_number(holes*thickness*count); unrounded = quantity_number(net*thickness*count)
    quantity = quantity_number(unrounded.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP))
    raw_ast = op('multiply', op('subtract', rows[0]['formula_ast'], op('add', *[r['formula_ast'] for r in rows[1:]]) if len(rows) > 1 else {'constant': '0'}), thickness_node, {'dimension': 'multiplicity'})
    return {'quantity': format(quantity, 'f'), 'gross': str(gross), 'deduction': str(deduction), 'unit': 'm²' if mode == 'area' else 'm³', 'parameters': {},
            'formula': '(外轮廓面积 '+str(outer)+' − 逐孔面积 '+str(holes)+')'+(' × 厚度 '+str(thickness) if mode == 'volume' else '')+' × '+str(count)+'；总量四舍五入至'+str(digits)+'位',
            'formula_ast': {'op': 'round', 'args': [raw_ast], 'digits': digits, 'mode': 'HALF_UP'}, 'dimensions': dimensions, 'source_evidence': refs,
            'coordinate_basis': comp['coordinate_basis'], 'polygon_rings': rows, 'polygon_checks': checks, 'unit_conversions': conversions,
            'polygon_summary': {'outer_area': str(outer), 'hole_area': str(holes), 'net_area': str(net), 'hole_count': len(rows)-1,
                                'point_count': sum(len(s) for s in shapes), 'thickness': str(thickness) if mode == 'volume' else None,
                                'multiplicity': str(count), 'unrounded_quantity': str(unrounded), 'rounding_digits': digits, 'rounding_adjustment': str(quantity-unrounded)},
            'rule_version': VERSION, 'rounding_rule': 'Decimal 80位；坐标、叉积、轮廓及孔面积不单独修约，仅扣孔后乘统一厚度及重复数量的总量HALF_UP修约', 'notice': NOTICE}


def validate(p, comp, saved=False):
    from .main import require_evidence, find
    from .task_execution import original_bytes
    result = calculate(comp); sources = {}; checked = set()
    for key, eid in result['source_evidence'].items():
        ev = require_evidence(p, eid); doc = find(p, 'documents', ev['document_id'])
        if ev.get('document_hash') != doc['hash']: raise HTTPException(409, '多边形依据与原件校验值不一致')
        if doc['hash'] not in checked: original_bytes(doc); checked.add(doc['hash'])
        sources[key] = {'evidence': ev, 'document_hash': doc['hash']}
    stamp = store.digest(sources)
    if saved and (comp.get('source_stamp') != stamp or comp.get('evidence') != result['source_evidence'] or comp.get('preview') != result):
        raise HTTPException(409, '多边形来源、轮廓或计算内容已变化，请修订后重新复核')
    return result, stamp
