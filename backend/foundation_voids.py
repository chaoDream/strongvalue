"""Axis-aligned rectangular and circular foundation void cross sections."""
from decimal import Decimal
from .compound_takeoff import PI,op,text
from .engine import DomainError
from .measurement_units import LENGTH
from .foundation_recesses import Z_FIELDS

VERSION='railing-post-foundation-1.5'
NOTICE='每根已审最终立柱对应一个独立基础，来源重复仅乘一次。只扣逐孔据原件列明的竖直圆孔或轴向矩形孔/凹槽；孔心采用基础各层共同中心局部XY，矩形孔长沿X、宽沿Y，不接受旋转角；Z=0为基础实体底面且向上。未填有限标高的孔贯通全部基础，填起止Z的孔仅扣实际正相交高度、仅校验相交层。矩形层逐轴检查，圆层检查矩形最远角点或圆孔剩余半径；不得越出相交层。正高度交叠的孔截面不得正面积重叠或相互包含，圆矩形按最近点精确检查；标高相离或端面接触可分别扣减。几何相切仅为几何判断，不代表结构净距或承载施工验收。台阶同心同轴紧接、上层不外伸，各层同一材料；单项全部基础同一孔表，孔数层数不增加基础数。逐孔逐层不修约，累计最终立柱和来源重复一次后，仅净总体积修约。斜孔、旋转矩形、任意异形凹槽、孔重叠并集、变径孔、预埋实体扣减、基础交叠及定位承载、垫层土方钢筋模板另核，不重复既有计量。'


def rectangular(h):return h.get('shape')=='rectangular'
def fields(h):return ('x','y','length','width') if rectangular(h) else ('x','y','diameter')
def enabled(c):return isinstance(c,dict) and isinstance(c.get('holes'),list) and any(isinstance(h,dict) and 'shape' in h for h in c['holes'])


def validate_config(c):
    if not isinstance(c,dict) or set(c)!={'unit','coordinate_basis','coordinate_evidence_id','scope_confirmed','holes'}:raise DomainError('基础孔表须明确单位、共同XYZ坐标依据、扣减范围确认及孔表')
    if c['scope_confirmed'] is not True:raise DomainError('须据原件确认孔截面、矩形轴向、贯通或有限标高范围及共同XYZ，不重复扣减')
    text(c['coordinate_basis'],'基础共同XYZ坐标与矩形轴向',2000);text(c['coordinate_evidence_id'],'基础孔位原始依据',200)
    if not isinstance(c['unit'],str) or c['unit'] not in LENGTH:raise DomainError('基础孔位、尺寸及标高须明确长度单位')
    if not isinstance(c['holes'],list) or not 1<=len(c['holes'])<=20:raise DomainError('每基础须明确1至20个孔；无孔请关闭孔扣减')
    names=set()
    for h in c['holes']:
        if not isinstance(h,dict):raise DomainError('基础孔须为明确的圆孔或轴向矩形孔对象')
        if 'shape' in h and h['shape']!='rectangular':raise DomainError('基础孔截面仅支持原圆孔或轴向矩形孔，不接受旋转或其他形状')
        keys={'name','parameters','evidence','measurement_basis'}|({'shape'} if rectangular(h) else set())|({'vertical_range'} if 'vertical_range' in h else set());f=set(fields(h))
        if set(h)!=keys or not isinstance(h['parameters'],dict) or set(h['parameters'])!=f or not isinstance(h['evidence'],dict) or set(h['evidence'])!=f|{'measurement_basis'}:raise DomainError('基础孔须完整填写孔心XY、圆孔实际直径或矩形实际长宽、扣减说明及逐参数原件')
        name=text(h['name'],'基础孔名',200)
        if name in names:raise DomainError('同一基础孔名不可重复')
        names.add(name);text(h['measurement_basis'],'本孔截面轴向及扣减范围说明',2000)
        for eid in h['evidence'].values():text(eid,'基础逐孔原件',200)
        if 'vertical_range' in h:
            v=h['vertical_range']
            if not isinstance(v,dict) or set(v)!={'parameters','evidence'} or not isinstance(v['parameters'],dict) or set(v['parameters'])!=set(Z_FIELDS) or not isinstance(v['evidence'],dict) or set(v['evidence'])!=set(Z_FIELDS):raise DomainError('有限标高孔须明确起止Z及逐参数原件，单位同孔位')
            for eid in v['evidence'].values():text(eid,'基础孔标高原件',200)


def square(node):return op('multiply',node,node)
def absolute(value,node):return node if value>=0 else op('subtract',{'constant':'0'},node)


def area(h,p,a):
    if rectangular(h):return p['length']*p['width'],op('multiply',a['length'],a['width'])
    return Decimal(PI)/4*p['diameter']*p['diameter'],op('multiply',op('divide',{'constant':PI},{'constant':'4'}),a['diameter'],a['diameter'])


def containment(h,p,a,outer,dim,circular):
    ext={axis:(p[side]/2 if rectangular(h) else p['diameter']/2) for axis,side in [('x','length'),('y','width')]}
    en={axis:op('divide',a[side] if rectangular(h) else a['diameter'],{'constant':'2'}) for axis,side in [('x','length'),('y','width')]}
    if circular and rectangular(h):
        margin=(outer['diameter']/2)**2-sum((abs(p[axis])+ext[axis])**2 for axis in ('x','y'))
        ast=op('subtract',square(op('divide',dim('diameter'),{'constant':'2'})),op('add',*[square(op('add',absolute(p[axis],a[axis]),en[axis])) for axis in ('x','y')]))
        return {'corner_squared':str(margin)},{'corner_squared':ast}
    if circular:
        margin=((outer['diameter']-p['diameter'])/2)**2-p['x']**2-p['y']**2
        if p['diameter']>=outer['diameter']:raise DomainError('圆孔实际直径须小于实际相交圆层直径')
        return {'radial_squared':str(margin)},{'radial_squared':op('subtract',square(op('divide',op('subtract',dim('diameter'),a['diameter']),{'constant':'2'})),op('add',square(a['x']),square(a['y'])))}
    margins={axis:str(outer[side]/2-ext[axis]-abs(p[axis])) for axis,side in [('x','length'),('y','width')]}
    asts={axis:op('subtract',op('subtract',op('divide',dim(side),{'constant':'2'}),en[axis]),absolute(p[axis],a[axis])) for axis,side in [('x','length'),('y','width')]}
    return margins,asts


def pair(h,p,a,previous):
    q={k:Decimal(v) for k,v in previous['normalized_parameters'].items()};b=previous['_asts']
    if rectangular(h) and rectangular(previous):
        margins={};asts={}
        for axis,side in [('x','length'),('y','width')]:
            delta=p[axis]-q[axis];margins[axis]=abs(delta)-(p[side]+q[side])/2;asts[axis]=op('subtract',absolute(delta,op('subtract',a[axis],b[axis])),op('divide',op('add',a[side],b[side]),{'constant':'2'}))
        axis=max(('x','y'),key=lambda k:margins[k]);return dict(margin=str(margins[axis]),formula_ast=asts[axis],margin_unit='m',separation_kind='rectangle_rectangle',axis_margins={k:str(v) for k,v in margins.items()},axis_formula_asts=asts)
    if not rectangular(h) and not rectangular(previous):
        distance=(p['x']-q['x'])**2+(p['y']-q['y'])**2;threshold=((p['diameter']+q['diameter'])/2)**2
        ast=op('subtract',op('add',*[square(op('subtract',a[axis],b[axis])) for axis in ('x','y')]),square(op('divide',op('add',a['diameter'],b['diameter']),{'constant':'2'})))
        return dict(margin=str(distance-threshold),formula_ast=ast,margin_unit='m²',separation_kind='circle_circle',distance_squared=str(distance),radius_sum_squared=str(threshold))
    rect,ra,circle,ca=(p,a,q,b) if rectangular(h) else (q,b,p,a);distances={};asts={}
    for axis,side in [('x','length'),('y','width')]:
        delta=circle[axis]-rect[axis];gap=abs(delta)-rect[side]/2;distances[axis]=max(gap,Decimal(0));asts[axis]=op('subtract',absolute(delta,op('subtract',ca[axis],ra[axis])),op('divide',ra[side],{'constant':'2'})) if gap>0 else {'constant':'0'}
    distance=sum(v*v for v in distances.values());threshold=(circle['diameter']/2)**2;ast=op('subtract',op('add',*[square(asts[axis]) for axis in ('x','y')]),square(op('divide',ca['diameter'],{'constant':'2'})))
    return dict(margin=str(distance-threshold),formula_ast=ast,margin_unit='m²',separation_kind='circle_rectangle',nearest_distances={k:str(v) for k,v in distances.items()},nearest_formula_asts=asts)
