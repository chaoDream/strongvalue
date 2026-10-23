"""Centred rectangular tiers, stacked without gaps, one foundation per post."""
from decimal import Decimal
from .compound_takeoff import op,text
from .engine import DomainError
from .measurement_units import LENGTH,VERSION as UNIT_VERSION
from .planar_takeoff import linear

VERSION='railing-post-foundation-1.1'
SHAPE='stepped_rectangular'
PARAMS=('length','width','height')
NOTICE='仅按明确选入的已审最终立柱根数，每根一个独立基础，来源重复仅乘一次。矩形基础按原三维尺寸；台阶基础须按同一基础底面局部Z=0、自下而上同心同轴垂直紧接叠置，逐层长宽不得外伸，填写高度为本层净层高而非累计高度；层数不乘基础个数。各层须同一材料做法，按层体积相加，不重复计相接面。未选立柱不计。基础定位、偏心旋转、悬挑、空隙、坡面变截面、基础间交叠、柱脚锚栓孔及预埋扣减、垫层土方钢筋模板和承载设计另核；不与既有基础或清单重复。同一来源立柱项不能在其他基础记录重复选入。'


def validate_row(d):
    if set(d)!={'material_index','shape','levels','length_unit','measurement_basis','evidence','stacking_confirmed'} or d['shape']!=SHAPE:raise DomainError('台阶基础须仅含立柱项、矩形台阶类型、逐层尺寸、单位、范围原件及叠置确认')
    if d['stacking_confirmed'] is not True:raise DomainError('须据原件确认台阶自下而上同心同轴紧接叠置、逐层不外伸，层高不是累计高度')
    levels=d['levels']
    if not isinstance(levels,list) or not 2<=len(levels)<=10:raise DomainError('台阶基础须明确2至10层，单层请用独立矩形基础')
    if not isinstance(d['evidence'],dict) or set(d['evidence'])!={'measurement_basis'}:raise DomainError('台阶范围须明确原件，尺寸原件应逐层填写')
    for level in levels:
        if not isinstance(level,dict) or set(level)!={'parameters','evidence'} or not isinstance(level['parameters'],dict) or set(level['parameters'])!=set(PARAMS) or not isinstance(level['evidence'],dict) or set(level['evidence'])!=set(PARAMS):raise DomainError('各台阶须完整且仅含本层长宽层高及逐参数原件，不得另填累计高度或数量')


def calculate(d,key,dimensions,refs,conversions):
    rows=[];volumes=[];height_asts=[];z=Decimal(0);previous=None;total=Decimal(0)
    for i,level in enumerate(d['levels'],1):
        prefix=key+'.levels.'+str(i);params={};asts={}
        for k in PARAMS:
            raw=level['parameters'][k];v=linear(raw,d['length_unit'],True);params[k]=v;dimensions[prefix+'.'+k]=str(raw);refs[prefix+'.'+k]=text(level['evidence'][k],'台阶逐层尺寸原件',200);asts[k]=op('multiply',{'dimension':prefix+'.'+k},{'constant':LENGTH[d['length_unit']]})
            conversions.append(dict(parameter=prefix+'.'+k,original_value=str(raw),source_unit=d['length_unit'],factor=LENGTH[d['length_unit']],canonical_value=str(v),canonical_unit='m',rule_version=UNIT_VERSION))
        if previous and any(params[k]>previous[k] for k in ('length','width')):raise DomainError('上层台阶长宽不得大于下层，请核对自下而上顺序及同心不外伸范围')
        single=params['length']*params['width']*params['height'];total+=single
        from_ast=op('add',*height_asts) if height_asts else {'constant':'0'};height_asts.append(asts['height']);to_ast=op('add',*height_asts);volume_ast=op('multiply',*[asts[k] for k in PARAMS]);volumes.append(volume_ast)
        rows.append({**level,'index':i,'normalized_parameters':{k:str(v) for k,v in params.items()},'from_z':str(z),'to_z':str(z+params['height']),'single_level_volume':str(single),'formula_asts':dict(from_z=from_ast,to_z=to_ast,single_level_volume=volume_ast)})
        z+=params['height'];previous=params
    return total,op('add',*volumes),rows
