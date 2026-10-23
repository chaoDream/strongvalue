"""Confirmed design-coordinate polylines. No image scaling or inferred pipe networks."""
from decimal import Decimal, ROUND_HALF_UP
from fastapi import HTTPException
from . import store
from .engine import DomainError, number, precise, quantity_number
from .compound_takeoff import op, text
from .measurement_units import LENGTH, VERSION as UNIT_VERSION
from .planar_takeoff import linear

VERSION = 'design-route-1.0'
NOTICE = '仅按已确认设计中心线的有序XYZ转折点计算三维折线长度；不从示意图测量，不自动连接交叉管线，不计弯头曲线、管件、接口扣减或施工附加量。不同构件间不自动去重；须另行核定计量边界。'


SNAPSHOT_FIELDS = ['id','revision','name','kind','parameters','quantity_type','route_points','coordinate_basis',
                   'coordinate_evidence_id','measurement_basis','basis_evidence_id','multiplicity','multiplicity_evidence_id',
                   'rounding_digits','rounding_evidence_id']


def snapshot(source):
    return {k: source[k] for k in SNAPSHOT_FIELDS}


def overlapping(a, b, c, d):
    """Exact collinearity and positive shared length; touching endpoints is allowed."""
    u = [b[k]-a[k] for k in range(3)]
    def parallel(v):
        return all(u[i]*v[j] == u[j]*v[i] for i, j in [(0, 1), (0, 2), (1, 2)])
    if not parallel([c[k]-a[k] for k in range(3)]) or not parallel([d[k]-a[k] for k in range(3)]):
        return False
    axis = next(k for k in range(3) if u[k])
    return min(max(a[axis], b[axis]), max(c[axis], d[axis])) > max(min(a[axis], b[axis]), min(c[axis], d[axis]))


@precise
def calculate(comp):
    points = comp.get('route_points')
    if comp.get('parameters') != {} or comp.get('quantity_type') != 'length' or not isinstance(points, list) or not 2 <= len(points) <= 101:
        raise DomainError('设计管线须有2至101个有序转折点，类型为长度，顶层参数为空')
    for key, label in [('name', '管线名称'), ('coordinate_basis', '共同原点、XYZ方向及高程基准'), ('measurement_basis', '中心线计量依据')]:
        text(comp.get(key), label)
    refs = {k: text(comp.get(k+'_evidence_id'), '管线原始依据', 200) for k in ['coordinate', 'basis', 'multiplicity', 'rounding']}
    count = number(comp.get('multiplicity'), True)
    if count != count.to_integral_value() or count > 1000000:
        raise DomainError('管线重复数量须为1至1000000的整数')
    digits = comp.get('rounding_digits')
    if type(digits) is not int or not 0 <= digits <= 12:
        raise DomainError('总长度修约位数须明确为0至12的整数')
    dimensions = {'multiplicity': str(comp['multiplicity'])}
    conversions, rows, coordinates, names, seen = [], [], [], set(), set()
    nodes = []
    for i, p in enumerate(points):
        if not isinstance(p, dict) or set(p) != {'name', 'parameters', 'unit', 'evidence'}:
            raise DomainError('转折点须包含名称、XYZ坐标、原始单位和逐坐标证据')
        name = text(p['name'], '转折点名称', 200)
        if name in names:
            raise DomainError('转折点名称不能重复')
        names.add(name)
        if not isinstance(p['parameters'], dict) or set(p['parameters']) != {'x', 'y', 'z'} or not isinstance(p['evidence'], dict) or set(p['evidence']) != {'x', 'y', 'z'}:
            raise DomainError('每个转折点须明确XYZ坐标和各自的原始证据，不能缺省高程')
        values = {k: linear(p['parameters'][k], p['unit']) for k in ['x', 'y', 'z']}
        xyz = tuple(values.values())
        if xyz in seen:
            raise DomainError('转折点坐标不能重复；零长段、闭环或回返须拆分并另行核定')
        seen.add(xyz)
        coordinates.append(xyz)
        point_nodes = {}
        for k in ['x', 'y', 'z']:
            key = str(i+1)+'.'+k
            dimensions[key] = str(p['parameters'][k])
            refs[key] = text(p['evidence'][k], '转折点坐标证据', 200)
            point_nodes[k] = op('multiply', {'dimension': key}, {'constant': LENGTH[p['unit']]})
            conversions.append({'parameter': key, 'original_value': dimensions[key], 'source_unit': p['unit'], 'factor': LENGTH[p['unit']],
                                'canonical_value': format(values[k], 'f'), 'canonical_unit': 'm', 'rule_version': UNIT_VERSION})
        nodes.append(point_nodes)
        rows.append({**p, 'index': i+1, 'normalized_parameters': {k: format(v, 'f') for k, v in values.items()}})
    segments, segment_nodes = [], []
    total = Decimal(0)
    for i, (a, b) in enumerate(zip(coordinates, coordinates[1:])):
        for j in range(i):
            if overlapping(a, b, coordinates[j], coordinates[j+1]):
                raise DomainError('同一管线存在共线重叠段 S'+str(j+1)+' / S'+str(i+1)+'，请核定边界，不能重复计长')
        delta = [b[k]-a[k] for k in range(3)]
        square = sum((v*v for v in delta), Decimal(0))
        length = square.sqrt()
        horizontal = (delta[0]*delta[0]+delta[1]*delta[1]).sqrt()
        diffs = [op('subtract', nodes[i+1][k], nodes[i][k]) for k in ['x', 'y', 'z']]
        ast = op('sqrt', op('add', *[op('multiply', v, v) for v in diffs]))
        segment_nodes.append(ast)
        total += length
        segments.append({'index': i+1, 'from_index': i+1, 'to_index': i+2, 'from_name': rows[i]['name'], 'to_name': rows[i+1]['name'],
                         'delta': dict(zip(['x', 'y', 'z'], map(str, delta))), 'squared_length': str(square), 'length': str(length),
                         'horizontal_length': str(horizontal), 'formula_ast': ast})
    gross = quantity_number(total*count)
    quantity = quantity_number(gross.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP))
    raw_ast = op('multiply', op('add', *segment_nodes), {'dimension': 'multiplicity'})
    return {'quantity': str(quantity), 'gross': str(gross), 'deduction': '0', 'unit': 'm', 'parameters': {},
            'formula': 'Σ√(ΔX² + ΔY² + ΔZ²) × '+str(count)+'；总量四舍五入至'+str(digits)+'位小数',
            'formula_ast': {'op': 'round', 'args': [raw_ast], 'digits': digits, 'mode': 'HALF_UP'},
            'dimensions': dimensions, 'source_evidence': refs, 'coordinate_basis': comp['coordinate_basis'],
            'route_points': rows, 'route_segments': segments, 'unit_conversions': conversions,
            'route_summary': {'point_count': len(rows), 'segment_count': len(segments), 'single_length': str(total), 'multiplicity': str(count),
                              'unrounded_quantity': str(gross), 'rounding_digits': digits, 'rounding_adjustment': str(quantity-gross)},
            'rounding_rule': 'Decimal 80位；各段平方根及长度不单独修约，段长相加乘重复数量后，仅总量HALF_UP至'+str(digits)+'位小数',
            'rule_version': VERSION, 'notice': NOTICE}


def validate(p, comp, saved=False):
    from .main import require_evidence, find
    from .task_execution import original_bytes
    if 'dxf_trace' in comp:
        from .dxf_routes import validate_trace
        validate_trace(p,comp)
    result = calculate(comp)
    sources, checked, evidence_cache = {}, set(), {}
    for key, eid in result['source_evidence'].items():
        if eid not in evidence_cache:
            ev=require_evidence(p,eid);doc=find(p,'documents',ev['document_id'])
            evidence_cache[eid]=(ev,doc)
        ev,doc=evidence_cache[eid]
        if ev['document_hash'] != doc['hash']:
            raise HTTPException(409, '管线坐标依据与原件校验值不一致')
        if doc['hash'] not in checked:
            original_bytes(doc)
            checked.add(doc['hash'])
        sources[key] = {'evidence': ev, 'document_hash': doc['hash']}
    stamp = store.digest(sources)
    if saved and (comp.get('source_stamp') != stamp or comp.get('evidence') != result['source_evidence'] or comp.get('preview') != result):
        raise HTTPException(409, '管线来源或计算内容已变化，请修订后重新复核')
    return result, stamp
