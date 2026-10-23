"""Solid cylindrical foundations and concentric, touching circular tiers."""
from decimal import Decimal
from .compound_takeoff import PI,op,text
from .engine import DomainError
from .measurement_units import LENGTH,VERSION as UNIT_VERSION
from .planar_takeoff import linear

VERSION='railing-post-foundation-1.2'
SHAPES=('circular','stepped_circular')
PARAMS=('diameter','height')
NOTICE='仅按明确选入的已审最终立柱根数，每根一个独立基础，来源重复仅乘一次。圆形为竖直实心等径圆柱，填写实际直径而非半径或公称尺寸，体积按π/4×直径²×净高；圆形台阶自同一基础底面局部Z=0向上同心同轴垂直紧接、无空隙，上层直径不得超过下层，填写本层净层高而非累计高度，层数不增加基础个数。各项各层须同一材料做法；矩形及矩形台阶沿用各自尺寸口径。未选立柱不计；定位、偏心、旋转异形、空隙悬挑、坡面连续变截面、基础交叠、柱脚锚栓孔及预埋扣减、垫层土方钢筋模板和承载设计另核，不与既有基础或清单重复。同一来源立柱项不能被其他基础记录重复选入。'


def validate_row(d):
    stepped=d['shape']=='stepped_circular'
    keys={'material_index','shape','length_unit','measurement_basis','evidence'}|({'levels','stacking_confirmed'} if stepped else {'parameters'})
    if d['shape'] not in SHAPES or set(d)!=keys:raise DomainError('圆形基础须仅含来源立柱项、圆形类型、实际直径净高、单位、范围原件；台阶须逐层填写并确认叠置')
    if stepped and d['stacking_confirmed'] is not True:raise DomainError('须据原件确认圆形台阶自下而上同心同轴紧接、直径不外伸，层高非累计高度')
    if not isinstance(d['evidence'],dict) or set(d['evidence'])!=({'measurement_basis'} if stepped else set(PARAMS)|{'measurement_basis'}):raise DomainError('圆形基础直径、净高及范围须逐项关联原件，台阶尺寸原件逐层填写')
    levels=d['levels'] if stepped else [dict(parameters=d['parameters'],evidence={k:d['evidence'][k] for k in PARAMS})]
    if not isinstance(levels,list) or stepped and not 2<=len(levels)<=10:raise DomainError('圆形台阶须明确2至10层，单层请选择实心圆柱基础')
    for v in levels:
        if not isinstance(v,dict) or set(v)!={'parameters','evidence'} or not isinstance(v['parameters'],dict) or set(v['parameters'])!=set(PARAMS) or not isinstance(v['evidence'],dict) or set(v['evidence'])!=set(PARAMS):raise DomainError('每个圆柱须仅含实际直径和本层净高及两项原件，不得另填半径、累计高度或数量')


def calculate(d,key,dimensions,refs,conversions):
    stepped=d['shape']=='stepped_circular';levels=d['levels'] if stepped else [dict(parameters=d['parameters'],evidence=d['evidence'])]
    rows=[];nodes=[];heights=[];z=Decimal(0);previous=None;total=Decimal(0)
    for i,v in enumerate(levels,1):
        prefix=key+'.levels.'+str(i) if stepped else key;params={};asts={}
        for k in PARAMS:
            raw=v['parameters'][k];n=linear(raw,d['length_unit'],True);params[k]=n;dimensions[prefix+'.'+k]=str(raw);refs[prefix+'.'+k]=text(v['evidence'][k],'圆形基础尺寸原件',200);asts[k]=op('multiply',{'dimension':prefix+'.'+k},{'constant':LENGTH[d['length_unit']]})
            conversions.append(dict(parameter=prefix+'.'+k,original_value=str(raw),source_unit=d['length_unit'],factor=LENGTH[d['length_unit']],canonical_value=str(n),canonical_unit='m',rule_version=UNIT_VERSION))
        if previous is not None and params['diameter']>previous:raise DomainError('上层圆形台阶实际直径不得大于下层，请核对自下而上同心不外伸范围')
        volume=Decimal(PI)/4*params['diameter']*params['diameter']*params['height'];node=op('multiply',op('divide',{'constant':PI},{'constant':'4'}),asts['diameter'],asts['diameter'],asts['height']);total+=volume;nodes.append(node)
        start=op('add',*heights) if heights else {'constant':'0'};heights.append(asts['height'])
        rows.append({**v,'index':i,'normalized_parameters':{k:str(n) for k,n in params.items()},'from_z':str(z),'to_z':str(z+params['height']),'single_level_volume':str(volume),'formula_asts':dict(from_z=start,to_z=op('add',*heights),single_level_volume=node)})
        z+=params['height'];previous=params['diameter']
    return total,op('add',*nodes) if stepped else nodes[0],rows if stepped else None,rows[0]['normalized_parameters']
