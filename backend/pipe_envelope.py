"""Evidence-backed outer envelopes along confirmed straight design segments."""
from decimal import Decimal, ROUND_HALF_UP
from fastapi import HTTPException
from . import store
from .compound_takeoff import op, text, PI
from .engine import DomainError, precise, quantity_number
from .measurement_units import LENGTH, VERSION as UNIT_VERSION
from .planar_takeoff import linear
from .route_takeoff import calculate as route_quantity, validate as validate_route, snapshot

VERSION = 'pipe-outer-envelope-1.0'
NOTICE = '仅计算明确实际外径的圆管外包占位体积，包含管内空腔，不是管壁材料体积。按来源未修约三维直段长度逐段累计并继承重复数量；不以DN推定外径，不自动计弯头弧长、管件、承插口、交接重叠或包封层。用于回填扣减前仍须核定同域包含及不重复范围。'


def source_component(p,cid):
    from .main import find
    source=find(p,'components',cid)
    if source['kind']!='design_route' or source['state']!='approved':
        raise HTTPException(409,'管道外包体积须选择当前已审定的设计坐标管线')
    validate_route(p,source,saved=True)
    return source


def candidates(p,cid):
    source=source_component(p,cid);r=route_quantity(source)
    return {'source_component_id':source['id'],'source_component_hash':store.digest(snapshot(source)),
            'source_name':source['name'],'source_revision':source['revision'],'multiplicity':source['multiplicity'],
            'route_points':r['route_points'],'route_segments':r['route_segments'],'notice':NOTICE}


@precise
def calculate(comp,source=None):
    source=source if source is not None else comp['source_geometry']
    if comp.get('parameters')!={} or comp.get('quantity_type')!='volume' or comp.get('outside_confirmed') is not True:
        raise DomainError('管道外包须为空顶层参数、体积类型，并确认录入实际外径及外包占位口径')
    text(comp.get('name'),'管道占位名称',200);text(comp.get('measurement_basis'),'外包计量范围及长度口径依据')
    if comp.get('source_component_id')!=source['id'] or comp.get('source_component_hash')!=store.digest(snapshot(source)):
        raise HTTPException(409,'来源管线已修订，请重新读取管段并核定实际外径')
    if comp.get('multiplicity') is not None:raise DomainError('管道外包重复数量继承来源，不得另行填写或再次乘数量')
    digits=comp.get('rounding_digits')
    if type(digits) is not int or not 0<=digits<=12:raise DomainError('外包总体积修约位数须明确为0至12的整数')
    r=route_quantity(source);sections=comp.get('pipe_sections')
    if not isinstance(sections,list) or len(sections)!=len(r['route_segments']):
        raise DomainError('须为来源每个直管段核定实际外径及计量依据，不能遗漏')
    by_index={}
    for d in sections:
        if not isinstance(d,dict) or set(d)!={'segment_index','outer_diameter','diameter_unit','evidence_id','measurement_basis','basis_evidence_id'} or type(d['segment_index']) is not int:
            raise DomainError('每段须包含管段序号、实际外径、原始单位、外径证据、计量说明及依据')
        i=d['segment_index']
        if not 1<=i<=len(sections) or i in by_index:raise DomainError('管段序号重复、过期或不存在')
        by_index[i]=d
    dimensions=dict(r['dimensions']);refs=dict(r['source_evidence']);conversions=list(r['unit_conversions'])
    refs['pipe_basis']=text(comp.get('basis_evidence_id'),'管道外包总体依据',200)
    refs['pipe_rounding']=text(comp.get('rounding_evidence_id'),'外包体积修约依据',200)
    total=Decimal(0);rows=[];nodes=[]
    for s in r['route_segments']:
        d=by_index[s['index']];text(d['measurement_basis'],'逐段外包范围说明')
        diameter=linear(d['outer_diameter'],d['diameter_unit'],True)
        key='pipe.'+str(s['index'])+'.outer_diameter'
        dimensions[key]=str(d['outer_diameter']);refs[key]=text(d['evidence_id'],'实际外径原始证据',200)
        refs['pipe.'+str(s['index'])+'.basis']=text(d['basis_evidence_id'],'逐段计量原始依据',200)
        dn=op('multiply',{'dimension':key},{'constant':LENGTH[d['diameter_unit']]})
        area_ast=op('multiply',op('divide',{'constant':PI},{'constant':'4'}),dn,dn)
        ast=op('multiply',area_ast,s['formula_ast'])
        area=Decimal(PI)/4*diameter*diameter;volume=area*Decimal(s['length']);total+=volume;nodes.append(ast)
        conversions.append({'parameter':key,'original_value':str(d['outer_diameter']),'source_unit':d['diameter_unit'],'factor':LENGTH[d['diameter_unit']],
                            'canonical_value':format(diameter,'f'),'canonical_unit':'m','rule_version':UNIT_VERSION})
        rows.append({**d,'from_name':s['from_name'],'to_name':s['to_name'],'normalized_outer_diameter':format(diameter,'f'),
                     'cross_section_area':str(area),'spatial_length':s['length'],'horizontal_length':s['horizontal_length'],
                     'single_volume':str(volume),'formula_ast':ast})
    count=Decimal(source['multiplicity']);gross=quantity_number(total*count)
    quantity=quantity_number(gross.quantize(Decimal(1).scaleb(-digits),rounding=ROUND_HALF_UP))
    return {'quantity':str(quantity),'gross':str(gross),'deduction':'0','unit':'m³','parameters':{},
            'formula':'Σ[π / 4 × 实际外径² × 未修约三维段长] × '+str(count)+'；总体积修约'+str(digits)+'位',
            'formula_ast':{'op':'round','args':[op('multiply',op('add',*nodes),{'dimension':'multiplicity'})],'digits':digits,'mode':'HALF_UP'},
            'dimensions':dimensions,'source_evidence':refs,'unit_conversions':conversions,'coordinate_basis':source['coordinate_basis'],
            'source_geometry':snapshot(source),'source_component_id':source['id'],'source_component_hash':comp['source_component_hash'],'pipe_sections':rows,
            'pipe_summary':{'source_name':source['name'],'source_revision':source['revision'],'segment_count':len(rows),'single_volume':str(total),
                            'multiplicity':str(count),'unrounded_quantity':str(gross),'rounding_digits':digits,'rounding_adjustment':str(quantity-gross),'pi_constant':PI},
            'rounding_rule':'Decimal 80位；π使用记录的固定十进制常量；段长、截面积及分段体积不单独修约，累计乘来源重复数量后，仅总体积HALF_UP至'+str(digits)+'位小数',
            'rule_version':VERSION,'notice':NOTICE}


def validate(p,comp,saved=False):
    from .main import require_evidence,find
    from .task_execution import original_bytes
    source=source_component(p,comp.get('source_component_id'));result=calculate(comp,source)
    sources={};checked=set()
    for key,eid in result['source_evidence'].items():
        ev=require_evidence(p,eid);doc=find(p,'documents',ev['document_id'])
        if ev['document_hash']!=doc['hash']:raise HTTPException(409,'管道实际外径或计量依据与原件校验值不一致')
        if doc['hash'] not in checked:original_bytes(doc);checked.add(doc['hash'])
        sources[key]={'evidence':ev,'document_hash':doc['hash']}
    stamp=store.digest({'source_component_hash':comp['source_component_hash'],'sources':sources})
    if saved and (comp.get('source_stamp')!=stamp or comp.get('evidence')!=result['source_evidence'] or comp.get('preview')!=result or comp.get('source_geometry')!=snapshot(source)):
        raise HTTPException(409,'管道外包来源、外径或计算记录已变化，请修订后重新复核')
    return result,stamp
