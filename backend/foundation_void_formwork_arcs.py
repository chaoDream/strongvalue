"""Circular void arcs share foundation XY axes, but each uses its own hole center."""
from . import foundation_formwork_arcs as arcs

VERSION='foundation-void-formwork-1.2'
NOTICE='仅计据原件选入的当前已审基础竖直孔壁。矩形孔可逐侧整宽或横向条带；圆孔可整周或局部圆弧，孔位和实际孔径来自已审来源，不使用基础直径。每条圆弧以所选孔自身圆心为角度原点，0°沿基础局部+X，从+Z俯视、+X向+Y逆时针为正；各孔圆心不同但轴向沿来源共同XY。同一来源基础项全部孔、层及跨记录圆弧须使用同一角度基准说明及原件并确认。只接受度（°），0≤起角<止角≤360°，跨0°拆分，不自动求余、反向或取小弧；0°与360°是同一周期边界。选弧展开宽=记录的固定π×实际孔径×弧角/360，乘孔与层共同范围内的实际接触高。圆孔与外界或其他孔点相切无正面积扣减，体积扣孔阈值不填实物理孔。矩形条带仍以基础中心为0，X侧沿Y、Y侧沿X，不能以孔中心为0；外界及相邻矩形孔仅扣选入横向和Z的真实无接触交集。继承全部最终基础个数一次，不再乘来源重复。同孔同层同侧，角度或横向与Z均正范围交叠禁止，整周/整宽和跨记录也检查；不同孔不因角度相同而重复。未选面不计，不推断支模工法；Decimal 80位，逐面及扣减不修约，仅总净面积按有据位数HALF_UP修约。孔底、水平面、斜孔、孔并集、任意空间接触、支撑措施及个体不同范围另核。材料、实际接触和修约须当前原件并独立复核，不与其他模板清单重复。'


def apply(d,item,h,key,dimensions,refs,full_width,full_ast,dim):
    width,node,data,asts=arcs.apply(d,key,dimensions,refs,full_width,full_ast)
    data.update(arc_center_x=h['normalized_parameters']['x'],arc_center_y=h['normalized_parameters']['y'],arc_diameter=h['normalized_parameters']['diameter'])
    asts.update(arc_center_x=dim(item,h,'x'),arc_center_y=dim(item,h,'y'),arc_diameter=dim(item,h,'diameter'))
    return width,node,data,asts


def checks(rows):
    # The shared outer-face helper has no hole identity; group before comparing.
    groups={}
    for d in rows:groups.setdefault((d['material_index'],d['hole_index']),[]).append(d)
    return sorted([c for group in groups.values() for c in arcs.checks(group)],key=lambda c:(c['right_index'],c['left_index']))
