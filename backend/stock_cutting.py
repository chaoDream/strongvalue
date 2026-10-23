"""Exact homogeneous fixed-stock cutting and offcut ledger; no BOQ mutation."""
from decimal import Decimal
from fractions import Fraction
from .compound_takeoff import op,text
from .engine import DomainError,number,quantity_number,precise
from .measurement_units import LENGTH,VERSION as UNIT_VERSION
from .planar_takeoff import linear

VERSION='railing-uniform-stock-cutting-1.0'
PARAMETERS=('stock_length','kerf_per_piece','trim_start','trim_end','reusable_min_length')
NOTICE='仅按本材料项全部重复后的整数件数，对单一实际下料长度和单一原材定尺自动排料，不跨材料项混排或使用库存余料。须确认每件下料已完整含本件专业搭接等尺寸；每件另计一次锯缝，包括本根最后一件，两端切除量另外核定，须避免与锯缝重复。每根容量精确向下取整，所需原材根数向上取整；仅在该固定口径内达到最少根数，不是多规格全局最优。零余料不入库，正余料长度达到核定留用下限则列待留用，低于下限列报废；留用清单不代表已入库或已投入其他工程。购入长度=净下料+锯缝+两端切除+报废余料+待留用余料，净工程量和报价不因附表改变。与同项比例搭接损耗备料互斥，不能重复叠加。连续件、板网片、变截面、接头配件、库存调拨及多定尺混排另核。'


def dec(value):return str(quantity_number(Decimal(value.numerator)/Decimal(value.denominator)))


@precise
def calculate(config,row,groups,prefix):
    if not isinstance(config,dict) or set(config)!=set(PARAMETERS)|{'length_unit','cut_basis_confirmed','measurement_basis','evidence'}:raise DomainError('定尺下料须完整填写原材定尺、每件锯缝、两端切除、余料下限、单位、口径确认、说明及依据')
    if not isinstance(config['evidence'],dict) or set(config['evidence'])!=set(PARAMETERS)|{'measurement_basis'}:raise DomainError('定尺下料参数及范围说明须分别关联原件')
    if config['cut_basis_confirmed'] is not True:raise DomainError('须确认实际单件下料已完整、每件一次锯缝及两端切除未重复')
    text(config['measurement_basis'],'定尺下料及余料口径说明')
    normalized={};dimensions={};refs={};conversions=[];asts={}
    for k in PARAMETERS:
        number(config[k],k in ('stock_length','reusable_min_length'))
        value=linear(config[k],config['length_unit'],k in ('stock_length','reusable_min_length'));normalized[k]=Fraction(value)
        key=prefix+'.'+k;dimensions[key]=str(config[k]);asts[k]=op('multiply',{'dimension':key},{'constant':LENGTH[config['length_unit']]})
        conversions.append(dict(parameter=key,original_value=str(config[k]),source_unit=config['length_unit'],factor=LENGTH[config['length_unit']],canonical_value=str(value),canonical_unit='m',rule_version=UNIT_VERSION))
    for k in (*PARAMETERS,'measurement_basis'):refs[prefix+'.'+k]=text(config['evidence'][k],'定尺下料逐参数依据',200)
    required=Fraction(row['normalized_quantity'])*Fraction(groups)
    if required.denominator!=1 or not 1<=required<=1000000:raise DomainError('定尺下料全部需求件数须为1至1000000整数，请核对每组件数和来源重复数量')
    required=int(required);cut=Fraction(row['single_member_length']);stock=normalized['stock_length'];kerf=normalized['kerf_per_piece']
    trim=normalized['trim_start']+normalized['trim_end'];usable=stock-trim;pitch=cut+kerf
    if usable<=0:raise DomainError('原材定尺须大于两端切除总长')
    capacity=usable//pitch
    if capacity<1:raise DomainError('原材可用长度不足一件实际下料加一次锯缝')
    stock_count=(required+capacity-1)//capacity
    full_count,remainder=divmod(required,capacity)
    patterns=[];values={k:Fraction(0) for k in ('net_length','kerf_length','trim_length','scrap_length','retained_length','loss_length','purchased_length')}
    formulas={k:[] for k in values};cut_ast=row['formula_ast']['args'][0];weight_ast=row['formula_ast']['args'][2]
    trim_ast=op('add',asts['trim_start'],asts['trim_end'])
    for bars,pieces in ((full_count,capacity),(1 if remainder else 0,remainder)):
        if not bars:continue
        offcut=usable-pieces*pitch;disposition='none' if not offcut else 'retain' if offcut>=normalized['reusable_min_length'] else 'scrap'
        per={'net_length':pieces*cut,'kerf_length':pieces*kerf,'trim_length':trim,'scrap_length':offcut if disposition=='scrap' else Fraction(0),'retained_length':offcut if disposition=='retain' else Fraction(0),'purchased_length':stock}
        per['loss_length']=per['kerf_length']+per['trim_length']+per['scrap_length']
        net_ast=op('multiply',cut_ast,{'constant':str(pieces)});kerf_ast=op('multiply',asts['kerf_per_piece'],{'constant':str(pieces)})
        off_ast=op('subtract',asts['stock_length'],op('add',net_ast,kerf_ast,trim_ast))
        per_asts={'net_length':net_ast,'kerf_length':kerf_ast,'trim_length':trim_ast,'scrap_length':off_ast if disposition=='scrap' else {'constant':'0'},'retained_length':off_ast if disposition=='retain' else {'constant':'0'},'purchased_length':asts['stock_length']}
        per_asts['loss_length']=op('add',kerf_ast,trim_ast,per_asts['scrap_length'])
        totals={k:v*bars for k,v in per.items()}
        for k in values:values[k]+=totals[k];formulas[k].append(op('multiply',per_asts[k],{'constant':str(bars)}))
        first=1+sum(p['stock_count'] for p in patterns)
        patterns.append(dict(index=len(patterns)+1,stock_count=bars,first_stock=first,last_stock=first+bars-1,pieces_per_stock=pieces,produced_count=bars*pieces,offcut_length=dec(offcut),offcut_disposition=disposition,per_stock={k:dec(v) for k,v in per.items()},total={k:dec(v) for k,v in totals.items()},per_stock_formula_asts=per_asts))
    length_asts={k:op('add',*v) for k,v in formulas.items()};weight=Fraction(row['normalized_unit_weight'])
    totals={**values,**{k.replace('_length','_mass'):v*weight for k,v in values.items()}}
    total_asts={**length_asts,**{k.replace('_length','_mass'):op('multiply',v,weight_ast) for k,v in length_asts.items()}}
    if values['purchased_length']!=values['net_length']+values['loss_length']+values['retained_length']:raise DomainError('定尺下料长度台账未闭合')
    return dict(**config,normalized_parameters={k:dec(v) for k,v in normalized.items()},cut_length=dec(cut),required_piece_count=required,required_count_formula_ast=op('multiply',row['formula_ast']['args'][1],{'dimension':'multiplicity'}),capacity=str(capacity),stock_count=stock_count,patterns=patterns,total={k:dec(v) for k,v in totals.items()},total_formula_asts=total_asts,
                capacity_certificate=dict(used_at_capacity=dec(capacity*pitch),usable_length=dec(usable),used_at_next=dec((capacity+1)*pitch),relation='capacity × pitch <= usable < (capacity + 1) × pitch'),
                stock_count_certificate=dict(previous_capacity=str((stock_count-1)*capacity),required=required,current_capacity=str(stock_count*capacity),relation='(stocks - 1) × capacity < required <= stocks × capacity'),
                balance=dict(purchased_length=dec(values['purchased_length']),net_plus_loss_plus_retained=dec(values['net_length']+values['loss_length']+values['retained_length']),difference='0'),
                dimensions=dimensions,source_evidence=refs,unit_conversions=conversions,rule_version=VERSION,notice=NOTICE)
