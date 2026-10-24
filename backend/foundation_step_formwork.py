"""Reviewed exposed ledges with rectangular and analytic curved intersections."""
from decimal import Decimal
from .compound_takeoff import op
from .engine import DomainError
from .measurement_units import LENGTH
from .foundation_formwork import layers
from .foundation_void_formwork import vertical, dim
from .foundation_voids import area
from . import curved_intersections as curved

ZERO = {'constant':'0'}
TWO = {'constant':'2'}
NOTICE = '水平模板可选整体顶底整面及同心紧接台阶的整个平台；逐项须据原件确认实际接触，不默认土面支模。平台标高取下层顶Z，毛面积为下层截面减紧邻上层完整名义截面；上层孔内的孔底不混入平台。平台孔仅取起Z<平台Z≤止Z，并扣孔截面减其与上层完整截面的交集；从平台起孔不扣下层，从平台止孔按实际外露部分扣减。矩形交集按共同XY精确裁切；涉及圆形仅支持完全包含、分离或相切，曲线边界局部相交须另核并阻止计量，不近似或漏扣。体积阈值不填实物理孔。尺寸、标高、孔及全部基础个数来自当前已审来源，数量继承一次；同基础项同平台跨记录不得重复。逐面逐孔不修约，仅总净面积按有据位数HALF_UP修约。整体顶底仍按真实端层及贯通孔截面计算。未选面不计；局部水平分区、孔底/孔顶、斜面、任意空间接触、个体不同做法、支撑措施及专业规则另核；材料、施工做法、范围及修约须原件并独立复核。'


CURVED_NOTICE = NOTICE.replace('涉及圆形仅支持完全包含、分离或相切，曲线边界局部相交须另核并阻止计量，不近似或漏扣','圆圆局部相交按双弓形、圆矩形局部相交按四象限解析裁切；工作精度200位后保留80位，采用记录固定π与十进制反角级数，不按网格或几何容差吸附边界，数值界限不能确认则阻止计量')

def section(item, tier):
    p = {k:Decimal(v) for k,v in tier['normalized_parameters'].items()}
    prefix = 'foundation.'+str(item['material_index'])+'.levels.'+str(tier['index'])
    a = {k:op('multiply',{'dimension':prefix+'.'+k},{'constant':LENGTH[item['length_unit']]}) for k in p}
    v, node = area({} if 'diameter' in p else {'shape':'rectangular'}, p, a)
    return p, a, v, node


def intersection(hp, ha, up, ua, hole_area, hole_ast, upper_area, upper_ast):
    """Return intersection with upper footprint, its source AST and exact branch."""
    hc, uc = 'diameter' in hp, 'diameter' in up
    if not hc and not uc:
        widths = []; nodes = []
        for axis, side in [('x','length'),('y','width')]:
            half_h = hp[side]/2; half_u = up[side]/2
            hn = op('divide',ha[side],TWO); un = op('divide',ua[side],TWO)
            lefts = [(hp[axis]-half_h,op('subtract',ha[axis],hn)),(-half_u,op('subtract',ZERO,un))]
            rights = [(hp[axis]+half_h,op('add',ha[axis],hn)),(half_u,un)]
            lo, ln = max(lefts,key=lambda v:v[0]); hi, rn = min(rights,key=lambda v:v[0])
            widths.append(max(Decimal(0),hi-lo)); nodes.append(op('subtract',rn,ln) if hi>lo else ZERO)
        return widths[0]*widths[1], op('multiply',*nodes), '矩形逐轴精确裁切'
    if hc and uc:
        r, R = hp['diameter']/2, up['diameter']/2; d2 = hp['x']**2+hp['y']**2
        if d2 >= (r+R)**2: return Decimal(0), ZERO, '圆形分离或外切'
        if R>=r and d2 <= (R-r)**2: return hole_area, hole_ast, '孔完全位于上层覆盖内（含内切）'
        if r>=R and d2 <= (r-R)**2: return upper_area, upper_ast, '孔完全包含上层覆盖（含内切）'
    else:
        rect, disk = (up,hp) if hc else (hp,up)
        dx, dy = abs(hp['x']),abs(hp['y']); a,b = rect['length']/2,rect['width']/2; r = disk['diameter']/2
        if max(dx-a,Decimal(0))**2+max(dy-b,Decimal(0))**2 >= r*r:
            return Decimal(0), ZERO, '圆矩形分离或外切'
        if (dx+a)**2+(dy+b)**2 <= r*r:
            return (upper_area,upper_ast,'孔完全包含上层覆盖（含内切）') if hc else (hole_area,hole_ast,'孔完全位于上层覆盖内（含内切）')
        if dx+r <= a and dy+r <= b:
            return (hole_area,hole_ast,'孔完全位于上层覆盖内（含内切）') if hc else (upper_area,upper_ast,'孔完全包含上层覆盖（含内切）')
    return curved.calculate(hp,ha,up,ua,hole_area,upper_area)


def geometry(item, level_index):
    tiers = layers(item)
    if type(level_index) is not int or not 1 <= level_index < len(tiers):
        raise DomainError('台阶平台须选择实际存在且有紧邻上层的整数下层序号')
    lower, upper = tiers[level_index-1:level_index+1]
    lp, la, lv, ln = section(item,lower); up, ua, uv, un = section(item,upper)
    z = Decimal(lower['to_z']); gross = lv-uv; node = op('subtract',ln,un); holes = []
    for h in item.get('opening_holes',[]):
        lo, hi, _ = vertical(h)
        if not lo < z <= hi: continue
        hp = {k:Decimal(v) for k,v in h['normalized_parameters'].items()}; ha = {k:dim(item,h,k) for k in hp}
        full, full_ast = area(h,hp,ha)
        intersection_result = intersection(hp,ha,up,ua,full,full_ast,uv,un)
        covered, covered_ast, branch = intersection_result[:3]
        curved_details = {'curved_intersection':intersection_result[3]} if len(intersection_result)==4 else {}
        cut = full-covered
        if cut < 0: raise DomainError('平台孔交集超过孔面积，请核对来源几何')
        holes.append(dict(hole_index=h['index'],name=h['name'],shape=h.get('shape','circular'),parameters=h['parameters'],unit=h['unit'],normalized_parameters=h['normalized_parameters'],
                          from_z=str(lo),to_z=str(hi),single_area=str(cut),formula_ast=op('subtract',full_ast,covered_ast),
                          full_area=str(full),covered_area=str(covered),intersection_rule=branch,
                          formula_asts=dict(full_area=full_ast,covered_area=covered_ast),**curved_details))
    extra = dict(upper_level_index=upper['index'],upper_normalized_parameters=upper['normalized_parameters'],single_lower_area=str(lv),single_upper_area=str(uv))
    return lower,z,gross,node,holes,extra,dict(single_lower_area=ln,single_upper_area=un)
