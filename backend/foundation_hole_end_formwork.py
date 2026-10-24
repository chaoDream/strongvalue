"""Internal void end faces against the concrete on the opposite side of the void."""
from decimal import Decimal
from .compound_takeoff import op
from .engine import DomainError
from .foundation_formwork import layers
from .foundation_void_formwork import vertical, dim
from .foundation_voids import area
from . import foundation_step_formwork as steps

FACES = {'hole_bottom':'孔底水平整面', 'hole_top':'孔顶水平整面'}
VERSION = 'foundation-horizontal-formwork-1.3'
NOTICE = ('孔底取已审孔起Z及其下侧混凝土，孔顶取止Z及其上侧混凝土；只计基础内部的端面，通向基础外部的孔口没有孔底/孔顶模板。'
          '台阶交界按混凝土侧实际层截面与所选孔的交集计毛面积，不按整孔或整层代替；再扣该侧其他孔与所选孔的实际交集。'
          '相接孔在本面另一侧不产生混凝土接触；同侧各孔由来源验证分离或相切，逐项扣减不会重复。零接触保留零面积，不推断支模。'
          '孔位、截面、标高、层及全部基础个数只读继承，不受体积扣孔阈值影响。每个孔同一端面及跨记录不得重复，整个平台不混入孔底/孔顶。'
          '矩形交集逐轴裁切，圆圆及圆矩形局部交集使用200位Decimal解析计算后保留80位和记录固定π；数值界限不能确认则阻止计量。'
          '仅总净面积按有据位数HALF_UP修约，材料、真实接触及工法须原件和独立复核。未选面不计，不自动判断施工方法。'
          '水平面局部分区、斜孔/旋转孔、异形并集、任意空间接触、个体不同做法、支撑措施及专业计量例外仍须另核。')


def hole(item, index):
    if type(index) is not int:
        raise DomainError('孔底/孔顶须选择已审来源存在的整数孔序号')
    h = next((h for h in item.get('opening_holes',[]) if h['index']==index),None)
    if h is None: raise DomainError('所选孔不存在，不能新增未审孔洞')
    return h


def profile(item, h):
    p = {k:Decimal(v) for k,v in h['normalized_parameters'].items()}
    a = {k:dim(item,h,k) for k in p}
    v,n = area(h,p,a)
    return p,a,v,n


def details(value, target):
    """Name the second disk for its actual role, retaining the shared AST."""
    return {**value,'terms':[{**t,'name':t['name'].replace('上层圆弓形',target+'圆弓形')} for t in value['terms']]}


def geometry(item, face, index):
    if face not in FACES: raise DomainError('孔水平端面须选择孔底或孔顶')
    h = hole(item,index); lo,hi,za = vertical(h); bottom = face=='hole_bottom'; z = lo if bottom else hi
    # The concrete side is below a bottom, above a top. Exact inequalities choose
    # the correct tier at a ledge without moving the plane by a tolerance.
    tier = next((t for t in layers(item) if (Decimal(t['from_z'])<z<=Decimal(t['to_z']) if bottom else Decimal(t['from_z'])<=z<Decimal(t['to_z']))),None)
    if tier is None: raise DomainError('该孔端通向基础外部，没有基础内部孔底/孔顶混凝土接触面；整体外端面请另选')
    hp,ha,full,full_ast = profile(item,h)
    up,ua,uv,un = steps.section(item,tier) if 'foundation_levels' in item else single_section(item,tier)
    intersection = steps.intersection(hp,ha,up,ua,full,full_ast,uv,un)
    gross,gross_ast,branch = intersection[:3]
    cuts = []
    for other in item.get('opening_holes',[]):
        if other['index']==index: continue
        ol,oh,_ = vertical(other)
        if not (ol<z<=oh if bottom else ol<=z<oh): continue
        p,a,v,n = profile(item,other)
        # Other voids on the concrete side are contained in this tier by source
        # validation. Their pairwise positive areas are disjoint on this side.
        relative = dict(hp); relative_asts = dict(ha)
        for axis in ('x','y'):
            relative[axis] = hp[axis]-p[axis]
            relative_asts[axis] = op('subtract',ha[axis],a[axis])
        clipped = steps.intersection(relative,relative_asts,p,a,full,full_ast,v,n)
        cut,node,rule = clipped[:3]
        cuts.append(dict(hole_index=other['index'],name=other['name'],shape=other.get('shape','circular'),parameters=other['parameters'],unit=other['unit'],
                         normalized_parameters=other['normalized_parameters'],from_z=str(ol),to_z=str(oh),single_area=str(cut),full_area=str(v),
                         intersection_rule=rule.replace('上层覆盖','相接孔截面'),formula_ast=node,formula_asts=dict(single_area=node,full_area=n),
                         **({'curved_intersection':details(clipped[3],'相接孔')} if len(clipped)==4 else {})))
    if sum((Decimal(d['single_area']) for d in cuts),Decimal(0))>gross:
        raise DomainError('孔端相接孔扣减超过与混凝土侧层的交集，当前数值精度不能确认，请另核')
    extra = dict(hole_name=h['name'],hole_shape=h.get('shape','circular'),hole_parameters=h['parameters'],hole_unit=h['unit'],
                 hole_normalized_parameters=h['normalized_parameters'],hole_from_z=str(lo),hole_to_z=str(hi),single_full_hole_area=str(full),
                 hole_end_intersection_rule=branch.replace('上层覆盖','混凝土侧层截面'),concrete_side='below' if bottom else 'above',
                 **({'hole_end_curved_intersection':details(intersection[3],'混凝土侧层')} if len(intersection)==4 else {}))
    return tier,z,gross,gross_ast,cuts,extra,dict(single_full_hole_area=full_ast),za['z_start' if bottom else 'z_end']


def single_section(item,tier):
    from .measurement_units import LENGTH
    p = {k:Decimal(v) for k,v in tier['normalized_parameters'].items()}
    a = {k:op('multiply',{'dimension':'foundation.'+str(item['material_index'])+'.'+k},{'constant':LENGTH[item['length_unit']]}) for k in p}
    v,n = area({} if 'diameter' in p else {'shape':'rectangular'},p,a)
    return p,a,v,n
