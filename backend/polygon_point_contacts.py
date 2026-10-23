"""Exact face-left walks for explicitly selected positive-width interiors."""
from functools import cmp_to_key
from .engine import DomainError
from .polygon_cap_partitions import checked, decimal, rational
from .polygon_takeoff import cross

VERSION = 'polygon-boundary-1.5'
RULE = 'xy-interior-contact-rational-1.0'
NOTICE = '已按有据确认的净区内部连通口径处理点接触：零宽接点不合并两个独立面积块。按精确边方向保留净区在左侧追踪边界；孔仅在一点接触裁剪边时，可能形成重复经过接点的派生外边界，孔仍为空白且原孔扣减记录保留。此口径不证明结构连接、施工接触、防水贯通或材料做法；各块仍须逐项核定。未启用的原面沿用原分区规则。'
ROUNDING = '沿用精确有理数裁剪、原子边抵消和派生面积；接点角序用精确叉积比较，不使用浮点角度、吸附或容差。每区各块精确面积闭合，80位显示、200位记录显示精度差，不调平；计入块合计后仅乘一次来源重复数量'
CANDIDATE_NOTICE = '上下净面支持X/Y窗口、两点无限斜线及分离净块独立核定。独立核定遇到点接触时，可另行明确选择“按净区内部连通处理点接触”并填写说明和原始依据，零宽接点不合并独立面积块。默认仍阻断点接触；不推断结构连接、施工做法、搭接或翻边。侧面沿用局部S/Z。'


def point(p):
    x,y = p
    return {'x':str(decimal(x)),'y':str(decimal(y)),'exact_x':rational(x),'exact_y':rational(y)}


def vector(a,b):
    return tuple(checked(b[k]-a[k]) for k in (0,1))


def angle_compare(a,b):
    """CCW order from +X, with exact orientation and no trigonometry."""
    def half(v):
        return 0 if v[1]>0 or v[1]==0 and v[0]>0 else 1
    h = half(a)-half(b)
    if h:
        return h
    value = checked(a[0]*b[1]-a[1]*b[0])
    return -1 if value>0 else 1 if value<0 else 0


def canonical_cycle(points, branches):
    # Keep shared branch vertices even when one visit continues straight through.
    while len(points)>=3:
        keep = [p for i,p in enumerate(points) if p in branches or cross(points[i-1],p,points[(i+1)%len(points)])!=0]
        if len(keep)==len(points):
            break
        points = keep
    if len(points)<3:
        raise DomainError('接点追踪得到退化边界，请核对原始轮廓与分割')
    smallest = min(points)
    return min(points[i:]+points[:i] for i,p in enumerate(points) if p==smallest)


def trace_loops(remaining, outgoing, incoming, contacts):
    """Choose the clockwise predecessor of each reverse incoming half-edge.

    Boundary edges keep the measured interior on their left. This deterministic
    face walk separates area sectors meeting only at a point. A bijection check
    refuses overlapping rays or an inconsistent embedding instead of guessing.
    """
    vertices = set(outgoing)|set(incoming)
    if any(not outgoing[p] or len(outgoing[p])!=len(incoming[p]) for p in vertices):
        raise DomainError('接点边界进入与离开不平衡，不能确认净区内部连通')
    branches = {p for p in vertices if len(outgoing[p])>1}
    successors = {}
    for p in sorted(vertices):
        rays = sorted(outgoing[p], key=cmp_to_key(lambda a,b:angle_compare(vector(p,a),vector(p,b))))
        if any(angle_compare(vector(p,a),vector(p,b))==0 for a,b in zip(rays,rays[1:])):
            raise DomainError('接点存在同方向重叠射线，不能唯一追踪边界')
        used = set(); pairings = []
        for a in sorted(incoming[p]):
            reverse = vector(p,a)
            comparisons = [(q,angle_compare(vector(p,q),reverse)) for q in rays]
            if any(c==0 for _,c in comparisons):
                raise DomainError('接点存在未抵消折返边，不能确认内部连通')
            preceding = [q for q,c in comparisons if c<0]
            q = preceding[-1] if preceding else rays[-1]
            if q in used:
                raise DomainError('接点边界配对不是一一对应，请核对原轮廓与分割')
            used.add(q); successors[(a,p)] = (p,q)
            if p in branches:
                pairings.append({'incoming_from':point(a),'outgoing_to':point(q)})
        if p in branches:
            contacts.append({'point':point(p),'degree':len(rays),'pairings':pairings})
    result = []; unused = set(remaining)
    while unused:
        start = min(unused); edge = start; points = []
        while True:
            if edge not in unused:
                raise DomainError('接点边界无法闭合为独立面，请核对原始轮廓')
            unused.remove(edge); points.append(edge[0]); edge = successors[edge]
            if edge==start:
                break
        result.append(canonical_cycle(points,branches))
    return result
