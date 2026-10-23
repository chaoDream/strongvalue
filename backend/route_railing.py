"""Adapt approved open XYZ design lines to explicit railing length bases."""
from decimal import Decimal
from fractions import Fraction
from . import store
from .compound_takeoff import op
from .engine import DomainError, precise
from .measurement_units import LENGTH
from .route_takeoff import calculate as route_quantity

VERSION='design-line-railing-1.0'
BASES={'spatial':'三维设计长度','horizontal':'水平投影长度'}
NOTICE='仅借用已审设计坐标线的原始XYZ及顺序，不将管线用途自动认定为栏杆安装中心线。须有据选择三维设计长度或水平投影长度，并逐段核定安装做法及范围；S沿所选长度口径从原段起点增加，不能混用坡长与投影端距。水平口径遇竖直段阻断，须按实际范围拆分来源或选择三维口径。不同段投影重合不自动扣重，跨段范围分别录入；不推断坡段曲线、立柱、多道扶手、接头、搭接或材料重量。'


def basis(value):
    if not isinstance(value,str) or value not in BASES:
        raise DomainError('设计坐标线栏杆须明确选择三维设计长度或水平投影长度')
    return value


@precise
def boundary(source,length_basis):
    basis(length_basis);r=route_quantity(source);rows=[]
    for seg in r['route_segments']:
        a=r['route_points'][seg['from_index']-1];b=r['route_points'][seg['to_index']-1]
        start,end=a['normalized_parameters'],b['normalized_parameters']
        axes=('x','y','z') if length_basis=='spatial' else ('x','y')
        square=sum(((Fraction(end[k])-Fraction(start[k]))**2 for k in axes),Fraction(0))
        if not square:
            raise DomainError('水平投影口径含竖直零长段，不能隐去该段；请拆分来源或明确三维计长')
        diffs=[]
        for k in axes:
            def coord(p,index):return op('multiply',{'dimension':str(index)+'.'+k},{'constant':LENGTH[p['unit']]})
            diffs.append(op('subtract',coord(b,seg['to_index']),coord(a,seg['from_index'])))
        identity={'from_xyz':start,'to_xyz':end,'edge_index':seg['index'],'length_basis':length_basis,'rule':VERSION}
        rows.append({'edge_id':store.digest(identity),'index':seg['index'],'edge_index':seg['index'],'ring_index':1,
                     'ring_name':source['name'],'role':'route','from_index':seg['from_index'],'to_index':seg['to_index'],
                     'from_xy':{k:start[k] for k in ('x','y')},'to_xy':{k:end[k] for k in ('x','y')},
                     'from_xyz':start,'to_xyz':end,'spatial_length':seg['length'],'horizontal_length':seg['horizontal_length'],
                     'length_basis':length_basis,'length':seg['length' if length_basis=='spatial' else 'horizontal_length'],
                     'squared_length':{'numerator':str(square.numerator),'denominator':str(square.denominator)},
                     'formula_ast':op('sqrt',op('add',*[op('multiply',d,d) for d in diffs]))})
    return {**r,'railing_edges':rows}
