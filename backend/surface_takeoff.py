"""Evidence-backed selection of exact boundary faces of a rectilinear solid."""
from collections import defaultdict
from decimal import Decimal
from fastapi import HTTPException
from . import store
from .engine import DomainError, precise, quantity_number
from .compound_takeoff import op, text
from .measurement_units import LENGTH
from .spatial_takeoff import calculate as solid_quantity, validate as validate_solid

VERSION = 'solid-boundary-1.0'
PURPOSES = {'formwork': '模板接触面', 'waterproof': '防水展开面'}
NOTICE = '边界面包含外表面及洞口/内腔表面；内部实体接触面已消除。边界面积不等于模板或防水工程量，每个面须按实际做法及适用计量规则明确计入或排除。仅支持轴向长方体净实体，不推断材料、施工接触、搭接、翻边或曲面。'
SNAPSHOT_FIELDS = ['id', 'revision', 'name', 'kind', 'parameters', 'quantity_type', 'regions', 'multiplicity',
                   'multiplicity_evidence_id', 'coordinate_basis', 'coordinate_evidence_id', 'measurement_basis', 'basis_evidence_id']


def snapshot(source):
    return {k: source[k] for k in SNAPSHOT_FIELDS}


def canonical(value):
    return '0' if not value else format(value.normalize(), 'f')


@precise
def boundary(source):
    """Coordinate compression; cells are exact intervals, never sampled pixels."""
    result = solid_quantity(source)
    positions = {axis: {} for axis in 'xyz'}
    for i, r in enumerate(result['spatial_regions']):
        v = {k: Decimal(n) for k, n in r['normalized_parameters'].items()}
        for axis, size in zip('xyz', ['length', 'width', 'height']):
            start = op('multiply', {'dimension': str(i+1)+'.'+axis}, {'constant': LENGTH[r['unit']]})
            end = op('add', start, op('multiply', {'dimension': str(i+1)+'.'+size}, {'constant': LENGTH[r['unit']]}))
            positions[axis].setdefault(v[axis], start)
            positions[axis].setdefault(v[axis]+v[size], end)
    coords = {axis: sorted(values) for axis, values in positions.items()}
    indices = {axis: {v: i for i, v in enumerate(values)} for axis, values in coords.items()}
    occupied, holes = set(), set()
    for r in result['spatial_regions']:
        v = {k: Decimal(n) for k, n in r['normalized_parameters'].items()}
        ranges = [range(indices[a][v[a]], indices[a][v[a]+v[s]]) for a, s in zip('xyz', ['length', 'width', 'height'])]
        target = occupied if r['operation'] == 'add' else holes
        target.update((x, y, z) for x in ranges[0] for y in ranges[1] for z in ranges[2])
    occupied -= holes
    if not occupied:
        raise DomainError('净实体为空，没有可核定的边界面')
    planes = defaultdict(set)
    for cell in occupied:
        for axis in range(3):
            uv = [i for i in range(3) if i != axis]
            for sign in (-1, 1):
                neighbor = list(cell); neighbor[axis] += sign
                if tuple(neighbor) not in occupied:
                    planes[(axis, sign, cell[axis]+(sign == 1))].add((cell[uv[0]], cell[uv[1]]))
    faces = []
    totals = {axis+sign: Decimal(0) for axis in 'xyz' for sign in ['-', '+']}
    for (axis, sign, plane), cells in sorted(planes.items()):
        a = 'xyz'[axis]; u, v = [c for c in 'xyz' if c != a]
        while cells:
            u0, v0 = min(cells); u1 = u0+1
            while (u1, v0) in cells: u1 += 1
            v1 = v0+1
            while all((i, v1) in cells for i in range(u0, u1)): v1 += 1
            cells.difference_update((i, j) for i in range(u0, u1) for j in range(v0, v1))
            c = {'normal': a+('+' if sign == 1 else '-'), 'plane': canonical(coords[a][plane]), 'u_axis': u, 'v_axis': v,
                 'u_from': canonical(coords[u][u0]), 'u_to': canonical(coords[u][u1]), 'v_from': canonical(coords[v][v0]), 'v_to': canonical(coords[v][v1])}
            area = (coords[u][u1]-coords[u][u0])*(coords[v][v1]-coords[v][v0])
            formula = op('multiply', op('subtract', positions[u][coords[u][u1]], positions[u][coords[u][u0]]),
                         op('subtract', positions[v][coords[v][v1]], positions[v][coords[v][v0]]))
            faces.append({**c, 'face_id': store.digest(c), 'area': str(area), 'formula_ast': formula})
            totals[c['normal']] += area
    if len(faces) > 1000:
        raise DomainError('边界分面超过1000项，请按专业计量范围拆分空间构件')
    return {'faces': [{**f, 'index': i+1} for i, f in enumerate(faces)], 'total_area': str(sum(totals.values(), Decimal(0))),
            'normal_totals': {k: str(v) for k, v in totals.items()}, 'dimensions': result['dimensions'],
            'source_evidence': result['source_evidence'], 'unit_conversions': result['unit_conversions']}


def source_component(p, cid):
    from .main import find
    source = find(p, 'components', cid)
    if source['kind'] != 'spatial_regions' or source['state'] != 'approved':
        raise HTTPException(409, '表面算量须选择当前已审定的空间长方体构件')
    validate_solid(p, source, saved=True)
    return source


def candidates(p, cid):
    source = source_component(p, cid)
    return {**boundary(source), 'source_component_id': source['id'], 'source_component_hash': store.digest(snapshot(source)),
            'source_name': source['name'], 'source_revision': source['revision'], 'multiplicity': source['multiplicity'], 'notice': NOTICE}


@precise
def calculate(comp, source=None):
    source = source if source is not None else comp['source_geometry']
    if comp.get('parameters') != {} or comp.get('quantity_type') != 'area' or comp.get('purpose') not in PURPOSES:
        raise DomainError('表面算量须选择模板/防水用途，顶层参数为空，结果为面积')
    text(comp.get('name'), '表面构件名称', 200)
    text(comp.get('measurement_basis'), '总体计量依据')
    if comp.get('source_component_id') != source['id'] or comp.get('source_component_hash') != store.digest(snapshot(source)):
        raise HTTPException(409, '来源空间构件已修订，请重新读取边界面并逐面核定')
    result = boundary(source)
    decisions = comp.get('face_decisions')
    if not isinstance(decisions, list) or len(decisions) != len(result['faces']):
        raise DomainError('须为全部边界面明确记录计入或排除，不能遗漏')
    by_id = {}
    refs = dict(result['source_evidence'])
    refs['surface_basis'] = text(comp.get('basis_evidence_id'), '总体表面计量依据', 200)
    for d in decisions:
        if not isinstance(d, dict) or set(d) != {'face_id', 'included', 'basis', 'evidence_id'} or not isinstance(d['face_id'], str) or type(d['included']) is not bool:
            raise DomainError('每面须包含面编号、布尔计量决定、计量说明和原始依据')
        if d['face_id'] in by_id:
            raise DomainError('边界面计量决定不能重复')
        text(d['basis'], '逐面计入或排除依据')
        text(d['evidence_id'], '逐面原始依据', 200)
        by_id[d['face_id']] = d
    if set(by_id) != {f['face_id'] for f in result['faces']}:
        raise DomainError('存在过期或未知边界面，请重新读取当前来源')
    selected, excluded, rows, nodes = Decimal(0), Decimal(0), [], []
    for f in result['faces']:
        d = by_id[f['face_id']]
        refs['face.'+str(f['index'])] = d['evidence_id']
        rows.append({**f, **d})
        if d['included']:
            selected += Decimal(f['area']); nodes.append(f['formula_ast'])
        else: excluded += Decimal(f['area'])
    count = Decimal(source['multiplicity'])
    return {'quantity': str(quantity_number(selected*count)), 'unit': 'm²', 'gross': str(quantity_number(Decimal(result['total_area'])*count)),
            'deduction': str(quantity_number(excluded*count)), 'parameters': {}, 'formula': '核定计入边界面积 '+str(selected)+' × '+str(count),
            'formula_ast': op('multiply', op('add', *nodes) if nodes else {'constant': '0'}, {'dimension': 'multiplicity'}),
            'dimensions': result['dimensions'], 'source_evidence': refs, 'unit_conversions': result['unit_conversions'],
            'coordinate_basis': source['coordinate_basis'], 'source_component_id': source['id'], 'source_component_hash': comp['source_component_hash'],
            'source_geometry': snapshot(source), 'surface_faces': rows,
            'surface_summary': {'purpose': comp['purpose'], 'purpose_label': PURPOSES[comp['purpose']], 'total_area': result['total_area'],
                                'included_area': str(selected), 'excluded_area': str(excluded), 'face_count': len(rows),
                                'included_count': sum(d['included'] for d in decisions), 'multiplicity': str(count),
                                'source_name': source['name'], 'source_revision': source['revision'], 'normal_totals': result['normal_totals']},
            'rule_version': VERSION, 'rounding_rule': 'Decimal 80位计算精度；表面积不另行修约', 'notice': NOTICE}


def validate(p, comp, saved=False):
    from .main import require_evidence, find
    from .task_execution import original_bytes
    source = source_component(p, comp.get('source_component_id'))
    result = calculate(comp, source)
    sources, checked = {}, set()
    for key, eid in result['source_evidence'].items():
        ev = require_evidence(p, eid); doc = find(p, 'documents', ev['document_id'])
        if ev['document_hash'] != doc['hash']:
            raise HTTPException(409, '表面依据与原件校验值不一致')
        if doc['hash'] not in checked: original_bytes(doc); checked.add(doc['hash'])
        sources[key] = {'evidence': ev, 'document_hash': doc['hash']}
    stamp = store.digest({'source_component_hash': comp['source_component_hash'], 'sources': sources})
    if saved and (comp.get('source_stamp') != stamp or comp.get('evidence') != result['source_evidence'] or comp.get('preview') != result or comp.get('source_geometry') != snapshot(source)):
        raise HTTPException(409, '表面来源、计量决定或计算内容已变化，请修订后重新复核')
    return result, stamp
