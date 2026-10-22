"""Exact eligibility checks for contained, non-overlapping circular through holes."""
from decimal import Decimal, ROUND_HALF_UP
from fastapi import HTTPException
from . import store
from .compound_takeoff import op, text, PI
from .engine import DomainError, precise, quantity_number
from .planar_takeoff import linear
from .measurement_units import LENGTH, VERSION as UNIT_VERSION
from .chamber_takeoff import calculate as chamber_quantity
from .chamber_plate import ROLES
from .volume_balance import snapshot

VERSION = 'circular-plate-offset-openings-1.0'
FIELDS = ['diameter','thickness','bottom_z']
HOLE_FIELDS = ['x','y','diameter']
NOTICE = '仅计水平等厚圆板及完全位于板内、互不重叠的圆形贯通孔。每板局部原点为板中心，XY在板平面内且须明确方向及依据；偏心位置不由井筒或示意图推断。孔与孔或板边界相切允许，正面积重叠、孔相互包含或越界均阻断，不将逐孔面积直接当作重叠孔并集。无孔时明确空孔表并核定依据。尺寸、标高及层次独立核定，数量继承已审井号一次；不代表结构安全、最小净距或整井专业验收。异形孔、凹槽及板间/井筒间空间扣重另核；回填仍须确认同域包含与不重复范围。'


@precise
def calculate(comp,source=None):
    source=source if source is not None else comp['source_geometry'];role=comp.get('plate_role')
    if comp.get('opening_mode')!='offset_circles' or comp.get('parameters')!={} or comp.get('quantity_type')!='volume' or not isinstance(role,str) or role not in ROLES or comp.get('scope_confirmed') is not True:
        raise DomainError('孔位井板须明确offset_circles模式、空顶层参数、体积、板用途及已核定范围')
    for k,label in [('name','井板名称'),('material','板材料规格'),('measurement_basis','板孔计量范围'),('construction_basis','板做法与标高基准')]:text(comp.get(k),label,200 if k in ('name','material') else 2000)
    if comp.get('multiplicity') is not None:raise DomainError('板数量须继承已审井数，不能额外乘数量')
    if comp.get('source_component_id')!=source['id'] or comp.get('source_component_hash')!=store.digest(snapshot(source)):
        raise HTTPException(409,'井筒来源已修订，请重新读取井号数量并核定板及逐孔位置')
    digits=comp.get('rounding_digits')
    if type(digits) is not int or not 0<=digits<=12:raise DomainError('净板总量修约位数须为0至12整数')
    selected=comp.get('plate_sections');source_rows=chamber_quantity(source)['chamber_wells']
    if not isinstance(selected,list) or not 1<=len(selected)<=50:raise DomainError('须明确选择1至50项井板')
    dimensions={};refs={};conversions=[];rows=[];nodes=[];seen=set();names=set();gross=Decimal(0);deduction=Decimal(0);count_total=Decimal(0);hole_count=0
    for k in ['basis','material','construction','rounding']:refs['plate_'+k]=text(comp.get(k+'_evidence_id'),'井板总体原始依据',200)
    refs.update({'source.'+k:v for k,v in source['evidence'].items()})
    def convert(params,unit,fields,positive,prefix):
        values={};ns={}
        for k in fields:
            values[k]=linear(params[k],unit,k in positive);key=prefix+k;dimensions[key]=str(params[k]);ns[k]=op('multiply',{'dimension':key},{'constant':LENGTH[unit]})
            conversions.append({'parameter':key,'original_value':dimensions[key],'source_unit':unit,'factor':LENGTH[unit],'canonical_value':format(values[k],'f'),'canonical_unit':'m','rule_version':UNIT_VERSION})
        return values,ns
    def square(n):return op('multiply',n,n)
    def volume(d,h):return op('multiply',op('divide',{'constant':PI},{'constant':'4'}),d,d,h)
    for i,d in enumerate(selected,1):
        if not isinstance(d,dict) or set(d)!={'name','source_well_index','parameters','unit','evidence','measurement_basis','coordinate_basis','openings'} or type(d['source_well_index']) is not int:
            raise DomainError('孔位井板须明确板名、来源井号、板径厚度标高、单位、原件、范围、局部坐标及孔表')
        name=text(d['name'],'板名称',200);index=d['source_well_index']
        if name in names or index in seen or not 1<=index<=len(source_rows):raise DomainError('板名或来源井号重复，或井号不存在')
        names.add(name);seen.add(index)
        if not isinstance(d['parameters'],dict) or set(d['parameters'])!=set(FIELDS) or not isinstance(d['evidence'],dict) or set(d['evidence'])!=set(FIELDS)|{'measurement_basis','coordinate_basis'}:
            raise DomainError('板径厚度标高、计量范围及板中心局部坐标依据均须完整')
        text(d['measurement_basis'],'板孔或无孔计量说明');text(d['coordinate_basis'],'板中心原点及局部XY方向')
        prefix='plate.'+str(i)+'.';v,ns=convert(d['parameters'],d['unit'],FIELDS,{'diameter','thickness'},prefix)
        refs.update({prefix+k:text(eid,'逐板原始依据',200) for k,eid in d['evidence'].items()})
        holes=d['openings']
        if not isinstance(holes,list) or len(holes)>20:raise DomainError('每板孔表须为0至20项；无孔明确空表并说明依据')
        hole_names=set();hole_rows=[];hole_nodes=[];checks=[];hole_total=Decimal(0)
        radius=v['diameter']/2
        for j,h in enumerate(holes,1):
            if not isinstance(h,dict) or set(h)!={'name','parameters','unit','evidence','measurement_basis'} or not isinstance(h['parameters'],dict) or set(h['parameters'])!=set(HOLE_FIELDS) or not isinstance(h['evidence'],dict) or set(h['evidence'])!=set(HOLE_FIELDS)|{'measurement_basis'}:
                raise DomainError('每孔须明确名称、中心XY和直径、原始单位、逐参数原件及贯通孔说明')
            hn=text(h['name'],'孔名称',200)
            if hn in hole_names:raise DomainError('同一板的孔名称不能重复')
            hole_names.add(hn);text(h['measurement_basis'],'本孔贯通及定位说明')
            hp=prefix+'hole.'+str(j)+'.';hv,hns=convert(h['parameters'],h['unit'],HOLE_FIELDS,{'diameter'},hp)
            refs.update({hp+k:text(eid,'逐孔原始依据',200) for k,eid in h['evidence'].items()})
            r=hv['diameter']/2;center=hv['x']*hv['x']+hv['y']*hv['y'];limit=(radius-r)*(radius-r)
            if r>=radius or center>limit:raise DomainError('贯通孔'+hn+'越出圆板或孔径覆盖整板，请核对局部孔位及计量边界')
            margin_ast=op('subtract',square(op('divide',op('subtract',ns['diameter'],hns['diameter']),{'constant':'2'})),op('add',square(hns['x']),square(hns['y'])))
            for prev in hole_rows:
                pv=prev['normalized_parameters'];pr=Decimal(pv['diameter'])/2
                dist=(hv['x']-Decimal(pv['x']))**2+(hv['y']-Decimal(pv['y']))**2;threshold=(r+pr)**2
                if dist<threshold:raise DomainError('贯通孔'+prev['name']+'与'+hn+'重叠或相互包含，须核定并集模型，不能重复扣减')
                pn=prev['_nodes'];dx=op('subtract',hns['x'],pn['x']);dy=op('subtract',hns['y'],pn['y'])
                check_ast=op('subtract',op('add',square(dx),square(dy)),square(op('divide',op('add',hns['diameter'],pn['diameter']),{'constant':'2'})))
                checks.append({'left':prev['name'],'right':h['name'],'center_distance_squared':str(dist),'radius_sum_squared':str(threshold),'margin':str(dist-threshold),
                               'state':'tangent' if dist==threshold else 'disjoint','formula_ast':check_ast})
            amount=Decimal(PI)/4*hv['diameter']*hv['diameter']*v['thickness'];hole_total+=amount
            ast=volume(hns['diameter'],ns['thickness']);hole_nodes.append(ast)
            hole_rows.append({**h,'index':j,'normalized_parameters':{k:format(x,'f') for k,x in hv.items()},'single_volume':str(amount),'formula_ast':ast,'_nodes':hns,
                              'containment':{'center_distance_squared':str(center),'limit_distance_squared':str(limit),'margin':str(limit-center),
                                             'state':'tangent' if center==limit else 'inside','formula_ast':margin_ast}})
        for h in hole_rows:h.pop('_nodes')
        well=source_rows[index-1];count=Decimal(well['multiplicity']);dimensions[prefix+'multiplicity']=str(well['multiplicity'])
        outer=Decimal(PI)/4*v['diameter']*v['diameter']*v['thickness'];net=outer-hole_total
        if net<=0:raise DomainError('开孔后净板体积须大于零，请另建适用模型')
        ast=op('multiply',op('subtract',volume(ns['diameter'],ns['thickness']),op('add',*hole_nodes) if hole_nodes else {'constant':'0'}),{'dimension':prefix+'multiplicity'})
        nodes.append(ast);gross+=outer*count;deduction+=hole_total*count;count_total+=count;hole_count+=len(holes)
        rows.append({**d,'index':i,'source_well_name':well['name'],'point_index':well['point_index'],'location':well['location'],'source_multiplicity':well['multiplicity'],
                     'normalized_parameters':{k:format(x,'f') for k,x in v.items()},'top_z':str(v['bottom_z']+v['thickness']),'single_gross_volume':str(outer),
                     'single_opening_volume':str(hole_total),'single_net_volume':str(net),'volume':str(net*count),'formula_ast':ast,'openings':hole_rows,'opening_checks':checks})
    quantity_number(gross);quantity_number(deduction);net=quantity_number(gross-deduction);quantity=quantity_number(net.quantize(Decimal(1).scaleb(-digits),rounding=ROUND_HALF_UP))
    return {'quantity':str(quantity),'gross':str(gross),'deduction':str(deduction),'unit':'m³','parameters':{},'plate_role':role,'opening_mode':'offset_circles','material':comp['material'],'construction_basis':comp['construction_basis'],
            'formula':'Σ[(π / 4 × 实际板径² × 厚度 − Σ互不重叠贯通圆孔体积) × 已审井数]；净量修约'+str(digits)+'位',
            'formula_ast':{'op':'round','args':[op('add',*nodes)],'digits':digits,'mode':'HALF_UP'},'dimensions':dimensions,'source_evidence':refs,'unit_conversions':conversions,
            'source_geometry':snapshot(source),'source_component_id':source['id'],'source_component_hash':comp['source_component_hash'],'plate_sections':rows,
            'plate_summary':{'role_label':ROLES[role],'source_name':source['name'],'source_revision':source['revision'],'well_item_count':len(rows),'plate_count':str(count_total),
                             'opening_mode':'offset_circles','hole_count':hole_count,'unrounded_quantity':str(net),'rounding_digits':digits,'rounding_adjustment':str(quantity-net),'pi_constant':PI},
            'rounding_rule':'Decimal 80位；孔包含/分离以平方距离精确比较；使用记录的固定π常量，逐孔不修约，累计继承井数后仅净量HALF_UP至'+str(digits)+'位小数',
            'rule_version':VERSION,'notice':NOTICE}
