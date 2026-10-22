"""Evidence-backed additive/subtractive measurement, never spatial Boolean inference."""
from decimal import Decimal
from fastapi import HTTPException
from . import store
from .engine import GEOMETRIES, DomainError, number, precise, quantity_number, takeoff

STRUCTURED_KINDS = ('compound','planar_regions','spatial_regions','surface_faces','design_route','station_trench','volume_balance')
VERSION = 'compound-geometry-1.0'
PI = '3.1415926535897932384626433832795028841971693993751'


def op(name, *args):
    return {'op': name, 'args': list(args)}


def geometry_ast(kind, prefix):
    def ref(key): return {'dimension': prefix+key}
    def const(value): return {'constant': value}
    r = ref
    if kind == 'slab': return op('multiply', r('length'), r('width'), r('thickness'))
    if kind == 'masonry': return op('multiply', r('length'), r('height'), r('thickness'))
    if kind == 'area': return op('multiply', r('length'), r('width'))
    if kind == 'pipe': return r('length')
    if kind == 'backfill': return op('subtract', r('excavation'), r('occupied'))
    if kind == 'trench': return op('multiply', op('divide', op('add', r('top_width'), r('bottom_width')), const('2')), r('depth'), r('length'))
    if kind == 'rebar_schedule': return op('multiply', r('length'), r('count'), r('unit_weight'))
    if kind == 'ring': return op('multiply', op('divide', const(PI), const('4')), op('subtract', op('multiply', r('outer_diameter'), r('outer_diameter')), op('multiply', r('inner_diameter'), r('inner_diameter'))), r('height'))
    return op('multiply', op('subtract', op('multiply', r('length'), r('width')), op('multiply', op('subtract', r('length'), op('multiply', const('2'), r('thickness'))), op('subtract', r('width'), op('multiply', const('2'), r('thickness'))))), r('height'))


def text(value, label, limit=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise DomainError(label+'必填且不能超长')
    return value.strip()


@precise
def calculate(comp):
    parts = comp.get('measurement_parts')
    if comp.get('parameters') != {} or not isinstance(parts, list) or not 1 <= len(parts) <= 50:
        raise DomainError('分项算量须有1至50项，顶层参数须为空')
    rows, dimensions, refs, additions, deductions = [], {}, {}, [], []
    gross, deducted, unit = Decimal(0), Decimal(0), None
    names = set()
    has_conversions = False
    for i, part in enumerate(parts):
        if not isinstance(part, dict) or set(part) not in ({'name', 'operation', 'kind', 'parameters', 'multiplicity', 'evidence', 'measurement_basis'}, {'name', 'operation', 'kind', 'parameters', 'multiplicity', 'evidence', 'measurement_basis', 'parameter_units'}):
            raise DomainError('分项字段不完整或包含未知字段')
        name = text(part['name'], '分项名称', 200)
        if name in names: raise DomainError('分项名称不能重复')
        names.add(name)
        kind = part['kind']
        if not isinstance(kind, str) or kind not in GEOMETRIES or part['operation'] not in ('add', 'subtract'):
            raise DomainError('分项类型或加扣方式无效')
        fields = set(GEOMETRIES[kind]['fields'])
        if not isinstance(part['parameters'], dict) or set(part['parameters']) != fields:
            raise DomainError('分项尺寸须完整，仅填写当前类型字段；扣减应另列分项')
        evidence = part['evidence']
        if not isinstance(evidence, dict) or set(evidence) != fields | {'multiplicity', 'measurement_basis'}:
            raise DomainError('每项尺寸、重复数量和计量依据都须分别关联原始证据')
        for key, eid in evidence.items(): text(eid, '来源证据', 200)
        text(part['measurement_basis'], '分项计量/扣减说明')
        count = number(part['multiplicity'], True)
        if count != count.to_integral_value() or count > 1000000:
            raise DomainError('重复数量须为1至1000000之间的整数')
        normalized, conversions = part['parameters'], []
        if 'parameter_units' in part:
            from .measurement_units import normalize
            normalized, conversions = normalize(kind, part['parameters'], part['parameter_units'])
            has_conversions = True
        result = takeoff(kind, {**normalized, 'deduction': '0'})
        if unit is not None and unit != result['unit']:
            raise DomainError('同一构件各分项须使用相同计量单位，不能将面积直接扣成体积')
        unit = result['unit']
        amount = quantity_number(Decimal(result['quantity']) * count)
        prefix = str(i+1)+'.'
        dimensions.update({prefix+k: str(v) for k, v in part['parameters'].items()})
        dimensions[prefix+'multiplicity'] = str(count)
        refs.update({prefix+k: eid for k, eid in evidence.items()})
        geometry = geometry_ast(kind, prefix)
        if conversions:
            from .measurement_units import converted_ast
            geometry = converted_ast(geometry, conversions, prefix)
        ast = op('multiply', geometry, {'dimension': prefix+'multiplicity'})
        if part['operation'] == 'add':
            gross += amount
            additions.append(ast)
        else:
            deducted += amount
            deductions.append(ast)
        rows.append({**part, 'index': i+1, 'unit': unit, 'single_quantity': result['quantity'],
                     'quantity': str(amount), 'formula': '('+result['formula'].removesuffix(' − deduction')+') × '+str(count),
                     'formula_ast': ast, 'geometry_rule_version': result['rule_version'],
                     **({'normalized_parameters':normalized, 'unit_conversions':conversions} if conversions else {})})
    if not additions or deducted > gross:
        raise DomainError('至少需要一个加项，扣减合计不能超过加项合计')
    quantity_number(gross)
    quantity_number(deducted)
    refs['measurement_basis'] = text(comp.get('basis_evidence_id'), '总计量依据证据', 200)
    return {'quantity': str(gross-deducted), 'gross': str(gross), 'deduction': str(deducted), 'unit': unit,
            'formula': '加项合计 '+str(gross)+' − 扣减合计 '+str(deducted), 'parameters': {},
            'formula_ast': op('subtract', op('add', *additions), op('add', *deductions)),
            'dimensions': dimensions, 'measurement_parts': rows, 'source_evidence': refs,
            'rounding_rule': 'Decimal 80位计算精度；不对分项或净量另行修约', 'rule_version': 'compound-geometry-1.1' if has_conversions else VERSION}


def validate(p, comp, saved=False):
    from .main import require_evidence, find
    from .task_execution import original_bytes
    result = calculate(comp)
    text(comp.get('name'), '构件名称', 200)
    text(comp.get('measurement_basis'), '总计量/扣减依据')
    sources, checked = {}, set()
    for key, eid in result['source_evidence'].items():
        evidence = require_evidence(p, eid)
        doc = find(p, 'documents', evidence['document_id'])
        if doc['id'] not in checked:
            original_bytes(doc)
            checked.add(doc['id'])
        if evidence.get('document_hash') != doc['hash']:
            raise HTTPException(409, '分项依据与原件校验值不一致')
        sources[key] = {'evidence': evidence, 'document_hash': doc['hash']}
    for i, part in enumerate(comp['measurement_parts']):
        if part['kind'] == 'rebar_schedule':
            for eid in part['evidence'].values():
                if find(p, 'documents', require_evidence(p, eid)['document_id'])['specialty'] != '结构':
                    raise HTTPException(409, '钢筋分项的每项参数和依据须引用结构配筋资料')
    stamp = store.digest(sources)
    if saved and (comp.get('source_stamp') != stamp or comp.get('evidence') != result['source_evidence'] or comp.get('preview') != result):
        raise HTTPException(409, '分项来源或计算内容已变化，请修订构件后重新送审')
    return result, stamp


def structured_validator(kind):
    if kind == 'volume_balance':
        from .volume_balance import validate as validate_balance
        return validate_balance
    if kind == 'station_trench':
        from .trench_takeoff import validate as validate_trench
        return validate_trench
    if kind == 'design_route':
        from .route_takeoff import validate as validate_route
        return validate_route
    if kind == 'surface_faces':
        from .surface_takeoff import validate as validate_surface
        return validate_surface
    if kind == 'spatial_regions':
        from .spatial_takeoff import validate as validate_spatial
        return validate_spatial
    if kind == 'planar_regions':
        from .planar_takeoff import validate as validate_planar
        return validate_planar
    return validate


def component_quantity(comp):
    if comp['kind'] == 'volume_balance':
        from .volume_balance import calculate as calculate_balance
        return calculate_balance(comp)
    if comp['kind'] == 'station_trench':
        from .trench_takeoff import calculate as calculate_trench
        return calculate_trench(comp)
    if comp['kind'] == 'design_route':
        from .route_takeoff import calculate as calculate_route
        return calculate_route(comp)
    if comp['kind'] == 'surface_faces':
        from .surface_takeoff import calculate as calculate_surface
        return calculate_surface(comp)
    if comp['kind'] == 'spatial_regions':
        from .spatial_takeoff import calculate as calculate_spatial
        return calculate_spatial(comp)
    if comp['kind'] == 'planar_regions':
        from .planar_takeoff import calculate as calculate_planar
        return calculate_planar(comp)
    return calculate(comp) if comp['kind'] == 'compound' else takeoff(comp['kind'], comp['parameters'])
