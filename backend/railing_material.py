"""Evidence-backed linear-stock railing material schedule; no inferred posts."""
from decimal import Decimal,ROUND_HALF_UP
from fastapi import HTTPException
from . import store
from .compound_takeoff import op,text
from .engine import DomainError,precise,number,quantity_number
from .measurement_units import LENGTH,WEIGHT_PER_LENGTH,VERSION as UNIT_VERSION
from .planar_takeoff import linear
from .railing_takeoff import calculate as railing_quantity,validate as validate_railing
from .surface_takeoff import check_sources

VERSION='railing-material-mass-1.0'
MODES={'continuous':'沿全范围连续构件','cut_pieces':'独立定长构件'}
NOTICE='只计算有据填写的等截面线材质量。连续构件按已审栏杆单组未修约计入长度乘全范围道数；定长构件按独立核定的每组件数及每件下料长度计算，不从栏杆间距自动推算立柱。每米质量须有原件，不以密度或含钢率猜测。坡段连续材料须采用三维长度；坐标线水平投影来源只可用于独立定长件。不同局部做法应拆分栏杆来源，不能让某道材料默认适用于未确认范围。不自动增加损耗、接头、搭接、立柱端部、基础、网片或板材质量，不把材料表质量替代栏杆安装清单长度。'
ROUNDING='Decimal 80位；采用来源单组未修约范围长度，各道/件长度和分项质量不修约，分项累计后只乘一次来源重复数量，仅总质量按明确位数HALF_UP修约'


def snapshot(source):return {k:v for k,v in source.items() if k!='state'}


def source_component(p,cid):
    from .main import find
    s=find(p,'components',cid)
    if s['kind']!='polygon_railing' or s['state']!='approved':raise HTTPException(409,'材料表须选择当前已审定的栏杆边线构件')
    validate_railing(p,s,saved=True)
    return s


def source_result(s):
    r=railing_quantity(s)
    if Decimal(r['railing_summary']['single_length'])<=0:raise DomainError('栏杆计入范围为空，不能作为材料表来源')
    return r


def candidates(p,cid):
    s=source_component(p,cid);r=source_result(s)
    return {'source_component_id':s['id'],'source_component_hash':store.digest(snapshot(s)),
            'source_name':s['name'],'source_revision':s['revision'],'source_railing':r,'notice':NOTICE}


@precise
def calculate(comp,source=None):
    s=source if source is not None else comp['source_geometry']
    if comp.get('parameters')!={} or comp.get('quantity_type')!='mass' or comp.get('scope_confirmed') is not True:
        raise DomainError('栏杆材料表须为空顶层参数、质量类型，并确认连续范围及独立件数下料口径')
    if comp.get('source_component_id')!=s['id'] or comp.get('source_component_hash')!=store.digest(snapshot(s)):
        raise HTTPException(409,'来源栏杆已修订，请重新读取并核定材料表')
    if 'multiplicity' in comp:raise DomainError('材料表重复数量继承来源，不得另填或再次乘数量')
    for key,label in [('name','材料表名称'),('material','材料类别与做法'),('measurement_basis','材料计量及不重复说明')]:text(comp.get(key),label,200 if key=='name' else 2000)
    digits=comp.get('rounding_digits')
    if type(digits) is not int or not 0<=digits<=12:raise DomainError('材料总质量修约位数须明确为0至12整数')
    r=source_result(s);summary=r['railing_summary'];items=comp.get('material_items')
    if not isinstance(items,list) or not 1<=len(items)<=50:raise DomainError('材料表须明确1至50项线材，不自动补充立柱或其他材料')
    dimensions=dict(r['dimensions']);refs=dict(r['source_evidence']);conversions=list(r['unit_conversions'])
    for key in ('basis','material','rounding'):refs['material.'+key]=text(comp.get(key+'_evidence_id'),'材料表原始依据',200)
    rows=[];nodes=[];names=set();total=Decimal(0)
    single_ast=op('add',*[e['selected_formula_ast'] for e in r['railing_edges']])
    for i,item in enumerate(items,1):
        common={'name','mode','quantity','unit_weight','weight_unit','measurement_basis','evidence'}
        if not isinstance(item,dict) or not isinstance(item.get('mode'),str) or item['mode'] not in MODES:raise DomainError('材料项须明确连续构件或独立定长构件')
        cut=item['mode']=='cut_pieces';fields=common|({'cut_length','length_unit'} if cut else set())
        proofs={'quantity','unit_weight','measurement_basis'}|({'cut_length'} if cut else set())
        if set(item)!=fields or not isinstance(item['evidence'],dict) or set(item['evidence'])!=proofs:raise DomainError('材料项字段与逐参数依据须完整且仅包含当前模式所需内容')
        name=text(item['name'],'材料项名称',200)
        if name in names:raise DomainError('同一材料表名称不得重复，请核对是否重复计入')
        names.add(name);text(item['measurement_basis'],'逐项材料计量说明')
        count=number(item['quantity'],True)
        if count!=count.to_integral_value() or count>1000000:raise DomainError('全范围道数或每组件数须为1至1000000的整数')
        unit=item['weight_unit']
        if not isinstance(unit,str) or unit not in WEIGHT_PER_LENGTH:raise DomainError('每米质量须选择质量/长度单位，不能使用单件质量或体积密度')
        raw=number(item['unit_weight'],True);factor=Decimal(WEIGHT_PER_LENGTH[unit]);weight=number((raw*factor).normalize(),True)
        key='material.'+str(i);dimensions[key+'.quantity']=str(item['quantity']);dimensions[key+'.unit_weight']=str(item['unit_weight'])
        for k in proofs:refs[key+'.'+k]=text(item['evidence'][k],'材料项逐参数原始依据',200)
        conversions.append({'parameter':key+'.unit_weight','original_value':str(item['unit_weight']),'source_unit':unit,'factor':str(factor),'canonical_value':str(weight),'canonical_unit':'kg/m','rule_version':UNIT_VERSION})
        weight_ast=op('multiply',{'dimension':key+'.unit_weight'},{'constant':str(factor)})
        if cut:
            length=linear(item['cut_length'],item['length_unit'],True);dimensions[key+'.cut_length']=str(item['cut_length'])
            length_ast=op('multiply',{'dimension':key+'.cut_length'},{'constant':LENGTH[item['length_unit']]})
            conversions.append({'parameter':key+'.cut_length','original_value':str(item['cut_length']),'source_unit':item['length_unit'],'factor':LENGTH[item['length_unit']],'canonical_value':str(length),'canonical_unit':'m','rule_version':UNIT_VERSION})
        else:
            if summary.get('source_kind')=='design_route' and summary['length_basis']!='spatial':raise DomainError('连续材料不能用坐标线水平投影长度下料，请选择已审三维栏杆来源；独立定长件可单独列项')
            length=Decimal(summary['single_length']);length_ast=single_ast
        stock_length=length*count;mass=stock_length*weight;total+=mass
        ast=op('multiply',length_ast,{'dimension':key+'.quantity'},weight_ast);nodes.append(ast)
        rows.append({**item,'index':i,'normalized_quantity':str(count),'normalized_unit_weight':str(weight),
                     'single_member_length':str(length),'single_group_stock_length':str(stock_length),'single_group_mass':str(mass),'formula_ast':ast})
    groups=Decimal(summary['multiplicity']);gross=quantity_number(total*groups)
    quantity=quantity_number(gross.quantize(Decimal(1).scaleb(-digits),rounding=ROUND_HALF_UP))
    return {'quantity':str(quantity),'unit':'kg','gross':str(gross),'deduction':'0','parameters':{},
            'formula':'Σ[单道或单件下料长 × 每组道数或件数 × 单位长度质量] × '+str(groups)+'；仅总质量修约'+str(digits)+'位',
            'formula_ast':{'op':'round','args':[op('multiply',op('add',*nodes),{'dimension':'multiplicity'})],'digits':digits,'mode':'HALF_UP'},
            'dimensions':dimensions,'source_evidence':refs,'unit_conversions':conversions,'coordinate_basis':r['coordinate_basis'],
            'source_geometry':snapshot(s),'source_component_id':s['id'],'source_component_hash':comp['source_component_hash'],
            'material':comp['material'],'material_items':rows,'source_railing':r,
            'material_summary':{'source_name':s['name'],'source_revision':s['revision'],'source_single_length':summary['single_length'],
                                'source_length_basis':summary.get('length_basis_label','多边形原边水平长度'),'item_count':len(rows),
                                'single_group_mass':str(total),'multiplicity':str(groups),'unrounded_quantity':str(gross),
                                'rounding_digits':digits,'rounding_adjustment':str(quantity-gross)},
            'rule_version':VERSION,'rounding_rule':ROUNDING,'notice':NOTICE}


def validate(p,comp,saved=False):
    s=source_component(p,comp.get('source_component_id'));result=calculate(comp,s)
    sources=check_sources(p,result['source_evidence']);stamp=store.digest({'source_component_hash':comp['source_component_hash'],'sources':sources})
    if saved and (comp.get('source_stamp')!=stamp or comp.get('evidence')!=result['source_evidence'] or comp.get('preview')!=result or comp.get('source_geometry')!=snapshot(s)):
        raise HTTPException(409,'栏杆材料来源、原件、参数或计算记录已变化，请修订后重新复核')
    return result,stamp
