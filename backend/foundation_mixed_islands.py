"""Homogeneous circle/polygon blocks in an explicitly mixed nearest-parent hierarchy."""
from fractions import Fraction
from .engine import DomainError,precise
from .compound_takeoff import text,op
from .measurement_units import LENGTH
from .polygon_takeoff import inside,edges,intersects
from . import foundation_circular_holes_windows as circles
from . import foundation_circular_islands as circle_islands
from . import foundation_polygon_windows as polygon
from . import foundation_simple_windows as simple
from . import foundation_perforated_windows as perforated
from . import foundation_multi_windows as multi

VERSION='foundation-horizontal-formwork-1.18'
METHOD='mixed_islands_exact_block_pairs'
RULE='圆形或直边根块及岛区各按本块外轮廓减同类所属W分别裁切真实接触；以真实有限边段及圆精确核定严格包含和最近父块；逐块200位解析积分保留80位后累计，基础数量继承一次，仅总量有据修约，不填实其他空区。'
NOTICE=('水平顶底、台阶平台及内部孔端支持2至4块圆形／直边混合嵌套岛区，至少各一种外轮廓且至少一个岛区。'
 '每块外轮廓及其0至4个W采用同类图形：圆形填圆心XY及实际直径，直边填沿边界有序3至12顶点；每直边块含孔最多36点，全部直边最多48点且全部最多8个W。'
 '共用基础中心XY及单位，各尺寸、支模／不支模用途、最近父块关系均有独立原件。子块外轮廓须严格在最近父块的指定W内，其他块严格分离；接触、交叠、跳层、自指、成环或选错孔拒绝。'
 '圆在凹孔内须圆心严格在内且到每条有限边段距离大于半径；直边在圆孔内须所有外顶点严格在圆内，不用外包框／顶点抽样代替圆边校验。'
 'W只排所属块、岛区按独立块计回，不创建实体孔H，不改变混凝土、材料或Z。平台排上层，孔端取正确侧层，H只扣各块净接触。'
 '全部块全部有效片段排所属W／上层／H后无剩余才证明整体严格零；完整窗口及跨记录逐块对精确有理数防重，父孔不能豁免岛区正重叠。'
 '平面层级不代表标高或数量，全部基础个数只继承一次；原件、实际支模、做法及修约须独立复核。'
 '同一净块直曲边混合孔、任意曲线、扇形、椭圆、交叠轮廓并集、超限、空间斜面和个体不同做法另核。')


def enabled(d):return isinstance(d,dict) and isinstance(d.get('plan_range'),dict) and d['plan_range'].get('shape')=='nested_mixed'


def part_config(c,p):
    common={k:c[k] for k in ('unit','coordinate_basis','coordinate_evidence_id','scope_confirmed')}
    if p['shape']=='circular':return common|dict(shape='circular_with_holes',parameters=p['parameters'],evidence=p['evidence'],holes=p['holes'])
    return common|dict(shape='polygon_with_holes' if p['holes'] else 'simple_polygon',parameters=p['parameters'],evidence=p['evidence'],**({'holes':p['holes']} if p['holes'] else {}))


def blocks(d):
    if enabled(d):return [(i,dict(plan_range=part_config(d['plan_range'],p))) for i,p in enumerate(d['plan_range']['parts'],1)]
    return circle_islands.blocks(d)


class MixedSimpleScene(simple.SimpleScene):
    canonical_horizontal_events=True


class MixedPerforatedScene(perforated.PerforatedScene):
    canonical_horizontal_events=True


def part_geometry(*args):
    c=args[0]['plan_range']
    if c['shape']=='circular_with_holes':return circles.geometry(*args)
    if c['shape']=='polygon_with_holes':return perforated.geometry(*args,scene_type=MixedPerforatedScene)
    return simple.geometry(*args,scene_type=MixedSimpleScene)


def bounds(d):
    from .foundation_horizontal_windows import bounds as bound
    boxes=[bound(p) for _,p in blocks(d)]
    return min(p[0] for p in boxes),max(p[1] for p in boxes),min(p[2] for p in boxes),max(p[3] for p in boxes)


def rat(v):
    v=Fraction(v);return str(v.numerator) if v.denominator==1 else str(v.numerator)+'/'+str(v.denominator)


class Q:
    """Source-bound rational arithmetic; topology has no Decimal rounding or tolerance."""
    def __init__(self,v,a=None):
        self.v=Fraction(v);self.a=a if a is not None else ({'constant':str(self.v.numerator)} if self.v.denominator==1 else op('divide',{'constant':str(self.v.numerator)},{'constant':str(self.v.denominator)}))
    def calc(self,o,name,fn):
        o=o if isinstance(o,Q) else Q(o);return Q(fn(self.v,o.v),op(name,self.a,o.a))
    def __add__(self,o):return self.calc(o,'add',lambda a,b:a+b)
    def __sub__(self,o):return self.calc(o,'subtract',lambda a,b:a-b)
    def __mul__(self,o):return self.calc(o,'multiply',lambda a,b:a*b)
    def __truediv__(self,o):return self.calc(o,'divide',lambda a,b:a/b)


def squared(a,b):return (a[0]-b[0])*(a[0]-b[0])+(a[1]-b[1])*(a[1]-b[1])
def points(g):return [(x.v,y.v) for x,y in g['points']]
def encoded(g):return dict(shape=g['shape'],**({'circle':[rat(v.v) for v in g['circle']]} if g['shape']=='circular' else {'points':[[rat(x.v),rat(y.v)] for x,y in g['points']]}))


def shapes(c,key=None):
    result=[]
    for i,p in enumerate(c['parts'],1):
        rs=[]
        for hi,h in enumerate([p]+p['holes']):
            prefix=None if key is None else key+'.parts.'+str(i)+('.window_holes.'+str(hi) if hi else '')
            ns={k:Q(Fraction(str(v))*Fraction(LENGTH[c['unit']]),op('multiply',{'dimension':prefix+'.'+k},{'constant':LENGTH[c['unit']]}) if prefix else None) for k,v in h['parameters'].items()}
            rs.append(dict(shape=p['shape'],**({'circle':(ns['x'],ns['y'],ns['diameter']/2)} if p['shape']=='circular' else {'points':[(ns['x'+str(j)],ns['y'+str(j)]) for j in range(1,len(ns)//2+1)]})))
        result.append(rs)
    return result


def condition(label,a,operator,b,unit='m²',**extra):
    return dict(label=label,value=rat(a.v),operator=operator,limit=rat(b.v),unit=unit,formula_asts=dict(value=a.a,limit=b.a),**extra)


def segment_distances(circle,ring):
    x,y,r=circle['circle'];ws=[]
    for i,((px,py),(qx,qy)) in enumerate(edges(ring['points']),1):
        dx,dy=qx-px,qy-py;t=((x-px)*dx+(y-py)*dy)/(dx*dx+dy*dy)
        t=Q(0) if t.v<0 else Q(1) if t.v>1 else t;X,Y=px+t*dx,py+t*dy;distance=squared((x,y),(X,Y))
        ws.append((distance,dict(edge_index=i,segment_parameter=rat(t.v),nearest_x=rat(X.v),nearest_y=rat(Y.v),formula_asts=dict(segment_parameter=t.a,nearest_x=X.a,nearest_y=Y.a))))
    return ws


def strict_inside(child,parent):
    common=dict(child=encoded(child),container=encoded(parent),conditions=[])
    if parent['shape']=='circular':
        x,y,R=parent['circle']
        if child['shape']=='circular':
            X,Y,r=child['circle'];d=squared((x,y),(X,Y));gap=(R-r)*(R-r)
            if r.v>=R.v or d.v>=gap.v:return None
            return common|dict(kind='circle_in_circle',conditions=[condition('子半径严格小于父孔半径',r,'<',R,'m'),condition('两圆心距平方小于半径差平方',d,'<',gap)])
        conditions=[condition('子外顶点P'+str(i)+'在父圆孔内',squared((x,y),v),'<',R*R,vertex_index=i) for i,v in enumerate(child['points'],1)]
        if any(Fraction(q['value'])>=Fraction(q['limit']) for q in conditions):return None
        return common|dict(kind='polygon_in_circle',conditions=conditions)
    if child['shape']=='circular':
        x,y,r=child['circle'];state=inside((x.v,y.v),points(parent));ws=segment_distances(child,parent)
        if state!=1 or any(d.v<=r.v*r.v for d,_ in ws):return None
        return common|dict(kind='circle_in_polygon',center_state=state,conditions=[condition('到父孔有限边段'+str(w['edge_index'])+'的距离平方大于半径平方',d,'>',r*r,edge_witness=w) for d,w in ws])
    cp,pp=points(child),points(parent);states=[inside(p,pp) for p in cp];pairs=len(cp)*len(pp)
    if any(v!=1 for v in states) or any(intersects(a,b,p,q) for a,b in edges(cp) for p,q in edges(pp)):return None
    return common|dict(kind='polygon_in_polygon',vertex_states=states,edge_pairs_checked=pairs,no_boundary_intersection=True)


def strict_disjoint(a,b):
    common=dict(left=encoded(a),right=encoded(b),conditions=[])
    if a['shape']==b['shape']=='circular':
        x,y,r=a['circle'];X,Y,R=b['circle'];d=squared((x,y),(X,Y));limit=(r+R)*(r+R)
        return None if d.v<=limit.v else common|dict(kind='circle_circle',conditions=[condition('外圆心距平方大于半径和平方',d,'>',limit)])
    if a['shape']!=b['shape']:
        circle,ring=(a,b) if a['shape']=='circular' else (b,a);x,y,r=circle['circle'];state=inside((x.v,y.v),points(ring));ws=segment_distances(circle,ring)
        if state!=-1 or any(d.v<=r.v*r.v for d,_ in ws):return None
        return common|dict(kind='circle_polygon',circle_side='left' if a['shape']=='circular' else 'right',center_state=state,conditions=[condition('到另一外轮廓有限边段'+str(w['edge_index'])+'的距离平方大于半径平方',d,'>',r*r,edge_witness=w) for d,w in ws])
    pa,pb=points(a),points(b)
    if any(intersects(a,b,p,q) for a,b in edges(pa) for p,q in edges(pb)) or inside(pa[0],pb)!=-1 or inside(pb[0],pa)!=-1:return None
    return common|dict(kind='polygon_polygon',left_point_state=-1,right_point_state=-1,edge_pairs_checked=len(pa)*len(pb),no_boundary_intersection=True)


@precise
def topology(c,key=None):
    rs=shapes(c,key);ancestors={i:[] for i in range(1,len(rs)+1)};checks=[]
    for i,r in enumerate(rs,1):
        for j,other in enumerate(rs[:i-1],1):
            proof=strict_disjoint(other[0],r[0])
            if proof:
                checks.append(dict(left_index=j,right_index=i,relation='strictly_disjoint',parent_hole_index=None,proof=proof));continue
            matches=[]
            for parent,child in [(j,i),(i,j)]:
                for hi,h in enumerate(rs[parent-1][1:],1):
                    proof=strict_inside(rs[child-1][0],h)
                    if proof:matches.append((parent,child,hi,proof))
            if len(matches)!=1:raise DomainError('混合岛区外轮廓须严格分离或严格在另一块的一个W内；接触、交叠或进入父块净区均拒绝')
            parent,child,hi,proof=matches[0];ancestors[child].append((parent,hi));checks.append(dict(left_index=parent,right_index=child,relation='inside_window_hole',parent_hole_index=hi,proof=proof))
    if not any(ancestors.values()):raise DomainError('混合岛区须至少一块严格在另一块内孔中')
    hierarchy=[]
    for i in ancestors:
        nearest=[(p,h) for p,h in ancestors[i] if not any(p in {a for a,_ in ancestors[o]} for o,_ in ancestors[i] if o!=p)]
        if len(nearest)>1:raise DomainError('无法核定混合岛区唯一最近父块')
        parent=dict(block_index=nearest[0][0],hole_index=nearest[0][1]) if nearest else None
        if c['parts'][i-1]['parent']!=parent:raise DomainError('B'+str(i)+'须显式选择几何核定的最近父块及W，不能跳层或将岛区声明为根块')
        hierarchy.append(dict(block_index=i,parent=parent,depth=len(ancestors[i])))
    return checks,hierarchy


def validate(c):
    if not isinstance(c,dict) or set(c)!={'shape','parts','unit','coordinate_basis','coordinate_evidence_id','scope_confirmed'} or not isinstance(c['parts'],list) or not 2<=len(c['parts'])<=4:raise DomainError('混合岛区须2至4块、共同单位及XY基准原件和确认')
    names=set();count=0;vertex_count=0;kinds=set()
    for i,p in enumerate(c['parts'],1):
        if not isinstance(p,dict) or set(p)!={'shape','name','parameters','evidence','measurement_basis','evidence_id','holes','parent'} or p['shape'] not in ('circular','polygon') or not isinstance(p['holes'],list) or len(p['holes'])>4:raise DomainError('每个混合块须明确圆形或直边类型、名称、尺寸及原件、支模与父关系说明及原件、0至4个同类W和明确父关系')
        name=text(p['name'],'混合净块名称',200)
        if name in names:raise DomainError('混合净块名称不能重复')
        names.add(name);kinds.add(p['shape']);text(p['measurement_basis'],'本块实际支模及父关系说明');text(p['evidence_id'],'本块支模及父关系原件',200);cfg=part_config(c,p)
        if p['shape']=='circular':circles.validate(cfg,min_holes=0)
        else:(perforated.validate if p['holes'] else simple.validate)(cfg)
        count+=len(p['holes'])
        if p['shape']=='polygon':vertex_count+=len(p['parameters'])//2+sum(len(h['parameters'])//2 for h in p['holes'])
        parent=p['parent']
        if parent is not None and (not isinstance(parent,dict) or set(parent)!={'block_index','hole_index'} or type(parent['block_index']) is not int or type(parent['hole_index']) is not int or not 1<=parent['block_index']<=len(c['parts']) or parent['block_index']==i or parent['hole_index']<1):raise DomainError('父关系须根块空值或其他块及W的整数序号，不能自指')
    if kinds!={'circular','polygon'}:raise DomainError('混合模式须同时有圆形及直边块；纯圆形或纯直边请用对应模式')
    if count>8 or vertex_count>48:raise DomainError('混合窗口全部最多8个W及48个直边顶点')
    for i,p in enumerate(c['parts'],1):
        visited={i};parent=p['parent']
        while parent is not None:
            j=parent['block_index']
            if parent['hole_index']>len(c['parts'][j-1]['holes']):raise DomainError('父块内孔不存在，请重新选择')
            if j in visited:raise DomainError('混合岛区父关系不能成环')
            visited.add(j);parent=c['parts'][j-1]['parent']
    topology(c)


@precise
def separation(a,b):
    from .foundation_horizontal_windows import checks
    pairs=[];touch=False
    for i,left in blocks(a):
        for j,right in blocks(b):
            base=dict(material_index=1,face='bottom')
            from .foundation_horizontal_windows import overlaps
            if overlaps(left,right):return dict(overlap=True)
            proof=checks([{**left,**base,'index':1,'name':'左净块'},{**right,**base,'index':2,'name':'右净块'}])[0]
            touch|=proof['state']=='boundary_touch';pairs.append(dict(left_block_index=i,right_block_index=j,proof=proof))
    return dict(method=METHOD,overlap=False,state='boundary_touch' if touch else 'separated',pairs=pairs,formula_asts={})


def geometry(*args):
    c=args[0]['plan_range'];checks,hierarchy=topology(c,args[6]);depths={q['block_index']:q['depth'] for q in hierarchy}
    def trace(parts):
        q=multi.trace(parts);q.update(rule_version=VERSION,method='混合圆形直边岛区逐块解析积分及累计',integration_rule=RULE)
        for term in q['terms']+q.get('partitions',[]):term['nesting_depth']=depths[term['block_index']]
        return q
    gross,node,cut,extra,nodes=multi.geometry(*args,part_config_fn=part_config,geometry_fn=part_geometry,topology_fn=lambda _:checks,trace_fn=trace)
    extra.pop('multi_polygon_window');extra.pop('polygon_window');extra.update(nested_mixed_window=True,window_nesting=hierarchy,window_intersection_rule=RULE)
    for p,h,raw in zip(extra['window_parts'],hierarchy,c['parts']):p.update(parent=h['parent'],nesting_depth=h['depth'],block_shape=raw['shape'])
    for h in cut:h['intersection_rule']=RULE
    if 'polygon_zero_contact' in extra:extra['polygon_zero_contact']['method']='全部混合根块及岛区的全部边界事件分段排所属W、上层及实体孔H后无剩余；按整体分割恒等式严格零接触，非容差归零'
    return gross,node,cut,extra,nodes
