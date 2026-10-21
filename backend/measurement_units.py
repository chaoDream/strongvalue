"""Exact physical-unit conversion for evidence-backed measurement parameters."""
from decimal import Decimal
from .engine import GEOMETRIES, DomainError, number, precise

VERSION = 'measurement-units-1.0'
LENGTH = {'m':'1', 'mm':'0.001', 'cm':'0.01', 'dm':'0.1', 'km':'1000'}
VOLUME = {'m³':'1', 'cm³':'0.000001', 'mm³':'0.000000001'}
WEIGHT_PER_LENGTH = {'kg/m':'1', 'g/m':'0.001', 'kg/mm':'1000', 'g/mm':'1'}


def profile(kind, key):
    if key == 'count': return {'canonical':'1','factors':{'1':'1'}}
    if key == 'unit_weight': return {'canonical':'kg/m','factors':WEIGHT_PER_LENGTH}
    if key in ('excavation','occupied'): return {'canonical':'m³','factors':VOLUME}
    return {'canonical':'m','factors':LENGTH}


def profiles():
    return {kind:{key:profile(kind,key) for key in value['fields']} for kind,value in GEOMETRIES.items()}


@precise
def normalize(kind, parameters, units):
    if not isinstance(units,dict) or set(units) != set(parameters):
        raise DomainError('原始单位须逐参数完整填写，不能缺项或夹带其他字段')
    normalized, conversions = {}, []
    for key in GEOMETRIES[kind]['fields']:
        value=parameters[key]
        config=profile(kind,key);source=units[key]
        if not isinstance(source,str) or source not in config['factors']:
            raise DomainError('参数 '+key+' 的原始单位不适用，不能跨计量维度换算')
        raw=number(value,key!='occupied');factor=Decimal(config['factors'][source]);converted=(raw*factor).normalize()
        try:number(converted,key!='occupied')
        except DomainError:raise DomainError('单位换算后的参数超出尺寸或18位小数精度范围，请核对原图单位与有效精度') from None
        normalized[key]=format(converted,'f')
        conversions.append({'parameter':key,'original_value':str(value),'source_unit':source,'factor':str(factor),
                            'canonical_value':format(converted,'f'),'canonical_unit':config['canonical'],'rule_version':VERSION})
    return normalized,conversions


def converted_ast(ast, conversions, prefix):
    """Express conversion in the generated AST while retaining raw dimension references."""
    factors={prefix+x['parameter']:x['factor'] for x in conversions}
    def visit(node):
        if 'dimension' in node and node['dimension'] in factors:
            return {'op':'multiply','args':[node,{'constant':factors[node['dimension']]}]}
        if 'args' in node:return {**node,'args':[visit(child) for child in node['args']]}
        return node
    return visit(ast)
