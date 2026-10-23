"""Evidence-backed per-hole thresholds; geometric voids remain separately recorded."""
from decimal import Decimal
from .compound_takeoff import op,text
from .engine import DomainError,number
from .measurement_units import LENGTH,VOLUME,VERSION as UNIT_VERSION
from . import foundation_voids,foundation_recesses

VERSION='railing-post-foundation-1.6'
AREA={'m²':'1','cm²':'0.0001','mm²':'0.000001'}
METRICS={'section_area':('m²',AREA),'single_volume':('m³',VOLUME)}
NOTICE='孔洞扣减阈值仅按当前项目原件核定的计量口径执行，不内置或推断官方标准、合同阈值。按每基础每个单孔未修约截面积或实际孔体积，逐孔与阈值比较；可选严格大于或大于等于，先判定后乘最终立柱及来源重复，不能把全部孔、层或基础合并凑阈值。符合时扣本孔全部实际相交体积，不符合时计量扣减为零；原实际孔体积及实体净体积仍完整保留，不改变几何或假装填实。未扣孔仍参与原件、逐层包含、孔间重叠和每层实体净体积检查。计量体积=毛体积−符合规则的扣减，实际实体净体积=毛体积−全部实际孔洞体积；两者明确区分。Decimal 80位，阈值比较和逐孔逐层不修约，仅累计后的计量总体积修约。每根最终立柱一个基础，来源重复一次；圆孔/轴向矩形孔可贯通或有限标高，坐标、台阶、几何及结构边界沿用原规则，旋转斜孔、孔并集、预埋实体及土方钢筋模板等仍另核。'


def enabled(c):return isinstance(c,dict) and 'deduction_rule' in c


def validate_config(c):
    from .foundation_openings import validate_config as validate_geometry
    validate_geometry({k:v for k,v in c.items() if k!='deduction_rule'})
    rule=c['deduction_rule']
    if not isinstance(rule,dict) or set(rule)!={'metric','comparison','threshold','unit','basis','evidence_id','confirmed'}:raise DomainError('孔洞扣减阈值须完整填写单孔指标、比较方式、阈值及单位、计量依据原件和确认')
    if not isinstance(rule['metric'],str) or rule['metric'] not in METRICS or rule['comparison'] not in ('gt','gte'):raise DomainError('孔洞扣减只支持单孔截面积或单孔实际体积，与阈值严格大于或大于等于比较')
    if rule['confirmed'] is not True:raise DomainError('须据原件确认单孔阈值指标、单位、边界比较和计量适用范围')
    canonical,factors=METRICS[rule['metric']]
    if not isinstance(rule['unit'],str) or rule['unit'] not in factors:raise DomainError('孔洞扣减阈值单位须与面积或体积指标一致，不得跨维度')
    number(rule['threshold']);number((number(rule['threshold'])*Decimal(factors[rule['unit']])).normalize());text(rule['basis'],'孔洞阈值计量范围及适用依据',2000);text(rule['evidence_id'],'孔洞阈值原件',200)


def apply(d,gross,gross_ast,tiers,parameters,key,dimensions,refs,conversions):
    validate_config(d['through_holes']);c=d['through_holes'];rule=c['deduction_rule'];clean={**d,'through_holes':{k:v for k,v in c.items() if k!='deduction_rule'}}
    physical_net,physical_net_ast,data,physical_amount=foundation_recesses.apply(clean,gross,gross_ast,tiers,parameters,key,dimensions,refs,conversions,profile=foundation_voids)
    canonical,factors=METRICS[rule['metric']];factor=factors[rule['unit']];threshold=(number(rule['threshold'])*Decimal(factor)).normalize();prefix=key+'.holes.rule.threshold';dimensions[prefix]=str(rule['threshold']);refs[key+'.holes.rule']=rule['evidence_id'];threshold_ast=op('multiply',{'dimension':prefix},{'constant':factor})
    conversions.append(dict(parameter=prefix,original_value=str(rule['threshold']),source_unit=rule['unit'],factor=factor,canonical_value=str(threshold),canonical_unit=canonical,rule_version=UNIT_VERSION))
    charged=Decimal(0);nodes=[];layer_amounts={};layer_nodes={};zero={'constant':'0'}
    for h in data['opening_holes']:
        hp=key+'.holes.'+str(h['index']);p={k:Decimal(v) for k,v in h['normalized_parameters'].items()};asts={k:op('multiply',{'dimension':hp+'.'+k},{'constant':LENGTH[c['unit']]}) for k in foundation_voids.fields(h)}
        metric,metric_ast=foundation_voids.area(h,p,asts) if rule['metric']=='section_area' else (Decimal(h['single_volume']),h['formula_ast']);difference=metric-threshold;deduct= difference>0 if rule['comparison']=='gt' else difference>=0
        amount=Decimal(h['single_volume']) if deduct else Decimal(0);node=h['formula_ast'] if deduct else zero;charged+=amount;nodes.append(node)
        h.update(deducted_volume=str(amount),deduction_formula_ast=node,deduction_decision=dict(metric_value=str(metric),unit=canonical,threshold_difference=str(difference),metric_formula_ast=metric_ast,threshold_difference_ast=op('subtract',metric_ast,threshold_ast),eligible=deduct,relation='gt' if difference>0 else 'eq' if difference==0 else 'lt'))
        for segment in h['segments']:
            i=segment['level_index'];part=Decimal(segment['single_volume']) if deduct else Decimal(0);part_ast=segment['formula_asts']['single_volume'] if deduct else zero;layer_amounts[i]=layer_amounts.get(i,Decimal(0))+part;layer_nodes.setdefault(i,[]).append(part_ast)
            segment['deducted_volume']=str(part);segment['formula_asts']['deducted_volume']=part_ast
    if tiers is not None:
        for layer in tiers:
            i=layer['index'];amount=layer_amounts.get(i,Decimal(0));node=op('add',*layer_nodes.get(i,[]));geometric_node=layer['formula_asts']['single_opening_volume'];geometric_net_node=layer['formula_asts']['net_level_volume']
            layer.update(geometric_opening_volume=layer['single_opening_volume'],geometric_net_volume=layer['net_level_volume'],single_opening_volume=str(amount),net_level_volume=str(Decimal(layer['single_level_volume'])-amount))
            layer['formula_asts'].update(geometric_opening_volume=geometric_node,geometric_net_volume=geometric_net_node,single_opening_volume=node,net_level_volume=op('subtract',layer['formula_asts']['single_level_volume'],node))
    deduction_ast=op('add',*nodes);data.update(single_geometric_opening_volume=str(physical_amount),single_geometric_net_volume=str(physical_net),single_opening_volume=str(charged),opening_deduction_rule={**rule,'normalized_threshold':str(threshold),'canonical_unit':canonical,'threshold_formula_ast':threshold_ast,'eligible_count':sum(h['deduction_decision']['eligible'] for h in data['opening_holes']),'excluded_count':sum(not h['deduction_decision']['eligible'] for h in data['opening_holes'])})
    data['opening_formula_asts'].update(single_geometric_opening_volume=data['opening_formula_asts']['single_opening_volume'],single_geometric_net_volume=physical_net_ast,single_opening_volume=deduction_ast)
    return gross-charged,op('subtract',gross_ast,deduction_ast),data,charged
