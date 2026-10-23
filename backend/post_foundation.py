"""Independent solid foundations, one per approved laid-out post."""
from decimal import Decimal,ROUND_HALF_UP
from fastapi import HTTPException
from . import store
from .compound_takeoff import op,text
from .engine import DomainError,precise,quantity_number
from .measurement_units import LENGTH,VERSION as UNIT_VERSION
from .planar_takeoff import linear
from .railing_material import calculate as material_quantity,validate as validate_material,snapshot
from .surface_takeoff import check_sources
from . import foundation_steps,foundation_round,foundation_openings,foundation_recesses

VERSION='railing-post-foundation-1.0'
PARAMS=('length','width','height')
NOTICE='仅对明确选入的已审布置立柱材料项，每根保留立柱对应一个独立水平矩形等截面基础，全部选入项须同一材料及做法。数量继承均分/定距、构造加柱、禁布移柱后的最终单组根数，来源重复仅乘一次；不以材料质量或栏杆长度倒算数量。一个材料项内全部立柱使用同一基础尺寸；差异做法须先按实际范围拆分材料项。未选项不计，不自动生成、定位或扣并基础交叠，不扣柱脚、锚栓孔和预埋件，不计垫层、土方、钢筋、模板或地基承载设计；不得与既有基础清单重复计量。同一来源材料项不得在其他基础记录重复选入。'


def source_component(p,cid):
    from .main import find
    s=find(p,'components',cid)
    if s['kind']!='railing_material' or s['state']!='approved':raise HTTPException(409,'立柱基础须选择当前已审定的栏杆材料表')
    validate_material(p,s,saved=True)
    return s


def eligible(r):return [v for v in r['material_items'] if 'post_layout' in v]


def candidates(p,cid):
    s=source_component(p,cid);r=material_quantity(s);items=eligible(r)
    if not items:raise DomainError('该材料表没有已核定布置的立柱项，独立定长件不能推定为立柱')
    from .foundation_recesses import NOTICE as SHAPE_NOTICE
    return dict(source_component_id=s['id'],source_component_hash=store.digest(snapshot(s)),source_name=s['name'],source_revision=s['revision'],multiplicity=r['material_summary']['multiplicity'],post_items=[dict(material_index=v['index'],material_name=v['name'],mode=v['mode'],post_count=v['post_layout']['post_count']) for v in items],notice=SHAPE_NOTICE)


def check_claims(p,comp):
    claimed={r['material_index'] for r in comp['foundation_items']}
    for other in p['components']:
        if other['kind']!='post_foundation' or other['id']==comp.get('id') or other.get('source_component_id')!=comp['source_component_id']:continue
        if claimed & {r['material_index'] for r in other['foundation_items']}:
            raise HTTPException(409,'该来源立柱材料项已被基础记录“'+other['name']+'”选入；请修订原基础，不能重复计量')


@precise
def calculate(comp,source=None):
    s=source if source is not None else comp['source_geometry']
    if comp.get('parameters')!={} or comp.get('quantity_type')!='volume' or comp.get('scope_confirmed') is not True:raise DomainError('立柱基础须为空顶层参数、体积类型，并确认一柱一基础及不重复计量范围')
    if 'multiplicity' in comp:raise DomainError('立柱基础重复数量继承来源，不得另填或再次乘数量')
    if comp.get('source_component_id')!=s['id'] or comp.get('source_component_hash')!=store.digest(snapshot(s)):raise HTTPException(409,'来源材料表已修订，请重新读取立柱数量并核定基础')
    for k,label in [('name','基础名称'),('material','基础材料与做法'),('measurement_basis','基础范围及不重复计量说明')]:text(comp.get(k),label,200 if k=='name' else 2000)
    digits=comp.get('rounding_digits')
    if type(digits) is not int or not 0<=digits<=12:raise DomainError('基础总体积修约位数须明确为0至12整数')
    r=material_quantity(s);available={v['index']:v for v in eligible(r)};items=comp.get('foundation_items')
    if not isinstance(items,list) or not 1<=len(items)<=50:raise DomainError('须明确选入1至50项已布置立柱，未选项不计基础')
    by_index={}
    for original in items:
        d={k:v for k,v in original.items() if k!='through_holes'} if isinstance(original,dict) else original
        if isinstance(original,dict) and 'through_holes' in original:foundation_openings.validate_config(original['through_holes'])
        stepped=isinstance(d,dict) and 'shape' in d
        if not isinstance(d,dict) or not stepped and set(d)!={'material_index','parameters','length_unit','measurement_basis','evidence'} or type(d.get('material_index')) is not int:raise DomainError('基础项须仅含来源材料项序号、尺寸、单位、范围说明和逐参数原件')
        i=d['material_index']
        if i not in available or i in by_index:raise DomainError('基础来源立柱项重复、过期或不适用；只接受已核定布置的立柱')
        if stepped:
            if d['shape'] in foundation_round.SHAPES:foundation_round.validate_row(d)
            else:foundation_steps.validate_row(d)
        elif not isinstance(d['parameters'],dict) or set(d['parameters'])!=set(PARAMS) or not isinstance(d['evidence'],dict) or set(d['evidence'])!=set(PARAMS)|{'measurement_basis'}:raise DomainError('基础长宽高及范围说明须完整并逐项关联原件，不得另填根数或扣减')
        text(d['measurement_basis'],'逐项基础适用范围说明');by_index[i]=original
    dimensions=dict(r['dimensions']);refs=dict(r['source_evidence']);conversions=list(r['unit_conversions'])
    for k in ('basis','material','rounding'):refs['foundation.'+k]=text(comp.get(k+'_evidence_id'),'基础总体原始依据',200)
    rows=[];nodes=[];total=Decimal(0);outer_total=Decimal(0);opening_total=Decimal(0);group_posts=0;count=Decimal(r['material_summary']['multiplicity'])
    for i,d in sorted(by_index.items()):
        v=available[i];key='foundation.'+str(i);params={};asts={}
        tiers=None
        if d.get('shape') in foundation_round.SHAPES:
            single,shape_ast,tiers,params=foundation_round.calculate(d,key,dimensions,refs,conversions)
        elif 'shape' in d:
            single,shape_ast,tiers=foundation_steps.calculate(d,key,dimensions,refs,conversions)
        else:
            for k in PARAMS:
                n=linear(d['parameters'][k],d['length_unit'],True);params[k]=str(n);dimensions[key+'.'+k]=str(d['parameters'][k]);refs[key+'.'+k]=text(d['evidence'][k],'基础尺寸原始依据',200);asts[k]=op('multiply',{'dimension':key+'.'+k},{'constant':LENGTH[d['length_unit']]})
                conversions.append(dict(parameter=key+'.'+k,original_value=str(d['parameters'][k]),source_unit=d['length_unit'],factor=LENGTH[d['length_unit']],canonical_value=str(n),canonical_unit='m',rule_version=UNIT_VERSION))
            single=Decimal(params['length'])*Decimal(params['width'])*Decimal(params['height']);shape_ast=op('multiply',*[asts[k] for k in PARAMS])
        original_single=single;opening_data={};opening_amount=Decimal(0)
        if 'through_holes' in d:single,shape_ast,opening_data,opening_amount=foundation_openings.apply(d,single,shape_ast,tiers,params,key,dimensions,refs,conversions)
        refs[key+'.measurement_basis']=text(d['evidence']['measurement_basis'],'基础逐项范围依据',200)
        n=v['post_layout']['post_count'];group_posts+=n;subtotal=single*n;total+=subtotal;outer_total+=original_single*n;opening_total+=opening_amount*n
        group_ast=op('multiply',shape_ast,v['post_layout']['formula_ast']);nodes.append(group_ast)
        rows.append({**d,**opening_data,'material_name':v['name'],'post_mode':v['mode'],'single_group_count':n,'total_count':str(Decimal(n)*count),**({'foundation_levels':tiers} if tiers is not None else {'normalized_parameters':params}),'single_foundation_volume':str(single),'single_group_volume':str(subtotal),'total_volume':str(subtotal*count),'formula_asts':dict(single_volume=shape_ast,single_group_count=v['post_layout']['formula_ast'],single_group_volume=group_ast,total_count=op('multiply',v['post_layout']['formula_ast'],{'dimension':'multiplicity'}),total_volume=op('multiply',group_ast,{'dimension':'multiplicity'}))})
        if opening_data:
            rows[-1].update(total_gross_volume=str(original_single*n*count),total_opening_volume=str(opening_amount*n*count))
            for field,node in opening_data['opening_formula_asts'].items():
                rows[-1]['formula_asts'][field]=node;rows[-1]['formula_asts'][field.replace('single_','total_')]=op('multiply',node,v['post_layout']['formula_ast'],{'dimension':'multiplicity'})
        if tiers is not None:
            for tier in tiers:
                tier['total_volume']=str(Decimal(tier.get('net_level_volume',tier['single_level_volume']))*n*count);tier['formula_asts']['total_volume']=op('multiply',tier['formula_asts'].get('net_level_volume',tier['formula_asts']['single_level_volume']),v['post_layout']['formula_ast'],{'dimension':'multiplicity'})
                if opening_data:
                    for field in ('single_level_volume','single_opening_volume'):
                        target='total_gross_volume' if field=='single_level_volume' else 'total_opening_volume';tier[target]=str(Decimal(tier[field])*n*count);tier['formula_asts'][target]=op('multiply',tier['formula_asts'][field],v['post_layout']['formula_ast'],{'dimension':'multiplicity'})
    stepped_table=any('foundation_levels' in row for row in rows)
    round_table=any(row.get('shape') in foundation_round.SHAPES for row in rows)
    opened=any('through_holes' in row for row in rows)
    finite=any(foundation_recesses.enabled(row.get('through_holes')) for row in rows)
    gross=quantity_number(total*count);quantity=quantity_number(gross.quantize(Decimal(1).scaleb(-digits),rounding=ROUND_HALF_UP))
    return dict(quantity=str(quantity),gross=str(quantity_number(outer_total*count)) if opened else str(gross),deduction=str(quantity_number(opening_total*count)) if opened else '0',unit='m³',parameters={},formula=('Σ[(单基础毛体积−逐孔逐层有效相交圆孔体积)×来源最终单组立柱根数] × ' if finite else 'Σ[(单基础毛体积−全部互不重叠竖直贯通圆孔体积)×来源最终单组立柱根数] × ' if opened else 'Σ[单基础实体体积（圆柱π/4×实际直径²×净高，台阶逐层相加）×来源最终单组立柱根数] × ' if round_table else 'Σ[单基础逐层长×宽×层高之和×来源最终单组立柱根数] × ' if stepped_table else 'Σ[基础长×宽×高×来源最终单组立柱根数] × ')+str(count)+'；仅总体积修约'+str(digits)+'位',formula_ast={'op':'round','args':[op('multiply',op('add',*nodes),{'dimension':'multiplicity'})],'digits':digits,'mode':'HALF_UP'},dimensions=dimensions,source_evidence=refs,unit_conversions=conversions,source_geometry=snapshot(s),source_component_id=s['id'],source_component_hash=comp['source_component_hash'],material=comp['material'],foundation_items=rows,foundation_summary=dict(source_name=s['name'],source_revision=s['revision'],selected_item_count=len(rows),unselected_post_item_count=len(available)-len(rows),single_group_count=group_posts,multiplicity=str(count),total_count=str(Decimal(group_posts)*count),single_group_volume=str(total),unrounded_quantity=str(gross),rounding_digits=digits,rounding_adjustment=str(quantity-gross)),rounding_rule='Decimal 80位；基础尺寸换算、单基础及分项体积不修约，累计乘来源重复一次后，仅总体积按HALF_UP修约；数量直接引用已审立柱布置公式',rule_version=foundation_recesses.VERSION if finite else foundation_openings.VERSION if opened else foundation_round.VERSION if round_table else foundation_steps.VERSION if stepped_table else VERSION,notice=foundation_recesses.NOTICE if finite else foundation_openings.NOTICE if opened else foundation_round.NOTICE if round_table else foundation_steps.NOTICE if stepped_table else NOTICE,**({'foundation_pi_constant':foundation_round.PI} if round_table or opened else {}),**({'foundation_opening_summary':dict(gross_volume=str(outer_total*count),opening_volume=str(opening_total*count),net_volume=str(gross),selected_hole_count=sum(len(row.get('opening_holes',[])) for row in rows),total_hole_count=str(sum((Decimal(len(row.get('opening_holes',[])))*row['single_group_count']*count for row in rows),Decimal(0))))} if opened else {}))


def validate(p,comp,saved=False):
    s=source_component(p,comp.get('source_component_id'));result=calculate(comp,s);check_claims(p,comp)
    sources=check_sources(p,result['source_evidence']);stamp=store.digest(dict(source_component_hash=comp['source_component_hash'],sources=sources))
    if saved and (comp.get('source_stamp')!=stamp or comp.get('evidence')!=result['source_evidence'] or comp.get('preview')!=result or comp.get('source_geometry')!=snapshot(s)):raise HTTPException(409,'立柱基础来源、尺寸、数量或计算记录已变化，请修订后重新复核')
    return result,stamp
