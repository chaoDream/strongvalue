"""Separate disjoint interiors of clipped polygon caps using exact boundary edges."""
from collections import Counter, defaultdict
from decimal import Decimal, localcontext
from fractions import Fraction
from . import store
from .compound_takeoff import op
from .engine import DomainError, precise, quantity_number
from .polygon_cap_partitions import checked, decimal, rational
from .polygon_line_partitions import clip, canonical_polygon
from .polygon_takeoff import cross, inside, on_segment, edges

VERSION = 'polygon-boundary-1.4'
RULE = 'xy-connected-rational-1.0'
MAX_EDGE_TESTS = 2_000_000
CANDIDATE_NOTICE = '上下净面支持共同设计X/Y窗口及两点无限斜线分区；可在原面分区设置中启用“分离净块独立核定”，逐块确定不同做法，默认仍按裁剪范围合计。分块遇到点接触或边界分支会阻断，须进一步核定范围。侧面使用原边局部S/Z，不推断施工接触、搭接或翻边。'
NOTICE = '已启用原面内分离净块独立核定：每个裁剪范围按实际连通边界分块，孔为空白；净块编号不是原图构件编号。仅处理边界没有分支的独立净块；若仅在一点接触、出现边界分支则阻断，须进一步核定分割范围。凸裁剪框不是净区外轮廓，不推断实际施工做法、搭接、翻边或实体接触。未启用的原面仍按原裁剪范围合计。'
ROUNDING = '裁剪、重叠有向边拆分抵消、闭环和逐块扣孔使用精确有理数；每原裁剪区的各净块精确面积合计须等于原净量。派生分子/分母AST按Decimal 80位显示，200位记录显示精度差，不调平或逐块修约；计入块合计后仅乘一次来源重复数量'


def signed_area(points):
    if len(points) < 3:
        return Fraction(0)
    origin = points[0]; total = Fraction(0)
    for a, b in edges(points):
        total = checked(total+checked(cross(origin, a, b)))
    return checked(total/2)


def contour(points, positive):
    if bool(signed_area(points) > 0) != positive:
        points = list(reversed(points))
    return canonical_polygon(points)


def separate(rings, domain, budget):
    """Clip oriented rings, atomize collinear overlaps, cancel directed bridges.

    No snapping or geometric epsilon is used. Degree > 1 is an explicit ambiguity
    instead of guessing which boundaries connect at a zero-width point contact.
    """
    segments = []
    for i, points in enumerate(rings):
        points = contour(points, i == 0)
        for a, b in edges(domain):
            line = (a[1]-b[1], b[0]-a[0], (b[1]-a[1])*a[0]-(b[0]-a[0])*a[1])
            points = clip(points, line, True)
        segments.extend((a,b) for a,b in edges(points) if a != b)
    vertices = sorted({p for segment in segments for p in segment})
    budget[0] += len(segments)*len(vertices)
    if budget[0] > MAX_EDGE_TESTS:
        raise DomainError('连通边界拆分超过2000000次精确边点校验预算，请拆分计量范围')
    directed = Counter()
    for a, b in segments:
        # Every possible overlap lies on an existing straight boundary segment;
        # all original rings are simple and strictly disjoint before clipping.
        axis = 0 if a[0] != b[0] else 1
        points = sorted((p for p in vertices if on_segment(a,b,p)), key=lambda p:p[axis], reverse=a[axis]>b[axis])
        directed.update(zip(points, points[1:]))
    remaining = set()
    for a,b in directed:
        weight = directed[(a,b)]-directed[(b,a)]
        if weight > 1:
            raise DomainError('连通边界重叠无法唯一消除，请核对原轮廓与分区')
        if weight == 1:
            remaining.add((a,b))
    outgoing = defaultdict(list); incoming = defaultdict(list)
    for a,b in remaining:
        outgoing[a].append(b); incoming[b].append(a)
    if any(len(outgoing[p]) != 1 or len(incoming[p]) != 1 for p in set(outgoing)|set(incoming)):
        raise DomainError('净区边界存在点接触或分支，不能自动分块；请进一步分割并核定实际范围')
    loops = []
    while remaining:
        start = min(remaining)[0]; points = []; p = start
        while True:
            points.append(p); q = outgoing[p][0]
            if (p,q) not in remaining:
                raise DomainError('净区边界不能形成独立闭环，请核定分割范围')
            remaining.remove((p,q)); p = q
            if p == start:
                break
        value = signed_area(points)
        if value:
            loops.append((canonical_polygon(points), value))
    outers = sorted((points, value) for points,value in loops if value>0)
    holes = [(points,value) for points,value in loops if value<0]
    result = [[(points,value)] for points,value in outers]
    for points,value in holes:
        parents = [i for i,(outer,_) in enumerate(outers) if inside(points[0],outer)==1]
        if len(parents) != 1:
            raise DomainError('派生孔无法唯一归属某一净块，请核定连通边界')
        result[parents[0]].append((points,value))
    for component in result:
        component[1:] = sorted(component[1:])
    return result


def exact_point(point):
    return tuple(Fraction(int(point['exact_'+axis]['numerator']),int(point['exact_'+axis]['denominator'])) for axis in ('x','y'))


def label_point(part, budget):
    """Find a strictly interior point by exact even-odd vertical intersections."""
    rings = [points for points,_ in part]; xs = sorted({x for points in rings for x,_ in points})
    budget[0] += (len(xs)-1)*sum(len(points) for points in rings)
    if budget[0]>MAX_EDGE_TESTS:
        raise DomainError('连通边界处理超过2000000次精确校验预算，请拆分计量范围')
    best = None
    for x0,x1 in zip(xs,xs[1:]):
        x = checked((x0+x1)/2); ys = []
        for points in rings:
            for a,b in edges(points):
                if min(a[0],b[0])<x<max(a[0],b[0]):
                    ys.append(checked(a[1]+(x-a[0])*(b[1]-a[1])/(b[0]-a[0])))
        ys.sort()
        if len(ys)%2:
            raise DomainError('净块内部定位无法确认，请核对派生边界')
        for y0,y1 in zip(ys[::2],ys[1::2]):
            if y1>y0:
                candidate = (min(x1-x0,y1-y0), x, checked((y0+y1)/2))
                if best is None or candidate>best:
                    best = candidate
    if best is None:
        raise DomainError('净块没有可确认的内部定位点，请核对边界')
    _,x,y = best
    return {'x':str(decimal(x)),'y':str(decimal(y)),'exact_x':rational(x),'exact_y':rational(y)}


@precise
def partition(source, groups, original):
    from .polygon_line_partitions import partition as clipped
    originals = {f['face_id']:f for f in original['faces']}
    selected = set(); legacy = []
    for group in groups:
        if isinstance(group,dict) and 'separate_regions' in group:
            fid = group.get('face_id')
            if group['separate_regions'] is not True or not isinstance(fid,str) or fid not in originals or originals[fid]['geometry_type']!='polygon_cap':
                raise DomainError('独立净块核定仅用于上下原面，须明确启用；停用时移除此字段')
            selected.add(fid)
            legacy.append({k:v for k,v in group.items() if k!='separate_regions'})
        else:
            legacy.append(group)
    result = clipped(source, legacy, original)
    if not selected:
        return result
    faces = []; checks = []; source_clips = []; budget = [0]; per_parent = Counter()
    for f in result['faces']:
        parent = f.get('parent_face_id')
        if parent not in selected:
            faces.append(f); continue
        if f.get('cap_clip_domain'):
            domain = [exact_point(p) for p in f['cap_clip_domain']]
        else:
            x0,x1,y0,y1 = [Fraction(f[k]) for k in ('u_from','u_to','v_from','v_to')]
            domain = [(x0,y0),(x1,y0),(x1,y1),(x0,y1)]
        rings = [[(Fraction(p['x']),Fraction(p['y'])) for p in r['points']] for r in f['boundary_rings']]
        pieces = separate(rings, domain, budget)
        expected = Fraction(int(f['cap_rational_area']['numerator']),int(f['cap_rational_area']['denominator']))
        actual = checked(sum((sum((v for _,v in part),Fraction(0)) for part in pieces),Fraction(0)))
        if actual != expected or not pieces:
            raise DomainError('连通净块精确面积与原裁剪区不闭合，请核定分割范围')
        if len(faces)+len(pieces) > 1000:
            raise DomainError('独立净块及其他计量面超过1000项，请拆分计量范围')
        generated = []
        for ci,part in enumerate(pieces,1):
            per_parent[parent] += 1; net = checked(sum((v for _,v in part),Fraction(0)))
            if net <= 0:
                raise DomainError('连通净块面积须大于零，请核对边界')
            contours = []
            for ri,(points,value) in enumerate(part,1):
                contours.append({'ring_index':ri,'role':'outer' if ri==1 else 'hole','area':str(decimal(abs(value))),**rational(abs(value)),
                    'points':[{'x':str(decimal(x)),'y':str(decimal(y)),'exact_x':rational(x),'exact_y':rational(y)} for x,y in points]})
            identity = [[[str(x),str(y)] for x,y in points] for points,_ in part]
            frac = rational(net)
            generated.append({**f,'face_id':store.digest({'parent':parent,'contours':identity,'rule':RULE}),
                'sub_index':per_parent[parent],'label':f['label']+f' · 独立块{ci}',
                'clip_parent_face_id':f['face_id'],'clip_parent_index':f['index'],'cap_component_index':ci,'cap_component_count':len(pieces),
                'position':f['position']+f'；原裁剪区第{f["sub_index"]}区 / 独立块{ci}，范围按派生边界坐标',
                'calculation':f'派生连通外面积 {decimal(part[0][1])} − 派生孔面积 {decimal(-sum((v for _,v in part[1:]),Fraction(0)))}',
                'area':str(quantity_number(decimal(net))),'cap_rational_area':frac,
                'formula_ast':op('divide',{'constant':frac['numerator']},{'constant':frac['denominator']}),
                'cap_clip_rule':RULE,'cap_component_rings':contours,'cap_label_point':label_point(part,budget),
                'cap_clip_rings':[{k:v for k,v in r.items() if k!='points'} for r in contours]})
        with localcontext() as ctx:
            ctx.prec = 200; display_sum = sum((Decimal(c['area']) for c in generated),Decimal(0))
            checks.append({'clip_parent_face_id':f['face_id'],'parent_face_id':parent,'parent_index':f['parent_index'],'clip_index':f['sub_index'],
                'component_count':len(generated),'clip_area':f['area'],'components_area':str(display_sum),'area_difference':str(display_sum-Decimal(f['area'])),
                'exact_rational_tiling':True,**rational(expected)})
        source_clips.append({'face_id':f['face_id'],'parent_face_id':parent,'parent_index':f['parent_index'],'clip_index':f['sub_index'],
            'position':f['position'],'area':f['area'],'cap_clip_rule':f['cap_clip_rule'],'cap_clip_rings':f['cap_clip_rings'],
            'cap_rational_area':f['cap_rational_area']})
        faces.extend(generated)
    if len(faces)>1000:
        raise DomainError('独立净块及其他计量面超过1000项，请拆分计量范围')
    totals = {k:Decimal(0) for k in result['normal_totals']}
    for f in faces:
        totals[f['normal']] += Decimal(f['area'])
    cap_checks = []
    for c in result.get('surface_cap_partition_checks',[]):
        if c['parent_face_id'] in selected:
            children = [f for f in faces if f.get('parent_face_id')==c['parent_face_id']]
            with localcontext() as ctx:
                ctx.prec=200; display_sum=sum((Decimal(f['area']) for f in children),Decimal(0))
                c={**c,'clipping_region_count':c['child_count'],'child_count':len(children),'children_area':str(display_sum),'area_difference':str(display_sum-Decimal(c['parent_area']))}
        cap_checks.append(c)
    result.update(faces=[{**f,'index':i+1} for i,f in enumerate(faces)],surface_cap_component_checks=checks,surface_cap_clip_sources=source_clips,
        surface_cap_partition_checks=cap_checks,normal_totals={k:str(v) for k,v in totals.items()},total_area=str(sum((Decimal(f['area']) for f in faces),Decimal(0))))
    return result
