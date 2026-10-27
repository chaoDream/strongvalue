"""Whole exterior top/bottom contact faces, derived from reviewed foundation geometry."""
from decimal import Decimal, ROUND_HALF_UP
from fastapi import HTTPException
from . import store
from .compound_takeoff import PI, op, text
from .engine import DomainError, precise, quantity_number
from .measurement_units import LENGTH
from .post_foundation import calculate as foundation_quantity
from .foundation_formwork import source_component, layers
from .foundation_void_formwork import vertical, dim
from .foundation_voids import area
from .railing_material import snapshot
from .surface_takeoff import check_sources
from . import foundation_step_formwork as steps
from . import foundation_hole_end_formwork as ends
from . import foundation_horizontal_windows as windows
from . import foundation_step_windows as step_windows
from . import foundation_round_step_windows as round_windows
from . import foundation_hole_end_windows as end_windows
from . import foundation_polygon_windows as polygon_windows
from . import foundation_simple_windows as simple_windows
from . import foundation_perforated_windows as perforated_windows
from . import foundation_multi_windows as multi_windows
from . import foundation_nested_windows as nested_windows
from . import foundation_circular_windows as circular_windows
from . import foundation_annular_windows as annular_windows
from . import foundation_eccentric_windows as eccentric_windows
from . import foundation_circular_holes_windows as circular_holes_windows

VERSION = 'foundation-horizontal-formwork-1.0'
FACES = {'top': '整体最顶面', 'bottom': '整体最底面', 'step':'台阶中间整个平台', **ends.FACES}
NOTICE = '仅计据原件选入的当前已审基础整体最顶面或最底面整面实际接触模板，不把土面或全部外表面默认当模板。矩形及圆柱取对应端层真实截面，同心台阶顶面取最上层、底面取最下层，不将中间台阶水平面或内部层间面算入。Z从基础底0向上，标高和尺寸只读来自已审来源，不接受手填面积、Z、孔或数量。顶面仅扣起Z<顶面Z≤止Z的真实孔截面，底面仅扣起Z≤底面Z<止Z的真实孔截面；未到该端面的盲孔不扣，体积扣孔阈值不填实物理孔。相交端面内的孔已由来源验证包含、分离或相切，截面逐孔相加，不做投影重叠扣减。每项继承全部最终基础个数一次，不再乘来源重复，同来源同基础项同端面及跨记录不能重复。逐面及逐孔不修约，只对总净面积按有据位数HALF_UP修约。未选面不计；局部水平分区、台阶中间水平面、孔底/孔顶、斜面、任意空间接触、个体不同范围、支撑措施及专业计量例外另核，不与其他模板清单重复。材料、真实接触工法和修约须原件并独立复核。'


def geometry(item, face):
    tier = layers(item)[-1 if face == 'top' else 0]
    zkey = 'to_z' if face == 'top' else 'from_z'
    z = Decimal(tier[zkey]); p = {k: Decimal(v) for k, v in tier['normalized_parameters'].items()}
    key = 'foundation.'+str(item['material_index'])
    if 'foundation_levels' in item: key += '.levels.'+str(tier['index'])
    asts = {k: op('multiply', {'dimension': key+'.'+k}, {'constant': LENGTH[item['length_unit']]}) for k in p}
    gross, node = area({}, p, asts) if 'diameter' in p else area({'shape':'rectangular'}, p, asts)
    holes = []
    for h in item.get('opening_holes', []):
        lo, hi, _ = vertical(h)
        if not (lo < z <= hi if face == 'top' else lo <= z < hi): continue
        hp = {k: Decimal(v) for k, v in h['normalized_parameters'].items()}
        cut, cut_ast = area(h, hp, {k: dim(item, h, k) for k in hp})
        holes.append(dict(hole_index=h['index'], name=h['name'], shape=h.get('shape','circular'),
                          parameters=h['parameters'], unit=h['unit'], normalized_parameters=h['normalized_parameters'],
                          from_z=str(lo), to_z=str(hi), single_area=str(cut), formula_ast=cut_ast))
    return tier, z, gross, node, holes


@precise
def candidates(p, cid):
    s = source_component(p, cid); r = foundation_quantity(s)
    items = []
    for item in r['foundation_items']:
        faces = []
        for face in ('top','bottom'):
            tier, z, gross, _, holes = geometry(item, face)
            faces.append(dict(face=face, label=FACES[face], level_index=tier['index'], z=str(z),
                              normalized_parameters=tier['normalized_parameters'], single_gross_area=str(gross),
                              single_opening_area=str(sum((Decimal(h['single_area']) for h in holes), Decimal(0))), apertures=holes))
        for level in range(1,len(layers(item))):
            face = dict(face='step',label=FACES['step'],level_index=level,z=layers(item)[level-1]['to_z'])
            try:
                tier,z,gross,_,holes,extra,_ = steps.geometry(item,level)
                face.update(normalized_parameters=tier['normalized_parameters'],single_gross_area=str(gross),single_opening_area=str(sum((Decimal(h['single_area']) for h in holes),Decimal(0))),apertures=holes,available=True,**extra)
            except DomainError as exc:
                face.update(available=False,reason=str(exc))
            faces.append(face)
        for h in item.get('opening_holes',[]):
            for face in ends.FACES:
                candidate = dict(face=face,label=FACES[face],hole_index=h['index'],hole_name=h['name'],z=str(vertical(h)[0 if face=='hole_bottom' else 1]))
                try:
                    tier,z,gross,_,holes,extra,_,_ = ends.geometry(item,face,h['index'])
                    candidate.update(level_index=tier['index'],normalized_parameters=tier['normalized_parameters'],single_gross_area=str(gross),
                                     single_opening_area=str(sum((Decimal(h['single_area']) for h in holes),Decimal(0))),apertures=holes,available=True,**extra)
                except DomainError as exc: candidate.update(available=False,reason=str(exc))
                faces.append(candidate)
        items.append(dict(material_index=item['material_index'], material_name=item['material_name'], total_count=item['total_count'], faces=faces))
    return dict(source_component_id=s['id'], source_component_hash=store.digest(snapshot(s)), source_name=s['name'],
                source_revision=s['revision'], foundation_items=items, notice=ends.NOTICE if any(f['face'] in ends.FACES for d in items for f in d['faces']) else steps.NOTICE if any(f['face']=='step' for d in items for f in d['faces']) else NOTICE)


def claim_key(d):
    return (d['material_index'],d['face'],d.get('hole_index') if d['face'] in ends.FACES else d.get('level_index') if d['face']=='step' else None)

def check_claims(p, comp):
    for other in p['components']:
        if other['kind'] != 'foundation_horizontal_formwork' or other['id'] == comp.get('id') or other['source_component_id'] != comp['source_component_id']: continue
        windows.check_frames(comp['contact_ranges'],other['contact_ranges'],conflict=True)
        if any(claim_key(a) == claim_key(b) and windows.overlaps(a,b) for a in comp['contact_ranges'] for b in other['contact_ranges']):
            raise HTTPException(409, '水平模板整端面与记录“'+other['name']+'”重复，请修订原记录或选不同端面')


@precise
def calculate(comp, source=None):
    source = source if source is not None else comp['source_geometry']
    if comp.get('parameters') != {} or comp.get('quantity_type') != 'area' or comp.get('scope_confirmed') is not True:
        raise DomainError('水平模板须为空顶层参数、面积类型，并确认整端面实际接触与不重复计量')
    if 'multiplicity' in comp: raise DomainError('水平模板继承全部基础个数，不能再填重复数量')
    if source.get('kind') != 'post_foundation': raise DomainError('水平模板只能引用已审立柱基础')
    if comp.get('source_component_id') != source['id'] or comp.get('source_component_hash') != store.digest(snapshot(source)):
        raise HTTPException(409, '基础来源已修订，请重新读取端面、孔洞及全部个数')
    for k, label in [('name','水平模板名称'),('material','水平模板材料与做法'),('measurement_basis','真实整端面接触及不重复计量说明')]: text(comp.get(k), label, 200 if k == 'name' else 2000)
    digits = comp.get('rounding_digits')
    if type(digits) is not int or not 0 <= digits <= 12: raise DomainError('水平模板总净面积修约位数须为0至12整数')
    selected = comp.get('contact_ranges')
    if not isinstance(selected, list) or not 1 <= len(selected) <= 200: raise DomainError('须选择1至200项基础水平整面，未选面不计')
    r = foundation_quantity(source); items = {d['material_index']:d for d in r['foundation_items']}
    dimensions = dict(r['dimensions']); refs = dict(r['source_evidence']); rows = []; conversions = []; names = set(); claims = set(); nodes = []; gross = Decimal(0); cut = Decimal(0)
    for k in ('basis','material','rounding'): refs['horizontal_formwork.'+k] = text(comp.get(k+'_evidence_id'), '水平模板原件', 200)
    for i, d in enumerate(selected, 1):
        if not isinstance(d, dict) or set(d)-{'plan_range'} != {'name','material_index','face','measurement_basis','evidence'} | ({'level_index'} if d.get('face') == 'step' else {'hole_index'} if isinstance(d.get('face'),str) and d['face'] in ends.FACES else set()):
            raise DomainError('水平模板范围仅含名称、基础项、水平面、实际接触说明及原件；平台另选下层序号，孔底/孔顶另选孔序号，尺寸/标高/数量均来自已审来源')
        if type(d['material_index']) is not int or d['material_index'] not in items: raise DomainError('水平模板基础项须为已审来源存在的整数序号')
        if not isinstance(d['face'], str) or d['face'] not in FACES: raise DomainError('水平模板须选择整体顶底、台阶整个平台或指定已审孔的内部孔底/孔顶')
        name = text(d['name'], '整端面范围名称', 200)
        if name in names: raise DomainError('水平模板范围名称不能重复')
        names.add(name)
        item = items[d['material_index']]
        if windows.enabled(d):
            windows.validate_config(d['plan_range']); windows.check_frames([d],selected[:i-1])
        if d['face'] in ends.FACES: ends.hole(item,d['hole_index'])
        if d['face']=='step' and (type(d['level_index']) is not int or not 1 <= d['level_index'] < len(layers(item))):
            raise DomainError('台阶平台须选择实际存在且有紧邻上层的整数下层序号')
        claim = claim_key(d)
        if any(claim_key(a)==claim and windows.overlaps(a,d) for a in selected[:i-1]): raise DomainError('同基础项同端面整面或XY窗口范围正重叠，不能重复计量')
        claims.add(claim)
        if not isinstance(d['evidence'], dict) or set(d['evidence']) != {'measurement_basis'}: raise DomainError('每项整端面实际接触说明须单独关联原件')
        text(d['measurement_basis'], '本整端面实际接触及孔扣减说明')
        refs['horizontal_formwork.'+str(i)+'.measurement_basis'] = text(d['evidence']['measurement_basis'], '整端面原件', 200)
        extra = {}; extra_asts = {}; z_ast = None
        if d['face'] in ends.FACES:
            tier,z,single_gross,gross_ast,holes,extra,extra_asts,z_ast = ends.geometry(item,d['face'],d['hole_index'])
        elif d['face']=='step':
            tier,z,single_gross,gross_ast,holes,extra,extra_asts = steps.geometry(item,d['level_index'])
        else:
            tier,z,single_gross,gross_ast,holes = geometry(item,d['face'])
        if windows.enabled(d):
            window_fn = circular_holes_windows.geometry if circular_holes_windows.enabled(d) else eccentric_windows.geometry if eccentric_windows.enabled(d) else annular_windows.geometry if annular_windows.enabled(d) else circular_windows.geometry if circular_windows.enabled(d) else nested_windows.geometry if nested_windows.enabled(d) else multi_windows.geometry if multi_windows.enabled(d) else perforated_windows.geometry if perforated_windows.enabled(d) else simple_windows.geometry if simple_windows.enabled(d) else polygon_windows.geometry if polygon_windows.enabled(d) else end_windows.geometry if d['face'] in ends.FACES else ((round_windows.geometry if 'diameter' in tier['normalized_parameters'] else step_windows.geometry) if d['face']=='step' else windows.geometry)
            single_gross,gross_ast,holes,window_extra,window_asts = window_fn(d,item,tier,single_gross,gross_ast,holes,'horizontal_formwork.'+str(i),dimensions,refs,conversions)
            extra.update(window_extra); extra_asts.update(window_asts)
        single_cut = sum((Decimal(h['single_area']) for h in holes), Decimal(0)); single_net = single_gross-single_cut
        if single_net < 0: raise DomainError('水平端面实际孔面积超过毛面积，请核对已审几何')
        count = Decimal(item['total_count']); count_ast = item['formula_asts']['total_count']; cut_ast = op('add', *[h['formula_ast'] for h in holes]); net_ast = op('subtract', gross_ast, cut_ast); total_ast = op('multiply', net_ast, count_ast)
        gross += single_gross*count; cut += single_cut*count; nodes.append(total_ast)
        rows.append({**d, 'index':i, 'material_name':item['material_name'], 'face_label':FACES[d['face']], 'level_index':tier['index'], 'z':str(z),
                     'normalized_parameters':tier['normalized_parameters'], 'total_count':str(count), 'single_gross_area':str(single_gross),
                     'single_opening_area':str(single_cut), 'single_net_area':str(single_net), 'total_gross_area':str(single_gross*count),
                     'total_opening_area':str(single_cut*count), 'total_net_area':str(single_net*count), 'apertures':holes,
                     **extra, 'formula_asts':dict(**extra_asts, z=z_ast if z_ast is not None else tier['formula_asts']['from_z' if d['face']=='bottom' else 'to_z'], total_count=count_ast,
                                         single_gross_area=gross_ast, single_opening_area=cut_ast, single_net_area=net_ast,
                                         total_gross_area=op('multiply',gross_ast,count_ast),total_opening_area=op('multiply',cut_ast,count_ast),total_net_area=total_ast)})
    quantity_number(gross); quantity_number(cut); net = quantity_number(gross-cut); q = quantity_number(net.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP))
    has_pi = any(d.get('circular_holes_window') or d.get('eccentric_circular_window') or d.get('annular_window') or d.get('circular_window') or 'diameter' in d['normalized_parameters'] or d.get('hole_shape')=='circular' or any(h['shape']=='circular' for h in d['apertures']) for d in rows)
    has_step = any(d['face']=='step' for d in rows)
    has_curve = any(h.get('curved_intersection') for d in rows for h in d['apertures'])
    has_end = any(d['face'] in ends.FACES for d in rows)
    has_window = any(windows.enabled(d) for d in rows)
    has_step_window = any(windows.enabled(d) and d['face']=='step' for d in rows)
    has_round_window = any(d.get('round_step_window') for d in rows)
    has_end_window = any(d.get('hole_end_window') for d in rows)
    has_polygon = any(d.get('polygon_window') for d in rows)
    has_simple = any(d.get('simple_polygon_window') for d in rows)
    has_perforated = any(d.get('perforated_polygon_window') for d in rows)
    has_multi = any(d.get('multi_polygon_window') for d in rows)
    has_nested = any(d.get('nested_polygon_window') for d in rows)
    has_circular = any(d.get('circular_window') for d in rows)
    has_annular = any(d.get('annular_window') for d in rows)
    has_eccentric = any(d.get('eccentric_circular_window') for d in rows)
    has_circular_holes = any(d.get('circular_holes_window') for d in rows)
    return dict(quantity=str(q), gross=str(gross), deduction=str(cut), unit='m²', parameters={}, material=comp['material'],
                formula=('Σ[(所选孔∩混凝土侧层∩窗口−所选孔∩同侧相接孔∩窗口)×全部基础个数；及其他已选水平面]；仅总净面积HALF_UP修约' if has_end_window else 'Σ[(所选整面或窗口内真实外露面积−本范围内实际孔交集)×全部基础个数]；平台窗口扣上层共同覆盖；仅总净面积HALF_UP修约' if has_step_window else 'Σ[(已选整面或XY窗口与真实端层交集−本范围内实际孔交集)×全部基础个数]；仅总净面积HALF_UP修约' if has_window else 'Σ[(所选水平面实际毛面积−实际孔交集)×全部基础个数]；孔端毛面积=所选孔与混凝土侧层交集，扣该侧相接孔交集；仅总净面积HALF_UP修约' if has_end else 'Σ[(已选水平面毛面积−该面实际孔交集)×已审全部基础个数]；平台毛面积=下层截面−上层完整截面；仅总净面积HALF_UP修约' if has_step else 'Σ[(已选整体顶面或底面真实截面−贯通该端面的实际孔截面)×已审全部基础个数]；仅总净面积HALF_UP修约')+str(digits)+'位',
                formula_ast=dict(op='round',args=[op('add',*nodes)],digits=digits,mode='HALF_UP'), dimensions=dimensions, source_evidence=refs,
                unit_conversions=conversions, source_unit_conversions=r['unit_conversions'], source_geometry=snapshot(source), source_component_id=source['id'],
                source_component_hash=comp['source_component_hash'], contact_ranges=rows,
                horizontal_formwork_summary=dict(source_name=source['name'],source_revision=source['revision'],range_count=len(rows),unrounded_quantity=str(net),rounding_digits=digits,rounding_adjustment=str(q-net)),
                **({'horizontal_formwork_pi_constant':PI} if has_pi else {}),
                rounding_rule='Decimal 80位及记录的固定π，沿用来源未修约尺寸和全部最终基础个数；逐面逐孔不修约，仅总净面积HALF_UP修约',rule_version=circular_holes_windows.VERSION if has_circular_holes else eccentric_windows.VERSION if has_eccentric else annular_windows.VERSION if has_annular else circular_windows.VERSION if has_circular else nested_windows.VERSION if has_nested else multi_windows.VERSION if has_multi else perforated_windows.VERSION if has_perforated else simple_windows.VERSION if has_simple else polygon_windows.VERSION if has_polygon else end_windows.VERSION if has_end_window else round_windows.VERSION if has_round_window else step_windows.VERSION if has_step_window else windows.VERSION if has_window else ends.VERSION if has_end else 'foundation-horizontal-formwork-1.2' if has_curve else 'foundation-horizontal-formwork-1.1' if has_step else VERSION,notice=circular_holes_windows.NOTICE if has_circular_holes else eccentric_windows.NOTICE if has_eccentric else annular_windows.NOTICE if has_annular else circular_windows.NOTICE if has_circular else nested_windows.NOTICE if has_nested else multi_windows.NOTICE if has_multi else perforated_windows.NOTICE if has_perforated else simple_windows.NOTICE if has_simple else polygon_windows.NOTICE if has_polygon else end_windows.NOTICE if has_end_window else round_windows.NOTICE if has_round_window else step_windows.NOTICE if has_step_window else windows.NOTICE if has_window else ends.NOTICE if has_end else steps.CURVED_NOTICE if has_curve else steps.NOTICE if has_step else NOTICE,**({'horizontal_window_checks':windows.checks(rows)} if has_window else {}))


def validate(p, comp, saved=False):
    s = source_component(p, comp.get('source_component_id')); r = calculate(comp, s); check_claims(p, comp); proofs = check_sources(p, r['source_evidence'])
    stamp = store.digest(dict(source_component_hash=comp['source_component_hash'],sources=proofs))
    if saved and (comp.get('source_stamp') != stamp or comp.get('evidence') != r['source_evidence'] or comp.get('preview') != r or comp.get('source_geometry') != snapshot(s)):
        raise HTTPException(409, '水平端面来源、实际孔扣减或计算已变化，请修订并重新复核')
    return r, stamp
