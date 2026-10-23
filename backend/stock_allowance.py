"""Evidence-backed linear stock planning, separate from net BOQ quantities."""
from decimal import Decimal
from .compound_takeoff import op,text
from .engine import DomainError,number,quantity_number,precise
from .measurement_units import LENGTH,VERSION as UNIT_VERSION
from .planar_takeoff import linear

VERSION='railing-linear-stock-allowance-1.0'
NOTICE='仅对明确启用的等截面线材项编制备料附表，不是完整采购单或排料优化。每组接头总数独立据图填写，不从路径长、柱数、道数或定尺推断，也不再乘道数或件数；每处搭接须同长，不同做法拆项。须确认原净下料不含本次搭接及损耗，不能重复追加已含余量的实际下料。搭接增量=每组接头数×每处搭接长度；损耗基数为净线材长度加搭接增量，备料=(净长+搭接增量)×(1+损耗率/100)。单位质量保持原件口径，来源重复只乘一次，不对分项作中间修约。不自动加入工程量清单、定额消耗、资源价格或报价，避免与组价既有损耗重复。未启用项未编备料，板网片、变截面、接头配件及立柱基础须另核。'
FIELDS=('joint_count','lap_length','loss_percent','measurement_basis')


@precise
def calculate(config,row,groups,prefix):
    if not isinstance(config,dict) or set(config)!=set(FIELDS)|{'length_unit','base_excludes_allowance','evidence'}:raise DomainError('线材备料须完整填写接头数、搭接长度及单位、损耗率、净量确认、说明及依据')
    if not isinstance(config['evidence'],dict) or set(config['evidence'])!=set(FIELDS):raise DomainError('线材备料接头、搭接、损耗及说明须分别关联原件')
    if config['base_excludes_allowance'] is not True:raise DomainError('须确认原净下料不含本次搭接及损耗，避免重复追加')
    text(config['measurement_basis'],'线材备料及不重复说明')
    count=number(config['joint_count']);loss=number(config['loss_percent']);number(config['lap_length']);lap=linear(config['lap_length'],config['length_unit'])
    if count!=count.to_integral_value() or count>1000000:raise DomainError('每组接头总数须为0至1000000的整数，不再乘道数或件数')
    if loss>100:raise DomainError('当前线材备料损耗率支持0至100%，请按原件核定')
    if (count==0)!=(lap==0):raise DomainError('无搭接时接头数及搭接长度均须明确为0；有接头时搭接长度须大于0')
    refs={prefix+'.'+k:text(config['evidence'][k],'线材备料逐参数依据',200) for k in FIELDS}
    dimensions={prefix+'.'+k:str(config[k]) for k in ('joint_count','lap_length','loss_percent')}
    count_ast={'dimension':prefix+'.joint_count'};lap_ast=op('multiply',{'dimension':prefix+'.lap_length'},{'constant':LENGTH[config['length_unit']]})
    net=Decimal(row['single_group_stock_length']);weight=Decimal(row['normalized_unit_weight'])
    net_ast=op('multiply',*row['formula_ast']['args'][:2]);weight_ast=row['formula_ast']['args'][2]
    extra=count*lap;loss_length=(net+extra)*loss/100;procurement=net+extra+loss_length
    extra_ast=op('multiply',count_ast,lap_ast);loss_ast=op('multiply',op('add',net_ast,extra_ast),op('divide',{'dimension':prefix+'.loss_percent'},{'constant':'100'}))
    asts={'net_length':net_ast,'lap_length':extra_ast,'loss_length':loss_ast,'procurement_length':op('add',net_ast,extra_ast,loss_ast)}
    values={'net_length':net,'lap_length':extra,'loss_length':loss_length,'procurement_length':procurement}
    values.update({k.replace('_length','_mass'):v*weight for k,v in list(values.items())})
    asts.update({k.replace('_length','_mass'):op('multiply',v,weight_ast) for k,v in list(asts.items())})
    return dict(**config,normalized_lap_length=str(lap),loss_fraction=str(loss/100),
                single_group={k:str(quantity_number(v)) for k,v in values.items()},
                total={k:str(quantity_number(v*groups)) for k,v in values.items()},
                single_group_formula_asts=asts,total_formula_asts={k:op('multiply',v,{'dimension':'multiplicity'}) for k,v in asts.items()},
                dimensions=dimensions,source_evidence=refs,
                unit_conversions=[dict(parameter=prefix+'.lap_length',original_value=str(config['lap_length']),source_unit=config['length_unit'],factor=LENGTH[config['length_unit']],canonical_value=str(lap),canonical_unit='m',rule_version=UNIT_VERSION)],
                rule_version=VERSION,notice=NOTICE)
