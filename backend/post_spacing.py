"""Optional exact distance limits between counted posts within each source range."""
from decimal import Decimal,localcontext
from fractions import Fraction
from functools import cmp_to_key
from copy import deepcopy
from .engine import DomainError,precise
from .compound_takeoff import op,text
from .measurement_units import LENGTH,VERSION as UNIT_VERSION
from .planar_takeoff import linear
from .post_adjustments import coordinate,compare
from .railing_posts import fraction_record

VERSION='railing-local-post-spacing-1.0'
NOTICE='仅核查同一材料项、同一已审来源计入区间内，按最终S排序的相邻两根实际计入柱之间的沿S距离，采用来源已审三维或水平口径；不是柱净空或两点直线距离。上限须有独立原件，不默认沿用原布置间距。等于上限允许，超限阻断；判定采用原边根式精确比较，不按显示值或容差放行。未归属端柱时单列边界至最近计入柱距离，该距离不作为实际柱间距或已通过的相邻对。无柱或只有一根柱的区间无相邻对，跨区间、缺口、转角、跨边及闭环首尾柱距未核查，仍须专业核定；本项不能证明全局柱距、专业构造或完整栏杆设计合规。不移动、增加或删除柱，不改变材料数量及质量。'
EQUAL_MODES=('spaced_posts','spaced_added_posts','spaced_adjusted_posts','spaced_added_adjusted_posts')


def exact_record(value,square):
    return dict(constant=fraction_record(value[0]),root_coefficient=fraction_record(value[1]),squared_edge_length=fraction_record(square))


def expression(record):
    def frac(v):return v['numerator'] if v['denominator']=='1' else v['numerator']+' / '+v['denominator']
    a,b,q=record['constant'],record['root_coefficient'],record['squared_edge_length']
    if b['numerator']=='0':return frac(a)
    root='('+frac(b)+') × √('+frac(q)+')'
    return root if a['numerator']=='0' else frac(a)+' + '+root


def display(value,square):
    # Sign and limit decisions never use this decimal rendering. Extra working
    # digits preserve a tiny positive difference after cancellation at large S.
    with localcontext() as c:
        c.prec=160
        v=Decimal(value[0].numerator)/Decimal(value[0].denominator)+(Decimal(value[1].numerator)/Decimal(value[1].denominator))*(Decimal(square.numerator)/Decimal(square.denominator)).sqrt()
    return str(+v)


def subtract(right,left):return right[0]-left[0],right[1]-left[1]


def position(row,p,equal):
    return (Fraction(p['s']),Fraction(0)) if p.get('moved') else coordinate(row,p,equal)


def identity(p):
    if p.get('additional_index'):return '构造加柱 #'+str(p['additional_index'])+' '+p['additional_label']
    return {'start':'区间起端','end':'区间终端'}.get(p.get('original_position_kind',p['position_kind']),'原规则 j='+str(p.get('bay_index','')))


@precise
def calculate(check,layout,source,mode,prefix):
    if isinstance(check,dict) and 'scope' in check:
        from .path_post_spacing import calculate as path_quantity
        return path_quantity(check,layout,source,mode,prefix)
    if not isinstance(check,dict) or set(check)!={'max_spacing','spacing_unit','measurement_basis','evidence'}:raise DomainError('实际柱距核查须完整填写上限、单位、范围说明及依据')
    if not isinstance(check['evidence'],dict) or set(check['evidence'])!={'max_spacing','measurement_basis'}:raise DomainError('实际柱距上限与核查范围说明须分别关联原始依据')
    text(check['measurement_basis'],'实际柱距核查范围说明');limit=linear(check['max_spacing'],check['spacing_unit'],True)
    refs={prefix+'.'+k:text(check['evidence'][k],'实际柱距逐参数依据',200) for k in ('max_spacing','measurement_basis')}
    key=prefix+'.max_spacing';limit_ast=op('multiply',{'dimension':key},{'constant':LENGTH[check['spacing_unit']]})
    rows=[];edges={e['edge_id']:e for e in source['railing_edges']};total=0;equal=mode in EQUAL_MODES
    for r in layout['post_ranges']:
        sq=edges[r['edge_id']]['squared_length'];square=Fraction(int(sq['numerator']),int(sq['denominator']))
        points=[p for p in layout['post_stations'] if p['range_id']==r['range_id']]
        coords={p['index']:position(r,p,equal) for p in points}
        points.sort(key=cmp_to_key(lambda x,y:compare(coords[x['index']],coords[y['index']],square)))
        pairs=[];maximum=None;max_index=None
        for i,(left,right) in enumerate(zip(points,points[1:]),1):
            distance=subtract(coords[right['index']],coords[left['index']])
            if compare(distance,(Fraction(0),Fraction(0)),square)<=0:raise DomainError('实际柱距核查发现同区间柱位重合，请修订布置')
            if compare(distance,(Fraction(limit),Fraction(0)),square)>0:raise DomainError(r['label']+' 柱'+str(left['index'])+'→柱'+str(right['index'])+' 实际柱距超过核定上限'+str(limit)+'m；须有据修订布置或上限，不能按原规则间距认定移后合规')
            if maximum is None or compare(distance,maximum,square)>0:maximum=distance;max_index=i
            pairs.append(dict(index=i,from_post=left['index'],to_post=right['index'],from_identity=identity(left),to_identity=identity(right),from_s=left['s'],to_s=right['s'],distance=display(distance,square),exact_distance=exact_record(distance,square),distance_formula_ast=op('subtract',deepcopy(right['station_formula_ast']),deepcopy(left['station_formula_ast'])),comparison='distance <= limit',status='within_limit'))
        total+=len(pairs);a=(Fraction(r['s_from']),Fraction(0));end=(Fraction(0),Fraction(1)) if r['full'] else (Fraction(r['s_to']),Fraction(0))
        start_gap=subtract(coords[points[0]['index']],a) if points else None
        end_gap=subtract(end,coords[points[-1]['index']]) if points else None
        rows.append(dict(range_id=r['range_id'],label=r['label'],post_count=len(points),pair_count=len(pairs),status='within_limit' if pairs else 'no_pairs',max_distance=display(maximum,square) if maximum is not None else None,max_pair_index=max_index,
                         unposted_start_distance=display(start_gap,square) if start_gap is not None else None,unposted_end_distance=display(end_gap,square) if end_gap is not None else None,
                         unposted_start_exact=exact_record(start_gap,square) if start_gap is not None else None,unposted_end_exact=exact_record(end_gap,square) if end_gap is not None else None,
                         fully_unposted=not points,pairs=pairs))
    if not total:raise DomainError('当前材料项没有区间内实际相邻柱对，不能启用柱距上限核查；端部、跨区间及跨边须另核')
    return dict(**check,normalized_limit=str(limit),limit_formula_ast=limit_ast,ranges=rows,pair_count=total,checked_range_count=sum(bool(r['pairs']) for r in rows),unpaired_range_count=sum(not r['pairs'] for r in rows),
                dimensions={key:str(check['max_spacing'])},source_evidence=refs,
                unit_conversions=[dict(parameter=key,original_value=str(check['max_spacing']),source_unit=check['spacing_unit'],factor=LENGTH[check['spacing_unit']],canonical_value=str(limit),canonical_unit='m',rule_version=UNIT_VERSION)],rule_version=VERSION,notice=NOTICE)
