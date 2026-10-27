"""Separate, evidence-bound contact blocks; reuse each validated polygon engine."""
from decimal import Decimal
from fractions import Fraction
from .engine import DomainError
from .compound_takeoff import text, op
from .polygon_takeoff import edges, inside, intersects
from . import foundation_polygon_windows as polygon
from . import foundation_simple_windows as simple
from . import foundation_perforated_windows as perforated

VERSION = 'foundation-horizontal-formwork-1.11'
METHOD = 'multi_polygon_vertical_sweep'
RULE = '各分离净块独立按外轮廓减窗口内孔裁切真实接触；逐块直线/圆弧解析积分以200位工作并保留80位，80位累计块结果后继承全部基础个数一次，仅总量有据修约；块间空区不计。'
NOTICE = ('水平顶底、台阶平台及内部孔端可选2至4块分离的直边窗口净区。每块外轮廓和各内孔3至12点，'
          '每块0至4孔及最多36点、全部最多8孔及48点；各块外轮廓严格分离，不接触、交叠或包含，各孔严格在所属外轮廓内且相互分离。'
          '逐块核定名称、支模范围说明及原件，逐坐标原件；共用单位、基础中心和XY方向，Z及全部个数继承已审来源。'
          '内孔W仅排除本块支模，不创建基础实体孔H、不扣混凝土、不生成孔壁或孔端。'
          '逐块分别与真实截面、上层及实体孔相交，不把块间空区、凹口或内孔填实；仅总量修约。'
          '全部净块的全部有效片段无剩余才证明严格零接触，微小正面积缝隙不归零；保留逐块过程及完整AST。'
          '同端面完整窗口净区用精确有理数扫描防重，块间空区及窗口内孔空区可另项，共边可分项，正重叠阻断，跨记录同样检查。'
          '原件、实际接触、材料及修约仍须独立复核；曲边、嵌套岛区、任意水平分区、斜面及个体不同做法另核。')


def enabled(d):
    return isinstance(d,dict) and isinstance(d.get('plan_range'),dict) and d['plan_range'].get('shape')=='multi_polygon'


def part_config(c,p):
    common={k:c[k] for k in ('unit','coordinate_basis','coordinate_evidence_id','scope_confirmed')}
    return dict(**common,shape='polygon_with_holes' if p['holes'] else 'simple_polygon',parameters=p['parameters'],evidence=p['evidence'],**({'holes':p['holes']} if p['holes'] else {}))


def ring_points(d):
    if not enabled(d):return perforated.ring_points(d)
    c=d['plan_range'];return [ring for p in c['parts'] for ring in perforated.ring_points(dict(plan_range=part_config(c,p)))]


def bounds(d):
    points=[p for ring in ring_points(d) for p in ring]
    return min(x for x,y in points),max(x for x,y in points),min(y for x,y in points),max(y for x,y in points)


def topology(c):
    outers=[[tuple(map(Fraction,p)) for p in polygon.vertices(part_config(c,part))] for part in c['parts']]
    checks=[]
    for i,outer in enumerate(outers,1):
        for j,other in enumerate(outers[:i-1],1):
            if any(intersects(a,b,p,q) for a,b in edges(outer) for p,q in edges(other)) or inside(outer[0],other)!=-1 or inside(other[0],outer)!=-1:
                raise DomainError('多块窗口外轮廓须严格分离，不能接触、交叠或包含；嵌套岛区另核')
            checks.append(dict(left='B'+str(j),right='B'+str(i),relation='strictly_disjoint'))
    return checks


def validate(c):
    if not isinstance(c,dict) or set(c)!={'shape','parts','unit','coordinate_basis','coordinate_evidence_id','scope_confirmed'} or not isinstance(c['parts'],list) or not 2<=len(c['parts'])<=4:
        raise DomainError('多块窗口须2至4个净块、共同单位及XY基准原件和范围确认')
    names=set();count=0;holes=0
    for p in c['parts']:
        if not isinstance(p,dict) or set(p)!={'name','parameters','evidence','measurement_basis','evidence_id','holes'} or not isinstance(p['holes'],list):
            raise DomainError('每个净块仅含名称、外轮廓XY及逐坐标原件、支模说明及原件、内孔列表（无孔填空列表）')
        name=text(p['name'],'窗口净块名称',200)
        if name in names:raise DomainError('窗口净块名称不能重复')
        names.add(name);text(p['measurement_basis'],'本净块实际支模范围说明');text(p['evidence_id'],'本净块支模范围原件',200)
        config=part_config(c,p)
        (perforated.validate if p['holes'] else simple.validate)(config)
        count+=len(p['parameters'])//2+sum(len(h['parameters'])//2 for h in p['holes']);holes+=len(p['holes'])
    if count>48 or holes>8:raise DomainError('多块窗口全部最多48个顶点和8个内孔')
    topology(c)


def separation(a,b):
    return simple.separation(a,b,ring_points=ring_points,method=METHOD)


def trace(parts):
    result=dict(rule_version=VERSION,method='分离窗口净块逐块解析积分及累计',integration_rule=RULE,working_digits=200,output_digits=80,accumulation_digits=80,pi_constant=parts[0][1]['pi_constant'],terms=[])
    def witness(q,i):
        return {**q,**{k:'B'+str(i)+' / '+q[k] for k in ('lower_boundary','upper_boundary') if k in q}}
    for i,m in parts:
        result['terms'] += [{**witness(q,i), 'name':'B'+str(i)+' / '+q['name'],'block_index':i} for q in m['terms']]
        if 'partitions' in m:
            result.setdefault('partitions',[]).extend({**q,'block_index':i,'included':witness(q['included'],i),
                'excluded':[witness(p,i) for p in q['excluded']],'remaining':[witness(p,i) for p in q['remaining']]} for q in m['partitions'])
    return result


def geometry(d,item,tier,original,full_ast,holes,key,dimensions,refs,conversions):
    c=d['plan_range'];parts=[]
    for i,p in enumerate(c['parts'],1):
        config=part_config(c,p);child={**d,'plan_range':config};part_key=key+'.parts.'+str(i)
        refs[part_key+'.basis']=p['evidence_id']
        gross,node,cut,extra,nodes=(perforated.geometry if p['holes'] else simple.geometry)(child,item,tier,original,full_ast,holes,part_key,dimensions,refs,conversions)
        cut_area=sum((Decimal(h['single_area']) for h in cut),Decimal(0));cut_node=op('add',*[h['formula_ast'] for h in cut])
        parts.append(dict(index=i,name=p['name'],measurement_basis=p['measurement_basis'],evidence_id=p['evidence_id'],plan_range=config,
            single_gross_area=str(gross),single_opening_area=str(cut_area),single_net_area=str(gross-cut_area),apertures=cut,**extra,
            formula_asts=dict(**nodes,single_gross_area=node,single_opening_area=cut_node,single_net_area=op('subtract',node,cut_node))))
    def total(field,rows=parts):return sum((Decimal(p[field]) for p in rows),Decimal(0))
    def add(field,rows=parts):return op('add',*[p['formula_asts'][field] for p in rows])
    gross=total('single_gross_area');gross_node=add('single_gross_area');clipped=[]
    for hi,h in enumerate(holes):
        cuts=[p['apertures'][hi] for p in parts]
        if any(q['hole_index']!=h['hole_index'] for q in cuts):raise DomainError('多块窗口实体孔索引不一致')
        q={**cuts[0], 'single_area':str(total('single_area',cuts)), 'formula_ast':op('add',*[v['formula_ast'] for v in cuts]),
           'formula_asts':dict(cuts[0]['formula_asts']), 'intersection_rule':'逐块按窗口净区与实际孔相交后累计，块间空区和窗口内孔不扣；'+RULE,
           'curved_intersection':trace([(p['index'],v['curved_intersection']) for p,v in zip(parts,cuts)])}
        q['formula_asts']['single_area']=q['formula_ast']
        for field in ('window_hole_area','covered_area','window_direct_area'):
            if field in q:q[field]=str(total(field,cuts));q['formula_asts'][field]=add(field,cuts)
        for field in ('polygon_cut_trace','window_hole_curved_intersection'):
            if field in q:q[field]=trace([(p['index'],v[field]) for p,v in zip(parts,cuts)])
        clipped.append(q)
    net_trace=trace([(p['index'],p['polygon_net_trace']) for p in parts]);direct=total('polygon_direct_net_area');zero=None
    if gross>0 and not net_trace['terms'] and clipped:
        index=next((i for i in reversed(range(len(clipped))) if Decimal(clipped[i]['single_area'])>0),None)
        if index is not None:
            others=[h for i,h in enumerate(clipped) if i!=index];amount=gross-total('single_area',others)
            if amount<0:raise DomainError('多块窗口完整覆盖分割余项非正，当前精度不能确认')
            h=clipped[index];node=op('subtract',gross_node,op('add',*[v['formula_ast'] for v in others]))
            h.update(single_area=str(amount),formula_ast=node);h['formula_asts']['single_area']=node
            zero=dict(method='全部净块的全部边界事件分段无剩余接触，按毛面积减其他孔扣的分割恒等式计量，非容差归零',canonical_hole_index=h['hole_index'],net_trace=net_trace)
    extra=dict(multi_polygon_window=True,polygon_window=True,window_parts=parts,window_part_checks=topology(c),
        full_face_area=str(original),window_area=str(total('window_area')),window_outer_area=str(sum((Decimal(p.get('window_outer_area',p['window_area'])) for p in parts),Decimal(0))),
        window_exclusion_area=str(sum((Decimal(p.get('window_exclusion_area','0')) for p in parts),Decimal(0))),window_intersection_rule=RULE,
        polygon_gross_trace=trace([(p['index'],p['polygon_gross_trace']) for p in parts]),polygon_net_trace=net_trace,polygon_direct_net_area=str(direct),
        window_curved_intersection=trace([(p['index'],p['window_curved_intersection']) for p in parts]),
        window_expression_definitions=[v for p in parts for v in p['window_expression_definitions']],plan_frame_hash=parts[0]['plan_frame_hash'])
    nodes=dict(full_face_area=full_ast,window_area=add('window_area'),polygon_direct_net_area=add('polygon_direct_net_area'),
        window_outer_area=op('add',*[p['formula_asts'].get('window_outer_area',p['formula_asts']['window_area']) for p in parts]),
        window_exclusion_area=op('add',*[p['formula_asts'].get('window_exclusion_area',{'constant':'0'}) for p in parts]))
    extra['plan_bounds']={}
    for field in ('x_start','x_end','y_start','y_end'):
        p=(min if field.endswith('start') else max)(parts,key=lambda p:Decimal(p[field]))
        extra[field]=p[field];extra['plan_bounds'][field]=p[field];nodes[field]=p['formula_asts'][field]
    if zero:extra['polygon_zero_contact']=zero
    if d['face'] in ('hole_top','hole_bottom'):extra['hole_end_window']=True
    if d['face']=='step':
        for field in ('window_lower_area','window_upper_area'):extra[field]=str(total(field));nodes[field]=add(field)
        extra['window_upper_curved_intersection']=trace([(p['index'],p['window_upper_curved_intersection']) for p in parts])
    return gross,gross_node,clipped,extra,nodes
