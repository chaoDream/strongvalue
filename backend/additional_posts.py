"""Explicit, evidence-backed internal posts added to an unchanged regular layout."""
from copy import deepcopy
from decimal import Decimal
from fractions import Fraction
from .compound_takeoff import op,text
from .engine import DomainError,precise
from .measurement_units import LENGTH,VERSION as UNIT_VERSION
from .planar_takeoff import linear
from .railing_posts import calculate as fixed_layout

VERSION='railing-fixed-added-posts-1.0'
EQUAL_VERSION='railing-equal-added-posts-1.0'
NOTICE='在已核定固定间距及端部归属的基础上，逐根按原件端距S增加内部构造柱，原定距柱和余跨保留，不重新均分或移柱。加柱S始终从来源原段起点按已审三维或水平口径增加，与定距锚向无关。加柱须严格在所选计入区间内部，不得与定距柱或其他加柱重合；区间端柱通过两端归属核定。每根加柱与本材料项原柱采用相同独立下料长及每米质量，不同规格须另行列项核对。不自动推断构造加柱、避让、跨边相交去重、全局柱距、基础或施工定位。'
EQUAL_NOTICE='在已核定最大间距、均分跨间及端部归属的基础上，逐根按原件端距S增加内部构造柱。原均分柱位置保持，不以加柱位置重新分段或均分；跨数和原均分跨间距描述追加前布置。加柱S从来源原段起点按已审三维或水平口径增加，须严格在所选计入区间内部，不得与原柱或其他加柱重合，端柱仍通过两端归属核定。每根加柱沿用本材料项独立核定的下料长及每米质量。系统不推断何处应加柱，不自动处理避让、跨边交叉去重、近重合、全局柱距、基础或施工定位，不能代替完整专业设计。'


@precise
def calculate(item,result,prefix):
    equal=item['mode']=='spaced_added_posts'
    base=deepcopy(item);base['mode']='spaced_posts' if equal else 'fixed_posts'
    plans=base.get('post_ranges')
    if not isinstance(plans,list):raise DomainError('构造加柱须明确逐区间布置')
    extras={}
    for plan in plans:
        if not isinstance(plan,dict) or not isinstance(plan.get('range_id'),str) or 'additional_posts' not in plan:raise DomainError('每个区间须明确构造加柱列表，无加柱填空列表')
        additions=plan.pop('additional_posts')
        if not isinstance(additions,list) or len(additions)>100:raise DomainError('每区间最多100根构造加柱，无加柱须为空列表')
        extras[plan.get('range_id')]=additions
    g=fixed_layout(base,result,prefix);edges={e['edge_id']:e for e in result['railing_edges']}
    stations=[];nodes=[];total=0
    for row in g['post_ranges']:
        additions=extras[row['range_id']];key=prefix+str(row['index'])+'.additional.'
        edge=edges[row['edge_id']];sq=edge['squared_length'];square=Fraction(int(sq['numerator']),int(sq['denominator']))
        a,b=Fraction(row['s_from']),Fraction(row['s_to']);d=Fraction(row['normalized_spacing'])
        base_stations=[p for p in g['post_stations'] if p['range_id']==row['range_id']]
        if len(stations)+len(base_stations)+len(additions)>1000:raise DomainError('每个材料项最多1000根单组立柱（含构造加柱），请拆分实际范围')
        normalized=[];seen=set();labels=set()
        for j,post in enumerate(additions,1):
            if not isinstance(post,dict) or set(post)!={'label','s','s_unit','measurement_basis','evidence'}:raise DomainError('构造加柱须逐根完整填写名称、端距S及单位、说明和依据')
            label=text(post['label'],'构造加柱名称',200)
            if label in labels:raise DomainError('同一区间构造加柱名称不能重复')
            labels.add(label);text(post['measurement_basis'],'构造加柱说明')
            if not isinstance(post['evidence'],dict) or set(post['evidence'])!={'s','measurement_basis'}:raise DomainError('构造加柱端距和说明须分别关联原始依据')
            k=key+str(j);s=linear(post['s'],post['s_unit'],True);exact=Fraction(s)
            if exact<=a or (exact*exact>=square if row['full'] else exact>=b):raise DomainError('构造加柱S须严格位于本计入区间内部，端柱请核定两端归属')
            if exact in seen:raise DomainError('构造加柱端距重复，不能重复计根')
            seen.add(exact)
            for p in base_stations:
                # Compare exact rational inputs with the original squared edge length;
                # a rounded square root must never decide whether two posts coincide.
                if p['position_kind']!='interior':continue
                n=p['bay_index']
                if equal:
                    offset=exact-a;spans=row['interval_count']
                    coincident=offset**2*spans**2==square*n*n if row['full'] else offset*spans==(b-a)*n
                elif row['direction']=='from_start':coincident=exact==a+n*d
                else:
                    offset=(row['interval_count']-n)*d
                    coincident=(exact+offset)**2==square if row['full'] else exact==b-offset
                if coincident:raise DomainError('构造加柱与已有'+('均分' if equal else '定距')+'柱重合，请核对端距，不重复计根')
            for name in ('s','measurement_basis'):g['source_evidence'][k+'.'+name]=text(post['evidence'][name],'构造加柱逐参数依据',200)
            g['dimensions'][k+'.s']=str(post['s'])
            ast=op('multiply',{'dimension':k+'.s'},{'constant':LENGTH[post['s_unit']]})
            g['unit_conversions'].append(dict(parameter=k+'.s',original_value=str(post['s']),source_unit=post['s_unit'],factor=LENGTH[post['s_unit']],canonical_value=str(s),canonical_unit='m',rule_version=UNIT_VERSION))
            normalized.append({**post,'index':j,'normalized_s':str(s),'station_formula_ast':ast})
            base_stations.append(dict(range_id=row['range_id'],range_label=row['label'],s=str(s),position_kind='additional',additional_index=j,additional_label=label,station_formula_ast=ast,**({} if equal else {'direction':row['direction']})))
        row.update(base_post_count=row['post_count'],additional_post_count=len(normalized),additional_posts=normalized,post_count=row['post_count']+len(normalized),formula_ast=op('add',row['formula_ast'],{'constant':str(len(normalized))}))
        nodes.append(row['formula_ast']);total+=len(normalized)
        stations.extend(sorted(base_stations,key=lambda p:Decimal(p['s'])))
    if not total:raise DomainError('构造加柱模式须至少明确一根加柱；无加柱请采用原布置模式')
    if len(stations)>1000:raise DomainError('每个材料项最多1000根单组立柱（含构造加柱），请拆分实际范围')
    for i,p in enumerate(stations,1):p['index']=i
    return {**g,'post_stations':stations,'base_post_count':g['post_count'],'additional_post_count':total,'post_count':len(stations),'formula_ast':op('add',*nodes),'rule_version':EQUAL_VERSION if equal else VERSION,'notice':EQUAL_NOTICE if equal else NOTICE}
