"""Confirmed rectangular bedding prisms along horizontal design-route projections."""
from decimal import Decimal, ROUND_HALF_UP
from fastapi import HTTPException
from . import store
from .compound_takeoff import op, text
from .engine import DomainError, precise, quantity_number
from .measurement_units import LENGTH, VERSION as UNIT_VERSION
from .planar_takeoff import linear
from .route_takeoff import calculate as route_quantity, validate as validate_route, snapshot

VERSION = 'route-rectangular-bedding-1.0'
FIELDS = ['width', 'thickness']
NOTICE = '仅计同一材料/同一层次、逐直段等宽等厚的矩形垫层：水平投影长×横向水平宽×竖向厚度。材料、施工做法及承载条件须另行核定；不按管径或槽底宽自动推算，不含管座包角、腋角、包封、转角交接扣重、压实/松方或损耗系数。变宽变厚、曲线或不同材料层须拆分建模。关联回填仍须独立核定同域包含和不重复范围。'


def source_component(p, cid):
    from .main import find
    source = find(p, 'components', cid)
    if source['kind'] != 'design_route' or source['state'] != 'approved':
        raise HTTPException(409, '管网垫层须选择当前已审定的设计坐标管线')
    validate_route(p, source, saved=True)
    return source


def route_data(source):
    r = route_quantity(source)
    if any(Decimal(s['horizontal_length']) == 0 for s in r['route_segments']):
        raise DomainError('来源含竖向零水平长管段，请按实际垫层范围拆分管线，不能使用三维管长代替水平长')
    return r


def candidates(p, cid):
    source = source_component(p, cid);r = route_data(source)
    return {'source_component_id':source['id'],'source_component_hash':store.digest(snapshot(source)),
            'source_name':source['name'],'source_revision':source['revision'],'multiplicity':source['multiplicity'],
            'route_points':r['route_points'],'route_segments':r['route_segments'],'notice':NOTICE}


@precise
def calculate(comp, source=None):
    source = source if source is not None else comp['source_geometry']
    if comp.get('parameters') != {} or comp.get('quantity_type') != 'volume' or comp.get('scope_confirmed') is not True or comp.get('length_basis') != 'horizontal':
        raise DomainError('垫层须为空顶层参数、体积类型，明确水平长度口径并确认同一材料层及逐段矩形范围')
    text(comp.get('name'), '垫层名称', 200);text(comp.get('material'), '本层材料与规格', 200)
    text(comp.get('measurement_basis'), '垫层计量范围与交接依据');text(comp.get('construction_basis'), '垫层施工做法及适用条件依据')
    if comp.get('source_component_id') != source['id'] or comp.get('source_component_hash') != store.digest(snapshot(source)):
        raise HTTPException(409, '来源管线已修订，请重新读取分段并核定全部垫层尺寸')
    if comp.get('multiplicity') is not None:raise DomainError('垫层重复数量继承来源，不能另行填写或再次乘数量')
    digits = comp.get('rounding_digits')
    if type(digits) is not int or not 0 <= digits <= 12:raise DomainError('垫层总量修约位数须明确为0至12的整数')
    r = route_data(source);sections = comp.get('bedding_sections')
    if not isinstance(sections, list) or len(sections) != len(r['route_segments']):
        raise DomainError('须为来源每个直段核定矩形垫层，不能遗漏；局部无垫层须拆分来源范围')
    by_index = {}
    for d in sections:
        if not isinstance(d, dict) or set(d) != {'segment_index','parameters','unit','evidence','measurement_basis'} or type(d['segment_index']) is not int:
            raise DomainError('每段须包含段序号、宽厚参数、原始单位、逐参数及范围证据和说明')
        i = d['segment_index']
        if not 1 <= i <= len(sections) or i in by_index:raise DomainError('垫层段号重复、过期或不存在')
        by_index[i] = d
    dimensions = dict(r['dimensions']);refs = dict(r['source_evidence']);conversions = list(r['unit_conversions'])
    for k in ['basis','material','construction','rounding']:
        refs['bedding_'+k] = text(comp.get(k+'_evidence_id'), '垫层原始依据', 200)
    total = Decimal(0);rows = [];nodes = []
    for j, seg in enumerate(r['route_segments']):
        d = by_index[seg['index']]
        if not isinstance(d['parameters'],dict) or set(d['parameters']) != set(FIELDS) or not isinstance(d['evidence'],dict) or set(d['evidence']) != set(FIELDS)|{'measurement_basis'}:
            raise DomainError('每段宽度、竖向厚度及范围说明须分别关联原始依据')
        text(d['measurement_basis'], '逐段垫层计量范围')
        values = {k:linear(d['parameters'][k],d['unit'],True) for k in FIELDS};ns = {}
        prefix = 'bedding.'+str(seg['index'])+'.'
        for k in FIELDS:
            key = prefix+k;dimensions[key] = str(d['parameters'][k]);ns[k] = op('multiply',{'dimension':key},{'constant':LENGTH[d['unit']]})
            conversions.append({'parameter':key,'original_value':dimensions[key],'source_unit':d['unit'],'factor':LENGTH[d['unit']],
                                'canonical_value':format(values[k],'f'),'canonical_unit':'m','rule_version':UNIT_VERSION})
        refs.update({prefix+k:text(eid,'垫层逐段原始证据',200) for k,eid in d['evidence'].items()})
        def coordinate(i, axis):
            return op('multiply',{'dimension':str(i+1)+'.'+axis},{'constant':LENGTH[r['route_points'][i]['unit']]})
        diffs = [op('subtract',coordinate(j+1,k),coordinate(j,k)) for k in ['x','y']]
        length_ast = op('sqrt',op('add',*[op('multiply',d,d) for d in diffs]))
        ast = op('multiply',ns['width'],ns['thickness'],length_ast);nodes.append(ast)
        area = values['width']*values['thickness'];volume = area*Decimal(seg['horizontal_length']);total += volume
        rows.append({**d,'from_name':seg['from_name'],'to_name':seg['to_name'],'normalized_parameters':{k:format(v,'f') for k,v in values.items()},
                     'cross_section_area':str(area),'horizontal_length':seg['horizontal_length'],'spatial_length':seg['length'],'single_volume':str(volume),'formula_ast':ast})
    count = Decimal(source['multiplicity']);gross = quantity_number(total*count)
    quantity = quantity_number(gross.quantize(Decimal(1).scaleb(-digits),rounding=ROUND_HALF_UP))
    return {'quantity':str(quantity),'gross':str(gross),'deduction':'0','unit':'m³','parameters':{},
            'formula':'Σ[横向水平宽 × 竖向厚度 × 未修约水平段长] × '+str(count)+'；垫层总量修约'+str(digits)+'位',
            'formula_ast':{'op':'round','args':[op('multiply',op('add',*nodes),{'dimension':'multiplicity'})],'digits':digits,'mode':'HALF_UP'},
            'dimensions':dimensions,'source_evidence':refs,'unit_conversions':conversions,'coordinate_basis':source['coordinate_basis'],
            'source_geometry':snapshot(source),'source_component_id':source['id'],'source_component_hash':comp['source_component_hash'],
            'bedding_sections':rows,'material':comp['material'],'construction_basis':comp['construction_basis'],
            'bedding_summary':{'source_name':source['name'],'source_revision':source['revision'],'segment_count':len(rows),'single_volume':str(total),
                               'multiplicity':str(count),'unrounded_quantity':str(gross),'rounding_digits':digits,'rounding_adjustment':str(quantity-gross),'length_basis':'horizontal'},
            'rounding_rule':'Decimal 80位；宽厚、水平段长和逐段体积不单独修约，累计乘来源重复数量后，仅总体积HALF_UP至'+str(digits)+'位小数',
            'rule_version':VERSION,'notice':NOTICE}


def validate(p, comp, saved=False):
    from .main import require_evidence, find
    from .task_execution import original_bytes
    source = source_component(p, comp.get('source_component_id'));result = calculate(comp, source)
    sources = {};checked = set()
    for key,eid in result['source_evidence'].items():
        ev = require_evidence(p,eid);doc = find(p,'documents',ev['document_id'])
        if ev['document_hash'] != doc['hash']:raise HTTPException(409,'垫层尺寸、材料或做法依据与原件校验值不一致')
        if doc['hash'] not in checked:original_bytes(doc);checked.add(doc['hash'])
        sources[key] = {'evidence':ev,'document_hash':doc['hash']}
    stamp = store.digest({'source_component_hash':comp['source_component_hash'],'sources':sources})
    if saved and (comp.get('source_stamp') != stamp or comp.get('evidence') != result['source_evidence'] or comp.get('preview') != result or comp.get('source_geometry') != snapshot(source)):
        raise HTTPException(409,'垫层来源、尺寸、材料或计算记录已变化，请修订后重新复核')
    return result,stamp
