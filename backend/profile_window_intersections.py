"""Evidence profile intersections and complete void coverage in a common XY window."""
from decimal import localcontext, ROUND_HALF_EVEN
from .compound_takeoff import PI
from .curved_intersections import N, square, ast, value, term, PRECISION
from .disk_window_intersection import integral, circle_bound
from .engine import DomainError

VERSION = 'decimal-profile-window-intersection-1.0'
RULE = '按窗口、各轮廓支撑、圆与所有水平边交点及两圆交点的X分段；段内边界次序不变，选最高下界和最低上界作直线或圆弧原函数解析积分，不用网格或容差。'


def profile(parameters, nodes, label):
    p = {k:N(v,nodes[k]) for k,v in parameters.items()}
    x,y = p.get('x',N(0)),p.get('y',N(0))
    if 'diameter' in p:
        r=p['diameter']/2
        return dict(label=label,circle=(x,y,r,label),left=x-r,right=x+r)
    return dict(label=label,left=x-p['length']/2,right=x+p['length']/2,bottom=y-p['width']/2,top=y+p['width']/2)


class Scene:
    """Every ordering event is shared by the area and containment proofs."""
    def __init__(self, profiles, bounds, nodes):
        self.profiles=profiles
        self.x1,self.x2,self.y1,self.y2=[N(v,nodes[k]) for v,k in zip(bounds,('x_start','x_end','y_start','y_end'))]
        self.cuts={self.x1.v:self.x1,self.x2.v:self.x2}
        circles=[p['circle'] for p in profiles if 'circle' in p]
        horizontal=[self.y1,self.y2]+[p[k] for p in profiles if 'circle' not in p for k in ('bottom','top')]
        for p in profiles:
            self.add(p['left']);self.add(p['right'])
        for x,y,r,label in circles:
            for boundary in horizontal:
                dy=boundary-y
                if abs(dy.v)<=r.v:
                    dx=(square(r)-square(dy)).sqrt();self.add(x-dx);self.add(x+dx)
        for i,(x,y,r,label) in enumerate(circles):
            for X,Y,R,other in circles[:i]:
                dx,dy=X-x,Y-y;d2=square(dx)+square(dy)
                if (r.v-R.v)**2<d2.v<(r.v+R.v)**2:
                    t=d2+square(r)-square(R);disc=N(4)*d2*square(r)-square(t)
                    foot=x+t*dx/(N(2)*d2)
                    if not dy.v:self.add(foot)
                    else:
                        offset=dy*disc.sqrt()/(N(2)*d2);self.add(foot-offset);self.add(foot+offset)
        points=[self.cuts[v] for v in sorted(self.cuts)]
        self.intervals=list(zip(points,points[1:]))

    def add(self,x):
        if self.x1.v<x.v<self.x2.v:self.cuts.setdefault(x.v,x)

    @staticmethod
    def at(bound,x):
        return bound[2].v if bound[0]=='line' else circle_bound(bound[2],bound[3],x)

    @staticmethod
    def bound_node(bound,x):
        if bound[0]=='line':return bound[2]
        cx,cy,r,label=bound[2]
        return cy+(square(r)-square(x-cx)).sqrt()*bound[3]

    def vertical(self,profiles,x):
        lows=[('line','窗口下界',self.y1,1)];highs=[('line','窗口上界',self.y2,1)]
        for p in profiles:
            if not p['left'].v<x<p['right'].v:return None
            if 'circle' in p:
                c=p['circle'];lows.append(('circle',p['label']+'下弧',c,-1));highs.append(('circle',p['label']+'上弧',c,1))
            else:
                lows.append(('line',p['label']+'下边',p['bottom'],1));highs.append(('line',p['label']+'上边',p['top'],1))
        low=max(lows,key=lambda b:self.at(b,x));high=min(highs,key=lambda b:self.at(b,x))
        return (low,high) if self.at(low,x)<self.at(high,x) else None

    def measure(self,profiles):
        total=N(0);terms=[]
        for a,b in self.intervals:
            bounds=self.vertical(profiles,(a.v+b.v)/2)
            if bounds is None:continue
            low,high=bounds;strip=integral(high,a,b)-integral(low,a,b)
            if strip.v<=0:raise DomainError('孔端窗口正交集积分非正，当前精度不能确认，请另核')
            total=total+strip
            terms.append({**term('区间'+str(len(terms)+1),strip,x_from=a,x_to=b), 'lower_boundary':low[1],'upper_boundary':high[1]})
        trace=dict(rule_version=VERSION,method='孔端窗口直线与圆弧分段解析积分',integration_rule=RULE,working_digits=PRECISION,output_digits=80,pi_constant=PI,angle_unit='rad',intermediate_rounding='HALF_EVEN',terms=terms)
        return value(total),ast(total),trace

    def coverage(self,target,voids):
        terms=[];positive=set()
        for a,b in self.intervals:
            mid=(a+b)/2;region=self.vertical(target,mid.v)
            if region is None:continue
            low,high=(self.at(t,mid.v) for t in region);pieces=[]
            for i,p in enumerate(voids):
                clip=self.vertical([target[0],p],mid.v)
                if clip is None:continue
                lo,hi=(self.at(t,mid.v) for t in clip)
                if lo<low or hi>high:raise DomainError('相接孔窗口交集超出混凝土侧层，请核对来源精度')
                pieces.append((lo,hi,i,clip))
            pieces.sort(key=lambda t:t[0]);cursor=low
            for lo,hi,i,clip in pieces:
                if lo>cursor:return None
                if lo<cursor:raise DomainError('同侧相接孔窗口正重叠，不能重复扣减')
                cursor=hi;positive.add(i)
            if cursor<high:return None
            witnesses=[];previous=self.bound_node(region[0],mid)
            for lo,hi,i,clip in pieces:
                lower=self.bound_node(clip[0],mid);upper=self.bound_node(clip[1],mid);gap=lower-previous
                witnesses.append(dict(hole_index=i+1,lower_boundary=clip[0][1],upper_boundary=clip[1][1],lower=str(value(lower)),upper=str(value(upper)),join_gap=str(value(gap)),formula_asts=dict(lower=ast(lower),upper=ast(upper),join_gap=ast(gap))))
                previous=upper
            terms.append(dict(x_from=str(value(a)),x_to=str(value(b)),x_mid=str(value(mid)),lower_boundary=region[0][1],upper_boundary=region[1][1],holes=[i+1 for lo,hi,i,clip in pieces],pieces=witnesses,formula_asts=dict(x_from=ast(a),x_to=ast(b),x_mid=ast(mid))))
        if not terms or not positive:return None
        return dict(method='每段所选孔与混凝土侧层窗口交集由同侧相接孔无重叠完整覆盖；按几何分割恒等式扣完毛面积，不用容差归零',working_digits=PRECISION,output_digits=80,terms=terms,canonical_hole=max(positive))


def compute(profiles,bounds,nodes):
    """Return gross, each physical cut and a certificate of exact zero, if any."""
    with localcontext() as ctx:
        ctx.prec=PRECISION;ctx.rounding=ROUND_HALF_EVEN
        scene=Scene(profiles,bounds,nodes)
        gross=Scene(profiles[:2],bounds,nodes).measure(profiles[:2])
        cuts=[Scene([profiles[0],p],bounds,nodes).measure([profiles[0],p]) for p in profiles[2:]]
        proof=scene.coverage(profiles[:2],profiles[2:]) if cuts else None
        return gross,cuts,proof
