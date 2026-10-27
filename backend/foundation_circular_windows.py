"""Source-bound disk windows; analytic integration and exact rational overlap."""
from fractions import Fraction
from .compound_takeoff import text, PI
from .engine import DomainError, precise
from .planar_takeoff import linear
from .curved_intersections import N, square, value
from .polygon_takeoff import edges, inside
from .profile_window_intersections import Scene, profile
from . import foundation_polygon_windows as polygon
from . import foundation_nested_windows as nested

VERSION='foundation-horizontal-formwork-1.13'
METHOD='circular_window_exact_distance'
FIELDS=('x','y','diameter')
RULE='圆形窗口作为真实圆轮廓参与全部直线及圆弧交点分段，逐段解析积分，平台扣上层覆盖、孔端取正确侧层，实际孔只扣共同交集；不以外包矩形、多边形逼近或网格代替圆。'
NOTICE=('水平顶底、台阶平台及内部孔端支持单个圆形XY窗口，逐项填写圆心X/Y、实际直径、单位及原件。'
        '圆心以基础中心为原点，与孔位共用正向；Z、真实基础截面和全部最终个数继承已审来源，不输入半径、面积或重复数量。'
        '圆窗可超出实体，只计真实接触，平台排除上层完整截面，孔端扣混凝土侧相接孔。'
        '200位解析积分保留80位及有序AST，全部有效分段无剩余才证明严格零接触，不以容差归零。'
        '同端面完整窗口精确有理数防重：圆圆比较圆心距平方与半径和平方，圆与直边净区检查圆心奇偶内外及逐边最短距离平方。'
        '正重叠或包含拒绝，外切或共享边界可分项；直边内孔残余空区可另列圆窗，嵌套岛区仍参与防重，跨记录同样检查。'
        '全部基础个数继承一次，仅总量有据修约；圆窗不创建实体孔、不改变混凝土或生成孔壁；实际支模、做法及修约须独立复核。'
        '本模式无窗口内孔、多圆并集、圆环、扇形、椭圆或任意曲边；斜面、个体不同做法及专业例外另核。')


def enabled(d):
    return isinstance(d,dict) and isinstance(d.get('plan_range'),dict) and d['plan_range'].get('shape')=='circular'


def parameters(c):
    return {k:linear(c['parameters'][k],c['unit'],k=='diameter') for k in FIELDS}


def validate(c):
    if not isinstance(c,dict) or set(c)!={'shape','parameters','evidence','unit','coordinate_basis','coordinate_evidence_id','scope_confirmed'} or not isinstance(c['parameters'],dict) or set(c['parameters'])!=set(FIELDS) or not isinstance(c['evidence'],dict) or set(c['evidence'])!=set(FIELDS):
        raise DomainError('圆形窗口须圆心X/Y、实际直径及逐参数原件、单位、共同XY基准和确认，不接受半径或手填面积')
    parameters(c)
    for eid in c['evidence'].values():text(eid,'圆形窗口圆心或直径原件',200)
    text(c['coordinate_basis'],'圆形窗口与孔位共用的XY基准');text(c['coordinate_evidence_id'],'圆形窗口XY基准原件',200)
    if c['scope_confirmed'] is not True:raise DomainError('须据原件确认圆形窗口圆心、实际直径及共同XY正向')


def bounds(d):
    p=parameters(d['plan_range']);x,y,r=p['x'],p['y'],p['diameter']/2
    return x-r,x+r,y-r,y+r


def rational(v):return str(v.numerator) if v.denominator==1 else str(v.numerator)+'/'+str(v.denominator)


@precise
def separation(a,b):
    """Distance is rational; strict inequalities distinguish tangency without tolerance."""
    side='left' if enabled(a) else 'right';circle,other=(a,b) if side=='left' else (b,a)
    c={k:Fraction(v) for k,v in parameters(circle['plan_range']).items()};x,y,r=c['x'],c['y'],c['diameter']/2
    common=dict(method=METHOD,circle_side=side,center_x=rational(x),center_y=rational(y),radius=rational(r),rational_unit='m',formula_asts={})
    if enabled(other):
        p={k:Fraction(v) for k,v in parameters(other['plan_range']).items()};R=p['diameter']/2;distance=(x-p['x'])**2+(y-p['y'])**2;limit=(r+R)**2
        if distance<limit:return dict(overlap=True)
        return dict(**common,overlap=False,state='boundary_touch' if distance==limit else 'separated',pair_kind='circle_circle',
                    other_x=rational(p['x']),other_y=rational(p['y']),other_radius=rational(R),distance_squared=rational(distance),limit_squared=rational(limit),edge_witnesses=[])
    rings=[[tuple(map(Fraction,p)) for p in ring] for ring in nested.ring_points(other)]
    states=[inside((x,y),ring) for ring in rings]
    # Center in any net region is sufficient. A center on a boundary is caught by distance=0 below.
    center_in_net=sum(s==1 for s in states)%2==1
    if center_in_net:return dict(overlap=True)
    witnesses=[];touching=False
    for ri,ring in enumerate(rings,1):
        for ei,((px,py),(qx,qy)) in enumerate(edges(ring),1):
            dx,dy=qx-px,qy-py;t=max(Fraction(0),min(Fraction(1),((x-px)*dx+(y-py)*dy)/(dx*dx+dy*dy)))
            X,Y=px+t*dx,py+t*dy;distance=(x-X)**2+(y-Y)**2
            if distance<r*r:return dict(overlap=True)
            touching|=distance==r*r
            witnesses.append(dict(ring_index=ri,edge_index=ei,x_from=rational(px),y_from=rational(py),x_to=rational(qx),y_to=rational(qy),
                                  segment_parameter=rational(t),nearest_x=rational(X),nearest_y=rational(Y),distance_squared=rational(distance),limit_squared=rational(r*r)))
    return dict(**common,overlap=False,state='boundary_touch' if touching else 'separated',pair_kind='circle_polygon',center_in_net=False,ring_center_states=states,edge_witnesses=witnesses)


class CircularScene(polygon.PolygonScene):
    def __init__(self,profiles,pts,bind,window):
        self.window=window;self.bind=bind
        box=[pts[0][0],pts[1][0],pts[0][1],pts[2][1]]
        Scene.__init__(self,profiles+[window],[n.v for n in box],dict(zip(('x_start','x_end','y_start','y_end'),(n.a for n in box))))
        self.cuts={v:bind(n) for v,n in self.cuts.items()}
        points=[self.cuts[v] for v in sorted(self.cuts)];self.intervals=list(zip(points,points[1:]))

    def vertical(self,profiles,x):
        return Scene.vertical(self,profiles+[self.window],x)

    def measure(self,*args,**kwargs):
        area,node,trace=super().measure(*args,**kwargs)
        trace.update(rule_version=VERSION,method='圆形窗口直线与圆弧分段解析积分',integration_rule=RULE)
        return area,node,trace


def geometry(*args):
    window=None;normalized=None
    def build(c,nodes):
        nonlocal window,normalized
        normalized=parameters(c);p={k:N(v,nodes[k]) for k,v in normalized.items()};x,y,r=p['x'],p['y'],p['diameter']/2
        window=profile(normalized,nodes,'圆形窗口')
        return [(x-r,y-r),(x+r,y-r),(x+r,y+r),(x-r,y+r)],N(PI)*square(r)
    gross,node,holes,extra,nodes=polygon.geometry(*args,parameter_keys=lambda _:FIELDS,window_builder=build,
        scene_type=lambda profiles,pts,bind:CircularScene(profiles,pts,bind,window))
    extra.pop('polygon_window');extra.pop('window_vertices')
    extra.update(circular_window=True,window_circle={k:str(v) for k,v in normalized.items()},window_intersection_rule=RULE)
    for h in holes:h['intersection_rule']=h['intersection_rule'].replace('凸多边形窗口','圆形窗口')
    return gross,node,holes,extra,nodes
