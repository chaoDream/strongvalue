"""Opt-in neighboring post distances along connected selected source paths."""
from collections import defaultdict
from copy import deepcopy
from decimal import Decimal,localcontext
from fractions import Fraction
from functools import cmp_to_key
from math import isqrt
from .engine import DomainError,precise
from .compound_takeoff import op,text
from .measurement_units import LENGTH,VERSION as UNIT_VERSION
from .planar_takeoff import linear
from .railing_posts import fraction_record
from .post_spacing import EQUAL_MODES,position,subtract,identity
from .post_adjustments import compare

VERSION='railing-connected-path-spacing-1.0'
NOTICE='按同一材料项、已审原边顺序连接实际连续计入区间，核查最终计入柱的沿路径相邻距离，包含跨相接区间、跨边转角及闭环首尾；采用来源已审三维或水平S口径，不是两柱直线距离或净空。缺口、排除边、不同环分别断开，不通过空间交点或近重合自动连接。闭环只有一根柱时核查回到自身的整圈距离；无柱路径及开路径端部仅披露，不作为已通过柱距。上限与范围须独立原件核定，等于允许，超限阻断；根式合并及有理数包围证明判定，不按显示小数或容差放行。不能证明完整栏杆设计或专业构造合规；不增删移动柱，不改变数量质量。'


def root(q):
    a,b=isqrt(q.numerator),isqrt(q.denominator)
    return Fraction(a,b) if a*a==q.numerator and b*b==q.denominator else None


def normalize(terms):
    """Merge rationally equivalent square classes without integer factorization."""
    constant=Fraction(0);out=[]
    for coefficient,square in terms:
        if not coefficient:continue
        r=root(square)
        if r is not None:constant+=coefficient*r;continue
        for i,(c,q) in enumerate(out):
            r=root(square/q)
            if r is not None:out[i]=(c+coefficient*r,q);break
        else:out.append((coefficient,square))
    return ([(constant,Fraction(1))] if constant else [])+[(c,q) for c,q in out if c]


def sign(terms):
    terms=normalize(terms)
    if not terms:return 0
    # Distinct rational square classes are linearly independent over Q. Once
    # exact zero is removed, narrowing outward rational bounds proves the sign.
    for bits in (64,128,256,512,1024,2048,4096,8192,16384):
        scale=1<<bits;lo=hi=Fraction(0)
        for c,q in terms:
            if q==1:a=b=Fraction(1)
            else:
                n=isqrt((q.numerator*scale*scale)//q.denominator)
                a,b=Fraction(n,scale),Fraction(n+1,scale)
            lo+=c*(a if c>=0 else b);hi+=c*(b if c>=0 else a)
        if lo>0:return 1
        if hi<0:return -1
    raise DomainError('路径柱距根式比较未能取得可靠包围证明，请拆分核定范围；不能按显示精度放行')


def record(terms):return [dict(coefficient=fraction_record(c),radicand=fraction_record(q)) for c,q in normalize(terms)]


def expression(v):
    def f(x):return x['numerator'] if x['denominator']=='1' else x['numerator']+'/'+x['denominator']
    return ' + '.join(f(t['coefficient']) if t['radicand']=={'numerator':'1','denominator':'1'} else '('+f(t['coefficient'])+') × √('+f(t['radicand'])+')' for t in v) or '0'


def display(terms):
    with localcontext() as c:
        c.prec=200
        value=sum((Decimal(a.numerator)/Decimal(a.denominator)*(Decimal(q.numerator)/Decimal(q.denominator)).sqrt() for a,q in normalize(terms)),Decimal(0))
    return str(+value)


@precise
def calculate(check,layout,source,mode,prefix):
    if not isinstance(check,dict) or set(check)!={'scope','max_spacing','spacing_unit','measurement_basis','evidence'} or check['scope']!='connected_path':raise DomainError('连续路径柱距核查须明确范围、上限、单位、说明及依据')
    if not isinstance(check['evidence'],dict) or set(check['evidence'])!={'max_spacing','measurement_basis'}:raise DomainError('连续路径上限与范围说明须分别关联原始依据')
    text(check['measurement_basis'],'连续路径柱距核查范围说明');limit=linear(check['max_spacing'],check['spacing_unit'],True)
    key=prefix+'.max_spacing';limit_ast=op('multiply',{'dimension':key},{'constant':LENGTH[check['spacing_unit']]})
    refs={prefix+'.'+k:text(check['evidence'][k],'路径柱距逐参数依据',200) for k in ('max_spacing','measurement_basis')}
    edges={e['edge_id']:e for e in source['railing_edges']};groups=defaultdict(list);edge_groups=defaultdict(list)
    for e in source['railing_edges']:edge_groups[e['ring_index']].append(e)
    rows={r['range_id']:r for r in layout['post_ranges']};equal=mode in EQUAL_MODES
    def sq(r):
        q=edges[r['edge_id']]['squared_length'];return Fraction(int(q['numerator']),int(q['denominator']))
    def start(r):return Fraction(r['s_from']),Fraction(0)
    def end(r):return (Fraction(0),Fraction(1)) if r['full'] else (Fraction(r['s_to']),Fraction(0))
    for r in rows.values():groups[edges[r['edge_id']]['ring_index']].append(r)
    def joined(a,b,wrap=False):
        ea,eb=edges[a['edge_id']],edges[b['edge_id']]
        if ea['edge_id']==eb['edge_id']:return not wrap and compare(end(a),start(b),sq(a))==0
        ordered=edge_groups[ea['ring_index']];i=next(i for i,e in enumerate(ordered) if e['edge_id']==ea['edge_id'])
        j=next(i for i,e in enumerate(ordered) if e['edge_id']==eb['edge_id'])
        adjacent=j==i+1 or (wrap and ea['role']!='route' and i==len(ordered)-1 and j==0)
        return adjacent and compare(end(a),(Fraction(0),Fraction(1)),sq(a))==0 and start(b)==(0,0)
    paths=[]
    for rs in groups.values():
        links=[joined(a,b) for a,b in zip(rs,rs[1:])]+[joined(rs[-1],rs[0],True)]
        if all(links):paths.append((rs,True));continue
        cut=next(i for i,v in enumerate(links) if not v);ordered=rs[cut+1:]+rs[:cut+1];current=[]
        for r in ordered:
            if current and not joined(current[-1],r,edges[current[-1]['edge_id']]['edge_index']>edges[r['edge_id']]['edge_index']):paths.append((current,False));current=[]
            current.append(r)
        if current:paths.append((current,False))
    output=[];total=0
    for path_index,(rs,closed) in enumerate(paths,1):
        points=[];by_range=defaultdict(list)
        for p in layout['post_stations']:by_range[p['range_id']].append(p)
        for j,r in enumerate(rs):
            ps=by_range[r['range_id']];ps.sort(key=cmp_to_key(lambda a,b:compare(position(r,a,equal),position(r,b,equal),sq(r))))
            points.extend((j,p) for p in ps)
        def span(j,a,b,ast):
            delta=subtract(b,a);r=rs[j]
            return dict(range_id=r['range_id'],label=r['label'],exact_distance=record([(delta[0],Fraction(1)),(delta[1],sq(r))]),distance_formula_ast=ast),[(delta[0],Fraction(1)),(delta[1],sq(r))]
        def between(left,right,wrap=False):
            li,lp=left;ri,rp=right;parts=[];terms=[]
            indices=([li] if li==ri and not wrap else list(range(li,len(rs)))+list(range(ri+1)) if wrap else list(range(li,ri+1)))
            for k,j in enumerate(indices):
                r=rs[j];first=k==0;last=k==len(indices)-1
                a=position(r,lp,equal) if first else start(r);b=position(r,rp,equal) if last else end(r)
                aa=lp['station_formula_ast'] if first else {'constant':r['s_from']}
                bb=rp['station_formula_ast'] if last else op('add',{'constant':r['s_from']},deepcopy(r['length_formula_ast']))
                part,ts=span(j,a,b,op('subtract',deepcopy(bb),deepcopy(aa)));parts.append(part);terms+=ts
            return parts,normalize(terms)
        pairs=[];maximum=None;max_index=None
        neighbors=list(zip(points,points[1:]))+([(points[-1],points[0])] if closed and points else [])
        for i,(left,right) in enumerate(neighbors,1):
            wrap=closed and i==len(neighbors);parts,terms=between(left,right,wrap)
            if sign(terms)<=0:raise DomainError('连续路径存在相邻柱重合，请核定端部归属及柱位')
            if sign(terms+[(-Fraction(limit),Fraction(1))])>0:raise DomainError('连续路径'+str(path_index)+' 柱'+str(left[1]['index'])+'→柱'+str(right[1]['index'])+(' 闭环首尾' if wrap else '')+' 实际柱距超过核定上限'+str(limit)+'m')
            if maximum is None or sign(terms+[(-c,q) for c,q in maximum])>0:maximum=terms;max_index=i
            pairs.append(dict(index=i,from_post=left[1]['index'],to_post=right[1]['index'],from_identity=identity(left[1]),to_identity=identity(right[1]),from_range=rs[left[0]]['label'],to_range=rs[right[0]]['label'],from_s=left[1]['s'],to_s=right[1]['s'],wrap=wrap,cross_range=left[0]!=right[0] or wrap,distance=display(terms),exact_distance=record(terms),segments=parts,distance_formula_ast=op('add',*[p['distance_formula_ast'] for p in parts]),comparison='distance <= limit',status='within_limit'))
        def boundary_gap(first):
            j,p=points[0 if first else -1];terms=[]
            for k in (range(j+1) if first else range(j,len(rs))):
                r=rs[k];a=start(r) if first or k!=j else position(r,p,equal);b=position(r,p,equal) if first and k==j else end(r)
                d=subtract(b,a);terms.extend([(d[0],Fraction(1)),(d[1],sq(r))])
            return display(terms),record(terms)
        start_gap,start_exact=boundary_gap(True) if points and not closed else (None,None)
        end_gap,end_exact=boundary_gap(False) if points and not closed else (None,None)
        total+=len(pairs)
        output.append(dict(index=path_index,label='连续路径'+str(path_index),closed=closed,range_ids=[r['range_id'] for r in rs],range_labels=[r['label'] for r in rs],post_count=len(points),pair_count=len(pairs),status='within_limit' if pairs else 'no_pairs',fully_unposted=not points,max_distance=display(maximum) if maximum is not None else None,max_pair_index=max_index,unposted_start_distance=start_gap,unposted_end_distance=end_gap,unposted_start_exact=start_exact,unposted_end_exact=end_exact,pairs=pairs))
    if not total:raise DomainError('连续路径没有可核查相邻柱对，无柱路径及开路径端部须另核')
    return dict(**check,normalized_limit=str(limit),limit_formula_ast=limit_ast,paths=output,pair_count=total,checked_path_count=sum(bool(p['pairs']) for p in output),unpaired_path_count=sum(not p['pairs'] for p in output),dimensions={key:str(check['max_spacing'])},source_evidence=refs,unit_conversions=[dict(parameter=key,original_value=str(check['max_spacing']),source_unit=check['spacing_unit'],factor=LENGTH[check['spacing_unit']],canonical_value=str(limit),canonical_unit='m',rule_version=UNIT_VERSION)],rule_version=VERSION,notice=NOTICE)
