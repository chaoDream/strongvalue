"""Exact union/difference of evidence-backed axis-aligned design cuboids."""
from decimal import Decimal
from fastapi import HTTPException
from . import store
from .engine import DomainError, number, precise, quantity_number
from .measurement_units import LENGTH, VERSION as UNIT_VERSION
from .compound_takeoff import op, text
from .planar_takeoff import linear, merged, length_ast

VERSION = 'orthogonal-solid-1.0'
FIELDS = ['x', 'y', 'z', 'length', 'width', 'height']
NOTICE = '仅计算同一设计坐标系的轴向长方体并集与扣减；Z为共同基准下的起点标高。分层示意不可测量，不自动确定专业扣减规则，不支持旋转、曲面或任意三维模型。两两相交体积仅用于定位，不可相加代替并集扣重。'


@precise
def calculate(comp):
    regions = comp.get('regions')
    if comp.get('parameters') != {} or comp.get('quantity_type') != 'volume' or not isinstance(regions, list) or not 1 <= len(regions) <= 20:
        raise DomainError('空间算量仅支持体积，顶层参数为空，长方体数量为1至20项')
    if any(comp.get(k) not in (None, '') for k in ['thickness', 'thickness_unit', 'thickness_evidence_id']):
        raise DomainError('空间算量逐体录入起点标高和高度，不能携带统一板厚')
    for key, label in [('name', '构件名称'), ('coordinate_basis', '共同原点及XYZ轴向依据'), ('measurement_basis', '计量与扣减依据')]:
        text(comp.get(key), label)
    refs = {key: text(comp.get(key+'_evidence_id'), '原始依据证据', 200) for key in ['coordinate', 'basis', 'multiplicity']}
    count = number(comp.get('multiplicity'), True)
    if count != count.to_integral_value() or count > 1000000:
        raise DomainError('整组重复数量须为1至1000000的整数')
    dimensions = {'multiplicity': str(comp['multiplicity'])}
    conversions, shapes, names, zs = [], [], set(), {}

    def node(key, unit):
        return op('multiply', {'dimension': key}, {'constant': LENGTH[unit]})

    for i, r in enumerate(regions):
        if not isinstance(r, dict) or set(r) != {'name', 'operation', 'parameters', 'unit', 'evidence', 'measurement_basis'}:
            raise DomainError('长方体须包含名称、加扣、坐标尺寸、单位、逐参数证据及计量说明')
        name = text(r['name'], '长方体名称', 200)
        if name in names:
            raise DomainError('长方体名称不能重复')
        names.add(name)
        if r['operation'] not in ('add', 'subtract') or not isinstance(r['parameters'], dict) or set(r['parameters']) != set(FIELDS):
            raise DomainError('长方体只支持XYZ起点、长宽高及加项/扣减')
        if not isinstance(r['evidence'], dict) or set(r['evidence']) != set(FIELDS) | {'measurement_basis'}:
            raise DomainError('每个长方体的XYZ、长宽高和计量说明须分别关联原始依据')
        text(r['measurement_basis'], '长方体计量说明')
        values = {k: linear(r['parameters'][k], r['unit'], k in ('length', 'width', 'height')) for k in FIELDS}
        prefix = str(i+1)+'.'
        for k in FIELDS:
            dimensions[prefix+k] = str(r['parameters'][k])
            conversions.append({'parameter': prefix+k, 'original_value': str(r['parameters'][k]), 'source_unit': r['unit'],
                                'factor': LENGTH[r['unit']], 'canonical_value': format(values[k], 'f'), 'canonical_unit': 'm', 'rule_version': UNIT_VERSION})
        for k, eid in r['evidence'].items():
            refs[prefix+k] = text(eid, '长方体原始证据', 200)
        ends = {axis: values[axis]+values[size] for axis, size in zip('xyz', ['length', 'width', 'height'])}
        starts_ast = {axis: node(prefix+axis, r['unit']) for axis in 'xyz'}
        ends_ast = {axis: op('add', starts_ast[axis], node(prefix+size, r['unit'])) for axis, size in zip('xyz', ['length', 'width', 'height'])}
        zs.setdefault(values['z'], starts_ast['z'])
        zs.setdefault(ends['z'], ends_ast['z'])
        shapes.append({'input': r, 'index': i+1, 'values': values, 'ends': ends, 'starts_ast': starts_ast, 'ends_ast': ends_ast,
                       'volume': values['length']*values['width']*values['height']})
    if not any(s['input']['operation'] == 'add' for s in shapes):
        raise DomainError('至少需要一个加项长方体')

    gross, deducted = Decimal(0), Decimal(0)
    layers, volume_nodes = [], []
    boundaries = sorted(zs)
    for bottom, top in zip(boundaries, boundaries[1:]):
        active = [s for s in shapes if s['values']['z'] <= bottom and s['ends']['z'] >= top]
        xs = {}
        for s in active:
            xs.setdefault(s['values']['x'], s['starts_ast']['x'])
            xs.setdefault(s['ends']['x'], s['ends_ast']['x'])
        x_values = sorted(xs)
        add_area, deduct_area = Decimal(0), Decimal(0)
        strips, area_nodes = [], []
        for left, right in zip(x_values, x_values[1:]):
            row = [s for s in active if s['values']['x'] <= left and s['ends']['x'] >= right]
            intervals = {kind: merged([(s['values']['y'], s['ends']['y'], s['starts_ast']['y'], s['ends_ast']['y'])
                                      for s in row if s['input']['operation'] == kind]) for kind in ['add', 'subtract']}
            add, sub = intervals['add'], intervals['subtract']
            if any(not any(a[0] <= d[0] and d[1] <= a[1] for a in add) for d in sub):
                raise DomainError('扣减长方体超出加项空间并集，请核对XYZ共同基准及分层计量边界')
            if not add:
                continue
            a = sum((y[1]-y[0] for y in add), Decimal(0))
            d = sum((y[1]-y[0] for y in sub), Decimal(0))
            width = right-left
            add_area += width*a
            deduct_area += width*d
            area_nodes.append(op('multiply', op('subtract', xs[right], xs[left]), op('subtract', length_ast(add), length_ast(sub))))
            strips.append({'x_from': str(left), 'x_to': str(right), 'width': str(width),
                           'add_intervals': [[str(y[0]), str(y[1])] for y in add], 'deduct_intervals': [[str(y[0]), str(y[1])] for y in sub],
                           'add_area': str(width*a), 'deduct_area': str(width*d), 'net_area': str(width*(a-d))})
        if not strips:
            continue
        height = top-bottom
        gross += add_area*height
        deducted += deduct_area*height
        volume_nodes.append(op('multiply', op('subtract', zs[top], zs[bottom]), op('add', *area_nodes)))
        layers.append({'z_from': str(bottom), 'z_to': str(top), 'height': str(height), 'region_indices': [s['index'] for s in active],
                       'add_area': str(add_area), 'deduct_area': str(deduct_area), 'net_area': str(add_area-deduct_area),
                       'net_volume': str((add_area-deduct_area)*height), 'strips': strips})

    naive_add = sum((s['volume'] for s in shapes if s['input']['operation'] == 'add'), Decimal(0))
    naive_sub = sum((s['volume'] for s in shapes if s['input']['operation'] == 'subtract'), Decimal(0))
    intersections = []
    for i, a in enumerate(shapes):
        for b in shapes[i+1:]:
            if a['input']['operation'] != b['input']['operation']:
                continue
            lengths = {axis: min(a['ends'][axis], b['ends'][axis])-max(a['values'][axis], b['values'][axis]) for axis in 'xyz'}
            if all(v > 0 for v in lengths.values()):
                intersections.append({'operation': a['input']['operation'], 'left': a['input']['name'], 'right': b['input']['name'],
                                      'volume': str(lengths['x']*lengths['y']*lengths['z'])})
    value = quantity_number((gross-deducted)*count)
    return {'quantity': str(value), 'gross': str(quantity_number(gross*count)), 'deduction': str(quantity_number(deducted*count)),
            'unit': 'm³', 'parameters': {}, 'formula': '(加项空间并集 '+str(gross)+' − 扣减空间并集 '+str(deducted)+') × '+str(count),
            'formula_ast': op('multiply', op('add', *volume_nodes), {'dimension': 'multiplicity'}), 'dimensions': dimensions, 'source_evidence': refs,
            'coordinate_basis': comp['coordinate_basis'], 'unit_conversions': conversions,
            'spatial_regions': [{**s['input'], 'index': s['index'], 'normalized_parameters': {k: format(v, 'f') for k, v in s['values'].items()},
                                 'cuboid_volume': str(s['volume'])} for s in shapes], 'spatial_layers': layers, 'spatial_intersections': intersections,
            'spatial_summary': {'naive_add_volume': str(naive_add), 'add_union_volume': str(gross), 'add_overlap_volume': str(naive_add-gross),
                                'naive_deduct_volume': str(naive_sub), 'deduct_union_volume': str(deducted), 'deduct_overlap_volume': str(naive_sub-deducted),
                                'net_volume': str(gross-deducted), 'multiplicity': str(count)}, 'rule_version': VERSION,
            'rounding_rule': 'Decimal 80位计算精度；坐标、面积与体积不另行修约', 'notice': NOTICE}


def validate(p, comp, saved=False):
    from .main import require_evidence, find
    from .task_execution import original_bytes
    result = calculate(comp)
    sources, checked = {}, set()
    for key, eid in result['source_evidence'].items():
        ev = require_evidence(p, eid)
        doc = find(p, 'documents', ev['document_id'])
        if ev['document_hash'] != doc['hash']:
            raise HTTPException(409, '空间算量原始依据与文件校验值不一致')
        if doc['hash'] not in checked:
            original_bytes(doc)
            checked.add(doc['hash'])
        sources[key] = {'evidence': ev, 'document_hash': doc['hash']}
    stamp = store.digest(sources)
    if saved and (comp.get('source_stamp') != stamp or comp.get('evidence') != result['source_evidence'] or comp.get('preview') != result):
        raise HTTPException(409, '空间算量来源或计算内容已变化，请修订后重新复核')
    return result, stamp
