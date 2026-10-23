"""Evidence-backed linear stock, developed panels and equal-bay post schedules."""
from decimal import Decimal,ROUND_HALF_UP
from fastapi import HTTPException
from . import store
from .compound_takeoff import op,text
from .engine import DomainError,precise,number,quantity_number
from .measurement_units import LENGTH,WEIGHT_PER_LENGTH,VERSION as UNIT_VERSION
from .planar_takeoff import linear
from .railing_takeoff import calculate as railing_quantity,validate as validate_railing
from .surface_takeoff import check_sources
from .panel_material import calculate as panel_quantity,NOTICE as PANEL_NOTICE
from .polygon_material import calculate as polygon_panel_quantity,NOTICE as POLYGON_PANEL_NOTICE
from .railing_posts import calculate as post_quantity,ranges as post_ranges,NOTICE as POST_NOTICE,FIXED_NOTICE as FIXED_POST_NOTICE

from .additional_posts import calculate as added_post_quantity,NOTICE as ADDED_POST_NOTICE,EQUAL_NOTICE as EQUAL_ADDED_POST_NOTICE

from .post_adjustments import calculate as adjusted_post_quantity,NOTICE as ADJUSTED_POST_NOTICE

VERSION='railing-material-mass-1.0'
MODES={'continuous':'沿全范围连续构件','cut_pieces':'独立定长构件','rect_panels':'矩形板件或网片','polygon_panels':'异形直边板件或网片','spaced_posts':'按最大间距均分立柱','fixed_posts':'固定间距留余跨立柱','fixed_added_posts':'固定间距及指定构造加柱','spaced_added_posts':'最大间距均分及指定构造加柱','fixed_adjusted_posts':'固定布置及禁布移柱'}
NOTICE='只计算有据填写的等截面线材质量。连续构件按已审栏杆单组未修约计入长度乘全范围道数；定长构件按独立核定的每组件数及每件下料长度计算，不从栏杆间距自动推算立柱。每米质量须有原件，不以密度或含钢率猜测。坡段连续材料须采用三维长度；坐标线水平投影来源只可用于独立定长件。不同局部做法应拆分栏杆来源，不能让某道材料默认适用于未确认范围。不自动增加损耗、接头、搭接、立柱端部、基础、网片或板材质量，不把材料表质量替代栏杆安装清单长度。'
PANEL_MATERIAL_NOTICE=(NOTICE.replace('只计算有据填写的等截面线材质量。','').replace('、网片或板材质量','').replace('坐标线水平投影来源只可用于独立定长件','坐标线水平投影来源只可用于独立定长件或独立展开板网片')+'；'+PANEL_NOTICE)
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
            'source_name':s['name'],'source_revision':s['revision'],'source_railing':r,'post_ranges':post_ranges(r),'notice':PANEL_MATERIAL_NOTICE+'；'+POLYGON_PANEL_NOTICE+'；'+POST_NOTICE+'；'+FIXED_POST_NOTICE+'；'+ADDED_POST_NOTICE+'；'+EQUAL_ADDED_POST_NOTICE+'；'+ADJUSTED_POST_NOTICE}


@precise
def calculate(comp,source=None):
    s=source if source is not None else comp['source_geometry']
    if comp.get('parameters')!={} or comp.get('quantity_type')!='mass' or comp.get('scope_confirmed') is not True:
        raise DomainError('栏杆材料表须为空顶层参数、质量类型，并确认范围、独立数量及下料或展开口径')
    if comp.get('source_component_id')!=s['id'] or comp.get('source_component_hash')!=store.digest(snapshot(s)):
        raise HTTPException(409,'来源栏杆已修订，请重新读取并核定材料表')
    if 'multiplicity' in comp:raise DomainError('材料表重复数量继承来源，不得另填或再次乘数量')
    for key,label in [('name','材料表名称'),('material','材料类别与做法'),('measurement_basis','材料计量及不重复说明')]:text(comp.get(key),label,200 if key=='name' else 2000)
    digits=comp.get('rounding_digits')
    if type(digits) is not int or not 0<=digits<=12:raise DomainError('材料总质量修约位数须明确为0至12整数')
    r=source_result(s);summary=r['railing_summary'];items=comp.get('material_items')
    if not isinstance(items,list) or not 1<=len(items)<=50:raise DomainError('材料表须明确1至50项材料，不自动补充立柱或其他材料')
    dimensions=dict(r['dimensions']);refs=dict(r['source_evidence']);conversions=list(r['unit_conversions'])
    for key in ('basis','material','rounding'):refs['material.'+key]=text(comp.get(key+'_evidence_id'),'材料表原始依据',200)
    rows=[];nodes=[];names=set();total=Decimal(0);polygon_points=0;post_count=0
    single_ast=op('add',*[e['selected_formula_ast'] for e in r['railing_edges']])
    for i,item in enumerate(items,1):
        common={'name','mode','quantity','unit_weight','weight_unit','measurement_basis','evidence'}
        if not isinstance(item,dict) or not isinstance(item.get('mode'),str) or item['mode'] not in MODES:raise DomainError('材料项须明确连续构件、独立定长构件、均分立柱或矩形/异形直边板网片')
        spaced=item['mode'] in ('spaced_posts','fixed_posts','fixed_added_posts','spaced_added_posts','fixed_adjusted_posts');cut=item['mode'] in ('cut_pieces','spaced_posts','fixed_posts','fixed_added_posts','spaced_added_posts','fixed_adjusted_posts');polygon=item['mode']=='polygon_panels';panel=item['mode'] in ('rect_panels','polygon_panels')
        fields=(common-{'quantity'} if spaced else common)|({'post_ranges','cut_length','length_unit'} if spaced else {'polygon_rings','coordinate_basis'} if polygon else {'length','width','length_unit','openings'} if panel else {'cut_length','length_unit'} if cut else set())
        proofs=({'unit_weight','measurement_basis'} if spaced else {'quantity','unit_weight','measurement_basis'})|({'coordinate_basis'} if polygon else {'length','width'} if panel else {'cut_length'} if cut else set())
        if set(item)!=fields or not isinstance(item['evidence'],dict) or set(item['evidence'])!=proofs:raise DomainError('材料项字段与逐参数依据须完整且仅包含当前模式所需内容')
        name=text(item['name'],'材料项名称',200)
        if name in names:raise DomainError('同一材料表名称不得重复，请核对是否重复计入')
        names.add(name);text(item['measurement_basis'],'逐项材料计量说明')
        layout=None
        if spaced:
            layout=(adjusted_post_quantity if item['mode']=='fixed_adjusted_posts' else added_post_quantity if item['mode'] in ('fixed_added_posts','spaced_added_posts') else post_quantity)(item,r,'material.'+str(i)+'.posts.')
            post_count+=layout['post_count']
            if post_count>5000:raise DomainError('每表最多5000根单组自动布置立柱，请拆分实际范围')
            count=Decimal(layout['post_count'])
            dimensions.update(layout['dimensions']);refs.update(layout['source_evidence']);conversions.extend(layout['unit_conversions'])
        else:count=number(item['quantity'],True)
        if count!=count.to_integral_value() or count>1000000:raise DomainError('全范围道数或每组独立件数、片数须为1至1000000的整数')
        unit=item['weight_unit'];units={'kg/m²':'1','g/m²':'0.001'} if panel else WEIGHT_PER_LENGTH
        if not isinstance(unit,str) or unit not in units:raise DomainError('板件须选kg/m²或g/m²，线材须选质量/长度单位，不能使用单件质量或体积密度')
        raw=number(item['unit_weight'],True);factor=Decimal(units[unit]);weight=number((raw*factor).normalize(),True)
        key='material.'+str(i)
        if not spaced:dimensions[key+'.quantity']=str(item['quantity'])
        dimensions[key+'.unit_weight']=str(item['unit_weight'])
        for k in proofs:refs[key+'.'+k]=text(item['evidence'][k],'材料项逐参数原始依据',200)
        conversions.append({'parameter':key+'.unit_weight','original_value':str(item['unit_weight']),'source_unit':unit,'factor':str(factor),'canonical_value':str(weight),'canonical_unit':'kg/m²' if panel else 'kg/m','rule_version':UNIT_VERSION})
        weight_ast=op('multiply',{'dimension':key+'.unit_weight'},{'constant':str(factor)})
        if panel:
            if polygon:
                rings=item['polygon_rings']
                if not isinstance(rings,list) or any(not isinstance(v,dict) or not isinstance(v.get('points'),list) for v in rings):raise DomainError('异形板片须明确外轮廓及逐孔顶点列表')
                polygon_points+=sum(len(v['points']) for v in rings)
                if polygon_points>1000:raise DomainError('每表异形板片合计最多1000个顶点，请按实际范围拆表')
            area=(polygon_panel_quantity if polygon else panel_quantity)(item,key+'.panel.')
            dimensions.update(area['dimensions']);refs.update(area['source_evidence']);conversions.extend(area['unit_conversions'])
            net=Decimal(area['quantity']);mass=net*count*weight;total+=mass
            ast=op('multiply',area['formula_ast'],{'dimension':key+'.quantity'},weight_ast);nodes.append(ast)
            rows.append({**item,'index':i,'normalized_quantity':str(count),'normalized_unit_weight':str(weight),'single_panel_net_area':str(net),'single_group_net_area':str(net*count),'single_group_mass':str(mass),'panel_geometry':area,'formula_ast':ast})
            continue
        if cut:
            length=linear(item['cut_length'],item['length_unit'],True);dimensions[key+'.cut_length']=str(item['cut_length'])
            length_ast=op('multiply',{'dimension':key+'.cut_length'},{'constant':LENGTH[item['length_unit']]})
            conversions.append({'parameter':key+'.cut_length','original_value':str(item['cut_length']),'source_unit':item['length_unit'],'factor':LENGTH[item['length_unit']],'canonical_value':str(length),'canonical_unit':'m','rule_version':UNIT_VERSION})
        else:
            if summary.get('source_kind')=='design_route' and summary['length_basis']!='spatial':raise DomainError('连续材料不能用坐标线水平投影长度下料，请选择已审三维栏杆来源；独立定长件可单独列项')
            length=Decimal(summary['single_length']);length_ast=single_ast
        stock_length=length*count;mass=stock_length*weight;total+=mass
        ast=op('multiply',length_ast,layout['formula_ast'] if spaced else {'dimension':key+'.quantity'},weight_ast);nodes.append(ast)
        rows.append({**item,'index':i,'normalized_quantity':str(count),'normalized_unit_weight':str(weight),
                     'single_member_length':str(length),'single_group_stock_length':str(stock_length),'single_group_mass':str(mass),'formula_ast':ast,**({'quantity':str(count),'post_layout':layout} if spaced else {})})
    has_adjusted=any(v['mode']=='fixed_adjusted_posts' for v in items)
    has_equal_added=any(v['mode']=='spaced_added_posts' for v in items)
    has_added=any(v['mode']=='fixed_added_posts' for v in items)
    has_fixed=any(v['mode']=='fixed_posts' for v in items)
    has_posts=any(v['mode'] in ('spaced_posts','fixed_posts','fixed_added_posts','spaced_added_posts','fixed_adjusted_posts') for v in items)
    has_polygons=any(v['mode']=='polygon_panels' for v in items)
    has_panels=any(v['mode'] in ('rect_panels','polygon_panels') for v in items)
    groups=Decimal(summary['multiplicity']);gross=quantity_number(total*groups)
    quantity=quantity_number(gross.quantize(Decimal(1).scaleb(-digits),rounding=ROUND_HALF_UP))
    return {'quantity':str(quantity),'unit':'kg','gross':str(gross),'deduction':'0','parameters':{},
            'formula':('Σ[核定下料长 × 移柱后保留根数 × 每米质量；及其他材料分项质量]' if has_adjusted else 'Σ[核定下料长 × (原规则布柱数＋指定构造加柱数) × 每米质量；及其他材料分项质量]' if has_equal_added else 'Σ[核定下料长 × (定距柱数＋指定构造加柱数) × 每米质量；及其他材料分项质量]' if has_added else 'Σ[核定下料长 × 逐区间定距留余跨立柱数 × 每米质量；及其他材料分项质量]' if has_fixed else 'Σ[核定下料长 × 逐区间均分立柱数 × 每米质量；及其他材料分项质量]' if has_posts else 'Σ[单片净展开面积 × 每组片数 × 单位面积质量；或线材下料长 × 每组数量 × 单位长度质量]' if has_panels else 'Σ[单道或单件下料长 × 每组道数或件数 × 单位长度质量]')+' × '+str(groups)+'；仅总质量修约'+str(digits)+'位',
            'formula_ast':{'op':'round','args':[op('multiply',op('add',*nodes),{'dimension':'multiplicity'})],'digits':digits,'mode':'HALF_UP'},
            'dimensions':dimensions,'source_evidence':refs,'unit_conversions':conversions,'coordinate_basis':r['coordinate_basis'],
            'source_geometry':snapshot(s),'source_component_id':s['id'],'source_component_hash':comp['source_component_hash'],
            'material':comp['material'],'material_items':rows,'source_railing':r,
            'material_summary':{'source_name':s['name'],'source_revision':s['revision'],'source_single_length':summary['single_length'],
                                'source_length_basis':summary.get('length_basis_label','多边形原边水平长度'),'item_count':len(rows),
                                'single_group_mass':str(total),'multiplicity':str(groups),'unrounded_quantity':str(gross),
                                'rounding_digits':digits,'rounding_adjustment':str(quantity-gross)},
            'rule_version':'railing-material-mass-1.7' if has_adjusted else 'railing-material-mass-1.6' if has_equal_added else 'railing-material-mass-1.5' if has_added else 'railing-material-mass-1.4' if has_fixed else 'railing-material-mass-1.3' if has_posts else 'railing-material-mass-1.2' if has_polygons else 'railing-material-mass-1.1' if has_panels else VERSION,'rounding_rule':ROUNDING+('；板件单片净展开面积与分项质量均不修约' if has_panels else ''),'notice':ADJUSTED_POST_NOTICE+('；'+EQUAL_ADDED_POST_NOTICE if has_equal_added else '')+('；'+ADDED_POST_NOTICE if has_added else '')+('；'+FIXED_POST_NOTICE if has_fixed else '')+('；'+POST_NOTICE if any(v['mode']=='spaced_posts' for v in items) else '')+('；'+PANEL_MATERIAL_NOTICE+'；'+POLYGON_PANEL_NOTICE if has_panels else '') if has_adjusted else EQUAL_ADDED_POST_NOTICE+('；'+ADDED_POST_NOTICE if has_added else '')+('；'+FIXED_POST_NOTICE if has_fixed else '')+('；'+POST_NOTICE if any(v['mode']=='spaced_posts' for v in items) else '')+('；'+PANEL_MATERIAL_NOTICE+'；'+POLYGON_PANEL_NOTICE if has_panels else '') if has_equal_added else ADDED_POST_NOTICE+('；'+FIXED_POST_NOTICE if has_fixed else '')+('；'+POST_NOTICE if any(v['mode']=='spaced_posts' for v in items) else '')+('；'+PANEL_MATERIAL_NOTICE+'；'+POLYGON_PANEL_NOTICE if has_panels else '') if has_added else FIXED_POST_NOTICE+('；'+POST_NOTICE if any(v['mode']=='spaced_posts' for v in items) else '')+('；'+PANEL_MATERIAL_NOTICE+'；'+POLYGON_PANEL_NOTICE if has_panels else '') if has_fixed else POST_NOTICE+('；'+PANEL_MATERIAL_NOTICE+'；'+POLYGON_PANEL_NOTICE if has_panels else '') if has_posts else PANEL_MATERIAL_NOTICE+'；'+POLYGON_PANEL_NOTICE if has_polygons else PANEL_MATERIAL_NOTICE if has_panels else NOTICE}


def validate(p,comp,saved=False):
    s=source_component(p,comp.get('source_component_id'));result=calculate(comp,s)
    sources=check_sources(p,result['source_evidence']);stamp=store.digest({'source_component_hash':comp['source_component_hash'],'sources':sources})
    if saved and (comp.get('source_stamp')!=stamp or comp.get('evidence')!=result['source_evidence'] or comp.get('preview')!=result or comp.get('source_geometry')!=snapshot(s)):
        raise HTTPException(409,'栏杆材料来源、原件、参数或计算记录已变化，请修订后重新复核')
    return result,stamp
