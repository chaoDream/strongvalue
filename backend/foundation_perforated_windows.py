"""Horizontal contact windows with explicit, evidence-bound excluded inner rings."""
from fractions import Fraction
from .engine import DomainError
from .compound_takeoff import text
from .polygon_takeoff import edges, inside, intersects
from . import foundation_polygon_windows as polygon
from . import foundation_simple_windows as simple

VERSION = 'foundation-horizontal-formwork-1.10'
METHOD = 'perforated_polygon_vertical_sweep'
RULE = '按外轮廓、窗口内孔及全部直线/圆弧交点划分X区间；奇偶配对得到扣内孔后的窗口片段，再裁切实际接触与基础实体孔，逐片解析积分，不用网格或容差。'
NOTICE = ('基础整体顶底、台阶平台及内部孔端支持带内孔的简单直边窗口。外轮廓和每个内孔3至12顶点，'
          '1至4个内孔、全部最多36点；内孔须严格在外轮廓内且相互分离，不接触、重叠或包含。'
          '逐点原始XY及依据、内孔不支模说明及原件必填，共用窗口单位、基础中心与孔位正向，Z和全部基础个数继承已审来源。'
          '窗口内孔W仅排除本窗口支模范围，不生成基础实体孔H、不扣混凝土体积、不生成孔壁或孔端。'
          '先扣窗口内孔再与真实截面、平台及孔端相交，基础实际孔只扣余下支模范围，不重复扣内孔。'
          '工作200位Decimal、保留80位及有序AST，全部有效片段无剩余才形成严格零接触证明。'
          '完整窗口净区以精确有理数扫描防重，内孔空区可另项，正重叠或包含阻断，共边或点接触可分项，跨记录同样检查。'
          '数量继承一次，仅总量有据修约；实际接触、做法、原件及修约仍须独立复核。'
          '曲边、多外轮廓、内孔嵌套成岛、任意水平分区、斜面及个体不同做法另核。')


def enabled(d):
    return isinstance(d,dict) and isinstance(d.get('plan_range'),dict) and d['plan_range'].get('shape')=='polygon_with_holes'


def ring_points(d):
    if not enabled(d): return [simple.points(d)]
    c=d['plan_range']
    return [polygon.vertices(c)]+[polygon.vertices({**h,'unit':c['unit']}) for h in c['holes']]


def topology(c):
    rings=[[tuple(map(Fraction,p)) for p in ring] for ring in ring_points(dict(plan_range=c))]
    checks=[]
    for i,hole in enumerate(rings[1:],1):
        if any(intersects(a,b,p,q) for a,b in edges(rings[0]) for p,q in edges(hole)) or any(inside(p,rings[0])!=1 for p in hole):
            raise DomainError('窗口内孔W'+str(i)+'须严格在外轮廓内，不能接触或穿越外边界')
        checks.append(dict(left='外轮廓',right='W'+str(i),relation='strictly_inside'))
        for j,other in enumerate(rings[1:i],1):
            if any(intersects(a,b,p,q) for a,b in edges(other) for p,q in edges(hole)) or inside(hole[0],other)!=-1 or inside(other[0],hole)!=-1:
                raise DomainError('窗口内孔之间不能接触、重叠或包含，嵌套岛区须另核')
            checks.append(dict(left='W'+str(j),right='W'+str(i),relation='disjoint'))
    return checks


def validate(c):
    if not isinstance(c,dict) or 'holes' not in c or not isinstance(c['holes'],list) or not 1<=len(c['holes'])<=4:
        raise DomainError('带内孔窗口须明确1至4个排除内孔')
    outer={k:v for k,v in c.items() if k!='holes'}
    simple.validate(outer)
    names=set();count=len(c['parameters'])//2
    for h in c['holes']:
        if not isinstance(h,dict) or set(h)!={'name','parameters','evidence','measurement_basis','evidence_id'}:
            raise DomainError('窗口内孔仅含名称、顶点XY及逐坐标原件、排除说明及原件；单位和坐标基准继承窗口')
        name=text(h['name'],'窗口内孔名称',200)
        if name in names:raise DomainError('窗口内孔名称不能重复')
        names.add(name)
        text(h['measurement_basis'],'窗口内孔不支模范围说明');text(h['evidence_id'],'内孔排除说明原件',200)
        simple.validate({**outer,'parameters':h['parameters'],'evidence':h['evidence']})
        count+=len(h['parameters'])//2
    if count>36:raise DomainError('窗口外轮廓及内孔合计最多36个顶点')
    topology(c)


def separation(a,b):
    return simple.separation(a,b,ring_points=ring_points,method=METHOD)


class PerforatedScene(simple.SimpleScene):
    def measure(self,include,exclude=(),certify=False):
        area,node,trace=super().measure(include,exclude,certify)
        trace.update(rule_version=VERSION,method='带内孔窗口多片段直线与圆弧解析积分',integration_rule=RULE)
        return area,node,trace


def geometry(*args,scene_type=PerforatedScene):
    c=args[0]['plan_range']
    gross,node,holes,extra,nodes=polygon.geometry(*args,scene_type=scene_type,inner_rings=c['holes'])
    extra.update(perforated_polygon_window=True,window_intersection_rule=RULE,window_ring_checks=topology(c))
    for h in holes:h['intersection_rule']=h['intersection_rule'].replace('凸多边形窗口','扣内孔后的窗口净区')
    return gross,node,holes,extra,nodes
