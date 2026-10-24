"""Analytic intersection of two disks and an axis-aligned evidence window."""
from decimal import Decimal, localcontext, ROUND_HALF_EVEN
from .engine import DomainError
from .compound_takeoff import op, PI
from .curved_intersections import N, square, absolute, atan2, ast, value, term, PRECISION

VERSION = 'decimal-disk-window-intersection-1.0'
RULE = '按圆支撑端点、圆与窗口水平边交点及两圆交点的X分段；每段取最低上边界和最高下边界，以圆弧原函数解析积分后相加，不使用网格或几何容差。'


def circle_bound(circle, sign, x):
    cx,cy,r,label = circle
    rad = r.v*r.v-(x-cx.v)**2
    if rad < 0: raise DomainError('共同交集分段越出圆支撑，当前精度不能确认，请另核')
    return cy.v+sign*rad.sqrt()


def primitive(circle, x):
    cx,cy,r,_ = circle; t = x-cx; rad = square(r)-square(t)
    if rad.v < 0: raise DomainError('共同交集圆弧积分端点越界，当前精度不能确认，请另核')
    h = rad.sqrt(); angle = N(atan2(absolute(t).v,h.v),op('atan2',absolute(t).a,h.a))
    if t.v < 0: angle = N(0)-angle
    return (t*h+square(r)*angle)/2


def integral(bound, left, right):
    kind,label,data,sign = bound
    if kind=='line': return data*(right-left)
    return data[1]*(right-left)+(primitive(data,right)-primitive(data,left))*sign


def calculate(hp,ha,up,ua,bounds,bound_asts,hole_clip,upper_clip):
    """Partial disk overlap only; return stored clips for proven identities."""
    with localcontext() as ctx:
        ctx.prec=PRECISION; ctx.rounding=ROUND_HALF_EVEN
        h={k:N(v,ha[k]) for k,v in hp.items()}; u={k:N(v,ua[k]) for k,v in up.items()}
        circles=[(h['x'],h['y'],h['diameter']/2,'孔圆'),(N(0),N(0),u['diameter']/2,'上层圆')]
        x1,x2,y1,y2=[N(v,bound_asts[k]) for v,k in zip(bounds,('x_start','x_end','y_start','y_end'))]
        domains=[(max([x1,c[0]-c[2]],key=lambda n:n.v),min([x2,c[0]+c[2]],key=lambda n:n.v)) for c in circles]
        lo=max([x1,min([c[0]-c[2] for c in circles],key=lambda n:n.v)],key=lambda n:n.v); hi=min([x2,max([c[0]+c[2] for c in circles],key=lambda n:n.v)],key=lambda n:n.v)
        empty=(Decimal(0),{'constant':'0'},'两圆与窗口无正面积共同交集')
        if lo.v>=hi.v:return empty
        cuts={lo.v:lo,hi.v:hi}
        for a,b in domains:
            for x in (a,b):
                if lo.v<x.v<hi.v:cuts.setdefault(x.v,x)
        for cx,cy,r,label in circles:
            for y in (y1,y2):
                dy=y-cy
                if abs(dy.v)<=r.v:
                    dx=(square(r)-square(dy)).sqrt()
                    for x in (cx-dx,cx+dx):
                        if lo.v<x.v<hi.v:cuts.setdefault(x.v,x)
        dx,dy=h['x'],h['y']; r,R=circles[0][2],circles[1][2]; d2=square(dx)+square(dy)
        t=d2+square(R)-square(r); disc=N(4)*d2*square(R)-square(t)
        if d2.v<=0 or disc.v<=0:raise DomainError('两圆窗口解析要求严格局部交集，请先核定包含及相切关系')
        foot=t*dx/(N(2)*d2); offset=dy*disc.sqrt()/(N(2)*d2)
        for x in (foot-offset,foot+offset):
            if lo.v<x.v<hi.v:cuts.setdefault(x.v,x)
        points=[cuts[v] for v in sorted(cuts)]; total=N(0); terms=[]
        identity=[True,True]
        for left,right in zip(points,points[1:]):
            mid=(left.v+right.v)/2
            own=[]
            for c in circles:
                if abs(mid-c[0].v)>=c[2].v:own.append(None)
                else:
                    top=min(y2.v,circle_bound(c,1,mid));bottom=max(y1.v,circle_bound(c,-1,mid));own.append((top,bottom) if top>bottom else None)
            if all(v is not None for v in own):
                uppers=[('line','窗口上界',y2,1)]+[('circle',c[3]+'上弧',c,1) for c in circles]
                lowers=[('line','窗口下界',y1,1)]+[('circle',c[3]+'下弧',c,-1) for c in circles]
                def at(b):return b[2].v if b[0]=='line' else circle_bound(b[2],b[3],mid)
                upper=min(uppers,key=at);lower=max(lowers,key=at);top,bottom=at(upper),at(lower)
                actual=(top,bottom) if top>bottom else None
            else:actual=None;top=bottom=Decimal(0)
            for i,own_range in enumerate(own):
                if own_range is not None and own_range!=actual:identity[i]=False
            if top<=bottom:continue
            strip=integral(upper,left,right)-integral(lower,left,right)
            if strip.v<=0:raise DomainError('圆窗口正交集积分非正，当前数值精度不能确认，请另核')
            total=total+strip
            terms.append({**term('区间'+str(len(terms)+1),strip,x_from=left,x_to=right),'upper_boundary':upper[1],'lower_boundary':lower[1]})
        if identity[0]:return hole_clip
        if identity[1]:return upper_clip
        if not terms:return empty
        covered=value(total)
        if not 0<covered<min(hole_clip[0],upper_clip[0]):raise DomainError('两圆窗口共同面积超出严格交集界限，当前精度不能确认，请另核')
        details=dict(rule_version=VERSION,method='两圆与矩形窗口分段圆弧解析积分',integration_rule=RULE,working_digits=PRECISION,output_digits=80,pi_constant=PI,angle_unit='rad',intermediate_rounding='HALF_EVEN',terms=terms)
        return covered,ast(total),details['method'],details
