"""Decimal analytic disk intersections and replayable arithmetic; no mesh tolerance."""
from decimal import Decimal, localcontext, ROUND_HALF_EVEN
from .compound_takeoff import PI, op
from .engine import DomainError

PRECISION = 200
VERSION = 'decimal-disk-intersection-1.0'


def atan2(y, x):
    """Upper half-plane radians, fixed recorded pi, half-angle reduced Taylor series."""
    if y < 0 or (not x and not y): raise DomainError('圆弓形角度要求上半平面非零向量')
    with localcontext() as ctx:
        output = ctx.prec; ctx.prec += 40; ctx.rounding = ROUND_HALF_EVEN
        if not y: value = Decimal(0) if x > 0 else Decimal(PI)
        elif not x: value = Decimal(PI)/2
        else:
            z = min(y,abs(x))/max(y,abs(x)); factor = 1
            while z > Decimal('.125'):
                z = z/(1+(1+z*z).sqrt()); factor *= 2
            term = z; total = z; n = 1
            while True:
                term *= -z*z; new = total+term/(2*n+1)
                if new == total: break
                total = new; n += 1
                if n > 2000: raise DomainError('圆弓形反角计算未收敛，请核对来源尺寸')
            value = total*factor
            if y > abs(x): value = Decimal(PI)/2-value
            if x < 0: value = Decimal(PI)-value
        ctx.prec = output
        return +value


class N:
    """A computed Decimal and its source expression, evaluated in identical order."""
    def __init__(self, value, ast=None):
        self.v = Decimal(value); self.a = ast if ast is not None else {'constant':str(value)}
    def __add__(self, b):
        b = node(b); return N(self.v+b.v,op('add',self.a,b.a))
    def __sub__(self, b):
        b = node(b); return N(self.v-b.v,op('subtract',self.a,b.a))
    def __mul__(self, b):
        b = node(b); return N(self.v*b.v,op('multiply',self.a,b.a))
    def __truediv__(self, b):
        b = node(b); return N(self.v/b.v,op('divide',self.a,b.a))
    def sqrt(self): return N(self.v.sqrt(),op('sqrt',self.a))


def node(v): return v if isinstance(v,N) else N(v)
def square(v): return v*v
def absolute(v): return v if v.v >= 0 else N(0)-v

def ast(v):
    return dict(op='decimal_precision',args=[v.a],working_digits=PRECISION,output_digits=80,mode='HALF_EVEN')

def value(v):
    with localcontext() as ctx:
        ctx.prec = 80; ctx.rounding = ROUND_HALF_EVEN; return +v.v


def segment(radius, distance, height=None):
    """Circular segment toward the chord normal, major segment by complement."""
    a = absolute(distance); h = height if height is not None else (square(radius)-square(a)).sqrt()
    if not h.v: angle = N(0)
    else: angle = N(atan2(h.v,a.v),op('atan2',h.a,a.a))
    minor = square(radius)*angle-a*h
    result = N(PI)*square(radius)-minor if distance.v < 0 else minor
    return result,angle,h


def term(name, area, **values):
    return dict(name=name,area=str(value(area)),formula_ast=ast(area),
                **{k:str(value(v)) for k,v in values.items()},
                formula_asts={k:ast(v) for k,v in values.items()})


def quadrant(x,y,r):
    if not x.v or not y.v: return N(0)
    sign = -1 if (x.v<0) != (y.v<0) else 1
    a = min([absolute(x),r],key=lambda n:n.v); b = min([absolute(y),r],key=lambda n:n.v)
    if square(a).v+square(b).v <= square(r).v: q = a*b
    else:
        ca,_,_ = segment(r,a); cb,_,_ = segment(r,b)
        q = N(PI)*square(r)/4-ca/2-cb/2
    return q*N(sign)


def calculate(hp,ha,up,ua,hole_area,upper_area):
    """Only called after exact containment/disjoint classifications have failed."""
    with localcontext() as ctx:
        ctx.prec = PRECISION; ctx.rounding = ROUND_HALF_EVEN
        h = {k:N(v,ha[k]) for k,v in hp.items()}; u = {k:N(v,ua[k]) for k,v in up.items()}
        terms = []
        if 'diameter' in hp and 'diameter' in up:
            r = h['diameter']/2; R = u['diameter']/2; d2 = square(h['x'])+square(h['y']); d = d2.sqrt()
            t = d2+square(r)-square(R)
            discriminant = N(4)*d2*square(r)-square(t)
            if discriminant.v <= 0: raise DomainError('圆圆局部交集判别式须为正，请核对来源精度')
            a = t/(N(2)*d); b = d-a; height = discriminant.sqrt()/(N(2)*d)
            first,angle,hc = segment(r,a,height); second,angle2,_ = segment(R,b,height); result = first+second
            terms = [term('孔圆弓形',first,radius=r,chord_distance=a,half_chord=hc,minor_half_angle=angle),term('上层圆弓形',second,radius=R,chord_distance=b,half_chord=hc,minor_half_angle=angle2)]
            method = '圆圆双弓形解析交集'
        else:
            hole_circle = 'diameter' in hp
            rect,disk = (u,h) if hole_circle else (h,u); r = disk['diameter']/2
            cx = N(0)-h['x'] if hole_circle else h['x']; cy = N(0)-h['y'] if hole_circle else h['y']
            x1 = cx-rect['length']/2; x2 = cx+rect['length']/2; y1 = cy-rect['width']/2; y2 = cy+rect['width']/2
            q22 = quadrant(x2,y2,r); q12 = quadrant(x1,y2,r); q21 = quadrant(x2,y1,r); q11 = quadrant(x1,y1,r)
            result = q22-q12-q21+q11
            terms = [term(name,q,x=x,y=y,radius=r) for name,q,x,y in [('Q右上',q22,x2,y2),('Q左上',q12,x1,y2),('Q右下',q21,x2,y1),('Q左下',q11,x1,y1)]]
            method = '圆矩形四象限解析交集（右上−左上−右下＋左下）'
        covered = value(result)
        if not 0 < covered < min(hole_area,upper_area):
            raise DomainError('曲线局部交集超出严格面积界限，当前数值精度不能确认，请另核；不按零孔或整孔代替')
        details = dict(rule_version=VERSION,method=method,working_digits=PRECISION,output_digits=80,pi_constant=PI,angle_unit='rad',intermediate_rounding='HALF_EVEN',terms=terms)
        return covered, ast(result), method, details
