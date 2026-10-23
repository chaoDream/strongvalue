"""Exact equal-bay post layout on approved straight railing ranges."""
from decimal import Decimal
from fractions import Fraction
from math import isqrt
from . import store
from .compound_takeoff import op,text
from .engine import DomainError,precise
from .planar_takeoff import linear
from .measurement_units import LENGTH,VERSION as UNIT_VERSION

VERSION='railing-equal-bay-posts-1.0'
FIXED_VERSION='railing-fixed-step-posts-1.0'
DIRECTIONS={'from_start':'从区间起端向终端定距','from_end':'从区间终端向起端定距'}
NOTICE='只按已审栏杆计入的每个直线区间均分跨间，跨间数为区间长度/核定最大间距向上取整。S沿来源栏杆已审三维或水平口径，不混用；逐区间明确起终端是否计柱，共享端点只能归属一次。每根下料长度和每米质量另据原件核定，不从栏杆高度推断。仅检查同原边相接区间端点及相同源顶点的重复计柱，跨边内部交叉、近重合、接头和构造加柱仍须专业核定；本模式不能代替完整栏杆设计。'

FIXED_NOTICE='只按已审直线栏杆区间逐段定距留余跨。须逐区间有据明确起端或终端锚向、固定间距和两端归属，内部柱从锚端按固定间距布置，剩余长度留在另一端，不重新均分。S始终按来源原段起点及已审三维或水平口径增加，不能因反向布置反写S。共享端点只归属一次，每根实际下料长及每米质量独立核定。端部不计柱时须另核跨区间实际柱距；不自动处理跨边内部交叉、近重合、构造加柱、避让、基础或施工定位。'


def ranges(result):
    rows=[]
    for edge in result['railing_edges']:
        if edge['mode']=='exclude':continue
        parts=edge['normalized_ranges'] if edge['mode']=='ranges' else [dict(index=0,s_from='0',s_to=edge['length'],length=edge['length'],formula_ast=edge['formula_ast'])]
        for part in parts:
            identity=dict(edge_id=edge['edge_id'],range_index=part['index'],s_from=part['s_from'],s_to=part['s_to'])
            rows.append({**identity,'range_id':store.digest(identity),'label':('S'+str(edge['edge_index']) if edge['role']=='route' else 'R'+str(edge['ring_index'])+' E'+str(edge['edge_index']))+(' 整段' if not part['index'] else ' 区间'+str(part['index'])),'length':part['length'],'full':edge['mode']=='full','length_formula_ast':part['formula_ast']})
    return rows


def fraction_record(v):return {'numerator':str(v.numerator),'denominator':str(v.denominator)}


@precise
def calculate(item,result,prefix):
    fixed=item['mode']=='fixed_posts'
    candidates=ranges(result);plans=item['post_ranges']
    if not isinstance(plans,list) or len(plans)!=len(candidates):raise DomainError('立柱须逐一核定全部已审计入区间的最大间距和两端归属，不能遗漏')
    by_id={}
    for plan in plans:
        if not isinstance(plan,dict) or set(plan)!=({'range_id','spacing','spacing_unit','start_post','end_post','measurement_basis','evidence'}|({'direction'} if fixed else set())) or not isinstance(plan['range_id'],str):raise DomainError('布柱区间须完整填写来源区间、最大间距及单位、两端决定、说明和逐参数依据')
        if plan['range_id'] in by_id:raise DomainError('立柱区间不能重复')
        by_id[plan['range_id']]=plan
    if set(by_id)!={r['range_id'] for r in candidates}:raise DomainError('立柱区间编号过期或不属于当前已审范围，请重新读取')
    dimensions={};refs={};conversions=[];rows=[];stations=[];nodes=[];seen={};edges={e['edge_id']:e for e in result['railing_edges']}
    for i,r in enumerate(candidates,1):
        plan=by_id[r['range_id']];key=prefix+str(i);proofs={'spacing','start_post','end_post','measurement_basis'}|({'direction'} if fixed else set())
        if fixed and (not isinstance(plan['direction'],str) or plan['direction'] not in DIRECTIONS):raise DomainError('定距布柱须逐区间明确从起端或终端布置，不能留空或混用方向')
        if not isinstance(plan['evidence'],dict) or set(plan['evidence'])!=proofs:raise DomainError('立柱间距、两端归属和说明须分别关联原始依据')
        text(plan['measurement_basis'],'逐区间布柱说明')
        if type(plan['start_post']) is not bool or type(plan['end_post']) is not bool:raise DomainError('每个区间两端须明确计柱或由其他区间归属，不能留空或填字符串')
        for k in proofs:refs[key+'.'+k]=text(plan['evidence'][k],'布柱逐参数依据',200)
        spacing=linear(plan['spacing'],plan['spacing_unit'],True);step=Fraction(spacing);edge=edges[r['edge_id']]
        square=edge['squared_length'];edge_square=Fraction(int(square['numerator']),int(square['denominator']))
        a,b=Decimal(r['s_from']),Decimal(r['s_to']);span_square=edge_square if r['full'] else Fraction(b-a)**2
        ratio=span_square/(step*step);n=isqrt(ratio.numerator//ratio.denominator)
        if Fraction(n*n)<ratio:n+=1
        if n>1000:raise DomainError('每个区间最多1000个跨间，请核定间距或拆分实际范围')
        count=n-1+int(plan['start_post'])+int(plan['end_post'])
        if len(stations)+count>1000:raise DomainError('每个材料项最多1000根单组立柱，请拆分实际范围')
        dimensions[key+'.spacing']=str(plan['spacing'])
        for k in ('start_post','end_post'):dimensions[key+'.'+k]=str(int(plan[k]))
        conversions.append(dict(parameter=key+'.spacing',original_value=str(plan['spacing']),source_unit=plan['spacing_unit'],factor=LENGTH[plan['spacing_unit']],canonical_value=str(spacing),canonical_unit='m',rule_version=UNIT_VERSION))
        ast=op('add',{'constant':str(n-1)},{'dimension':key+'.start_post'},{'dimension':key+'.end_post'});nodes.append(ast)
        distance=Decimal(r['length'])/n
        if fixed:
            divisible=span_square==n*n*step*step
            regular=n if divisible else n-1
            residual=Decimal(0) if divisible else Decimal(r['length'])-regular*spacing
            step_ast=op('multiply',{'dimension':key+'.spacing'},{'constant':LENGTH[plan['spacing_unit']]})
            end_ast=op('add',{'constant':str(a)},r['length_formula_ast'])
        for j in range(n+1):
            if j==0 and not plan['start_post'] or j==n and not plan['end_post']:continue
            station=a if j==0 else b if j==n else a+(b-a)*Decimal(j)/n
            station_ast=op('add',{'constant':str(a)},op('divide',op('multiply',r['length_formula_ast'],{'constant':str(j)}),{'constant':str(n)}))
            if fixed:
                if j==0:station_ast={'constant':str(a)}
                elif j==n:station_ast=end_ast
                elif plan['direction']=='from_start':
                    station=a+spacing*j;station_ast=op('add',{'constant':str(a)},op('multiply',step_ast,{'constant':str(j)}))
                else:
                    station=b-spacing*(n-j);station_ast=op('subtract',end_ast,op('multiply',step_ast,{'constant':str(n-j)}))
            # Only exact input boundary identities are used for duplicate ownership.
            # Decimal display positions never create a geometric equality tolerance.
            at_start=j==0 and a==0
            at_end=j==n and (r['full'] or Fraction(b)**2==edge_square)
            identity=None
            if at_start or at_end:
                point=edge[('from_xyz' if at_start else 'to_xyz') if 'from_xyz' in edge else ('from_xy' if at_start else 'to_xy')]
                identity=('vertex',tuple(Fraction(point[k]) for k in sorted(point)))
            elif j in (0,n):identity=('edge',r['edge_id'],Fraction(a if j==0 else b))
            if identity is not None:
                if identity in seen:raise DomainError('共享端点重复计柱：'+seen[identity]+' / '+r['label']+'；请明确只归属一个区间')
                seen[identity]=r['label']
            stations.append(dict(index=len(stations)+1,range_id=r['range_id'],range_label=r['label'],bay_index=j,s=str(station),position_kind='start' if j==0 else 'end' if j==n else 'interior',station_formula_ast=station_ast,**({'direction':plan['direction']} if fixed else {})))
        rows.append({**r,**plan,'index':i,'normalized_spacing':str(spacing),'interval_count':n,'interior_count':n-1,'post_count':count,'actual_spacing':str(distance),'formula_ast':ast,'ceil_certificate':{'lower_spacing_square':fraction_record((n-1)**2*step*step),'range_length_square':fraction_record(span_square),'upper_spacing_square':fraction_record(n*n*step*step),'relation':'lower < range ≤ upper'},**({'regular_interval_count':regular,'regular_spacing':str(spacing),'residual_spacing':str(residual),'residual_side':('end' if plan['direction']=='from_start' else 'start') if not divisible else 'none','residual_formula_ast':op('subtract',r['length_formula_ast'],op('multiply',step_ast,{'constant':str(regular)}))} if fixed else {})})
        if fixed:rows[-1].pop('actual_spacing')
    if not stations:raise DomainError('当前端部归属与间距未生成任何立柱，请核对实际布置')
    return dict(post_ranges=rows,post_stations=stations,post_count=len(stations),formula_ast=op('add',*nodes),dimensions=dimensions,source_evidence=refs,unit_conversions=conversions,rule_version=FIXED_VERSION if fixed else VERSION,notice=FIXED_NOTICE if fixed else NOTICE)
