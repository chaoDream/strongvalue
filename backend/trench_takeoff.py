"""Evidence-backed station sections along an approved design route."""
from decimal import Decimal, ROUND_HALF_UP
from fastapi import HTTPException
from . import store
from .compound_takeoff import op, text
from .engine import DomainError, precise, quantity_number
from .measurement_units import LENGTH, VERSION as UNIT_VERSION
from .planar_takeoff import linear
from .route_takeoff import calculate as route_quantity, validate as validate_route

VERSION = 'station-trench-1.0'
METHODS = {'average_end_area': '平均端面积法', 'linear_prismoidal': '宽深线性变化棱台法'}
FIELDS = ['bottom_width', 'top_width', 'depth']
SNAPSHOT_FIELDS = ['id','revision','name','kind','parameters','quantity_type','route_points','coordinate_basis',
                   'coordinate_evidence_id','measurement_basis','basis_evidence_id','multiplicity','multiplicity_evidence_id',
                   'rounding_digits','rounding_evidence_id']
NOTICE = '按已审管线的水平投影分段计量；挖深和断面须独立核定，不从管道高程推断地面或开挖底。平均端面积法是所选近似方法；棱台法仅适用于段内上下宽及挖深分别线性变化的梯形断面。不自动套用放坡、工作面、地质或施工参数，不扣除转角/相邻沟槽重叠，不推算回填或运输。'


def snapshot(source):
    return {k: source[k] for k in SNAPSHOT_FIELDS}


def source_component(p, cid):
    from .main import find
    source = find(p, 'components', cid)
    if source['kind'] != 'design_route' or source['state'] != 'approved':
        raise HTTPException(409, '变断面沟槽须选择当前已审定的设计坐标管线')
    validate_route(p, source, saved=True)
    return source


def route_data(source):
    r = route_quantity(source)
    if any(Decimal(s['horizontal_length']) == 0 for s in r['route_segments']):
        raise DomainError('来源含水平投影为零的竖向管段，请按实际沟槽范围另建管线，不能直接按空间管长计土方')
    return r


def candidates(p, cid):
    source = source_component(p, cid)
    r = route_data(source)
    return {'route_points': r['route_points'], 'route_segments': r['route_segments'], 'source_component_id': source['id'],
            'source_component_hash': store.digest(snapshot(source)), 'source_name': source['name'], 'source_revision': source['revision'],
            'multiplicity': source['multiplicity'], 'coordinate_basis': source['coordinate_basis'], 'notice': NOTICE}


@precise
def calculate(comp, source=None):
    source = source if source is not None else comp['source_geometry']
    if comp.get('parameters') != {} or comp.get('quantity_type') != 'volume' or (not isinstance(comp.get('method'),str) or comp['method'] not in METHODS):
        raise DomainError('变断面沟槽须明确选择断面法，顶层参数为空，结果为体积')
    text(comp.get('name'), '沟槽名称', 200)
    for k, label in [('measurement_basis','断面法适用条件与交接计量依据'),('geology_basis','地质与场地依据'),('construction_basis','开挖施工及工作面依据')]:
        text(comp.get(k), label)
    if comp.get('source_component_id') != source['id'] or comp.get('source_component_hash') != store.digest(snapshot(source)):
        raise HTTPException(409, '来源管线已修订，请重新读取站点并核定全部断面')
    r = route_data(source)
    refs = dict(r['source_evidence'])
    for k, name in [('basis','断面法依据'),('geology','地质场地依据'),('construction','施工工作面依据'),('rounding','体积修约依据')]:
        refs['trench_'+k] = text(comp.get(k+'_evidence_id'), name, 200)
    digits = comp.get('rounding_digits')
    if type(digits) is not int or not 0 <= digits <= 12:
        raise DomainError('体积总量修约位数须为0至12的整数')
    stations = comp.get('stations')
    if not isinstance(stations, list) or len(stations) != len(r['route_points']):
        raise DomainError('须为来源管线的每个转折点核定一个断面，不能缺项')
    by_index = {}
    for s in stations:
        if not isinstance(s, dict) or set(s) != {'point_index','parameters','unit','evidence','measurement_basis'} or type(s['point_index']) is not int:
            raise DomainError('每站须包含来源点序号、上下宽挖深、单位、逐参数证据及断面说明')
        i = s['point_index']
        if not 1 <= i <= len(stations) or i in by_index:
            raise DomainError('站点序号过期、重复或不存在')
        by_index[i] = s
    dimensions = dict(r['dimensions']); conversions = list(r['unit_conversions'])
    rows, section_nodes, normalized = [], [], []
    for p in r['route_points']:
        s = by_index[p['index']]
        if not isinstance(s['parameters'], dict) or set(s['parameters']) != set(FIELDS) or not isinstance(s['evidence'], dict) or set(s['evidence']) != set(FIELDS)|{'measurement_basis'}:
            raise DomainError('每站底宽、顶宽、挖深及断面说明均须分别关联证据')
        text(s['measurement_basis'], '站点断面说明')
        values = {k: linear(s['parameters'][k], s['unit'], k != 'depth') for k in FIELDS}
        if values['depth'] < 0 or values['top_width'] < values['bottom_width']:
            raise DomainError('挖深不得为负，槽顶宽不得小于槽底宽；特殊断面须另建专业模型')
        prefix = 'station.'+str(p['index'])+'.'; ns = {}
        for k in FIELDS:
            key = prefix+k; dimensions[key] = str(s['parameters'][k])
            ns[k] = op('multiply', {'dimension':key}, {'constant':LENGTH[s['unit']]})
            conversions.append({'parameter':key,'original_value':dimensions[key],'source_unit':s['unit'],'factor':LENGTH[s['unit']],
                                'canonical_value':format(values[k],'f'),'canonical_unit':'m','rule_version':UNIT_VERSION})
        refs.update({prefix+k: text(eid, '站点原始证据', 200) for k,eid in s['evidence'].items()})
        area = (values['bottom_width']+values['top_width'])/2*values['depth']
        section_nodes.append(op('multiply', op('divide', op('add',ns['bottom_width'],ns['top_width']),{'constant':'2'}),ns['depth']))
        normalized.append(values)
        rows.append({**s,'point_name':p['name'],'normalized_parameters':{k:format(v,'f') for k,v in values.items()},'area':str(area),
                     'formula_ast':section_nodes[-1]})
    segments, volume_nodes, total = [], [], Decimal(0)
    for j, seg in enumerate(r['route_segments']):
        start, end = normalized[j], normalized[j+1]
        a, b = Decimal(rows[j]['area']), Decimal(rows[j+1]['area'])
        mid = {k:(start[k]+end[k])/2 for k in FIELDS}
        mid_area = (mid['bottom_width']+mid['top_width'])/2*mid['depth']
        def width_sum(index):
            s = rows[index];prefix = 'station.'+str(s['point_index'])+'.'
            return op('add',*[op('multiply',{'dimension':prefix+k},{'constant':LENGTH[s['unit']]}) for k in ['bottom_width','top_width']])
        mid_width = op('divide',op('add',width_sum(j),width_sum(j+1)),{'constant':'4'})
        mid_depth = op('divide',op('add',*[op('multiply',{'dimension':'station.'+str(i+1)+'.depth'},{'constant':LENGTH[rows[i]['unit']]}) for i in [j,j+1]]),{'constant':'2'})
        mid_ast = op('multiply',mid_width,mid_depth)
        area_nodes = [section_nodes[j],section_nodes[j+1]]
        if comp['method'] == 'linear_prismoidal':
            mean = (a+4*mid_area+b)/6
            mean_ast = op('divide',op('add',area_nodes[0],op('multiply',{'constant':'4'},mid_ast),area_nodes[1]),{'constant':'6'})
        else:
            mean = (a+b)/2
            mean_ast = op('divide',op('add',*area_nodes),{'constant':'2'})
        def coord(index, axis):
            return op('multiply',{'dimension':str(index+1)+'.'+axis},{'constant':LENGTH[r['route_points'][index]['unit']]})
        differences = [op('subtract',coord(j+1,k),coord(j,k)) for k in ['x','y']]
        length_ast = op('sqrt',op('add',*[op('multiply',d,d) for d in differences]))
        length = Decimal(seg['horizontal_length']); volume = mean*length; total += volume
        ast = op('multiply',mean_ast,length_ast);volume_nodes.append(ast)
        segments.append({'index':j+1,'from_index':j+1,'to_index':j+2,'from_name':seg['from_name'],'to_name':seg['to_name'],
                         'horizontal_length':str(length),'spatial_length':seg['length'],'start_area':str(a),'end_area':str(b),
                         'mid_parameters':{k:str(v) for k,v in mid.items()},'mid_area':str(mid_area),'mean_area':str(mean),'volume':str(volume),'formula_ast':ast})
    count = Decimal(source['multiplicity']); gross = quantity_number(total*count)
    quantity = quantity_number(gross.quantize(Decimal(1).scaleb(-digits),rounding=ROUND_HALF_UP))
    return {'quantity':str(quantity),'gross':str(gross),'deduction':'0','unit':'m³','parameters':{},
            'formula':('Σ[(A起 + 4A中 + A终) / 6 × 水平长]' if comp['method']=='linear_prismoidal' else 'Σ[(A起 + A终) / 2 × 水平长]')+' × '+str(count)+'；总量修约'+str(digits)+'位',
            'formula_ast':{'op':'round','args':[op('multiply',op('add',*volume_nodes),{'dimension':'multiplicity'})],'digits':digits,'mode':'HALF_UP'},
            'dimensions':dimensions,'source_evidence':refs,'unit_conversions':conversions,'source_geometry':snapshot(source),
            'source_component_id':source['id'],'source_component_hash':comp['source_component_hash'],'coordinate_basis':source['coordinate_basis'],
            'trench_stations':rows,'trench_segments':segments,'geology_basis':comp['geology_basis'],'construction_basis':comp['construction_basis'],
            'trench_summary':{'method':comp['method'],'method_label':METHODS[comp['method']],'single_volume':str(total),'multiplicity':str(count),
                              'source_name':source['name'],'source_revision':source['revision'],'station_count':len(rows),'unrounded_quantity':str(gross),
                              'rounding_digits':digits,'rounding_adjustment':str(quantity-gross)},
            'rounding_rule':'Decimal 80位；断面和分段体积不单独修约，累计乘来源重复数量后，仅总量HALF_UP至'+str(digits)+'位小数',
            'rule_version':VERSION,'notice':NOTICE}


def validate(p, comp, saved=False):
    from .main import require_evidence, find
    from .task_execution import original_bytes
    source = source_component(p, comp.get('source_component_id'))
    result = calculate(comp, source);sources = {};checked = set()
    for key,eid in result['source_evidence'].items():
        ev = require_evidence(p,eid);doc = find(p,'documents',ev['document_id'])
        if ev['document_hash'] != doc['hash']:raise HTTPException(409,'沟槽依据与原件校验值不一致')
        if doc['hash'] not in checked:original_bytes(doc);checked.add(doc['hash'])
        sources[key] = {'evidence':ev,'document_hash':doc['hash']}
    stamp = store.digest({'source_component_hash':comp['source_component_hash'],'sources':sources})
    if saved and (comp.get('source_stamp') != stamp or comp.get('evidence') != result['source_evidence'] or comp.get('preview') != result or comp.get('source_geometry') != snapshot(source)):
        raise HTTPException(409,'沟槽来源、断面或计算内容已变化，请修订后重新复核')
    return result,stamp
