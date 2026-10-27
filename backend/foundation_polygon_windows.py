"""Source-bound convex XY windows; straight-edge/circle analytic intersections."""
from decimal import Decimal, localcontext, ROUND_HALF_EVEN
from . import store
from .compound_takeoff import op, text, PI
from .engine import DomainError
from .measurement_units import LENGTH, VERSION as UNIT_VERSION
from .planar_takeoff import linear
from .curved_intersections import N, square, ast, value, term, PRECISION
from .disk_window_intersection import integral
from .profile_window_intersections import Scene, profile
from . import foundation_step_formwork as steps
from . import foundation_hole_end_formwork as ends
from .foundation_formwork import layers
from .foundation_void_formwork import dim

VERSION = 'foundation-horizontal-formwork-1.8'
RULE = '按顶点、直线交点、直线与圆及圆圆交点划分X区间；逐段取真实上下界，扣除排除轮廓并集，以直线或圆弧原函数解析积分，不用网格或容差。'
NOTICE = ('整体顶底、台阶平台及内部孔端支持3至12顶点的严格凸多边形XY窗口，含有据旋转矩形和斜边。'
          '顶点按边界顺时针或逆时针依次填写，逐坐标关联原件；共用基础中心和孔位XY正向，不从角度或屏幕推算。'
          '窗口可超出实体，只计真实交集；平台排除紧邻上层完整截面，孔端取混凝土正确侧层并扣同侧相接孔。'
          '工作200位Decimal，保留80位解析过程和AST；不能确认则阻断，不以容差归零。'
          '同端面窗口按分离轴检查实际窗口正重叠，包含与整面重复均拒绝，边界相接允许，跨记录同样检查。'
          '数量继承全部基础个数一次，仅总净面积按有据位数HALF_UP修约。原件、接触工法、材料和修约须独立复核。'
          '凹多边形、曲边窗口、斜面、斜孔、个体不同做法、支撑措施和专业计量例外另核。')


def enabled(d):
    return isinstance(d, dict) and isinstance(d.get('plan_range'), dict) and d['plan_range'].get('shape') == 'convex_polygon'


def keys(c):
    return tuple(axis+str(i) for i in range(1, len(c['parameters'])//2+1) for axis in ('x', 'y'))


def vertices(c):
    return [(linear(c['parameters']['x'+str(i)], c['unit']), linear(c['parameters']['y'+str(i)], c['unit']))
            for i in range(1, len(c['parameters'])//2+1)]


def cross(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])


def validate(c):
    fields = {'shape', 'parameters', 'unit', 'evidence', 'coordinate_basis', 'coordinate_evidence_id', 'scope_confirmed'}
    if set(c) != fields or not isinstance(c['parameters'], dict) or not 6 <= len(c['parameters']) <= 24 or len(c['parameters']) % 2:
        raise DomainError('凸多边形窗口须3至12个有序顶点，仅填写形状、逐顶点XY坐标及原件、单位、共同基准和确认')
    expected = set(keys(c))
    if set(c['parameters']) != expected or not isinstance(c['evidence'], dict) or set(c['evidence']) != expected:
        raise DomainError('凸多边形窗口须连续编号x1/y1至xn/yn，逐坐标关联原件')
    for eid in c['evidence'].values(): text(eid, '窗口顶点坐标原件', 200)
    text(c['coordinate_basis'], '窗口与孔位共用的XY基准')
    text(c['coordinate_evidence_id'], '窗口坐标基准原件', 200)
    if c['scope_confirmed'] is not True: raise DomainError('须据原件确认凸多边形窗口坐标和共用XY正向')
    pts = vertices(c)
    # All other vertices must lie strictly on the same side of every edge.
    # Local turn tests alone would accidentally admit a self-intersecting star.
    signs = []
    for i, a in enumerate(pts):
        b = pts[(i+1) % len(pts)]
        values = [cross(a, b, p) for j, p in enumerate(pts) if j not in (i, (i+1) % len(pts))]
        if any(v == 0 for v in values) or not (all(v > 0 for v in values) or all(v < 0 for v in values)):
            raise DomainError('窗口须严格凸且无重复、共线或自交顶点；按边界顺序填写，不自动排序')
        signs.append(values[0] > 0)
    if len(set(signs)) != 1: raise DomainError('窗口顶点顺序无效，须沿同一方向绕行')


def bounds(d):
    pts = vertices(d['plan_range'])
    return min(x for x, y in pts), max(x for x, y in pts), min(y for x, y in pts), max(y for x, y in pts)


def points(d):
    if enabled(d): return vertices(d['plan_range'])
    from .foundation_horizontal_windows import bounds as rect_bounds
    x1, x2, y1, y2 = rect_bounds(d)
    return [(x1,y1), (x2,y1), (x2,y2), (x1,y2)]


def separation(a, b):
    """Exact Decimal separating-axis test; zero means shared boundary."""
    pa, pb = points(a), points(b)
    best = None
    for pts in (pa, pb):
        for p, q in zip(pts, pts[1:]+pts[:1]):
            axis = (p[1]-q[1], q[0]-p[0])
            av = [x*axis[0]+y*axis[1] for x,y in pa]
            bv = [x*axis[0]+y*axis[1] for x,y in pb]
            gap = max(min(av)-max(bv), min(bv)-max(av))
            if best is None or gap > best['gap']: best = dict(gap=gap, axis=axis)
    return best


class PolygonScene(Scene):
    def __init__(self, profiles, pts, bind, rings=None):
        self.pts = pts
        self.bind = bind
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        box = [min(xs,key=lambda n:n.v), max(xs,key=lambda n:n.v), min(ys,key=lambda n:n.v), max(ys,key=lambda n:n.v)]
        super().__init__(profiles, [n.v for n in box], dict(zip(('x_start','x_end','y_start','y_end'), (n.a for n in box))))
        self.cuts={v:bind(n) for v,n in self.cuts.items()}
        self.edges = []
        equations = []
        segments = [(i, a, b) for i,(a,b) in enumerate(zip(pts,pts[1:]+pts[:1]),1)] if rings is None else [
            (('外轮廓' if ri==0 else '内孔W'+str(ri))+'边'+str(i),a,b)
            for ri,ring in enumerate(rings) for i,(a,b) in enumerate(zip(ring,ring[1:]+ring[:1]),1)]
        for i,(x,y),(X,Y) in segments:
            self.add(x)
            if x.v == X.v: continue
            m = bind((Y-y)/(X-x)); intercept = bind(y-m*x)
            self.edges.append((min(x.v,X.v), max(x.v,X.v), ('slope',('窗口边'+str(i)) if rings is None else str(i), (m,intercept), 1)))
            equations.append((bind(y-Y),bind(X-x),bind(x*Y-X*y),[(x,y),(X,Y)]))
        equations += [(N(0),N(1),N(0)-p[k],[]) for p in profiles if 'circle' not in p for k in ('bottom','top')]
        for p in profiles:
            if 'circle' not in p: continue
            cx,cy,r,_ = p['circle']
            for aa,bb,cc,endpoints in equations:
                # Implicit line coefficients remain exact for finite input
                # decimals; dividing to a slope first would split shared vertices.
                c = bind(aa*cx+bb*cy+cc); A = bind(square(aa)+square(bb)); B = bind(N(2)*aa*c); C = bind(square(c)-square(bb)*square(r))
                disc = bind(square(B)-N(4)*A*C)
                if disc.v < 0: continue
                known = [x for x,y in endpoints if square(x-cx).v+square(y-cy).v==square(r).v]
                if len(known)==2: roots=known
                elif known: roots=[known[0],cx-B/A-(known[0]-cx)]
                else:
                    root = bind(disc.sqrt()); roots=[cx+(N(0)-B-root)/(N(2)*A),cx+(N(0)-B+root)/(N(2)*A)]
                for x in roots:self.add(bind(x))
        for i,(a,b,c,ends_a) in enumerate(equations):
            for A,B,C,ends_b in equations[:i]:
                determinant = bind(a*B-A*b)
                if not determinant.v:continue
                shared = [x for x,y in ends_a if (A*x+B*y+C).v==0]+[x for x,y in ends_b if (a*x+b*y+c).v==0]
                self.add(shared[0] if shared else bind((b*C-B*c)/determinant))
        pts = [self.cuts[v] for v in sorted(self.cuts)]
        self.intervals = list(zip(pts, pts[1:]))

    @staticmethod
    def at(bound, x):
        if bound[0] == 'slope': return bound[2][0].v*x+bound[2][1].v
        return Scene.at(bound, x)

    @staticmethod
    def bound_node(bound,x):
        if bound[0]=='slope':return bound[2][0]*x+bound[2][1]
        return Scene.bound_node(bound,x)

    def vertical(self, profiles, x):
        base = super().vertical(profiles, x)
        if base is None: return None
        edges = [e for lo,hi,e in self.edges if lo < x < hi]
        if len(edges) != 2: raise DomainError('凸窗口分段未得到两条边界，当前精度不能确认')
        low = max([base[0], min(edges,key=lambda b:self.at(b,x))],key=lambda b:self.at(b,x))
        high = min([base[1], max(edges,key=lambda b:self.at(b,x))],key=lambda b:self.at(b,x))
        return (low,high) if self.at(low,x) < self.at(high,x) else None

    @staticmethod
    def integrate(bound, a, b):
        if bound[0] != 'slope': return integral(bound,a,b)
        m,c = bound[2]
        # Factored form avoids cancellation between large coordinate squares.
        return (m*(a+b)/2+c)*(b-a)

    def measure(self, include, exclude=(), certify=False):
        total = N(0); terms = []; partitions=[]
        for a,b in self.intervals:
            mid = (a.v+b.v)/2; region = self.vertical(include,mid)
            if region is None: continue
            remaining = [region]
            used=[]
            for p in exclude:
                clip = self.vertical([p],mid)
                if clip is None: continue
                used.append((p['label'],clip))
                next_ranges = []
                for lo,hi in remaining:
                    if self.at(clip[1],mid) <= self.at(lo,mid) or self.at(clip[0],mid) >= self.at(hi,mid):
                        next_ranges.append((lo,hi)); continue
                    if self.at(lo,mid) < self.at(clip[0],mid): next_ranges.append((lo,clip[0]))
                    if self.at(clip[1],mid) < self.at(hi,mid): next_ranges.append((clip[1],hi))
                remaining = next_ranges
            if certify:
                midpoint=(a+b)/2
                def witness(pair):
                    lo,hi=pair;l=self.bind(self.bound_node(lo,midpoint));h=self.bind(self.bound_node(hi,midpoint))
                    return dict(lower_boundary=lo[1],upper_boundary=hi[1],lower=str(value(l)),upper=str(value(h)),formula_asts=dict(lower=ast(l),upper=ast(h)))
                partitions.append(dict(x_from=str(value(a)),x_to=str(value(b)),x_mid=str(value(midpoint)),
                    included=witness(region),excluded=[dict(profile=label,**witness(v)) for label,v in used],remaining=[witness(v) for v in remaining],
                    formula_asts=dict(x_from=ast(a),x_to=ast(b),x_mid=ast(midpoint))))
            for low,high in remaining:
                strip = self.bind(self.bind(self.integrate(high,a,b))-self.bind(self.integrate(low,a,b)))
                if strip.v <= 0: raise DomainError('多边形窗口正交集积分非正，当前精度不能确认')
                total = self.bind(total+strip)
                terms.append({**term('分段'+str(len(terms)+1),strip,x_from=a,x_to=b), 'lower_boundary':low[1], 'upper_boundary':high[1]})
        return value(total), ast(total), dict(rule_version=VERSION,method='凸多边形窗口直线与圆弧解析积分',integration_rule=RULE,
                   working_digits=PRECISION,output_digits=80,pi_constant=PI,terms=terms,**({'partitions':partitions} if certify else {}))


def geometry(d,item,tier,original,full_ast,holes,key,dimensions,refs,conversions,scene_type=PolygonScene,inner_rings=(),parameter_keys=keys,window_builder=None):
    c = d['plan_range']; nodes = {}
    for k in parameter_keys(c):
        dimensions[key+'.'+k] = str(c['parameters'][k]); refs[key+'.'+k] = c['evidence'][k]
        nodes[k] = op('multiply',{'dimension':key+'.'+k},{'constant':LENGTH[c['unit']]})
        conversions.append(dict(parameter=key+'.'+k,original_value=str(c['parameters'][k]),source_unit=c['unit'],factor=LENGTH[c['unit']],canonical_value=str(linear(c['parameters'][k],c['unit'])),canonical_unit='m',rule_version=UNIT_VERSION))
    refs[key+'.plan_coordinate'] = c['coordinate_evidence_id']
    inner_nodes=[]
    for i,ring in enumerate(inner_rings,1):
        ring_key=key+'.window_holes.'+str(i); rn={}
        refs[ring_key+'.basis']=ring['evidence_id']
        for k in keys(ring):
            name=ring_key+'.'+k; dimensions[name]=str(ring['parameters'][k]);refs[name]=ring['evidence'][k]
            rn[k]=op('multiply',{'dimension':name},{'constant':LENGTH[c['unit']]})
            conversions.append(dict(parameter=name,original_value=dimensions[name],source_unit=c['unit'],factor=LENGTH[c['unit']],canonical_value=str(linear(ring['parameters'][k],c['unit'])),canonical_unit='m',rule_version=UNIT_VERSION))
        inner_nodes.append(rn)
    with localcontext() as ctx:
        ctx.prec = PRECISION; ctx.rounding = ROUND_HALF_EVEN
        definitions=[]; expressions={}
        def bind(n):
            if set(n.a) in ({'constant'},{'dimension'}):return n
            fingerprint=store.digest(n.a)
            if fingerprint not in expressions:
                name=key+'.derived.'+str(len(definitions)+1)
                definitions.append(dict(dimension=name,value=str(n.v),formula_ast=dict(op='decimal_precision',args=[n.a],working_digits=PRECISION,output_digits=PRECISION,mode='HALF_EVEN')))
                dimensions[name]=str(n.v);expressions[fingerprint]=name
            return N(n.v,{'dimension':expressions[fingerprint]})
        if window_builder is not None:
            pts,window=window_builder(c,nodes)
        else:
            pts = [(N(x,nodes['x'+str(i)]),N(y,nodes['y'+str(i)])) for i,(x,y) in enumerate(vertices(c),1)]
            # Translate the shoelace sum to the first vertex for numerical stability.
            window = N(0); origin = pts[0]
            for a,b in zip(pts[1:-1],pts[2:]):
                window = window+(a[0]-origin[0])*(b[1]-origin[1])-(a[1]-origin[1])*(b[0]-origin[0])
            window = window/N(2)
            if window.v < 0: window = N(0)-window
        all_points=[pts]; ring_rows=[]; outer_window=window; hole_window=N(0)
        if inner_rings:
            ring_rows.append(dict(index=0,name='外轮廓',role='outer',area=str(value(window)),formula_ast=ast(window),vertices=[dict(x=str(value(x)),y=str(value(y))) for x,y in pts]))
            for ri,(ring,rn) in enumerate(zip(inner_rings,inner_nodes),1):
                points=[(N(x,rn['x'+str(i)]),N(y,rn['y'+str(i)])) for i,(x,y) in enumerate(vertices({**ring,'unit':c['unit']}),1)]
                origin=points[0];area=N(0)
                for a,b in zip(points[1:-1],points[2:]):area=area+(a[0]-origin[0])*(b[1]-origin[1])-(a[1]-origin[1])*(b[0]-origin[0])
                area=area/N(2)
                if area.v<0:area=N(0)-area
                hole_window=hole_window+area;all_points.append(points)
                ring_rows.append(dict(index=ri,name=ring['name'],role='window_hole',area=str(value(area)),formula_ast=ast(area),vertices=[dict(x=str(value(x)),y=str(value(y))) for x,y in points]))
            window=outer_window-hole_window
        cp,ca,_,_ = steps.section(item,tier) if 'foundation_levels' in item else ends.single_section(item,tier)
        layer = profile(cp,ca,'混凝土侧层' if d['face'] in ends.FACES else '端层')
        include = [layer]; exclude = []
        if d['face'] in ends.FACES:
            hp,ha,_,_ = ends.profile(item,ends.hole(item,d['hole_index']))
            include.insert(0,profile(hp,ha,'所选孔'))
        if d['face'] == 'step':
            up,ua,_,_ = steps.section(item,layers(item)[d['level_index']])
            exclude = [profile(up,ua,'上层完整截面')]
        def scene(profiles): return scene_type(profiles,pts,bind,**({'rings':all_points} if inner_rings else {}))
        def measure(inc, exc=()): return scene(inc+list(exc)).measure(inc,exc)
        gross,node,trace = measure(include,exclude)
        hole_profiles = []
        for h in holes:
            hp = {k:Decimal(v) for k,v in h['normalized_parameters'].items()}
            ha = {k:dim(item,{'index':h['hole_index'],'unit':h['unit']},k) for k in hp}
            hole_profiles.append(profile(hp,ha,'实际孔H'+str(h['hole_index'])))
        clipped = []
        for h,hp in zip(holes,hole_profiles):
            amount,an,ht = measure(include+[hp],exclude)
            full = h.get('full_area',h['single_area']); full_node = h.get('formula_asts',{}).get('full_area',h['formula_ast'])
            q = {k:v for k,v in h.items() if k not in ('curved_intersection','formula_asts','single_area','formula_ast','intersection_rule')}
            q.update(full_area=full,single_area=str(amount),formula_ast=an,curved_intersection=ht,
                     formula_asts=dict(full_area=full_node,single_area=an),intersection_rule='真实水平接触范围∩凸多边形窗口∩实际孔；扣除上层完整截面后计量')
            if d['face'] == 'step':
                raw,rn,rt = measure([hp]); covered,cn,ct = measure([hp]+exclude)
                q.update(window_hole_area=str(raw),covered_area=str(covered),window_hole_curved_intersection=rt)
                q['formula_asts'].update(window_hole_area=rn,covered_area=cn)
                # Existing platform curve tables display the common upper coverage.
                q['curved_intersection'] = ct
                q['polygon_cut_trace'] = ht
            if d['face'] in ends.FACES:
                q.update(full_end_intersection_area=h['single_area'],window_direct_area=str(amount))
                q['formula_asts'].update(full_end_intersection_area=h['formula_ast'],window_direct_area=an)
            clipped.append(q)
        net_scene=scene(include+exclude+hole_profiles)
        direct_net,net_node,net_trace = net_scene.measure(include,exclude+hole_profiles,certify=True)
        zero_proof = None
        if gross > 0 and not net_trace['terms'] and clipped:
            # Absence of remaining strips follows from all boundary-order events,
            # not from rounding a small residual to zero.
            index = next((i for i in reversed(range(len(clipped))) if Decimal(clipped[i]['single_area']) > 0),None)
            if index is not None:
                other = [h for i,h in enumerate(clipped) if i != index]
                amount = gross-sum((Decimal(h['single_area']) for h in other),Decimal(0))
                if amount < 0: raise DomainError('多边形窗口完整覆盖余项非正，当前精度不能确认')
                h = clipped[index]; an = op('subtract',node,op('add',*[h['formula_ast'] for h in other]))
                h.update(single_area=str(amount),formula_ast=an); h['formula_asts']['single_area'] = an
                h['intersection_rule'] += '；全部边界分段无剩余接触，按毛面积减其他孔扣的分割恒等式计量'
                zero_proof = dict(method='全部边界事件分段扣除上层与实际孔后无剩余区间，严格零接触；分割余项非容差归零',canonical_hole_index=h['hole_index'],net_trace=net_trace)
        bx = tuple(fn(p[axis].v for p in pts) for axis,fn in [(0,min),(0,max),(1,min),(1,max)]) if window_builder else bounds(d); ast_bounds = {}
        for k,axis,fn in [('x_start',0,min),('x_end',0,max),('y_start',1,min),('y_end',1,max)]:
            ast_bounds[k] = fn(pts,key=lambda p:p[axis].v)[axis].a
        extra = dict(**dict(zip(('x_start','x_end','y_start','y_end'),map(str,bx))),plan_bounds=dict(zip(('x_start','x_end','y_start','y_end'),map(str,bx))),
                     polygon_window=True,window_vertices=[dict(x=str(value(x)),y=str(value(y))) for x,y in pts],
                     full_face_area=str(original),window_area=str(value(window)),window_intersection_rule=RULE,
                     window_curved_intersection=trace,polygon_gross_trace=trace,polygon_net_trace=net_trace,polygon_direct_net_area=str(direct_net),
                     window_expression_definitions=definitions,
                     plan_frame_hash=store.digest(dict(basis=c['coordinate_basis'].strip(),evidence_id=c['coordinate_evidence_id'].strip())))
        if zero_proof: extra['polygon_zero_contact'] = zero_proof
        if inner_rings:
            extra.update(window_rings=ring_rows,window_outer_area=str(value(outer_window)),window_exclusion_area=str(value(hole_window)))
            ast_bounds.update(window_outer_area=ast(outer_window),window_exclusion_area=ast(hole_window))
        if d['face'] in ends.FACES: extra['hole_end_window'] = True
        if d['face'] == 'step':
            lo,ln,lt = measure(include); up,un,ut = measure(include+exclude)
            extra.update(window_lower_area=str(lo),window_upper_area=str(up),window_curved_intersection=lt,window_upper_curved_intersection=ut)
            ast_bounds.update(window_lower_area=ln,window_upper_area=un)
        return gross,node,clipped,extra,dict(**ast_bounds,full_face_area=full_ast,window_area=ast(window),polygon_direct_net_area=net_node)
