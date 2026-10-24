"""Evidence-defined rectangular windows on rectangular exposed ledges."""
from decimal import Decimal
from .compound_takeoff import op
from .engine import DomainError
from .foundation_formwork import layers
from .foundation_void_formwork import dim
from .foundation_hole_end_formwork import details
from . import foundation_step_formwork as steps
from . import foundation_horizontal_windows as windows

VERSION = 'foundation-horizontal-formwork-1.5'
NOTICE = windows.NOTICE.replace('整体最顶面、最底面可选整面或有据XY矩形窗口。','整体最顶面、最底面及矩形台阶平台可选整面或有据XY矩形窗口。').replace('窗口可越过外轮廓，仅计窗口与真实端层截面的交集；','窗口可越过外轮廓；整体端面只计窗口与真实端层交集。矩形平台毛面积=(下层∩窗口)−(上层完整名义截面∩窗口)，孔扣=(孔∩窗口)−(孔∩上层∩窗口)，不把上层孔内的孔底混入平台；平台仅扣起Z<平台Z≤止Z的孔。').replace('台阶平台与孔底孔顶仍限既有整面，局部平台/孔端','圆形台阶平台与孔底孔顶仍限既有整面，局部圆平台/孔端')
ZERO = {'constant':'0'}
TWO = {'constant':'2'}


def upper_box(d, up, ua, bound_asts):
    """Keep the chosen bound's AST; empty rectangles never enter disk clipping."""
    values = windows.bounds(d); selected = {}; nodes = {}
    for i, (axis, side) in enumerate((('x','length'),('y','width'))):
        half = up[side]/2; half_ast = op('divide',ua[side],TWO)
        selected[axis+'_start'],nodes[axis+'_start'] = max([(values[2*i],bound_asts[axis+'_start']),(-half,op('subtract',ZERO,half_ast))],key=lambda v:v[0])
        selected[axis+'_end'],nodes[axis+'_end'] = min([(values[2*i+1],bound_asts[axis+'_end']),(half,half_ast)],key=lambda v:v[0])
    if any(selected[a+'_start']>=selected[a+'_end'] for a in ('x','y')): return None
    p = {}; a = {}
    for axis,side in (('x','length'),('y','width')):
        p[side] = selected[axis+'_end']-selected[axis+'_start']; p[axis] = (selected[axis+'_end']+selected[axis+'_start'])/2
        a[side] = op('subtract',nodes[axis+'_end'],nodes[axis+'_start']); a[axis] = op('divide',op('add',nodes[axis+'_end'],nodes[axis+'_start']),TWO)
    return p,a,p['length']*p['width'],op('multiply',a['length'],a['width'])


def geometry(d,item,tier,original,full_ast,holes,key,dimensions,refs,conversions):
    up,ua,_,_ = steps.section(item,layers(item)[d['level_index']])
    if 'diameter' in up or 'diameter' in tier['normalized_parameters']:
        raise DomainError('局部平台窗口当前仅支持矩形台阶，圆形平台须另核；可选既有整个平台')
    _,_,lower,lower_ast = steps.section(item,tier)
    raw_keys = ('hole_index','name','shape','parameters','unit','normalized_parameters','from_z','to_z')
    raw_holes = [{**{k:h[k] for k in raw_keys},'single_area':h['full_area'],'formula_ast':h['formula_asts']['full_area']} for h in holes]
    lower_cut,lower_node,clipped,extra,asts = windows.geometry(d,item,tier,lower,lower_ast,raw_holes,key,dimensions,refs,conversions)
    box = upper_box(d,up,ua,asts); covered,covered_node = box[2:] if box else (Decimal(0),ZERO)
    gross = lower_cut-covered
    if gross < 0: raise DomainError('平台窗口上层覆盖超过下层交集，请核对来源几何')
    for h in clipped:
        in_window = Decimal(h['single_area']); window_ast = h['formula_ast']; result = None
        if box:
            p,a,box_area,box_ast = box; hp = {k:Decimal(v) for k,v in h['normalized_parameters'].items()}; ha = {k:dim(item,{'index':h['hole_index'],'unit':h['unit']},k) for k in hp}
            relative = {**hp,**{axis:hp[axis]-p[axis] for axis in ('x','y')}}
            relative_asts = {**ha,**{axis:op('subtract',ha[axis],a[axis]) for axis in ('x','y')}}
            result = steps.intersection(relative,relative_asts,p,a,Decimal(h['full_area']),h['formula_asts']['full_area'],box_area,box_ast)
        overlap,node,branch = result[:3] if result else (Decimal(0),ZERO,'上层与窗口无正面积交集')
        net = in_window-overlap
        if net < 0: raise DomainError('平台窗口孔的共同覆盖超过窗口内孔交集，当前精度不能确认，请另核')
        window_curve = h.pop('curved_intersection',None)
        h.update(window_hole_area=str(in_window),covered_area=str(overlap),single_area=str(net),formula_ast=op('subtract',window_ast,node),
                 intersection_rule='（孔∩窗口）−（孔∩上层∩窗口）；'+branch,
                 formula_asts={**h['formula_asts'],'window_hole_area':window_ast,'covered_area':node,'single_area':op('subtract',window_ast,node)})
        if window_curve: h['window_hole_curved_intersection'] = window_curve
        if result and len(result)==4: h['curved_intersection'] = details(result[3],'上层与窗口共同覆盖')
    extra.update(full_face_area=str(original),window_lower_area=str(lower_cut),window_upper_area=str(covered),window_intersection_rule='（下层∩窗口）−（上层完整名义截面∩窗口）')
    asts.update(full_face_area=full_ast,window_lower_area=lower_node,window_upper_area=covered_node)
    return gross,op('subtract',lower_node,covered_node),clipped,extra,asts
