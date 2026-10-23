"""Explicit internal-post moves and closed no-post ranges on a regular layout."""
from copy import deepcopy
from decimal import Decimal
from fractions import Fraction
from functools import cmp_to_key
from .compound_takeoff import op,text
from .engine import DomainError,precise
from .measurement_units import LENGTH,VERSION as UNIT_VERSION
from .planar_takeoff import linear
from .railing_posts import calculate as original_layout
from .additional_posts import calculate as added_layout

VERSION='railing-fixed-adjusted-posts-1.0'
EQUAL_VERSION='railing-equal-adjusted-posts-1.0'
NOTICE='按已审固定间距及两端归属建立原布置，再按原件内部节点号j逐根移柱，数量及规格不变，不自动删柱、加柱或重新均分。j按原布置S递增编号，不是全部材料表柱号；S从来源原段起点按已审三维或水平口径增加。禁布范围为含两端的闭区间，移后全部计入柱须在禁布范围之外；内部柱不得移到区间端部、与其他柱重合或交叉换序，端柱只能通过原两端归属核定。原定距跨数及余跨仅描述调整前布置，移后实际柱距、构造合规、跨边交叉、基础及施工定位须另核，不自动寻找避让位置。'

EQUAL_NOTICE='按已审最大间距、均分跨间及两端归属建立原布置，再按原件原内部均分节点号j逐根移柱，原节点位置及公式保留，数量及规格不变，不自动删柱、加柱或重新均分。j按原布置S递增编号，不是材料表总柱号；新S从来源原段起点按已审三维或水平口径增加。禁布范围为含两端的闭区间，移后全部计入柱必须在其外；内部柱不得移到区间端部、与其他柱重合或交叉换序，端柱仍通过原两端归属核定。原跨数、最大间距及均分跨间距描述移前布置，不能据此认定移后实际柱距合规，移后专业构造、跨边交叉、基础及施工定位须另核，不自动寻找避让位置。'

COMBINED_MODES=('fixed_added_adjusted_posts','spaced_added_adjusted_posts')
COMBINED_NOTICE='先按已审定距或均分规则及两端归属建立原规则柱，再按原件列出原构造加柱，明确构成移前完整布置。原规则内部柱按节点j识别，原构造加柱按本区间输入序号识别；两类编号独立，不是总柱号，均须分别有类型、编号及新S依据。原构造加柱须在原布置中合法且不重合，移柱不能修复非法原布置。仅移动内部柱，保留原类型、编号、名称、原S及表达式，数量规格不变。新S沿来源原段已审口径增加；全部移后柱须严格保持原S顺序、互不重合且位于禁布闭区间外，端柱仍由原归属核定。不自动删柱、加柱、重排、重新均分或选择避让位置；原间距、跨数及余跨仅描述原规则，移后全局柱距、构造、跨边交叉、基础及施工定位须另核。'


def compare(a,b,square):
    """Exact sign of (a0+a1*sqrt(square)) - (b0+b1*sqrt(square))."""
    x,y=a[0]-b[0],a[1]-b[1]
    if not y:return (x>0)-(x<0)
    if not x:return (y>0)-(y<0)
    if (x>0)==(y>0):return (x>0)-(x<0)
    z=x*x-y*y*square
    return ((z>0)-(z<0))*((x>0)-(x<0))


def coordinate(row,p,equal=False):
    if p['position_kind']=='additional':return Fraction(p['s']),Fraction(0)
    a,b=Fraction(row['s_from']),Fraction(row['s_to']);n=p['bay_index'];step=Fraction(row['normalized_spacing'])
    if p['position_kind']=='start':return a,Fraction(0)
    if p['position_kind']=='end':return (Fraction(0),Fraction(1)) if row['full'] else (b,Fraction(0))
    if equal:
        ratio=Fraction(n,row['interval_count'])
        return (Fraction(0),ratio) if row['full'] else (a+(b-a)*ratio,Fraction(0))
    if row['direction']=='from_start':return a+n*step,Fraction(0)
    offset=(row['interval_count']-n)*step
    return (-offset,Fraction(1)) if row['full'] else (b-offset,Fraction(0))


@precise
def calculate(item,result,prefix):
    combined=item['mode'] in COMBINED_MODES
    equal=item['mode'] in ('spaced_adjusted_posts','spaced_added_adjusted_posts')
    base=deepcopy(item);base['mode']=('spaced_added_posts' if equal else 'fixed_added_posts') if combined else ('spaced_posts' if equal else 'fixed_posts');plans=base.get('post_ranges')
    if not isinstance(plans,list):raise DomainError('避让移柱须明确逐区间布置')
    controls={}
    for plan in plans:
        if not isinstance(plan,dict) or not isinstance(plan.get('range_id'),str) or not {'avoidance_ranges','post_moves'}<=set(plan):raise DomainError('每区间须明确禁布范围和移柱列表，无则为空列表')
        avoided,moves=plan.pop('avoidance_ranges'),plan.pop('post_moves')
        if not isinstance(avoided,list) or len(avoided)>20 or not isinstance(moves,list) or len(moves)>100:raise DomainError('每区间最多20个禁布范围、100根显式移柱')
        controls[plan['range_id']]=(avoided,moves)
    g=(added_layout if combined else original_layout)(base,result,prefix);edges={r['edge_id']:r for r in result['railing_edges']};changed=forbidden=0
    for row in g['post_ranges']:
        key=prefix+str(row['index']);avoided,moves=controls[row['range_id']];edge=edges[row['edge_id']];sq=edge['squared_length'];square=Fraction(int(sq['numerator']),int(sq['denominator']))
        a=Fraction(row['s_from']);end=(Fraction(0),Fraction(1)) if row['full'] else (Fraction(row['s_to']),Fraction(0))
        points=[p for p in g['post_stations'] if p['range_id']==row['range_id']]
        def identity(p):return ('additional',p['additional_index']) if p.get('additional_index') else ('regular',p['bay_index'])
        if combined:
            points.sort(key=cmp_to_key(lambda x,y:compare(coordinate(row,x,equal),coordinate(row,y,equal),square)))
            for p in points:p.update(original_position_kind=p['position_kind'],original_node_type=identity(p)[0],original_node_index=identity(p)[1])
        original={identity(p):coordinate(row,p,equal) for p in points};positions=dict(original)
        for p in points:p.update(original_s=p['s'],original_station_formula_ast=deepcopy(p['station_formula_ast']),moved=False)
        row['avoidance_ranges']=[];row['post_moves']=[];labels=set();intervals=set()
        for j,avoid in enumerate(avoided,1):
            if not isinstance(avoid,dict) or set(avoid)!={'label','from','to','unit','measurement_basis','evidence'}:raise DomainError('禁布范围须完整填写名称、起终S、单位、说明和依据')
            label=text(avoid['label'],'禁布范围名称',200);text(avoid['measurement_basis'],'禁布范围说明')
            if not isinstance(avoid['evidence'],dict) or set(avoid['evidence'])!={'from','to','measurement_basis'}:raise DomainError('禁布起终端距和说明须分别关联原始依据')
            lo,hi=linear(avoid['from'],avoid['unit']),linear(avoid['to'],avoid['unit']);low,high=Fraction(lo),Fraction(hi)
            if low<a or low>=high or compare((high,Fraction(0)),end,square)>0:raise DomainError('禁布范围须在本计入区间内且起距小于终距，不得跨越缺口')
            if label in labels or (low,high) in intervals:raise DomainError('同一区间禁布名称或等值范围不能重复')
            labels.add(label);intervals.add((low,high));k=key+'.avoidance.'+str(j)
            asts={}
            for name in ('from','to'):
                g['dimensions'][k+'.'+name]=str(avoid[name]);asts[name]=op('multiply',{'dimension':k+'.'+name},{'constant':LENGTH[avoid['unit']]})
                g['unit_conversions'].append(dict(parameter=k+'.'+name,original_value=str(avoid[name]),source_unit=avoid['unit'],factor=LENGTH[avoid['unit']],canonical_value=str(lo if name=='from' else hi),canonical_unit='m',rule_version=UNIT_VERSION))
            for name in ('from','to','measurement_basis'):g['source_evidence'][k+'.'+name]=text(avoid['evidence'][name],'禁布范围逐参数依据',200)
            row['avoidance_ranges'].append({**avoid,'index':j,'normalized_from':str(lo),'normalized_to':str(hi),'from_formula_ast':asts['from'],'to_formula_ast':asts['to'],'original_post_count':sum(compare(p,(low,Fraction(0)),square)>=0 and compare(p,(high,Fraction(0)),square)<=0 for p in original.values()),'final_post_count':0})
        seen=set()
        for j,move in enumerate(moves,1):
            if not isinstance(move,dict) or set(move)!=({'node_index','s','s_unit','measurement_basis','evidence'}|({'node_type'} if combined else set())):raise DomainError('移柱须完整填写原内部节点号j、新端距S及单位、说明和依据')
            node=move['node_index'];text(move['measurement_basis'],'移柱说明')
            kind=move.get('node_type','regular')
            if not isinstance(kind,str) or kind not in ('regular','additional') or (kind=='additional' and not combined):raise DomainError('须明确移柱类型regular或additional')
            if type(node) is not int or node<1:raise DomainError('移柱原编号须为正整数')
            ref=(kind,node)
            if ref not in original or (kind=='regular' and node>=row['interval_count']):raise DomainError('只可移动原规则内部节点或原构造加柱，不能移动端柱或不存在的编号')
            if ref in seen:raise DomainError('同一原内部柱不能重复移柱')
            seen.add(ref)
            if not isinstance(move['evidence'],dict) or set(move['evidence'])!=({'node_index','s','measurement_basis'}|({'node_type'} if combined else set())):raise DomainError('原节点号、新端距S和移柱说明须分别关联原始依据')
            s=linear(move['s'],move['s_unit'],True);target=(Fraction(s),Fraction(0))
            if Fraction(s)<=a or compare(target,end,square)>=0:raise DomainError('移柱目标S须严格在本计入区间内部，不能移到端部或缺口')
            if compare(target,original[ref],square)==0:raise DomainError('移柱目标与原位置相同，请删除无变化的移柱记录')
            k=key+'.moves.'+str(j);g['dimensions'][k+'.s']=str(move['s']);ast=op('multiply',{'dimension':k+'.s'},{'constant':LENGTH[move['s_unit']]})
            for name in (('node_type','node_index','s','measurement_basis') if combined else ('node_index','s','measurement_basis')):g['source_evidence'][k+'.'+name]=text(move['evidence'][name],'移柱逐参数依据',200)
            g['unit_conversions'].append(dict(parameter=k+'.s',original_value=str(move['s']),source_unit=move['s_unit'],factor=LENGTH[move['s_unit']],canonical_value=str(s),canonical_unit='m',rule_version=UNIT_VERSION))
            p=next(p for p in points if identity(p)==ref);p.update(s=str(s),station_formula_ast=ast,moved=True,position_kind='moved')
            positions[ref]=target;row['post_moves'].append({**move,'index':j,'original_s':p['original_s'],'normalized_s':str(s),'station_formula_ast':ast});changed+=1
        ordered=[positions[identity(p)] for p in points]
        if combined:
            ids={id(p) for p in points};first=next(i for i,p in enumerate(g['post_stations']) if id(p) in ids)
            g['post_stations'][first:first+len(points)]=points
        if any(compare(x,y,square)>=0 for x,y in zip(ordered,ordered[1:])):raise DomainError('移柱后与其他柱重合或交叉换序，请按原节点顺序核定目标S')
        for avoid in row['avoidance_ranges']:
            low,high=Fraction(avoid['normalized_from']),Fraction(avoid['normalized_to'])
            if any(compare(p,(low,Fraction(0)),square)>=0 and compare(p,(high,Fraction(0)),square)<=0 for p in ordered):raise DomainError('移后仍有计入柱落在禁布闭区间（含边界），请核对移柱或端部归属')
        forbidden+=len(avoided);row['moved_post_count']=len(moves)
    if not changed and not forbidden:raise DomainError('避让移柱模式须至少明确一个禁布范围或一根移柱，无调整请用原布置模式')
    if combined:
        for i,p in enumerate(g['post_stations'],1):p['index']=i
    return {**g,'moved_post_count':changed,'avoidance_range_count':forbidden,'rule_version':('railing-equal-added-adjusted-posts-1.0' if equal else 'railing-fixed-added-adjusted-posts-1.0') if combined else EQUAL_VERSION if equal else VERSION,'notice':COMBINED_NOTICE if combined else EQUAL_NOTICE if equal else NOTICE}
