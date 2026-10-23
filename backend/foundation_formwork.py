"""Evidence-selected vertical outer contact surfaces of reviewed post foundations."""
from decimal import Decimal, ROUND_HALF_UP
from fastapi import HTTPException
from . import store
from .compound_takeoff import PI, op, text
from .engine import DomainError, precise, quantity_number
from .measurement_units import LENGTH, VERSION as UNIT_VERSION
from .planar_takeoff import linear
from .post_foundation import calculate as foundation_quantity, validate as validate_foundation
from .railing_material import snapshot
from .surface_takeoff import check_sources
from . import foundation_formwork_arcs as arcs
from . import foundation_formwork_strips as strips

VERSION = 'foundation-outer-formwork-1.0'
FACES = {'x_minus':'X负侧', 'x_plus':'X正侧', 'y_minus':'Y负侧', 'y_plus':'Y正侧', 'circumference':'整周圆柱侧面'}
NOTICE = '仅计明确选入的已审立柱基础外侧竖直接触面。矩形按局部X/Y四侧分别选择，圆柱仅支持本层整周；起止Z均以同一基础底面为0向上，须在所选层内。未选面不计，不把贴土面或全部外表面默认当模板。同面正高度交叠禁止，跨记录也不能重复。侧边矩形开口按实际相交高及开口宽扣除，体积不扣孔的阈值不能恢复不存在的接触面；内部孔壁、孔底、顶底面、台阶水平面、圆弧局部面、支撑及施工措施另核。面积继承每项全部最终基础个数，不再乘来源重复；同项各基础须同一接触范围。差异范围应先拆分来源。尺寸、孔洞、根数来自当前已审基础，模板做法、实际接触范围和修约须有原件并独立复核；不自动判断施工工法、基础相交或模板结构设计，不与已有模板清单重复。'
ZERO = {'constant':'0'}


def source_component(p, cid):
    from .main import find
    s = find(p, 'components', cid)
    if s['kind'] != 'post_foundation' or s['state'] != 'approved':
        raise HTTPException(409, '基础模板须选择当前已审定的立柱独立基础')
    validate_foundation(p, s, saved=True)
    return s


def layers(d):
    if 'foundation_levels' in d:
        return d['foundation_levels']
    key = 'foundation.'+str(d['material_index'])
    return [dict(index=1, normalized_parameters=d['normalized_parameters'], from_z='0', to_z=d['normalized_parameters']['height'],
                 formula_asts=dict(from_z=ZERO, to_z=op('multiply', {'dimension':key+'.height'}, {'constant':LENGTH[d['length_unit']]})))]


def candidates(p, cid):
    s = source_component(p, cid); r = foundation_quantity(s)
    return dict(source_component_id=s['id'], source_component_hash=store.digest(snapshot(s)), source_name=s['name'], source_revision=s['revision'],
                foundation_items=[dict(material_index=d['material_index'], material_name=d['material_name'], total_count=d['total_count'],
                    levels=[dict(index=v['index'], from_z=v['from_z'], to_z=v['to_z'], normalized_parameters=v['normalized_parameters'],
                                 faces=['circumference'] if 'diameter' in v['normalized_parameters'] else ['x_minus','x_plus','y_minus','y_plus']) for v in layers(d)]) for d in r['foundation_items']], notice=strips.NOTICE)


def bounds(d):
    return (linear(d['parameters']['z_start'],d['unit']), linear(d['parameters']['z_end'],d['unit']))


def overlap(a, b):
    return all(a[k] == b[k] for k in ('material_index','level_index','face')) and max(bounds(a)[0],bounds(b)[0]) < min(bounds(a)[1],bounds(b)[1]) and strips.overlaps(a,b) and (not (arcs.enabled(a) or arcs.enabled(b)) or arcs.gap(a,b)<0)


def check_claims(p, comp):
    for other in p['components']:
        if other['kind'] != 'foundation_formwork' or other['id'] == comp.get('id') or other['source_component_id'] != comp['source_component_id']:
            continue
        arcs.check_frames(comp['contact_ranges'],other['contact_ranges'],conflict=True)
        strips.check_frames(comp['contact_ranges'],other['contact_ranges'],conflict=True)
        if any(overlap(a,b) for a in comp['contact_ranges'] for b in other['contact_ranges']):
            raise HTTPException(409, '模板接触范围与记录“'+other['name']+'”重复，请修订原记录或核定互不重叠的范围')


@precise
def calculate(comp, source=None):
    source = source if source is not None else comp['source_geometry']
    if comp.get('parameters') != {} or comp.get('quantity_type') != 'area' or comp.get('scope_confirmed') is not True:
        raise DomainError('基础模板须为空顶层参数、面积类型，并确认真实外侧接触面及不重复计量')
    if 'multiplicity' in comp: raise DomainError('模板继承全部基础个数，不得再填重复数量')
    if source.get('kind') != 'post_foundation': raise DomainError('模板只能引用立柱基础，不能从体积倒算接触面积')
    if comp.get('source_component_id') != source['id'] or comp.get('source_component_hash') != store.digest(snapshot(source)):
        raise HTTPException(409, '基础来源已修订，请重新读取尺寸、孔洞、根数及接触范围')
    for k,label in [('name','模板名称'),('material','模板材料及施工做法'),('measurement_basis','实际接触及不重复计量范围')]:
        text(comp.get(k),label,200 if k == 'name' else 2000)
    digits = comp.get('rounding_digits')
    if type(digits) is not int or not 0 <= digits <= 12: raise DomainError('模板总面积修约位数须明确为0至12整数')
    selected = comp.get('contact_ranges')
    if not isinstance(selected,list) or not 1 <= len(selected) <= 200: raise DomainError('须明确1至200项外侧接触面标高范围，未选面不计')
    r = foundation_quantity(source); by_index = {d['material_index']:d for d in r['foundation_items']}
    dimensions = dict(r['dimensions']); refs = dict(r['source_evidence']); conversions = []
    for k in ('basis','material','rounding'): refs['formwork.'+k] = text(comp.get(k+'_evidence_id'),'模板原始依据',200)
    rows=[]; names=set(); gross=Decimal(0); deduction=Decimal(0); nodes=[]
    for i,d in enumerate(selected,1):
        if not isinstance(d,dict) or set(d)-{'arc_range','lateral_range'} != {'name','material_index','level_index','face','parameters','unit','evidence','measurement_basis'}:
            raise DomainError('每项模板须仅含名称、基础项与层序号、外侧面、起止Z、单位及逐参数原件和范围说明')
        if type(d['material_index']) is not int or d['material_index'] not in by_index or type(d['level_index']) is not int:
            raise DomainError('模板基础项或层序号须为所选已审来源中的整数序号')
        name=text(d['name'],'接触范围名称',200)
        if name in names: raise DomainError('接触范围名称不能重复')
        names.add(name); item=by_index[d['material_index']]; tiers=layers(item)
        if not 1 <= d['level_index'] <= len(tiers): raise DomainError('模板来源层不存在')
        tier=tiers[d['level_index']-1]; p={k:Decimal(v) for k,v in tier['normalized_parameters'].items()}; circular='diameter' in p
        if not isinstance(d['face'],str) or d['face'] not in (('circumference',) if circular else ('x_minus','x_plus','y_minus','y_plus')):
            raise DomainError('矩形须明确X/Y正负外侧，圆形须选择本层圆柱侧面；局部范围另填圆弧角度')
        if arcs.enabled(d):
            if not circular or d['face']!='circumference': raise DomainError('局部圆弧只适用于圆柱或圆台阶外侧，矩形面不能填角度')
            arcs.validate_config(d['arc_range']);arcs.check_frames([d],selected[:i-1])
        if strips.enabled(d):
            if circular or arcs.enabled(d):raise DomainError('局部横向条带仅适用于矩形外侧面，不能用于圆柱或与圆弧混填')
            strips.validate_config(d['lateral_range']);strips.check_frames([d],selected[:i-1])
        if not isinstance(d['parameters'],dict) or set(d['parameters']) != {'z_start','z_end'} or not isinstance(d['evidence'],dict) or set(d['evidence']) != {'z_start','z_end','measurement_basis'}:
            raise DomainError('每项接触起止Z及适用说明须完整并逐项关联原件')
        text(d['measurement_basis'],'本面真实接触范围及开口扣减依据')
        lo,hi=bounds(d)
        if lo < Decimal(tier['from_z']) or hi > Decimal(tier['to_z']) or hi <= lo: raise DomainError('接触起止Z须满足本层底≤起始Z<终止Z≤本层顶；跨层须分项')
        if any(overlap(d,previous) for previous in selected[:i-1]): raise DomainError('同基础项同层圆柱侧面的角度与标高均正范围重叠，不能重复计量' if arcs.enabled(d) or any(arcs.enabled(v) for v in selected[:i-1]) else '同基础项、同层、同面的横向与标高范围均正重叠，不能重复计量')
        key='formwork.'+str(i); asts={}
        for k in ('z_start','z_end'):
            value=lo if k=='z_start' else hi; dimensions[key+'.'+k]=str(d['parameters'][k]); asts[k]=op('multiply',{'dimension':key+'.'+k},{'constant':LENGTH[d['unit']]})
            conversions.append(dict(parameter=key+'.'+k,original_value=str(d['parameters'][k]),source_unit=d['unit'],factor=LENGTH[d['unit']],canonical_value=str(value),canonical_unit='m',rule_version=UNIT_VERSION))
        refs.update({key+'.'+k:text(eid,'接触面原件',200) for k,eid in d['evidence'].items()})
        fk='foundation.'+str(item['material_index']); lp=fk+'.levels.'+str(tier['index']) if 'foundation_levels' in item else fk
        size='diameter' if circular else 'width' if d['face'].startswith('x_') else 'length'
        width=p[size]*(Decimal(PI) if circular else 1); width_ast=op('multiply',{'dimension':lp+'.'+size},{'constant':LENGTH[item['length_unit']]},*([{'constant':PI}] if circular else []))
        arc_data={};arc_asts={}
        if arcs.enabled(d):width,width_ast,arc_data,arc_asts=arcs.apply(d,key,dimensions,refs,width,width_ast)
        strip_data={};strip_asts={}
        if strips.enabled(d):width,width_ast,strip_data,strip_asts=strips.apply(d,key,dimensions,refs,conversions,width,width_ast)
        height=hi-lo; height_ast=op('subtract',asts['z_end'],asts['z_start']); single_gross=width*height; gross_ast=op('multiply',width_ast,height_ast)
        apertures=[]; cut_nodes=[]; cut=Decimal(0)
        for h in item.get('opening_holes',[]):
            if circular or h.get('shape') != 'rectangular': continue
            hp={k:Decimal(v) for k,v in h['normalized_parameters'].items()}; axis='x' if d['face'].startswith('x_') else 'y'; outer='length' if axis=='x' else 'width'; perpendicular='width' if axis=='x' else 'length'; sign=Decimal(-1) if d['face'].endswith('minus') else Decimal(1)
            if sign*hp[axis]+hp[outer]/2 != p[outer]/2: continue
            start=Decimal(h['z_start']); end=Decimal(h['z_end']); a=max(lo,start); b=min(hi,end)
            if b <= a: continue
            hs=h['vertical_formula_asts']; a_ast=asts['z_start'] if lo>=start else hs['z_start']; b_ast=asts['z_end'] if hi<=end else hs['z_end']; depth_ast=op('subtract',b_ast,a_ast)
            hkey=fk+'.holes.'+str(h['index']); opening_width_ast=op('multiply',{'dimension':hkey+'.'+perpendicular},{'constant':LENGTH[h['unit']]}); aperture_width=hp[perpendicular];aperture_data={};aperture_asts={}
            if strips.enabled(d):
                clipped=strips.clip(d,h,perpendicular,fk,strip_asts)
                if clipped is None:continue
                aperture_width,opening_width_ast,aperture_data,aperture_asts=clipped
            area=aperture_width*(b-a); node=op('multiply',opening_width_ast,depth_ast); cut+=area; cut_nodes.append(node)
            apertures.append(dict(hole_index=h['index'],name=h['name'],**aperture_data,width=str(aperture_width),z_start=str(a),z_end=str(b),height=str(b-a),single_area=str(area),formula_asts=dict(**aperture_asts,width=opening_width_ast,z_start=a_ast,z_end=b_ast,height=depth_ast,single_area=node)))
        net=single_gross-cut
        if net < 0: raise DomainError('侧面开口面积超过接触范围，请核对已审基础几何')
        count=Decimal(item['total_count']); cut_ast=op('add',*cut_nodes); net_ast=op('subtract',gross_ast,cut_ast); count_ast=item['formula_asts']['total_count']; total_ast=op('multiply',net_ast,count_ast)
        gross+=single_gross*count; deduction+=cut*count; nodes.append(total_ast)
        rows.append({**d,'index':i,'material_name':item['material_name'],'face_label':FACES[d['face']],'layer_from_z':tier['from_z'],'layer_to_z':tier['to_z'],'normalized_parameters':dict(z_start=str(lo),z_end=str(hi)),
                     **strip_data,**arc_data,**({'face_label':'局部圆柱侧面'} if arc_data else {}),'width':str(width),'height':str(height),'total_count':str(count),'single_gross_area':str(single_gross),'single_opening_area':str(cut),'single_net_area':str(net),
                     'total_gross_area':str(single_gross*count),'total_opening_area':str(cut*count),'total_net_area':str(net*count),'apertures':apertures,
                     'formula_asts':dict(**strip_asts,**arc_asts,z_start=asts['z_start'],z_end=asts['z_end'],width=width_ast,height=height_ast,total_count=count_ast,single_gross_area=gross_ast,single_opening_area=cut_ast,single_net_area=net_ast,total_gross_area=op('multiply',gross_ast,count_ast),total_opening_area=op('multiply',cut_ast,count_ast),total_net_area=total_ast)})
    has_arcs=any(arcs.enabled(d) for d in selected);has_strips=any(strips.enabled(d) for d in selected)
    quantity_number(gross); quantity_number(deduction); net=quantity_number(gross-deduction); quantity=quantity_number(net.quantize(Decimal(1).scaleb(-digits),rounding=ROUND_HALF_UP))
    return dict(quantity=str(quantity),gross=str(gross),deduction=str(deduction),unit='m²',parameters={},material=comp['material'],
                formula='Σ[(已选外侧展开宽×实际接触高−侧边矩形开口实际相交面积)×已审全部基础个数]；仅总面积修约'+str(digits)+'位',
                formula_ast=dict(op='round',args=[op('add',*nodes)],digits=digits,mode='HALF_UP'),dimensions=dimensions,source_evidence=refs,unit_conversions=conversions,source_unit_conversions=r['unit_conversions'],
                source_geometry=snapshot(source),source_component_id=source['id'],source_component_hash=comp['source_component_hash'],contact_ranges=rows,
                formwork_summary=dict(source_name=source['name'],source_revision=source['revision'],range_count=len(rows),unrounded_quantity=str(net),rounding_digits=digits,rounding_adjustment=str(quantity-net)),
                **({'formwork_pi_constant':PI} if any(d['face']=='circumference' for d in selected) else {}),
                **({'arc_checks':arcs.checks(rows)} if has_arcs else {}),
                **({'lateral_checks':strips.checks(rows)} if has_strips else {}),
                rounding_rule='Decimal 80位及记录的固定π；采用来源未修约尺寸和最终全部基础个数，逐面及开口不修约，不再乘来源重复，仅总面积HALF_UP修约',rule_version=strips.VERSION if has_strips else arcs.VERSION if has_arcs else VERSION,notice=strips.NOTICE if has_strips else arcs.NOTICE if has_arcs else NOTICE)


def validate(p, comp, saved=False):
    source=source_component(p,comp.get('source_component_id')); result=calculate(comp,source); check_claims(p,comp)
    proofs=check_sources(p,result['source_evidence']); stamp=store.digest(dict(source_component_hash=comp['source_component_hash'],sources=proofs))
    if saved and (comp.get('source_stamp')!=stamp or comp.get('evidence')!=result['source_evidence'] or comp.get('preview')!=result or comp.get('source_geometry')!=snapshot(source)):
        raise HTTPException(409,'模板来源、接触范围、开口、数量或计算记录已变化，请修订后重新复核')
    return result,stamp
