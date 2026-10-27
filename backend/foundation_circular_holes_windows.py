"""One outer disk minus 2–4 strictly internal, mutually separated source-bound disks."""
from fractions import Fraction
from .compound_takeoff import text,PI,op
from .engine import DomainError,precise
from .measurement_units import LENGTH,VERSION as UNIT_VERSION
from .curved_intersections import N,square,ast,value
from .polygon_takeoff import edges,inside
from .profile_window_intersections import profile
from . import foundation_polygon_windows as polygon
from . import foundation_circular_windows as circular
from . import foundation_annular_windows as annular
from . import foundation_eccentric_windows as eccentric

VERSION='foundation-horizontal-formwork-1.16'
METHOD='circular_holes_exact_disk_distance'
RULE='一个圆形外轮廓减2至4个严格内部且互相严格分离的窗口内圆W；逐圆真实边界事件分段解析积分后裁切实际水平接触与实体孔H，不填实内圆、不重复扣W与H，不用外包矩形、网格或多边形逼近。'
NOTICE=('水平顶底、台阶平台及内部孔端支持圆形外轮廓内2至4个圆形不支模区W。逐项填外圆心XY及外径，各W名称、圆心XY、实际直径、逐尺寸原件及不支模说明原件；全部共用基础中心原点、单位和XY正向，不填偏移或附加数量。'
        '精确有理数检查每个W严格在外圆内，任意两W严格分离；相切、重叠、包含、越界、重名或缺原件均拒绝。W只从本窗支模排除，不创建实体孔H、不扣混凝土或生成孔壁／孔端。'
        '200位真实圆弧解析积分保留80位及有序AST；平台先排上层，孔端取正确混凝土侧层，H只扣余下净区。全部有效外圆候选片段扣所有W及H后无剩余才证明严格零接触。'
        '同端面完整窗口精确防重：圆形类检查外圆分离或完整外圆落在对方某一个W内；直边逐连通净块检查到外圆心的最小距离，以及是否完整在某一W内，不拼接不同块或不同孔的极值。'
        '正重叠拒绝，窗口间边界接触可分项，未用W可有据另项；跨记录同样检查，不按实体外或物理孔投影豁免。Z、真实截面与全部基础个数继承来源一次，仅最终总净量有据修约。'
        '原件、实际支模、材料及修约须独立复核；超过4个W、相交或接触W并集、曲边岛区、扇形、椭圆、多外圆并集、任意曲边、斜面及个体不同做法另核。')


def enabled(d):
    return isinstance(d,dict) and isinstance(d.get('plan_range'),dict) and d['plan_range'].get('shape')=='circular_with_holes'


def circle(c,unit=None):
    return circular.parameters(c if unit is None else {**c,'unit':unit})


def rational_disk(p):return (Fraction(p['x']),Fraction(p['y']),Fraction(p['diameter'])/2)
def distance(a,b):return (a[0]-b[0])**2+(a[1]-b[1])**2


@precise
def topology(c):
    rat=circular.rational;outer=rational_disk(circle(c));holes=[rational_disk(circle(h,c['unit'])) for h in c['holes']];contained=[];pairs=[]
    for i,h in enumerate(holes,1):
        d=distance(outer,h);gap=(outer[2]-h[2])**2
        if h[2]>=outer[2] or d>=gap:raise DomainError('W'+str(i)+'须严格在外圆内；相切或越界拒绝')
        contained.append(dict(index=i,relation='strictly_inside',center_distance_squared=rat(d),radius_gap_squared=rat(gap),rational_unit='m²'))
        for j,other in enumerate(holes[:i-1],1):
            d=distance(h,other);limit=(h[2]+other[2])**2
            if d<=limit:raise DomainError('W'+str(j)+'与W'+str(i)+'须严格分离，不能接触、重叠或包含')
            pairs.append(dict(left_index=j,right_index=i,relation='strictly_disjoint',center_distance_squared=rat(d),radius_sum_squared=rat(limit),rational_unit='m²'))
    return contained,pairs


def validate(c,*,min_holes=2):
    fields={'shape','parameters','evidence','unit','coordinate_basis','coordinate_evidence_id','scope_confirmed','holes'}
    if not isinstance(c,dict) or set(c)!=fields or not isinstance(c['holes'],list) or not min_holes<=len(c['holes'])<=4:
        raise DomainError('多内圆窗口须圆形外轮廓、2至4个内圆列表、共同单位／XY基准和确认；一个内圆请用可偏心内圆模式')
    circular.validate({k:v for k,v in c.items() if k!='holes'})
    names=set()
    for h in c['holes']:
        if not isinstance(h,dict) or set(h)!={'name','parameters','evidence','measurement_basis','evidence_id'}:
            raise DomainError('各W须名称、圆心XY及实际直径、逐尺寸原件、不支模说明及原件，单位和原点继承外圆')
        name=text(h['name'],'窗口内圆名称',200)
        if name in names:raise DomainError('窗口内圆名称不能重复')
        names.add(name);text(h['measurement_basis'],'内圆W不支模说明');text(h['evidence_id'],'内圆W用途原件',200)
        circular.validate({k:v for k,v in c.items() if k!='holes'}|{'parameters':h['parameters'],'evidence':h['evidence']})
    topology(c)


def bounds(d):return circular.bounds(d)


def disks(d):
    if enabled(d):
        c=d['plan_range'];return rational_disk(circle(c)),[rational_disk(circle(h,c['unit'])) for h in c['holes']]
    outer,inner=eccentric.disks(d);return outer,[inner] if inner[2]>0 else []


@precise
def separation(a,b):
    rat=circular.rational;side='left' if enabled(a) else 'right';window,other=(a,b) if side=='left' else (b,a)
    outer,holes=disks(window);x,y,R=outer
    common=dict(method=METHOD,window_side=side,outer=[rat(v) for v in outer],holes=[[rat(v) for v in h] for h in holes],rational_unit='m',formula_asts={})
    if enabled(other) or eccentric.enabled(other) or annular.enabled(other) or circular.enabled(other):
        other_outer,other_holes=disks(other);S=other_outer[2];d=distance(outer,other_outer);limit=(R+S)**2;branch=None;index=None;operator='>='
        if d>=limit:branch='outer_separation'
        else:
            for label,container,target in [('other_in_window_hole',holes,other_outer),('window_in_other_hole',other_holes,outer)]:
                for i,h in enumerate(container,1):
                    if h[2]>=target[2] and distance(h,target)<=(h[2]-target[2])**2:
                        branch=label;index=i;d=distance(h,target);limit=(h[2]-target[2])**2;operator='<=';break
                if branch:break
        if branch is None:return dict(overlap=True)
        # Strictly separate internal holes cannot collectively cover a connected
        # outer boundary. Absence of overlap requires one complete outer disk
        # inside a single opposite hole, or disjoint outer disks.
        return dict(**common,overlap=False,state='boundary_touch' if d==limit else 'separated',pair_kind='disks',branch=branch,hole_index=index,
                    other_outer=[rat(v) for v in other_outer],other_holes=[[rat(v) for v in h] for h in other_holes],distance_squared=rat(d),limit_squared=rat(limit),operator=operator,components=[])
    proofs=[];touching=False
    for bi,raw in enumerate(annular.components(other),1):
        rings=[[tuple(map(Fraction,p)) for p in ring] for ring in raw];states=[inside((x,y),ring) for ring in rings];in_net=sum(v==1 for v in states)%2==1;witnesses=[]
        for ri,ring in enumerate(rings):
            for ei,((px,py),(qx,qy)) in enumerate(edges(ring),1):
                dx,dy=qx-px,qy-py;t=max(Fraction(0),min(Fraction(1),((x-px)*dx+(y-py)*dy)/(dx*dx+dy*dy)));X,Y=px+t*dx,py+t*dy
                witnesses.append(dict(ring_index=ri,edge_index=ei,x_from=rat(px),y_from=rat(py),x_to=rat(qx),y_to=rat(qy),segment_parameter=rat(t),nearest_x=rat(X),nearest_y=rat(Y),distance_squared=rat(distance((x,y),(X,Y)))))
        minimum=Fraction(0) if in_net else min(Fraction(w['distance_squared']) for w in witnesses);maxima=[]
        for i,h in enumerate(holes,1):
            v=max(rings[0],key=lambda p:distance(h,p));m=distance(h,v)
            maxima.append(dict(hole_index=i,maximum_squared=rat(m),limit_squared=rat(h[2]**2),farthest_x=rat(v[0]),farthest_y=rat(v[1])))
        selected=next((q for q in maxima if Fraction(q['maximum_squared'])<=Fraction(q['limit_squared'])),None)
        if minimum<R*R and selected is None:return dict(overlap=True)
        branch='outside_outer' if minimum>=R*R else 'inside_window_hole'
        touching|=minimum==R*R if branch=='outside_outer' else selected['maximum_squared']==selected['limit_squared']
        proofs.append(dict(block_index=bi,center_in_net=in_net,ring_center_states=states,minimum_outer_squared=rat(minimum),outer_limit_squared=rat(R*R),
                           branch=branch,hole_index=selected['hole_index'] if branch=='inside_window_hole' else None,inner_maxima=maxima,edge_witnesses=witnesses))
    return dict(**common,overlap=False,state='boundary_touch' if touching else 'separated',pair_kind='polygon',components=proofs)


class CircularHolesScene(circular.CircularScene):
    def __init__(self,profiles,pts,bind,outer,holes):
        self.holes=holes;super().__init__(profiles+holes,pts,bind,outer)

    def measure(self,include,exclude=(),certify=False):
        area,node,trace=super().measure(include,list(exclude)+self.holes,certify=certify)
        trace.update(rule_version=VERSION,method='圆形多内圆窗口真实圆弧解析积分',integration_rule=RULE);return area,node,trace


def geometry(*args):
    d,_,_,_,_,_,key,dimensions,refs,conversions=args;c=d['plan_range'];contained,pairs=topology(c)
    outer=None;profiles=[];rows=[];outer_area=None;excluded=None;hole_nodes=[]
    def build(c,nodes):
        nonlocal outer,outer_area,excluded
        normalized=circle(c);p={k:N(v,nodes[k]) for k,v in normalized.items()};x,y,R=p['x'],p['y'],p['diameter']/2
        outer=profile(normalized,nodes,'窗口外圆');outer_area=N(PI)*square(R);excluded=N(0)
        for i,(h,proof) in enumerate(zip(c['holes'],contained),1):
            prefix=key+'.window_holes.'+str(i);ns={};hp=circle(h,c['unit']);refs[prefix+'.basis']=h['evidence_id']
            for k in circular.FIELDS:
                name=prefix+'.'+k;dimensions[name]=str(h['parameters'][k]);refs[name]=h['evidence'][k];ns[k]=op('multiply',{'dimension':name},{'constant':LENGTH[c['unit']]})
                conversions.append(dict(parameter=name,original_value=dimensions[name],source_unit=c['unit'],factor=LENGTH[c['unit']],canonical_value=str(hp[k]),canonical_unit='m',rule_version=UNIT_VERSION))
            hn={k:N(v,ns[k]) for k,v in hp.items()};r=hn['diameter']/2;area=N(PI)*square(r);excluded=excluded+area
            proof={**proof,'formula_asts':dict(center_distance_squared=ast(square(x-hn['x'])+square(y-hn['y'])),radius_gap_squared=ast(square(R-r)))}
            rows.append(dict(index=i,name=h['name'],normalized_parameters={k:str(v) for k,v in hp.items()},area=str(value(area)),containment=proof,formula_asts=dict(area=ast(area))))
            profiles.append(profile(hp,ns,'窗口内圆W'+str(i)));hole_nodes.append((hn,r))
        for q in pairs:
            a,r=hole_nodes[q['left_index']-1];b,s=hole_nodes[q['right_index']-1]
            q['formula_asts']=dict(center_distance_squared=ast(square(a['x']-b['x'])+square(a['y']-b['y'])),radius_sum_squared=ast(square(r+s)))
        return [(x-R,y-R),(x+R,y-R),(x+R,y+R),(x-R,y+R)],outer_area-excluded
    gross,node,holes,extra,nodes=polygon.geometry(*args,parameter_keys=lambda _:circular.FIELDS,window_builder=build,
        scene_type=lambda ps,pts,bind:CircularHolesScene(ps,pts,bind,outer,profiles))
    extra.pop('polygon_window');extra.pop('window_vertices')
    extra.update(circular_holes_window=True,window_outer_circle={k:str(v) for k,v in circle(c).items()},window_circular_holes=rows,window_hole_checks=pairs,
                 window_outer_area=str(value(outer_area)),window_exclusion_area=str(value(excluded)),window_intersection_rule=RULE)
    nodes.update(window_outer_area=ast(outer_area),window_exclusion_area=ast(excluded))
    for h in holes:h['intersection_rule']=h['intersection_rule'].replace('凸多边形窗口','外圆减全部内圆W后的净区')
    if 'polygon_zero_contact' in extra:extra['polygon_zero_contact']['method']='全部外圆候选接触边界事件分段排除所有窗口内圆W、上层及实际孔H后无剩余，按净区毛面积减其他实际孔扣的分割恒等式计量；非容差归零'
    return gross,node,holes,extra,nodes
