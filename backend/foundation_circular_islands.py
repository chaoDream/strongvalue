"""Explicit nearest-parent circular islands; disjoint net blocks, rational topology."""
from fractions import Fraction
from .engine import DomainError,precise
from .compound_takeoff import text,op
from .measurement_units import LENGTH
from .curved_intersections import N,square,ast
from . import foundation_circular_holes_windows as circles
from . import foundation_circular_windows as circular
from . import foundation_multi_windows as multi

VERSION='foundation-horizontal-formwork-1.17'
METHOD='circular_islands_exact_block_pairs'
RULE='圆形根块及内圆W中的岛区分别按本块外圆减所属W裁切真实接触；最近父块／内圆显式核定，精确严格包含；逐块200位解析积分保留80位再累计，全部基础数量继承一次，仅总量有据修约，不填实其余空区。'
NOTICE=('水平顶底、台阶平台及内部孔端支持2至4个显式父关系的圆形支模净块，至少一块为另一块内圆W中的岛区。'
        '每块外圆及0至4个W分别填写共同原点XY、实际直径和逐项原件、支模／不支模说明及原件，全部最多8个W。'
        '每个W严格在本块外圆内且两两严格分离；岛区外圆严格在最近父块的指定W内，其他外圆严格分离；相切、交叠、跳层、自指、成环、选错孔或缺原件拒绝。'
        'W仅排除所属块，岛区按独立块计回，不创建实体孔H或改变混凝土、材料及Z；孔端取正确侧层，平台先排上层，H只扣各块净接触。'
        '全部块的全部有效边界事件分段无剩余才证明严格零接触，不按显示零或容差归零。完整窗口逐块对精确有理数防重，未用父孔空区可另项，岛区正重叠阻断，跨记录同样检查。'
        '父关系只是平面层级，不是标高或数量；单位和XY基准共同，来源全部数量只乘一次。原件、真实支模、材料及修约须独立复核。'
        '超过4块／8孔、相接或交叠轮廓合并、混合直曲边嵌套、扇形、椭圆、多外圆交叠并集、任意曲边、空间斜面及个体不同做法另核。')


def enabled(d):return isinstance(d,dict) and isinstance(d.get('plan_range'),dict) and d['plan_range'].get('shape')=='nested_circular'


def part_config(c,p):
    return {k:c[k] for k in ('unit','coordinate_basis','coordinate_evidence_id','scope_confirmed')}|dict(shape='circular_with_holes',parameters=p['parameters'],evidence=p['evidence'],holes=p['holes'])


def blocks(d):
    if not enabled(d):return [(None,d)]
    c=d['plan_range'];return [(i,dict(plan_range=part_config(c,p))) for i,p in enumerate(c['parts'],1)]


def bounds(d):
    boxes=[circles.bounds(p) for _,p in blocks(d)]
    return min(p[0] for p in boxes),max(p[1] for p in boxes),min(p[2] for p in boxes),max(p[3] for p in boxes)


@precise
def topology(c):
    data=[circles.disks(dict(plan_range=part_config(c,p))) for p in c['parts']];ancestors={i:[] for i in range(1,len(data)+1)};checks=[];rat=circular.rational
    for i,(outer,_) in enumerate(data,1):
        for j,(other,_) in enumerate(data[:i-1],1):
            d=circles.distance(outer,other);limit=(outer[2]+other[2])**2
            if d>limit:
                checks.append(dict(left_index=j,right_index=i,relation='strictly_disjoint',center_distance_squared=rat(d),limit_squared=rat(limit),operator='>',parent_hole_index=None));continue
            parent,child=(j,i) if other[2]>outer[2] else (i,j)
            target=data[child-1][0];matches=[]
            for hi,h in enumerate(data[parent-1][1],1):
                gap=(h[2]-target[2])**2;distance=circles.distance(h,target)
                if h[2]>target[2] and distance<gap:matches.append((hi,distance,gap))
            if len(matches)!=1:raise DomainError('圆形岛区外圆须严格在另一块的一个内圆W内；外圆相切、交叠或落入父块净区均拒绝')
            hi,distance,gap=matches[0];ancestors[child].append((parent,hi))
            checks.append(dict(left_index=parent,right_index=child,relation='inside_window_hole',parent_hole_index=hi,center_distance_squared=rat(distance),limit_squared=rat(gap),operator='<'))
    if not any(ancestors.values()):raise DomainError('圆形岛区须至少一块严格在另一块的内圆中；分离圆窗可另列接触范围')
    hierarchy=[]
    for i in ancestors:
        nearest=[(p,h) for p,h in ancestors[i] if not any(p in {a for a,_ in ancestors[o]} for o,_ in ancestors[i] if o!=p)]
        if len(nearest)>1:raise DomainError('无法核定唯一最近父块')
        parent=dict(block_index=nearest[0][0],hole_index=nearest[0][1]) if nearest else None
        if c['parts'][i-1]['parent']!=parent:raise DomainError('B'+str(i)+'须显式选择几何核定的最近父块及内圆W；不能跳层或将岛区声明为根块')
        hierarchy.append(dict(block_index=i,parent=parent,depth=len(ancestors[i])))
    return checks,hierarchy


def validate(c):
    if not isinstance(c,dict) or set(c)!={'shape','parts','unit','coordinate_basis','coordinate_evidence_id','scope_confirmed'} or not isinstance(c['parts'],list) or not 2<=len(c['parts'])<=4:raise DomainError('圆形岛区窗口须2至4块、共同单位／XY基准及确认')
    names=set();count=0
    for i,p in enumerate(c['parts'],1):
        if not isinstance(p,dict) or set(p)!={'name','parameters','evidence','measurement_basis','evidence_id','holes','parent'} or not isinstance(p['holes'],list) or len(p['holes'])>4:raise DomainError('每个圆形块须名称、圆心XY／直径及原件、支模和父关系说明及原件、0至4内圆和明确父关系')
        name=text(p['name'],'圆形净块名称',200)
        if name in names:raise DomainError('圆形净块名称不能重复')
        names.add(name);text(p['measurement_basis'],'本块支模及父块内圆关系说明');text(p['evidence_id'],'本块支模及父关系原件',200)
        circles.validate(part_config(c,p),min_holes=0);count+=len(p['holes']);parent=p['parent']
        if parent is not None and (not isinstance(parent,dict) or set(parent)!={'block_index','hole_index'} or type(parent['block_index']) is not int or type(parent['hole_index']) is not int or not 1<=parent['block_index']<=len(c['parts']) or parent['block_index']==i or parent['hole_index']<1):raise DomainError('父块须根块空值或另一块及内圆的整数序号，不能自指')
    if count>8:raise DomainError('圆形岛区窗口全部最多8个内圆W')
    for i,p in enumerate(c['parts'],1):
        parent=p['parent'];visited={i}
        while parent is not None:
            j=parent['block_index']
            if parent['hole_index']>len(c['parts'][j-1]['holes']):raise DomainError('父块内圆不存在，请重新选择')
            if j in visited:raise DomainError('圆形岛区父关系不能成环')
            visited.add(j);parent=c['parts'][j-1]['parent']
    topology(c)


@precise
def separation(a,b):
    pairs=[];touch=False
    for i,left in blocks(a):
        for j,right in blocks(b):
            q=circles.separation(left,right)
            if q['overlap']:return dict(overlap=True)
            touch|=q['state']=='boundary_touch';pairs.append(dict(left_block_index=i,right_block_index=j,proof=q))
    return dict(method=METHOD,overlap=False,state='boundary_touch' if touch else 'separated',pairs=pairs,formula_asts={})


def geometry(*args):
    c=args[0]['plan_range'];checks,hierarchy=topology(c);depths={q['block_index']:q['depth'] for q in hierarchy}
    def trace(parts):
        q=multi.trace(parts);q.update(rule_version=VERSION,method='圆形根块及岛区逐块真实圆弧解析积分',integration_rule=RULE)
        for term in q['terms']+q.get('partitions',[]):term['nesting_depth']=depths[term['block_index']]
        return q
    gross,node,cut,extra,nodes=multi.geometry(*args,part_config_fn=part_config,geometry_fn=circles.geometry,topology_fn=lambda _:checks,trace_fn=trace)
    key=args[6]
    def source_circle(i,hole=None):
        prefix=key+'.parts.'+str(i)+('.window_holes.'+str(hole) if hole else '')
        cfg=c['parts'][i-1];cfg=cfg['holes'][hole-1] if hole else cfg
        values=circular.parameters(dict(parameters=cfg['parameters'],unit=c['unit']))
        return {k:N(v,op('multiply',{'dimension':prefix+'.'+k},{'constant':LENGTH[c['unit']]})) for k,v in values.items()}
    for q in checks:
        a=source_circle(q['left_index'],q['parent_hole_index']);b=source_circle(q['right_index']);ra,rb=a['diameter']/2,b['diameter']/2
        q['formula_asts']=dict(center_distance_squared=ast(square(a['x']-b['x'])+square(a['y']-b['y'])),limit_squared=ast(square(ra-rb) if q['operator']=='<' else square(ra+rb)))
        q['rational_unit']='m²'
    extra.pop('multi_polygon_window');extra.pop('polygon_window');extra.update(nested_circular_window=True,window_nesting=hierarchy,window_intersection_rule=RULE)
    for p,h in zip(extra['window_parts'],hierarchy):p.update(parent=h['parent'],nesting_depth=h['depth'])
    for h in cut:h['intersection_rule']=RULE
    if 'polygon_zero_contact' in extra:extra['polygon_zero_contact']['method']='全部圆形根块及岛区的全部有效外圆候选分段排除所属W、上层及实体孔H后无剩余；分割恒等式严格零接触，非容差归零'
    return gross,node,cut,extra,nodes
