"""One disk minus a strictly internal disk; exact containment and overlap proofs."""
from fractions import Fraction
from .compound_takeoff import text, PI
from .engine import DomainError, precise
from .planar_takeoff import linear
from .curved_intersections import N, square, ast, value
from .polygon_takeoff import edges, inside
from .profile_window_intersections import profile
from . import foundation_polygon_windows as polygon
from . import foundation_circular_windows as circular
from . import foundation_annular_windows as annular

VERSION='foundation-horizontal-formwork-1.15'
METHOD='eccentric_window_exact_disk_distance'
FIELDS=('x','y','outer_diameter','inner_x','inner_y','inner_diameter')
RULE='外圆减严格在内的可偏心窗口内圆W，两个圆心均按基础共同XY绝对局部坐标；真实圆弧按全部边界事件分段解析积分，再裁切实际水平接触及实体孔，不填实内圆、不以外包矩形或网格逼近。'
NOTICE=('水平顶底、台阶平台及内部孔端支持一个外圆减一个严格内部的可偏心内圆W。逐项填写外圆心XY、外径、内圆心XY及内径，所有坐标共用基础中心原点及单位；内圆坐标不是相对外圆的偏移。'
        '内圆须严格在外圆内，精确比较两圆心距平方与半径差平方，接触或越界拒绝；同心也可采用本模式，既有同心圆环模式保留。'
        '内圆不支模说明及原件独立必填，W仅排除本窗支模，不创建实体孔H、不扣混凝土或生成孔壁／孔端。Z、截面及全部基础个数继承已审来源。'
        '200位真实圆弧解析积分保留80位及有序AST；平台先排上层，孔端取正确侧层，实体孔只扣外圆减W后的净区。全部有效外圆候选片段无剩余才证明严格零接触，W不计入实体孔扣。'
        '同端面完整窗口按精确有理数防重：圆形类检查外圆分离或一个外圆完全落在另一内圆；直边每个连通净块分别检查到外圆心的最小距离和到内圆心的最大距离。'
        '正重叠拒绝，边界接触可分项，内圆未用空区可另项，跨记录同样检查，不按实体外或物理孔投影豁免。'
        '全部个数只继承一次，仅总净量有据修约；实际支模、材料、原件及修约须独立复核。多个内圆、扇形、椭圆、多圆并集、任意曲边及斜面／个体不同做法另核。')


def enabled(d):
    return isinstance(d,dict) and isinstance(d.get('plan_range'),dict) and d['plan_range'].get('shape')=='eccentric_circular'


def parameters(c):
    return {k:linear(c['parameters'][k],c['unit'],k in ('outer_diameter','inner_diameter')) for k in FIELDS}


@precise
def containment(c):
    p={k:Fraction(v) for k,v in parameters(c).items()};R,r=p['outer_diameter']/2,p['inner_diameter']/2
    distance=(p['x']-p['inner_x'])**2+(p['y']-p['inner_y'])**2;gap=(R-r)**2
    if r>=R or distance>=gap:raise DomainError('窗口内圆W须严格在外圆内部，圆心距须严格小于外半径减内半径；相切或越界均拒绝')
    return dict(relation='strictly_inside',center_distance_squared=circular.rational(distance),radius_gap_squared=circular.rational(gap),rational_unit='m²')


def validate(c):
    fields={'shape','parameters','evidence','unit','coordinate_basis','coordinate_evidence_id','scope_confirmed','exclusion_basis','exclusion_evidence_id'}
    if not isinstance(c,dict) or set(c)!=fields or not isinstance(c['parameters'],dict) or set(c['parameters'])!=set(FIELDS) or not isinstance(c['evidence'],dict) or set(c['evidence'])!=set(FIELDS):
        raise DomainError('可偏心内圆窗口须外圆心XY、外径、内圆心XY及内径、逐项原件、共同单位与XY基准、内圆不支模说明及原件和确认；不能手填面积或偏移替代绝对坐标')
    containment(c)
    for eid in c['evidence'].values():text(eid,'外圆／内圆圆心或直径原件',200)
    for key,label in [('coordinate_basis','与孔位共用的XY基准'),('coordinate_evidence_id','共同XY基准原件'),('exclusion_basis','内圆W不支模范围说明'),('exclusion_evidence_id','内圆W不支模原件')]:text(c[key],label,200 if key.endswith('_id') else 2000)
    if c['scope_confirmed'] is not True:raise DomainError('须据原件确认两个圆心、内外径、内圆排除及共同XY正向')


def bounds(d):
    p=parameters(d['plan_range']);x,y,r=p['x'],p['y'],p['outer_diameter']/2
    return x-r,x+r,y-r,y+r


def disks(d):
    if enabled(d):
        p={k:Fraction(v) for k,v in parameters(d['plan_range']).items()}
        return (p['x'],p['y'],p['outer_diameter']/2),(p['inner_x'],p['inner_y'],p['inner_diameter']/2)
    x,y,R,r=annular.radial(d);return (x,y,R),(x,y,r)


@precise
def separation(a,b):
    rat=circular.rational;side='left' if enabled(a) else 'right';punctured,other=(a,b) if side=='left' else (b,a)
    outer,inner=disks(punctured);x,y,R=outer;ix,iy,r=inner
    common=dict(method=METHOD,window_side=side,outer=[rat(v) for v in outer],inner=[rat(v) for v in inner],rational_unit='m',formula_asts={})
    def squared(a,b):return (a[0]-b[0])**2+(a[1]-b[1])**2
    if enabled(other) or annular.enabled(other) or circular.enabled(other):
        other_outer,other_inner=disks(other);X,Y,S=other_outer;I,J,s=other_inner
        distance=squared(outer,other_outer)
        if distance>=(R+S)**2:branch='outer_separation';limit=(R+S)**2;operator='>='
        elif r>=S and squared(inner,other_outer)<=(r-S)**2:branch='other_in_inner';distance=squared(inner,other_outer);limit=(r-S)**2;operator='<='
        elif s>=R and squared(outer,other_inner)<=(s-R)**2:branch='self_in_other_inner';distance=squared(outer,other_inner);limit=(s-R)**2;operator='<='
        else:return dict(overlap=True)
        # Each hole is strictly inside its own outer disk. Crossing outer circles
        # have a common boundary neighborhood untouched by either hole; in the
        # containment case, a whole smaller outer boundary can be excluded only
        # when the larger hole contains that outer disk.
        return dict(**common,overlap=False,state='boundary_touch' if distance==limit else 'separated',pair_kind='disks',branch=branch,operator=operator,
                    other_outer=[rat(v) for v in other_outer],other_inner=[rat(v) for v in other_inner],distance_squared=rat(distance),limit_squared=rat(limit),components=[])
    proofs=[];touching=False
    for bi,raw in enumerate(annular.components(other),1):
        rings=[[tuple(map(Fraction,p)) for p in ring] for ring in raw];states=[inside((x,y),ring) for ring in rings];in_net=sum(v==1 for v in states)%2==1;witnesses=[]
        for ri,ring in enumerate(rings):
            for ei,((px,py),(qx,qy)) in enumerate(edges(ring),1):
                dx,dy=qx-px,qy-py;t=max(Fraction(0),min(Fraction(1),((x-px)*dx+(y-py)*dy)/(dx*dx+dy*dy)));X,Y=px+t*dx,py+t*dy
                witnesses.append(dict(ring_index=ri,edge_index=ei,x_from=rat(px),y_from=rat(py),x_to=rat(qx),y_to=rat(qy),segment_parameter=rat(t),nearest_x=rat(X),nearest_y=rat(Y),distance_squared=rat(squared((x,y),(X,Y)))))
        minimum=Fraction(0) if in_net else min(Fraction(w['distance_squared']) for w in witnesses)
        vertex=max(rings[0],key=lambda p:squared((ix,iy),p));maximum=squared((ix,iy),vertex)
        if minimum<R*R and maximum>r*r:return dict(overlap=True)
        touching|=minimum==R*R or maximum==r*r
        proofs.append(dict(block_index=bi,center_in_net=in_net,ring_center_states=states,minimum_outer_squared=rat(minimum),maximum_inner_squared=rat(maximum),
                           farthest_x=rat(vertex[0]),farthest_y=rat(vertex[1]),outer_limit_squared=rat(R*R),inner_limit_squared=rat(r*r),
                           branch='outside_outer' if minimum>=R*R else 'inside_inner',edge_witnesses=witnesses))
    return dict(**common,overlap=False,state='boundary_touch' if touching else 'separated',pair_kind='polygon',components=proofs)


def geometry(*args):
    outer=None;inner=None;normalized=None;outer_area=None;inner_area=None;proof=None
    def build(c,nodes):
        nonlocal outer,inner,normalized,outer_area,inner_area,proof
        normalized=parameters(c);p={k:N(v,nodes[k]) for k,v in normalized.items()};x,y,R,r=p['x'],p['y'],p['outer_diameter']/2,p['inner_diameter']/2
        def disk(xk,yk,dk,label):return profile(dict(x=normalized[xk],y=normalized[yk],diameter=normalized[dk]),dict(x=nodes[xk],y=nodes[yk],diameter=nodes[dk]),label)
        outer=disk('x','y','outer_diameter','窗口外圆');inner=disk('inner_x','inner_y','inner_diameter','窗口内圆W');outer_area=N(PI)*square(R);inner_area=N(PI)*square(r)
        proof={**containment(c),'formula_asts':dict(center_distance_squared=ast(square(x-p['inner_x'])+square(y-p['inner_y'])),radius_gap_squared=ast(square(R-r)))}
        return [(x-R,y-R),(x+R,y-R),(x+R,y+R),(x-R,y+R)],N(PI)*(R-r)*(R+r)
    class EccentricScene(annular.AnnularScene):
        def measure(self,*args,**kwargs):
            area,node,trace=super().measure(*args,**kwargs);trace.update(rule_version=VERSION,method='可偏心内圆窗口真实圆弧解析积分',integration_rule=RULE);return area,node,trace
    c=args[0]['plan_range'];args[8][args[6]+'.window_inner_basis']=c['exclusion_evidence_id']
    gross,node,holes,extra,nodes=polygon.geometry(*args,parameter_keys=lambda _:FIELDS,window_builder=build,
        scene_type=lambda profiles,pts,bind:EccentricScene(profiles,pts,bind,outer,inner))
    extra.pop('polygon_window');extra.pop('window_vertices')
    extra.update(eccentric_circular_window=True,window_circular_hole={k:str(v) for k,v in normalized.items()},window_inner_containment=proof,
                 window_outer_area=str(value(outer_area)),window_exclusion_area=str(value(inner_area)),window_intersection_rule=RULE)
    nodes.update(window_outer_area=ast(outer_area),window_exclusion_area=ast(inner_area))
    for h in holes:h['intersection_rule']=h['intersection_rule'].replace('凸多边形窗口','外圆减偏心内圆后的窗口净区')
    if 'polygon_zero_contact' in extra:extra['polygon_zero_contact']['method']='全部外圆候选接触边界事件分段扣窗口内圆W、上层及实际孔后无剩余，按净区毛面积减其他实际孔扣的分割恒等式计量；非容差归零'
    return gross,node,holes,extra,nodes
