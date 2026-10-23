"""Per-stock end trims and reusable-offcut thresholds, with original evidence."""
from fractions import Fraction
from .compound_takeoff import op,text
from .engine import DomainError,number
from .measurement_units import LENGTH,VERSION as UNIT_VERSION
from .planar_takeoff import linear
from .stock_cutting import dec

VERSION='railing-stock-processing-1.0'
PARAMS=('trim_start','trim_end','reusable_min_length')
NOTICE='S1沿用本组原材起终端切除与余料阈值；备选S2起逐规格据原件核定，两端切除各计一次，每件锯缝仍统一且含最后一件，不重复扣端切。逐规格可用长=该定尺−该规格起端切除−终端切除，余料按该规格留用下限分类。先最小购入总长度，同长度再最少根数；5000节点内搜索，仅双下界达到或搜索穷尽才证明最优。开启有限供应时按核定上限，否则须有按需供应依据；不自动预留库存、回用余料、采购或增加净工程量及报价。不同锯缝、材质截面、采购价格及跨表统筹另核。'


def apply(config,options,unit,prefix,dimensions,refs,conversions):
    if not isinstance(config,dict) or set(config)!={'alternatives','scope_confirmed','measurement_basis','evidence_id'}:raise DomainError('逐规格加工须明确备选端切和余料阈值、口径确认、说明及原件')
    if config['scope_confirmed'] is not True:raise DomainError('须据原件确认逐规格端切及余料阈值，S1沿用本组，锯缝及切除未重复')
    text(config['measurement_basis'],'逐规格加工范围说明');refs[prefix+'.processing_basis']=text(config['evidence_id'],'逐规格加工范围原件',200)
    rows=config['alternatives']
    if not isinstance(rows,list) or len(rows)!=len(options)-1:raise DomainError('须按S2至全部备选顺序填写加工参数，不得缺项或多项')
    for i,(row,o) in enumerate(zip(rows,options[1:]),2):
        if not isinstance(row,dict) or set(row)!=set(PARAMS)|{'evidence'} or not isinstance(row['evidence'],dict) or set(row['evidence'])!=set(PARAMS):raise DomainError('每种备选加工须仅含起端、终端切除、留用下限及三项逐参数原件')
        for k in PARAMS:
            number(row[k],k=='reusable_min_length');v=linear(row[k],unit,k=='reusable_min_length');key=prefix+'.processing.'+str(i)+'.'+k
            dimensions[key]=str(row[k]);refs[key]=text(row['evidence'][k],'逐规格加工参数原件',200);o[k]=Fraction(v);o['processing_asts'][k]=op('multiply',{'dimension':key},{'constant':LENGTH[unit]})
            conversions.append(dict(parameter=key,original_value=str(row[k]),source_unit=unit,factor=LENGTH[unit],canonical_value=str(v),canonical_unit='m',rule_version=UNIT_VERSION))
        if o['trim_start']+o['trim_end']>=o['length']:raise DomainError('逐规格起终端切除之和须小于该定尺原长')


def describe(option):
    asts=dict(option['processing_asts']);asts['usable_length']=op('subtract',option['ast'],op('add',asts['trim_start'],asts['trim_end']))
    return {**{k:dec(option[k]) for k in PARAMS},'usable_length':dec(option['length']-option['trim_start']-option['trim_end']),'formula_asts':asts}
