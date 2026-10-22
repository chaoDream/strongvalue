"""Evidence-backed vertical circular chamber barrels; route points are location references only."""
from decimal import Decimal, ROUND_HALF_UP
from fastapi import HTTPException
from . import store
from .compound_takeoff import op, text, PI
from .engine import DomainError, precise, quantity_number, number
from .measurement_units import LENGTH, VERSION as UNIT_VERSION
from .planar_takeoff import linear
from .route_takeoff import calculate as route_quantity, validate as validate_route, snapshot

VERSION = 'circular-chamber-barrel-1.0'
FIELDS = ['outer_diameter','inner_diameter','height']
PURPOSES = {'wall_material':'井筒材料体积','outer_envelope':'井筒外包占位体积'}
NOTICE = '仅计竖直等内外径圆筒井段，不代表整座检查井工程量。关联站点只作定位索引，不从管线高程推断井底、井深或地面；井数逐项有据填写，不继承管线重复数量。不含井底垫层、底板、盖板、井盖、收口、流槽、踏步、洞口扣减及施工附加量。不同材料/井段须分别编制；不同构件间不自动扣重。材料体积不等于回填占位；回填只可引用已审外包体积，含井内空腔，且仍须另审同域包含及不重复范围。'


def source_component(p, cid):
    from .main import find
    source = find(p, 'components', cid)
    if source['kind'] != 'design_route' or source['state'] != 'approved':
        raise HTTPException(409,'检查井井筒须关联当前已审定的设计坐标管线')
    validate_route(p,source,saved=True)
    return source


def candidates(p,cid):
    source=source_component(p,cid);r=route_quantity(source)
    return {'source_component_id':source['id'],'source_component_hash':store.digest(snapshot(source)),
            'source_name':source['name'],'source_revision':source['revision'],'route_points':r['route_points'],
            'route_multiplicity':source['multiplicity'],'notice':NOTICE}


@precise
def calculate(comp,source=None):
    source=source if source is not None else comp['source_geometry']
    purpose=comp.get('purpose')
    if comp.get('parameters')!={} or comp.get('quantity_type')!='volume' or not isinstance(purpose,str) or purpose not in PURPOSES or comp.get('locations_confirmed') is not True:
        raise DomainError('井筒须为空顶层参数、体积类型，明确材料/外包用途，并确认站点定位、井数及计量范围')
    text(comp.get('name'),'井筒构件名称',200);text(comp.get('material'),'同一材料及规格',200)
    text(comp.get('measurement_basis'),'井筒用途、定位和计量范围');text(comp.get('construction_basis'),'井筒做法及适用条件')
    if comp.get('multiplicity') is not None:raise DomainError('井筒数量须逐井填写，不能填写整组重复数量或继承管线数量')
    if comp.get('source_component_id')!=source['id'] or comp.get('source_component_hash')!=store.digest(snapshot(source)):
        raise HTTPException(409,'来源管线已修订，请重新读取站点并核定井筒定位及尺寸')
    digits=comp.get('rounding_digits')
    if type(digits) is not int or not 0<=digits<=12:raise DomainError('井筒总体积修约位数须明确为0至12的整数')
    r=route_quantity(source);wells=comp.get('chamber_wells')
    if not isinstance(wells,list) or not 1<=len(wells)<=50:raise DomainError('井筒清单须有1至50项，不能自动将全部管线点认作检查井')
    refs={};dimensions={};conversions=[];rows=[];nodes=[];names=set();points=set();total=Decimal(0);wall_total=Decimal(0);outer_total=Decimal(0);well_count=Decimal(0)
    for k in ['basis','material','construction','rounding']:
        refs['chamber_'+k]=text(comp.get(k+'_evidence_id'),'井筒原始依据',200)
    for i,w in enumerate(wells,1):
        if not isinstance(w,dict) or set(w)!={'name','point_index','parameters','unit','multiplicity','evidence','measurement_basis'} or type(w['point_index']) is not int:
            raise DomainError('逐井须明确名称、关联点、内外径高度、原始单位、数量、逐参数证据及范围说明')
        name=text(w['name'],'井号及井段名称',200);point=w['point_index']
        if name in names or point in points or not 1<=point<=len(r['route_points']):raise DomainError('井号或关联站点重复，或站点不存在；同站不同井段须分构件编制')
        names.add(name);points.add(point)
        if not isinstance(w['parameters'],dict) or set(w['parameters'])!=set(FIELDS) or not isinstance(w['evidence'],dict) or set(w['evidence'])!=set(FIELDS)|{'multiplicity','measurement_basis'}:
            raise DomainError('每井实际内外径、高度、数量及定位说明须分别关联原始依据')
        text(w['measurement_basis'],'逐井定位及计量范围依据')
        count=number(w['multiplicity'],True)
        if count!=count.to_integral_value() or count>1000000:raise DomainError('逐井数量须为1至1000000的整数并提供明确的重复范围依据')
        v={k:linear(w['parameters'][k],w['unit'],True) for k in FIELDS}
        if v['inner_diameter']>=v['outer_diameter']:raise DomainError('实际内径须小于外径且大于零；实心井基或其他形状须另建适用构件')
        prefix='chamber.'+str(i)+'.';ns={}
        for k in FIELDS:
            key=prefix+k;dimensions[key]=str(w['parameters'][k]);ns[k]=op('multiply',{'dimension':key},{'constant':LENGTH[w['unit']]})
            conversions.append({'parameter':key,'original_value':dimensions[key],'source_unit':w['unit'],'factor':LENGTH[w['unit']],
                                'canonical_value':format(v[k],'f'),'canonical_unit':'m','rule_version':UNIT_VERSION})
        dimensions[prefix+'multiplicity']=str(w['multiplicity'])
        refs.update({prefix+k:text(eid,'逐井原始依据',200) for k,eid in w['evidence'].items()})
        # Only selected location points are relied on. The complete route is independently validated at every gate.
        location=r['route_points'][point-1]
        refs.update({prefix+'location.'+k:eid for k,eid in location['evidence'].items()})
        refs['chamber_coordinate']=source['coordinate_evidence_id']
        outer=Decimal(PI)/4*v['outer_diameter']*v['outer_diameter']*v['height']
        wall=Decimal(PI)/4*(v['outer_diameter']*v['outer_diameter']-v['inner_diameter']*v['inner_diameter'])*v['height']
        square=op('multiply',ns['outer_diameter'],ns['outer_diameter'])
        if purpose=='wall_material':square=op('subtract',square,op('multiply',ns['inner_diameter'],ns['inner_diameter']))
        single=wall if purpose=='wall_material' else outer;value=single*count
        ast=op('multiply',op('divide',{'constant':PI},{'constant':'4'}),square,ns['height'],{'dimension':prefix+'multiplicity'});nodes.append(ast)
        total+=value;wall_total+=wall*count;outer_total+=outer*count;well_count+=count
        rows.append({**w,'index':i,'location':location,'normalized_parameters':{k:format(value,'f') for k,value in v.items()},
                     'single_wall_volume':str(wall),'single_outer_volume':str(outer),'single_volume':str(single),'volume':str(value),'formula_ast':ast})
    gross=quantity_number(total);quantity=quantity_number(gross.quantize(Decimal(1).scaleb(-digits),rounding=ROUND_HALF_UP))
    return {'quantity':str(quantity),'gross':str(gross),'deduction':'0','unit':'m³','parameters':{},'purpose':purpose,'material':comp['material'],'construction_basis':comp['construction_basis'],
            'formula':('Σ[π / 4 × (实际外径² − 实际内径²) × 井筒高度 × 核定井数]' if purpose=='wall_material' else 'Σ[π / 4 × 实际外径² × 井筒高度 × 核定井数]')+'；总量修约'+str(digits)+'位',
            'formula_ast':{'op':'round','args':[op('add',*nodes)],'digits':digits,'mode':'HALF_UP'},'dimensions':dimensions,'source_evidence':refs,'unit_conversions':conversions,
            'source_geometry':snapshot(source),'source_component_id':source['id'],'source_component_hash':comp['source_component_hash'],'coordinate_basis':source['coordinate_basis'],
            'chamber_wells':rows,'chamber_summary':{'purpose_label':PURPOSES[purpose],'source_name':source['name'],'source_revision':source['revision'],'location_count':len(rows),'well_count':str(well_count),
                                                 'route_multiplicity_not_inherited':source['multiplicity'],'wall_volume':str(wall_total),'outer_volume':str(outer_total),'unrounded_quantity':str(gross),
                                                 'rounding_digits':digits,'rounding_adjustment':str(quantity-gross),'pi_constant':PI},
            'rounding_rule':'Decimal 80位及记录的固定π常量；逐井不单独修约，仅所选用途总体积HALF_UP至'+str(digits)+'位小数；核定井数与管线重复数量独立',
            'rule_version':VERSION,'notice':NOTICE}


def validate(p,comp,saved=False):
    from .main import require_evidence,find
    from .task_execution import original_bytes
    source=source_component(p,comp.get('source_component_id'));result=calculate(comp,source);sources={};checked=set()
    for key,eid in result['source_evidence'].items():
        ev=require_evidence(p,eid);doc=find(p,'documents',ev['document_id'])
        if ev['document_hash']!=doc['hash']:raise HTTPException(409,'井筒定位、尺寸或计量依据与原件不一致')
        if doc['hash'] not in checked:original_bytes(doc);checked.add(doc['hash'])
        sources[key]={'evidence':ev,'document_hash':doc['hash']}
    stamp=store.digest({'source_component_hash':comp['source_component_hash'],'sources':sources})
    if saved and (comp.get('source_stamp')!=stamp or comp.get('evidence')!=result['source_evidence'] or comp.get('preview')!=result or comp.get('source_geometry')!=snapshot(source)):
        raise HTTPException(409,'井筒来源、定位、尺寸或计算记录已变化，请修订后重新复核')
    return result,stamp
