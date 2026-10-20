"""Pure, versioned geometry and pricing calculations. Never evaluates user code."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, ROUND_HALF_EVEN, ROUND_DOWN, localcontext
from functools import wraps

GEOMETRIES = {
    'slab': {'label': '矩形底板 / 垫层', 'unit': 'm³', 'fields': {'length': '长度（m）', 'width': '宽度（m）', 'thickness': '厚度（m）'}},
    'walls': {'label': '等厚矩形池壁', 'unit': 'm³', 'fields': {'length': '外长（m）', 'width': '外宽（m）', 'thickness': '壁厚（m）', 'height': '壁高（m）'}},
    'pipe': {'label': '确认直线管段 / 栏杆', 'unit': 'm', 'fields': {'length': '确认设计长度（m）'}},
    'area': {'label': '矩形防水 / 楼地面 / 模板接触面', 'unit': 'm²', 'fields': {'length': '展开长度（m）', 'width': '展开宽度（m）'}},
    'masonry': {'label': '净墙体 / 隔墙 / 梁', 'unit': 'm³', 'fields': {'length': '净长（m）', 'height': '高度（m）', 'thickness': '厚度（m）'}},
    'trench': {'label': '已核定断面的梯形沟槽', 'unit': 'm³', 'fields': {'length': '沟槽长度（m）', 'bottom_width': '槽底宽（m）', 'top_width': '槽顶宽（m）', 'depth': '挖深（m）'}},
    'backfill': {'label': '已审开挖扣实体积的回填', 'unit': 'm³', 'fields': {'excavation': '已审开挖体积（m³）', 'occupied': '占用体积（m³）'}},
    'ring': {'label': '圆形井壁 / 环形构筑物', 'unit': 'm³', 'fields': {'outer_diameter': '外径（m）', 'inner_diameter': '内径（m）', 'height': '高度（m）'}},
    'rebar_schedule': {'label': '钢筋表逐规格汇总（须配筋证据）', 'unit': 'kg', 'fields': {'length': '单根下料长度（m）', 'count': '根数', 'unit_weight': '核定每米质量（kg/m）'}},
}


class DomainError(ValueError):
    pass


def precise(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with localcontext() as context:
            context.prec = 80
            return fn(*args, **kwargs)
    return wrapped


def number(value, positive=False):
    try:
        n = Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise DomainError('请输入有效数字')
    if not n.is_finite() or n.adjusted() > 12 or n > Decimal('1e12') or n < 0 or (positive and n == 0):
        raise DomainError('数值须为有限非负数，尺寸须大于零且不超过 1e12')
    if len(n.as_tuple().digits) > 18 or n.as_tuple().exponent < -18:
        raise DomainError('输入最多保留18位有效数字和18位小数，请核对原始精度')
    return n


def money(n):
    if not n.is_finite() or n.adjusted() > 18:
        raise DomainError('金额超出当前引擎支持范围，请核对单位和消耗系数')
    return str(n.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def quantity_number(value):
    """Geometry outputs may retain more digits than manually entered parameters."""
    try:
        n = Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise DomainError('工程量无效')
    if not n.is_finite() or n < 0 or n > Decimal('1e18') or len(n.as_tuple().digits) > 80:
        raise DomainError('工程量超出确定性引擎范围')
    return n


def rounding_config(rules):
    config = rules.get('rounding_config', {})
    digits = config.get('unit_price_digits', 2)
    if type(digits) is not int or not 0 <= digits <= 6 or config.get('mode', 'HALF_UP') not in ('HALF_UP', 'HALF_EVEN', 'DOWN'):
        raise DomainError('单价修约位数须为0至6位，方式须为 HALF_UP / HALF_EVEN / DOWN')
    return digits, {'HALF_UP': ROUND_HALF_UP, 'HALF_EVEN': ROUND_HALF_EVEN, 'DOWN': ROUND_DOWN}[config.get('mode', 'HALF_UP')]


def expression(expr, context, depth=0):
    """Small arithmetic AST, no eval, functions, attributes or user code."""
    if depth > 8:
        raise DomainError('费用表达式超过8层')
    if isinstance(expr, str):
        if expr not in context:
            raise DomainError('未知或尚未计算的费用基数：'+expr)
        return context[expr]
    if not isinstance(expr, dict):
        raise DomainError('费用表达式须为引用或受控运算对象')
    if set(expr) == {'constant'}:
        return number(expr['constant'])
    if set(expr) != {'op', 'args'} or expr['op'] not in ('add', 'subtract', 'multiply', 'divide') or not isinstance(expr['args'], list) or not 2 <= len(expr['args']) <= 8:
        raise DomainError('费用表达式仅支持2至8个参数的加减乘除')
    values = [expression(v, context, depth+1) for v in expr['args']]
    value = values[0]
    for v in values[1:]:
        if expr['op'] == 'add': value += v
        elif expr['op'] == 'subtract': value -= v
        elif expr['op'] == 'multiply': value *= v
        elif v == 0: raise DomainError('费用表达式除数为零')
        else: value /= v
        if not value.is_finite() or abs(value) > Decimal('1e18'):
            raise DomainError('费用基数超出范围')
    return value


def validate_rules(rules):
    rounding_config(rules)
    known = {'subtotal', 'running', 'labor', 'material', 'machinery', 'equipment'}
    fee_ids = set()
    def validate(expr, depth=0, budget=None):
        budget = budget if budget is not None else [100]
        budget[0] -= 1
        if depth > 8 or budget[0] < 0: raise DomainError('费用表达式过于复杂')
        if isinstance(expr, str):
            if expr not in known: raise DomainError('未知或向前引用的费用基数：'+expr)
            return
        if not isinstance(expr, dict): raise DomainError('费用表达式须为基数引用或受控运算')
        if set(expr) == {'constant'}:
            number(expr['constant'])
            return
        if set(expr) != {'op', 'args'} or expr['op'] not in ('add', 'subtract', 'multiply', 'divide') or not isinstance(expr['args'], list) or not 2 <= len(expr['args']) <= 8:
            raise DomainError('费用表达式仅允许2至8项加减乘除')
        for item in expr['args']: validate(item, depth+1, budget)
    for fee in rules.get('fees', []):
        validate(fee['basis'])
        if number(fee['rate']) > 1: raise DomainError('费率须在0至1之间')
        key = str(fee.get('key', fee['name']))
        if key in fee_ids: raise DomainError('费用名称/标识重复')
        fee_ids.add(key)
        known.add('fee:'+key)


@precise
def takeoff(kind, params):
    if kind not in GEOMETRIES:
        raise DomainError('不支持该构件类型；不能使用历史含钢量推算精确钢筋量')
    definition = GEOMETRIES[kind]
    if kind == 'slab':
        keys, formula, unit = ['length', 'width', 'thickness'], 'L × W × T − deduction', 'm³'
    elif kind == 'walls':
        keys, formula, unit = ['length', 'width', 'thickness', 'height'], '[L × W − (L − 2T) × (W − 2T)] × H − deduction', 'm³'
    elif kind == 'pipe':
        keys, formula, unit = ['length'], 'L − deduction', 'm'
    else:
        keys, unit = list(definition['fields']), definition['unit']
        formula = {'area': 'L × W − deduction', 'masonry': 'L × H × T − deduction',
                   'trench': '(Btop + Bbottom) / 2 × depth × L − deduction',
                   'backfill': 'excavation − occupied − deduction',
                   'ring': 'π / 4 × (Douter² − Dinner²) × H − deduction',
                   'rebar_schedule': 'length × count × unit_weight − deduction'}[kind]
    p = {k: number(params.get(k), k != 'occupied') for k in keys}
    deduction = number(params.get('deduction', '0'))
    if kind == 'slab':
        gross = p['length'] * p['width'] * p['thickness']
    elif kind == 'walls':
        if p['thickness'] * 2 >= min(p['length'], p['width']):
            raise DomainError('池壁厚度超过净空允许范围')
        gross = (p['length'] * p['width'] - (p['length']-2*p['thickness']) * (p['width']-2*p['thickness'])) * p['height']
    elif kind == 'pipe':
        gross = p['length']
    elif kind == 'area':
        gross = p['length'] * p['width']
    elif kind == 'masonry':
        gross = p['length'] * p['height'] * p['thickness']
    elif kind == 'trench':
        if p['top_width'] < p['bottom_width']:
            raise DomainError('槽顶宽不得小于槽底宽，特殊断面须另建专业规则')
        gross = (p['top_width']+p['bottom_width'])/2*p['depth']*p['length']
    elif kind == 'backfill':
        gross = p['excavation']-p['occupied']
    elif kind == 'ring':
        if p['outer_diameter'] <= p['inner_diameter']:
            raise DomainError('环形构件外径须大于内径')
        pi = Decimal('3.1415926535897932384626433832795028841971693993751')
        gross = pi/4*(p['outer_diameter']**2-p['inner_diameter']**2)*p['height']
    elif kind == 'rebar_schedule':
        if p['count'] != p['count'].to_integral_value():
            raise DomainError('钢筋根数须为整数')
        gross = p['length']*p['count']*p['unit_weight']
    if deduction > gross:
        raise DomainError('扣减量不得超过毛工程量')
    if gross.adjusted() > 18:
        raise DomainError('工程量超出当前单体引擎范围，请核对尺寸单位')
    return {'quantity': str(gross-deduction), 'gross': str(gross), 'unit': unit,
            'formula': formula, 'rule_version': 'geometry-1.0' if kind in ('slab', 'walls', 'pipe') else 'geometry-2.0', 'parameters': params}


@precise
def calculate(items, rules):
    """Round unit price, line total, then each fee. Rate basis is explicit."""
    validate_rules(rules)
    digits, rounding_mode = rounding_config(rules)
    def rounded(value, precision=2):
        if not value.is_finite() or value.adjusted() > 18:
            raise DomainError('金额超出范围')
        return value.quantize(Decimal(1).scaleb(-precision), rounding=rounding_mode)
    lines, categories = [], {k: Decimal(0) for k in ['labor', 'material', 'machinery', 'equipment']}
    for item in items:
        resources = []
        direct = Decimal(0)
        for quota in item['quotas']:
            coefficient = number(quota.get('coefficient', '1'), True)
            for r in quota['resources']:
                cost = number(r['consumption']) * number(r['price']) * coefficient
                direct += cost
                resources.append({**r, 'quota_code': quota['code'], 'coefficient': str(coefficient), 'cost': str(cost)})
                category = {'人工': 'labor', '材料': 'material', '机械': 'machinery', '设备': 'equipment'}.get(r.get('type'))
                if category:
                    categories[category] += cost*quantity_number(item['quantity'])
        unit = rounded(direct, digits)
        total = rounded(unit * quantity_number(item['quantity']))
        lines.append({**item, 'resources_snapshot': resources, 'unit_price': str(unit), 'amount': str(total)})
    subtotal = sum((Decimal(x['amount']) for x in lines), Decimal(0))
    running, fees = subtotal, []
    context = {**categories, 'subtotal': subtotal, 'running': running}
    fee_ids = set()
    for fee in rules.get('fees', []):
        basis = expression(fee['basis'], context)
        if basis < 0:
            raise DomainError('费用计算基数不得为负数')
        rate = number(fee['rate'])
        if rate > 1:
            raise DomainError('费率使用小数，范围为 0 到 1')
        amount = rounded(basis * rate)
        fees.append({**fee, 'base_amount': money(basis), 'amount': money(amount)})
        running += amount
        context['running'] = running
        key = str(fee.get('key', fee['name']))
        if key in fee_ids:
            raise DomainError('费用名称/标识重复')
        fee_ids.add(key)
        context['fee:'+key] = amount
    return {'lines': lines, 'subtotal': money(subtotal), 'fees': fees, 'total': money(running),
            'rounding': f'{rounding_mode}; unit_price={digits}, line_total=2, fee=2', 'engine': 'pricing-2.0',
            'resource_bases': {k: str(v) for k, v in categories.items()}}
