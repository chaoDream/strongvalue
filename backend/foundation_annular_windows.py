"""Evidence-bound concentric annuli; true arc integration and rational radial proofs."""
from fractions import Fraction
from .compound_takeoff import text, PI
from .engine import DomainError, precise
from .planar_takeoff import linear
from .curved_intersections import N, square, ast, value
from .polygon_takeoff import edges, inside
from .profile_window_intersections import profile
from . import foundation_polygon_windows as polygon
from . import foundation_circular_windows as circular
from . import foundation_multi_windows as multi
from . import foundation_perforated_windows as perforated
from . import foundation_nested_windows as nested

VERSION='foundation-horizontal-formwork-1.14'
METHOD='annular_window_exact_radial_distance'
FIELDS=('x','y','outer_diameter','inner_diameter')
RULE='同心圆环按外圆减窗口内圆W形成支模净区，真实圆弧参与全部边界事件分段；逐段解析积分裁切实际水平接触及实体孔，不以外包矩形或多边形逼近代替圆环，不填实内圆。'
NOTICE=('水平顶底、台阶平台及内部孔端支持单个同心圆环XY窗口，逐项填写圆心XY、实际外径及内径、共同单位和原件，严格0<内径<外径。'
        '窗口内圆W须有不支模说明及原件，只排除本窗口范围，不创建基础实体孔H、不扣混凝土或生成孔壁／孔端。'
        '共同XY正向与已审孔位一致；Z、截面及全部最终基础个数继承来源。实际孔仅扣环带内交集，平台先排除上层完整覆盖，孔端取正确混凝土侧层。'
        '200位真实圆弧解析积分保留80位及有序AST；全部外圆候选片段扣窗口内圆、上层及实际孔后无剩余才证明严格零接触，不用容差。'
        '同端面完整窗口以精确有理数径向距离防重：圆／圆环按外分离或落在对方内圆判定；直边每个连通净块以最小及最大圆心距平方判定。'
        '正重叠拒绝，内外切可分项；内圆未用空区可另项，岛区同样检查，跨记录不按实体外或物理孔投影豁免。'
        '全部个数继承一次，仅总净量有据修约；原件、支模、材料及修约须独立复核。偏心内圆、扇形、椭圆、多圆并集、任意曲边及曲边嵌套、斜面或个体不同做法另核。')


def enabled(d):
    return isinstance(d,dict) and isinstance(d.get('plan_range'),dict) and d['plan_range'].get('shape')=='annular'


def parameters(c):
    return {k:linear(c['parameters'][k],c['unit'],k in ('outer_diameter','inner_diameter')) for k in FIELDS}


def validate(c):
    fields={'shape','parameters','evidence','unit','coordinate_basis','coordinate_evidence_id','scope_confirmed','exclusion_basis','exclusion_evidence_id'}
    if not isinstance(c,dict) or set(c)!=fields or not isinstance(c['parameters'],dict) or set(c['parameters'])!=set(FIELDS) or not isinstance(c['evidence'],dict) or set(c['evidence'])!=set(FIELDS):
        raise DomainError('同心圆环须圆心XY、实际外径／内径及逐项原件、共同单位和XY基准、内圆不支模说明及原件、确认；不接受偏心内圆、半径或面积')
    p=parameters(c)
    if not p['inner_diameter']<p['outer_diameter']:raise DomainError('圆环须严格0<实际内径<实际外径；无内圆请用圆形窗口')
    for eid in c['evidence'].values():text(eid,'圆环圆心或内外径原件',200)
    for key,label in [('coordinate_basis','圆环与孔位共用的XY基准'),('coordinate_evidence_id','圆环XY基准原件'),('exclusion_basis','内圆W不支模范围说明'),('exclusion_evidence_id','内圆W不支模原件')]:text(c[key],label,200 if key.endswith('_id') else 2000)
    if c['scope_confirmed'] is not True:raise DomainError('须据原件确认圆环圆心、实际内外径、内圆排除及共同XY正向')


def bounds(d):
    p=parameters(d['plan_range']);x,y,r=p['x'],p['y'],p['outer_diameter']/2
    return x-r,x+r,y-r,y+r


def radial(d):
    p=parameters(d['plan_range']) if enabled(d) else circular.parameters(d['plan_range'])
    p={k:Fraction(v) for k,v in p.items()}
    return p['x'],p['y'],p['outer_diameter']/2 if enabled(d) else p['diameter']/2,p['inner_diameter']/2 if enabled(d) else Fraction(0)


def components(d):
    if multi.enabled(d) or nested.enabled(d):
        c=d['plan_range'];return [perforated.ring_points(dict(plan_range=multi.part_config(c,p))) for p in c['parts']]
    return [nested.ring_points(d)]


@precise
def separation(a,b):
    rat=circular.rational;side='left' if enabled(a) else 'right';annulus,other=(a,b) if side=='left' else (b,a)
    x,y,R,r=radial(annulus)
    common=dict(method=METHOD,annulus_side=side,center_x=rat(x),center_y=rat(y),outer_radius=rat(R),inner_radius=rat(r),rational_unit='m',formula_asts={})
    if enabled(other) or circular.enabled(other):
        X,Y,S,s=radial(other);distance=(x-X)**2+(y-Y)**2
        if distance>=(R+S)**2:branch='outer_separation';limit=(R+S)**2
        elif r>=S and distance<=(r-S)**2:branch='other_in_inner';limit=(r-S)**2
        elif s>=R and distance<=(s-R)**2:branch='self_in_other_inner';limit=(s-R)**2
        else:return dict(overlap=True)
        return dict(**common,overlap=False,state='boundary_touch' if distance==limit else 'separated',pair_kind='radial',branch=branch,
                    other_x=rat(X),other_y=rat(Y),other_outer_radius=rat(S),other_inner_radius=rat(s),distance_squared=rat(distance),limit_squared=rat(limit),components=[])
    proofs=[];touching=False
    for bi,raw in enumerate(components(other),1):
        rings=[[tuple(map(Fraction,p)) for p in ring] for ring in raw];states=[inside((x,y),ring) for ring in rings]
        in_net=sum(v==1 for v in states)%2==1;witnesses=[]
        for ri,ring in enumerate(rings):
            for ei,((px,py),(qx,qy)) in enumerate(edges(ring),1):
                dx,dy=qx-px,qy-py;t=max(Fraction(0),min(Fraction(1),((x-px)*dx+(y-py)*dy)/(dx*dx+dy*dy)))
                X,Y=px+t*dx,py+t*dy;distance=(x-X)**2+(y-Y)**2
                witnesses.append(dict(ring_index=ri,edge_index=ei,x_from=rat(px),y_from=rat(py),x_to=rat(qx),y_to=rat(qy),segment_parameter=rat(t),nearest_x=rat(X),nearest_y=rat(Y),distance_squared=rat(distance)))
        minimum=Fraction(0) if in_net else min(Fraction(w['distance_squared']) for w in witnesses)
        vertex=max(rings[0],key=lambda p:(p[0]-x)**2+(p[1]-y)**2);maximum=(vertex[0]-x)**2+(vertex[1]-y)**2
        if minimum<R*R and maximum>r*r:return dict(overlap=True)
        branch='outside_outer' if minimum>=R*R else 'inside_inner'
        touching|=minimum==R*R or maximum==r*r
        proofs.append(dict(block_index=bi,center_in_net=in_net,ring_center_states=states,minimum_squared=rat(minimum),maximum_squared=rat(maximum),
                           farthest_x=rat(vertex[0]),farthest_y=rat(vertex[1]),outer_limit_squared=rat(R*R),inner_limit_squared=rat(r*r),branch=branch,edge_witnesses=witnesses))
    return dict(**common,overlap=False,state='boundary_touch' if touching else 'separated',pair_kind='polygon',components=proofs)


class AnnularScene(circular.CircularScene):
    def __init__(self,profiles,pts,bind,outer,inner):
        self.inner=inner
        super().__init__(profiles+[inner],pts,bind,outer)

    def measure(self,include,exclude=(),certify=False):
        area,node,trace=super().measure(include,list(exclude)+[self.inner],certify=certify)
        trace.update(rule_version=VERSION,method='同心圆环窗口直线与圆弧分段解析积分',integration_rule=RULE)
        return area,node,trace


def geometry(*args):
    outer=None;inner=None;normalized=None;outer_area=None;inner_area=None
    def build(c,nodes):
        nonlocal outer,inner,normalized,outer_area,inner_area
        normalized=parameters(c);p={k:N(v,nodes[k]) for k,v in normalized.items()};x,y,R,r=p['x'],p['y'],p['outer_diameter']/2,p['inner_diameter']/2
        def disk(key,label):return profile({k:normalized[k] for k in ('x','y')}|{'diameter':normalized[key]},{k:nodes[k] for k in ('x','y')}|{'diameter':nodes[key]},label)
        outer=disk('outer_diameter','圆环外圆');inner=disk('inner_diameter','窗口内圆W');outer_area=N(PI)*square(R);inner_area=N(PI)*square(r)
        return [(x-R,y-R),(x+R,y-R),(x+R,y+R),(x-R,y+R)],N(PI)*(R-r)*(R+r)
    c=args[0]['plan_range'];args[8][args[6]+'.window_inner_basis']=c['exclusion_evidence_id']
    gross,node,holes,extra,nodes=polygon.geometry(*args,parameter_keys=lambda _:FIELDS,window_builder=build,
        scene_type=lambda profiles,pts,bind:AnnularScene(profiles,pts,bind,outer,inner))
    extra.pop('polygon_window');extra.pop('window_vertices')
    extra.update(annular_window=True,window_annulus={k:str(v) for k,v in normalized.items()},window_outer_area=str(value(outer_area)),window_exclusion_area=str(value(inner_area)),window_intersection_rule=RULE)
    nodes.update(window_outer_area=ast(outer_area),window_exclusion_area=ast(inner_area))
    for h in holes:h['intersection_rule']=h['intersection_rule'].replace('凸多边形窗口','圆环窗口净区')
    if 'polygon_zero_contact' in extra:extra['polygon_zero_contact']['method']='全部外圆候选接触边界事件分段扣窗口内圆W、上层及实际孔后无剩余，按环带毛面积减其他实际孔扣的分割恒等式计量；非容差归零'
    return gross,node,holes,extra,nodes
