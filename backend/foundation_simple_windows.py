"""Evidence-bound simple polygon windows with multiple vertical interior spans."""
from fractions import Fraction
from .compound_takeoff import text, PI
from .engine import DomainError
from .curved_intersections import N, value, ast, term, PRECISION
from .polygon_takeoff import simple, cross, edges, intersects
from .profile_window_intersections import Scene
from . import foundation_polygon_windows as convex

VERSION = 'foundation-horizontal-formwork-1.9'
RULE = '按所有顶点及直线/圆弧边界交点划分X区间；窗口按奇偶规则配对全部内部纵向片段，逐片裁切并扣除覆盖轮廓，以原函数解析积分，不用网格或容差。'
NOTICE = ('基础整体顶底、台阶平台及内部孔端支持3至12顶点的简单直边多边形窗口，可含凹角、L形及U形。'
          '按边界顺序填写真实XY及逐坐标原件，窗口无自带内孔；重复、共线、自交及非相邻接触均拒绝，不排序或自动修形。'
          '共用基础中心和孔位XY正向，Z及全部基础个数只读继承；窗口可越过实体，仅计真实交集。'
          '同一X区间内的多个内部片段分别积分，不将凹口或分离片段之间空区算入；平台排除上层，孔端取正确侧层及同侧相接孔。'
          '200位Decimal工作、80位输出并保留有序AST依赖，完整覆盖须全部片段无剩余，不以容差归零。'
          '同端面完整窗口以精确有理数竖向扫描检查，正重叠/包含拒绝，共边或点接触可分项，跨记录同样检查。'
          '全部个数继承一次，仅总量有据修约；实际接触、工法、材料和修约须独立复核。'
          '曲边窗口、多外轮廓、自带内孔、任意水平分区、斜面、斜孔、个体不同做法及专业例外另核。')


def enabled(d):
    return isinstance(d,dict) and isinstance(d.get('plan_range'),dict) and d['plan_range'].get('shape')=='simple_polygon'


def validate(c):
    fields={'shape','parameters','unit','evidence','coordinate_basis','coordinate_evidence_id','scope_confirmed'}
    if set(c)!=fields or not isinstance(c['parameters'],dict) or not 6<=len(c['parameters'])<=24 or len(c['parameters'])%2:
        raise DomainError('简单多边形窗口须3至12个有序顶点、逐坐标原件、单位、共同XY基准及确认')
    expected=set(convex.keys(c))
    if set(c['parameters'])!=expected or not isinstance(c['evidence'],dict) or set(c['evidence'])!=expected:
        raise DomainError('多边形窗口须连续编号x1/y1至xn/yn，逐坐标关联原件')
    for eid in c['evidence'].values():text(eid,'窗口顶点原件',200)
    text(c['coordinate_basis'],'窗口与孔位共用的XY基准');text(c['coordinate_evidence_id'],'窗口坐标基准原件',200)
    if c['scope_confirmed'] is not True:raise DomainError('须据原件确认简单多边形窗口坐标及共同正向')
    pts=[tuple(map(Fraction,p)) for p in convex.vertices(c)]
    simple(pts)
    if any(cross(pts[i-1],p,pts[(i+1)%len(pts)])==0 for i,p in enumerate(pts)):
        raise DomainError('窗口相邻三顶点不得共线，请保留实际转角并删除冗余点')
    if sum(a[0]*b[1]-a[1]*b[0] for a,b in edges(pts))==0:
        raise DomainError('窗口轮廓面积须大于零')


def points(d):
    if enabled(d):return convex.vertices(d['plan_range'])
    return convex.points(d)


def rational(v):
    return str(v.numerator) if v.denominator==1 else str(v.numerator)+'/'+str(v.denominator)


def spans(poly,x):
    ys=sorted(a[1]+(x-a[0])*(b[1]-a[1])/(b[0]-a[0]) for a,b in edges(poly) if min(a[0],b[0])<x<max(a[0],b[0]))
    if len(ys)%2:raise DomainError('多边形窗口防重边界未闭合')
    return list(zip(ys[::2],ys[1::2]))


def separation(a,b,ring_points=None,method='simple_polygon_vertical_sweep'):
    """Exact positive-area overlap test, including containment and concave bays."""
    ra,rb=([[tuple(map(Fraction,p)) for p in ring] for ring in (ring_points(d) if ring_points else [points(d)])] for d in (a,b))
    pa,pb=([p for ring in rs for p in ring] for rs in (ra,rb))
    ea,eb=([edge for ring in rs for edge in edges(ring)] for rs in (ra,rb))
    cuts={p[0] for p in pa+pb}
    for p,q in ea:
        for P,Q in eb:
            u=(q[0]-p[0],q[1]-p[1]);v=(Q[0]-P[0],Q[1]-P[1]);den=u[0]*v[1]-u[1]*v[0]
            if not den:continue
            t=((P[0]-p[0])*v[1]-(P[1]-p[1])*v[0])/den
            s=((P[0]-p[0])*u[1]-(P[1]-p[1])*u[0])/den
            if 0<=t<=1 and 0<=s<=1:cuts.add(p[0]+t*u[0])
    partitions=[];xs=sorted(cuts)
    for lo,hi in zip(xs,xs[1:]):
        def interior(rings):
            ys=sorted(y for ring in rings for pair in spans(ring,mid) for y in pair)
            return list(zip(ys[::2],ys[1::2]))
        mid=(lo+hi)/2;left,right=interior(ra),interior(rb)
        if any(max(l,L)<min(h,H) for l,h in left for L,H in right):return dict(overlap=True)
        if left and right:
            partitions.append(dict(x_from=rational(lo),x_to=rational(hi),x_mid=rational(mid),
                                   left=[[rational(v) for v in pair] for pair in left],right=[[rational(v) for v in pair] for pair in right]))
    touching=any(intersects(p,q,P,Q) for p,q in ea for P,Q in eb)
    return dict(overlap=False,state='boundary_touch' if touching else 'separated',method=method,
                rational_unit='m',partitions=partitions,formula_asts={})


class SimpleScene(convex.PolygonScene):
    def vertical_spans(self,profiles,x):
        base=Scene.vertical(self,profiles,x)
        if base is None:return []
        bounds=sorted((e for lo,hi,e in self.edges if lo<x<hi),key=lambda e:self.at(e,x))
        if len(bounds)%2:raise DomainError('简单多边形窗口边界未闭合，当前精度不能确认')
        result=[]
        for low,high in zip(bounds[::2],bounds[1::2]):
            low=max((low,base[0]),key=lambda e:self.at(e,x));high=min((high,base[1]),key=lambda e:self.at(e,x))
            if self.at(low,x)<self.at(high,x):result.append((low,high))
        return result

    def measure(self,include,exclude=(),certify=False):
        total=N(0);terms=[];partitions=[]
        for a,b in self.intervals:
            mid=(a.v+b.v)/2
            for region_index,region in enumerate(self.vertical_spans(include,mid),1):
                remaining=[region];used=[]
                for p in exclude:
                    for clip in self.vertical_spans([p],mid):
                        if self.at(clip[1],mid)<=self.at(region[0],mid) or self.at(clip[0],mid)>=self.at(region[1],mid):continue
                        used.append((p['label'],clip));next_ranges=[]
                        for lo,hi in remaining:
                            if self.at(clip[1],mid)<=self.at(lo,mid) or self.at(clip[0],mid)>=self.at(hi,mid):
                                next_ranges.append((lo,hi));continue
                            if self.at(lo,mid)<self.at(clip[0],mid):next_ranges.append((lo,clip[0]))
                            if self.at(clip[1],mid)<self.at(hi,mid):next_ranges.append((clip[1],hi))
                        remaining=next_ranges
                if certify:
                    midpoint=(a+b)/2
                    def witness(pair):
                        lo,hi=pair;l=self.bind(self.bound_node(lo,midpoint));h=self.bind(self.bound_node(hi,midpoint))
                        return dict(lower_boundary=lo[1],upper_boundary=hi[1],lower=str(value(l)),upper=str(value(h)),formula_asts=dict(lower=ast(l),upper=ast(h)))
                    partitions.append(dict(x_from=str(value(a)),x_to=str(value(b)),x_mid=str(value(midpoint)),window_region_index=region_index,
                        included=witness(region),excluded=[dict(profile=label,**witness(v)) for label,v in used],remaining=[witness(v) for v in remaining],
                        formula_asts=dict(x_from=ast(a),x_to=ast(b),x_mid=ast(midpoint))))
                for low,high in remaining:
                    strip=self.bind(self.bind(self.integrate(high,a,b))-self.bind(self.integrate(low,a,b)))
                    if strip.v<=0:raise DomainError('简单多边形窗口正交集积分非正，当前精度不能确认')
                    total=self.bind(total+strip)
                    terms.append({**term('分段'+str(len(terms)+1),strip,x_from=a,x_to=b),'window_region_index':region_index,'lower_boundary':low[1],'upper_boundary':high[1]})
        return value(total),ast(total),dict(rule_version=VERSION,method='简单多边形窗口多片段直线与圆弧解析积分',integration_rule=RULE,
                    working_digits=PRECISION,output_digits=80,pi_constant=PI,terms=terms,**({'partitions':partitions} if certify else {}))


def geometry(*args):
    gross,node,holes,extra,nodes=convex.geometry(*args,scene_type=SimpleScene)
    extra.update(simple_polygon_window=True,window_intersection_rule=RULE)
    for h in holes:h['intersection_rule']=h['intersection_rule'].replace('凸多边形窗口','简单多边形窗口')
    return gross,node,holes,extra,nodes
