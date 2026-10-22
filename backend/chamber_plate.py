"""Horizontal constant-thickness circular chamber plates with a concentric through opening."""
from decimal import Decimal, ROUND_HALF_UP
from fastapi import HTTPException
from . import store
from .compound_takeoff import op, text, PI
from .engine import DomainError, precise, quantity_number
from .planar_takeoff import linear
from .measurement_units import LENGTH, VERSION as UNIT_VERSION
from .chamber_takeoff import calculate as chamber_quantity, validate as validate_chamber
from .volume_balance import snapshot

VERSION = 'circular-chamber-plate-1.0'
FIELDS = ['diameter','opening_diameter','thickness','bottom_z']
ROLES = {'base':'井底板','cover':'井盖板'}
NOTICE = '仅计水平、等厚圆形井底板或盖板，以及明确的同心圆形贯通孔；无孔须显式填零并有依据。实际板径、厚度、孔径及板底标高独立核定，不由井筒内外径或管线高程推断。逐井数量继承当前已审井筒，不再乘管线数量。不含井盖框、钢筋、收口、流槽、踏步、预埋件、非贯通凹槽、偏心/多孔或特殊板型；不同层及材料须另建构件。回填仅引用板净实体体积，仍须核定同域包含、标高及与井筒等扣减不重复范围，不自动进行空间判断。'


def source_component(p,cid):
    from .main import find
    source=find(p,'components',cid)
    if source['kind']!='circular_chamber' or source['state']!='approved':
        raise HTTPException(409,'井底盖板须选择当前已审定的圆形井筒构件')
    validate_chamber(p,source,saved=True)
    return source


def candidates(p,cid):
    source=source_component(p,cid);r=chamber_quantity(source)
    return {'source_component_id':source['id'],'source_component_hash':store.digest(snapshot(source)),
            'source_name':source['name'],'source_revision':source['revision'],'chamber_wells':r['chamber_wells'],'notice':NOTICE}


@precise
def calculate(comp,source=None):
    if 'opening_mode' in comp:
        from .plate_openings import calculate as calculate_openings
        return calculate_openings(comp,source)
    source=source if source is not None else comp['source_geometry'];role=comp.get('plate_role')
    if comp.get('parameters')!={} or comp.get('quantity_type')!='volume' or not isinstance(role,str) or role not in ROLES or comp.get('scope_confirmed') is not True:
        raise DomainError('井底盖板须为空顶层参数、体积类型，明确板用途并核定圆形板、同心贯通孔及独立标高范围')
    text(comp.get('name'),'井底盖板名称',200);text(comp.get('material'),'本层板材料规格',200)
    text(comp.get('measurement_basis'),'板及孔的计量范围');text(comp.get('construction_basis'),'板施工做法及标高基准')
    if comp.get('multiplicity') is not None:raise DomainError('板数量继承所选已审井筒，不能额外填写重复数量')
    if comp.get('source_component_id')!=source['id'] or comp.get('source_component_hash')!=store.digest(snapshot(source)):
        raise HTTPException(409,'井筒来源已修订，请重新读取井号及数量，并核定板孔尺寸与标高')
    digits=comp.get('rounding_digits')
    if type(digits) is not int or not 0<=digits<=12:raise DomainError('板总体积修约位数须明确为0至12的整数')
    source_rows=chamber_quantity(source)['chamber_wells'];selected=comp.get('plate_sections')
    if not isinstance(selected,list) or not 1<=len(selected)<=50:raise DomainError('须明确选择1至50项井号及板，不能将来源每个井段自动认定有板')
    refs={};dimensions={};conversions=[];rows=[];nodes=[];names=set();seen=set();gross=Decimal(0);deduction=Decimal(0);count_total=Decimal(0)
    for k in ['basis','material','construction','rounding']:refs['plate_'+k]=text(comp.get(k+'_evidence_id'),'井底盖板原始依据',200)
    refs.update({'source.'+k:v for k,v in source['evidence'].items()})
    for i,d in enumerate(selected,1):
        if not isinstance(d,dict) or set(d)!={'name','source_well_index','parameters','unit','evidence','measurement_basis'} or type(d['source_well_index']) is not int:
            raise DomainError('每板须包含名称、来源井号序号、板孔厚标高参数、单位、逐参数证据及说明')
        name=text(d['name'],'板名称',200);index=d['source_well_index']
        if name in names or index in seen or not 1<=index<=len(source_rows):raise DomainError('板名称或来源井号重复，或来源井号不存在；同井不同层请另建构件')
        names.add(name);seen.add(index)
        if not isinstance(d['parameters'],dict) or set(d['parameters'])!=set(FIELDS) or not isinstance(d['evidence'],dict) or set(d['evidence'])!=set(FIELDS)|{'measurement_basis'}:
            raise DomainError('实际板径、同心贯通孔径、厚度、独立板底标高及范围说明须分别关联原始依据')
        text(d['measurement_basis'],'本板及贯通孔范围说明')
        values={k:linear(d['parameters'][k],d['unit'],k in ('diameter','thickness')) for k in FIELDS}
        if values['opening_diameter']<0 or values['opening_diameter']>=values['diameter']:
            raise DomainError('贯通孔径须大于等于零且小于板径；无孔显式填零，不允许全扣或越界孔')
        well=source_rows[index-1];count=Decimal(well['multiplicity']);prefix='plate.'+str(i)+'.';ns={}
        for k in FIELDS:
            key=prefix+k;dimensions[key]=str(d['parameters'][k]);ns[k]=op('multiply',{'dimension':key},{'constant':LENGTH[d['unit']]})
            conversions.append({'parameter':key,'original_value':dimensions[key],'source_unit':d['unit'],'factor':LENGTH[d['unit']],
                                'canonical_value':format(values[k],'f'),'canonical_unit':'m','rule_version':UNIT_VERSION})
        dimensions[prefix+'multiplicity']=str(well['multiplicity']);refs.update({prefix+k:text(eid,'逐板原始依据',200) for k,eid in d['evidence'].items()})
        area=Decimal(PI)/4*values['diameter']*values['diameter'];hole_area=Decimal(PI)/4*values['opening_diameter']*values['opening_diameter']
        outer=area*values['thickness'];hole=hole_area*values['thickness'];net=outer-hole
        ast=op('multiply',op('subtract',op('multiply',op('divide',{'constant':PI},{'constant':'4'}),ns['diameter'],ns['diameter'],ns['thickness']),
                            op('multiply',op('divide',{'constant':PI},{'constant':'4'}),ns['opening_diameter'],ns['opening_diameter'],ns['thickness'])),{'dimension':prefix+'multiplicity'})
        nodes.append(ast);gross+=outer*count;deduction+=hole*count;count_total+=count
        rows.append({**d,'index':i,'source_well_name':well['name'],'point_index':well['point_index'],'location':well['location'],
                     'source_multiplicity':well['multiplicity'],'normalized_parameters':{k:format(v,'f') for k,v in values.items()},'top_z':str(values['bottom_z']+values['thickness']),
                     'single_gross_volume':str(outer),'single_opening_volume':str(hole),'single_net_volume':str(net),'volume':str(net*count),'formula_ast':ast})
    quantity_number(gross);quantity_number(deduction);net=quantity_number(gross-deduction)
    quantity=quantity_number(net.quantize(Decimal(1).scaleb(-digits),rounding=ROUND_HALF_UP))
    return {'quantity':str(quantity),'gross':str(gross),'deduction':str(deduction),'unit':'m³','parameters':{},'plate_role':role,'material':comp['material'],'construction_basis':comp['construction_basis'],
            'formula':'Σ[(π / 4 × 实际板径² × 厚度 − π / 4 × 同心贯通孔径² × 厚度) × 已审井数]；净量修约'+str(digits)+'位',
            'formula_ast':{'op':'round','args':[op('add',*nodes)],'digits':digits,'mode':'HALF_UP'},'dimensions':dimensions,'source_evidence':refs,'unit_conversions':conversions,
            'source_geometry':snapshot(source),'source_component_id':source['id'],'source_component_hash':comp['source_component_hash'],'plate_sections':rows,
            'plate_summary':{'role_label':ROLES[role],'source_name':source['name'],'source_revision':source['revision'],'well_item_count':len(rows),'plate_count':str(count_total),
                             'unrounded_quantity':str(net),'rounding_digits':digits,'rounding_adjustment':str(quantity-net),'pi_constant':PI},
            'rounding_rule':'Decimal 80位及记录的固定π常量；外板及孔体积不单独修约，乘已审井数累计后，仅净实体总体积HALF_UP至'+str(digits)+'位小数',
            'rule_version':VERSION,'notice':NOTICE}


def validate(p,comp,saved=False):
    from .main import require_evidence,find
    from .task_execution import original_bytes
    source=source_component(p,comp.get('source_component_id'));result=calculate(comp,source);proofs={};checked=set()
    for key,eid in result['source_evidence'].items():
        ev=require_evidence(p,eid);doc=find(p,'documents',ev['document_id'])
        if ev['document_hash']!=doc['hash']:raise HTTPException(409,'板孔尺寸、标高或计量依据与原件不一致')
        if doc['hash'] not in checked:original_bytes(doc);checked.add(doc['hash'])
        proofs[key]={'evidence':ev,'document_hash':doc['hash']}
    stamp=store.digest({'source_component_hash':comp['source_component_hash'],'sources':proofs})
    if saved and (comp.get('source_stamp')!=stamp or comp.get('evidence')!=result['source_evidence'] or comp.get('preview')!=result or comp.get('source_geometry')!=snapshot(source)):
        raise HTTPException(409,'井底盖板来源、孔径、尺寸或计算记录已变化，请修订后重新复核')
    return result,stamp
