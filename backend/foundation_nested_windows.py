"""Explicit nearest-parent islands inside excluded contact-window holes."""
from fractions import Fraction
from .engine import DomainError
from .compound_takeoff import text
from .polygon_takeoff import edges,inside,intersects
from . import foundation_polygon_windows as polygon
from . import foundation_simple_windows as simple
from . import foundation_perforated_windows as perforated
from . import foundation_multi_windows as multi

VERSION='foundation-horizontal-formwork-1.12'
METHOD='nested_polygon_vertical_sweep'
RULE='各根块及内孔中的岛区分别按外轮廓减所属内孔裁切真实接触；明确最近父块/内孔，精确核对平面嵌套层级，不填实其余空区；逐块200位解析积分保留80位，80位累计后继承全部基础个数一次，仅总量有据修约。'
NOTICE=('水平顶底、台阶平台及内部孔端支持显式父块内孔中的嵌套支模岛区。2至4块，各轮廓3至12点，每块最多4孔及36点、全部8孔及48点。'
        '每块须明确根块或最近父块B及其内孔W，逐块支模及关系说明、逐坐标原件，共用单位与基础中心XY；至少存在一项真实嵌套。'
        '岛区外轮廓须严格在所选父块内孔内，不能接触或穿越；其他块严格分离，不能相交、接触或落入其他块支模净区。'
        '父关系不得成环、跳层或与真实几何不符，支持最多四块形成的平面层级0至3；层级不代表Z、基础层号或数量。'
        '内孔W仅从所属块排除，岛区以独立块计入；不创建基础实体孔H、不扣混凝土或生成孔壁/孔端，不填实未选空区。'
        '实际孔H只扣各块净接触交集，全部基础数量累计后继承一次，全部有效片段无剩余才证明整体严格零接触。'
        '完整窗口净区以精确有理数竖向扫描防重，残余内孔空区可另项，岛区正重叠阻断，跨记录及混合旧窗口同样检查。'
        '原件、关系、真实接触、材料及修约须独立复核；曲边、接触或交叠轮廓合并、任意水平分区、斜面及个体不同做法另核。')


def enabled(d):
    return isinstance(d,dict) and isinstance(d.get('plan_range'),dict) and d['plan_range'].get('shape')=='nested_polygon'


def ring_points(d):
    if not enabled(d):return multi.ring_points(d)
    c=d['plan_range'];return [r for p in c['parts'] for r in perforated.ring_points(dict(plan_range=multi.part_config(c,p)))]


def bounds(d):
    pts=[p for ring in ring_points(d) for p in ring]
    return min(x for x,y in pts),max(x for x,y in pts),min(y for x,y in pts),max(y for x,y in pts)


def strict_inside(child,parent):
    return all(inside(p,parent)==1 for p in child) and not any(intersects(a,b,p,q) for a,b in edges(child) for p,q in edges(parent))


def topology(c):
    rings=[[[tuple(map(Fraction,p)) for p in ring] for ring in perforated.ring_points(dict(plan_range=multi.part_config(c,part)))] for part in c['parts']]
    checks=[];ancestors={i:[] for i in range(1,len(rings)+1)}
    for i,rs in enumerate(rings,1):
        outer=rs[0]
        for j,other_rs in enumerate(rings[:i-1],1):
            other=other_rs[0]
            if any(intersects(a,b,p,q) for a,b in edges(outer) for p,q in edges(other)):
                raise DomainError('嵌套窗口块外轮廓不能接触、交叉或重叠')
            parent,child=(j,i) if inside(outer[0],other)==1 else (i,j) if inside(other[0],outer)==1 else (None,None)
            if parent is None:
                checks.append(dict(left='B'+str(j),right='B'+str(i),relation='strictly_disjoint'));continue
            holes=[hi for hi,h in enumerate(rings[parent-1][1:],1) if strict_inside(rings[child-1][0],h)]
            if len(holes)!=1:raise DomainError('B'+str(child)+'外轮廓须严格在B'+str(parent)+'的一个内孔空区内，不能穿越孔边或重叠父块支模净区')
            ancestors[child].append((parent,holes[0]))
            checks.append(dict(left='B'+str(parent),right='B'+str(child),relation='inside_window_hole',parent_hole_index=holes[0]))
    if not any(ancestors.values()):raise DomainError('嵌套岛区模式须至少一块严格位于另一块内孔中；分离块请用多块分离模式')
    hierarchy=[]
    for i in range(1,len(rings)+1):
        nearest=[(p,h) for p,h in ancestors[i] if not any(p in {a for a,_ in ancestors[other]} for other,_ in ancestors[i] if other!=p)]
        if len(nearest)>1:raise DomainError('不能确认唯一最近父块，请核对嵌套轮廓')
        parent=dict(block_index=nearest[0][0],hole_index=nearest[0][1]) if nearest else None
        if c['parts'][i-1]['parent']!=parent:raise DomainError('B'+str(i)+'须显式选择几何核定的最近父块及内孔，不能跳层、选错孔或将岛区声明为根块')
        hierarchy.append(dict(block_index=i,parent=parent,depth=len(ancestors[i])))
    return checks,hierarchy


def validate(c):
    if not isinstance(c,dict) or set(c)!={'shape','parts','unit','coordinate_basis','coordinate_evidence_id','scope_confirmed'} or not isinstance(c['parts'],list) or not 2<=len(c['parts'])<=4:
        raise DomainError('嵌套岛区窗口须2至4块、共同单位及XY基准原件和确认')
    names=set();points=0;holes=0
    for i,p in enumerate(c['parts'],1):
        if not isinstance(p,dict) or set(p)!={'name','parameters','evidence','measurement_basis','evidence_id','holes','parent'} or not isinstance(p['holes'],list):
            raise DomainError('嵌套块须名称、外轮廓XY及逐点原件、支模与父块关系说明及原件、内孔列表、明确父关系')
        name=text(p['name'],'嵌套块名称',200)
        if name in names:raise DomainError('嵌套块名称不能重复')
        names.add(name);text(p['measurement_basis'],'本块实际支模与父块内孔关系说明');text(p['evidence_id'],'本块支模及关系原件',200)
        pc=multi.part_config(c,p);(perforated.validate if p['holes'] else simple.validate)(pc)
        points+=len(p['parameters'])//2+sum(len(h['parameters'])//2 for h in p['holes']);holes+=len(p['holes'])
        parent=p['parent']
        if parent is not None and (not isinstance(parent,dict) or set(parent)!={'block_index','hole_index'} or type(parent['block_index']) is not int or type(parent['hole_index']) is not int or not 1<=parent['block_index']<=len(c['parts']) or parent['block_index']==i or parent['hole_index']<1):
            raise DomainError('父关系须为根块空值，或当前其他块及内孔的整数序号，不得自指')
    if points>48 or holes>8:raise DomainError('嵌套窗口全部最多48点及8个内孔')
    for i,p in enumerate(c['parts'],1):
        parent=p['parent']
        if parent is not None and parent['hole_index']>len(c['parts'][parent['block_index']-1]['holes']):raise DomainError('父块内孔不存在，请重新选择当前内孔')
        visited={i}
        while parent is not None:
            j=parent['block_index']
            if j in visited:raise DomainError('父块关系不能成环')
            visited.add(j);parent=c['parts'][j-1]['parent']
    topology(c)


def separation(a,b):
    return simple.separation(a,b,ring_points=ring_points,method=METHOD)


def geometry(d,item,tier,original,full_ast,holes,key,dimensions,refs,conversions):
    checks,hierarchy=topology(d['plan_range']);depths={h['block_index']:h['depth'] for h in hierarchy}
    def trace(parts):
        result=multi.trace(parts);result.update(rule_version=VERSION,method='显式父块内孔岛区逐块解析积分及累计',integration_rule=RULE)
        for q in result['terms']+result.get('partitions',[]):q['nesting_depth']=depths[q['block_index']]
        return result
    gross,node,cut,extra,nodes=multi.geometry(d,item,tier,original,full_ast,holes,key,dimensions,refs,conversions,topology_fn=lambda _:checks,trace_fn=trace)
    extra.update(nested_polygon_window=True,window_nesting=hierarchy,window_intersection_rule=RULE)
    for p,h in zip(extra['window_parts'],hierarchy):p.update(parent=h['parent'],nesting_depth=h['depth'])
    for h in cut:h['intersection_rule']='各根块及岛区净区分别与实体孔相交后累计，不重复父块内孔；'+RULE
    return gross,node,cut,extra,nodes
