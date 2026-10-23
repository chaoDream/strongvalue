"""Evidence-backed horizontal railing runs along original polygon edges."""
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction
from fastapi import HTTPException
from . import store
from .compound_takeoff import op, text
from .engine import DomainError, precise, quantity_number
from .measurement_units import LENGTH, VERSION as UNIT_VERSION
from .planar_takeoff import linear
from .polygon_takeoff import calculate as polygon_quantity, validate as validate_polygon
from .surface_takeoff import snapshot, check_sources

VERSION = 'polygon-railing-length-1.0'
NOTICE = '仅按已审水平多边形原边的设计水平长度计栏杆，包含闭合末边及孔边候选；每边须明确排除、整边或局部区间。S从所列原边起点沿边增加，跨边范围须分别录入，区间不得重叠。来源轮廓不证明安装位置、栏杆高度或实际做法，须依据原件确认。重复数量只继承一次，不推断坡段三维长、立柱数量、扶手多道、弯头、搭接、质量或扣减规范。'
ROUNDING = 'Decimal 80位；原边平方根及逐区间长度不单独修约，累计后仅乘一次来源重复数量，总长HALF_UP按明确位数修约；局部端距以精确有理数平方比较原边长度上限，不按显示长度容差放行'


def source_component(p, cid):
    from .main import find
    s = find(p, 'components', cid)
    if s['kind'] != 'polygon_region' or s['state'] != 'approved':
        raise HTTPException(409, '栏杆边线须选择当前已审定的水平设计多边形')
    validate_polygon(p, s, saved=True)
    return s


@precise
def boundary(source):
    r = polygon_quantity(source); rows = []
    for ring in r['polygon_rings']:
        points = ring['points']; ri = ring['index']
        for i, a in enumerate(points):
            j = (i+1)%len(points); b = points[j]
            start, end = a['normalized_parameters'], b['normalized_parameters']
            delta = [Decimal(end[k])-Decimal(start[k]) for k in ('x','y')]
            square = sum((Fraction(v)**2 for v in delta), Fraction(0))
            length = (delta[0]**2+delta[1]**2).sqrt()
            def coordinate(p, index, k):
                return op('multiply', {'dimension':f'{ri}.{index}.{k}'}, {'constant':LENGTH[p['unit']]})
            diffs = [op('subtract',coordinate(b,j+1,k),coordinate(a,i+1,k)) for k in ('x','y')]
            identity = {'ring_index':ri,'edge_index':i+1,'from_xy':start,'to_xy':end,'role':ring['role'],'rule':VERSION}
            rows.append({**identity,'edge_id':store.digest(identity),'index':len(rows)+1,'ring_name':ring['name'],
                         'from_index':i+1,'to_index':j+1,'length':str(length),
                         'squared_length':{'numerator':str(square.numerator),'denominator':str(square.denominator)},
                         'formula_ast':op('sqrt',op('add',*[op('multiply',d,d) for d in diffs]))})
    return {**r, 'railing_edges':rows}


def candidates(p,cid):
    s=source_component(p,cid);r=boundary(s)
    return {'source_component_id':s['id'],'source_component_hash':store.digest(snapshot(s)),
            'source_name':s['name'],'source_revision':s['revision'],'multiplicity':s['multiplicity'],
            'railing_edges':r['railing_edges'],'notice':NOTICE}


@precise
def calculate(comp,source=None):
    s=source if source is not None else comp['source_geometry']
    if comp.get('parameters')!={} or comp.get('quantity_type')!='length' or comp.get('scope_confirmed') is not True:
        raise DomainError('栏杆边线须为空顶层参数、长度类型，并确认水平计长、安装范围及重复数量口径')
    if comp.get('source_component_id')!=s['id'] or comp.get('source_component_hash')!=store.digest(snapshot(s)):
        raise HTTPException(409,'来源多边形已修订，请重新读取边线并核定范围')
    if 'multiplicity' in comp:raise DomainError('栏杆重复数量继承来源，不得另填或再次乘数量')
    for key,label in [('name','栏杆名称'),('measurement_basis','栏杆计量依据'),('installation_basis','安装位置与做法依据'),('material','栏杆材料做法')]:
        text(comp.get(key),label,200 if key=='name' else 2000)
    digits=comp.get('rounding_digits')
    if type(digits) is not int or not 0<=digits<=12:raise DomainError('栏杆总长修约位数须明确为0至12的整数')
    r=boundary(s);edges=r['railing_edges'];decisions=comp.get('edge_decisions')
    if not isinstance(decisions,list) or len(decisions)!=len(edges):raise DomainError('须为全部外轮廓及孔边明确计量决定，不能遗漏')
    by_id={}
    for d in decisions:
        if not isinstance(d,dict) or set(d)!={'edge_id','mode','ranges','basis','evidence_id'} or not isinstance(d['edge_id'],str) or not isinstance(d['mode'],str) or d['mode'] not in ('exclude','full','ranges'):
            raise DomainError('逐边须明确边编号、排除/整边/局部区间、区间列表、说明及原始依据')
        if d['edge_id'] in by_id:raise DomainError('原边计量决定不能重复')
        text(d['basis'],'逐边计量说明');text(d['evidence_id'],'逐边原始依据',200)
        if not isinstance(d['ranges'],list) or (d['mode']=='ranges' and not 1<=len(d['ranges'])<=20) or (d['mode']!='ranges' and d['ranges']):
            raise DomainError('局部计量每边须有1至20个区间，排除及整边须为空区间列表')
        by_id[d['edge_id']]=d
    if set(by_id)!={e['edge_id'] for e in edges}:raise DomainError('存在未知或过期原边，请重新读取来源')
    if sum(len(d['ranges']) for d in decisions)>1000:raise DomainError('栏杆局部区间超过1000项，请拆分计量范围')
    dimensions=dict(r['dimensions']);refs=dict(r['source_evidence']);conversions=list(r['unit_conversions'])
    for key in ('basis','installation','material','rounding'):
        refs['railing.'+key]=text(comp.get(key+'_evidence_id'),'栏杆原始依据',200)
    rows=[];nodes=[];total=Decimal(0);available=Decimal(0)
    for e in edges:
        d=by_id[e['edge_id']];key='railing.'+str(e['index']);refs[key+'.basis']=d['evidence_id']
        available+=Decimal(e['length']);parts=[]
        if d['mode']=='full':
            ast=e['formula_ast'];value=Decimal(e['length'])
        elif d['mode']=='exclude':ast={'constant':'0'};value=Decimal(0)
        else:
            square=e['squared_length'];limit=Fraction(int(square['numerator']),int(square['denominator']))
            for i,part in enumerate(d['ranges'],1):
                if not isinstance(part,dict) or set(part)!={'from','to','unit','from_evidence_id','to_evidence_id'}:
                    raise DomainError('局部区间须明确起终距、长度单位及两端原始依据')
                a=linear(part['from'],part['unit']);b=linear(part['to'],part['unit'])
                if not 0<=a<b or Fraction(b)**2>limit:raise DomainError('局部区间须满足0≤起距<终距≤原边实际长度，不得跨边或越界')
                expr={}
                for k,v in (('from',a),('to',b)):
                    dk=key+'.'+str(i)+'.'+k;dimensions[dk]=str(part[k]);refs[dk]=text(part[k+'_evidence_id'],'区间端距原始依据',200)
                    expr[k]=op('multiply',{'dimension':dk},{'constant':LENGTH[part['unit']]})
                    conversions.append({'parameter':dk,'original_value':str(part[k]),'source_unit':part['unit'],'factor':LENGTH[part['unit']],
                                        'canonical_value':str(v),'canonical_unit':'m','rule_version':UNIT_VERSION})
                parts.append({**part,'index':i,'s_from':str(a),'s_to':str(b),'length':str(b-a),
                              'formula_ast':op('subtract',expr['to'],expr['from'])})
            parts.sort(key=lambda v:Decimal(v['s_from']))
            if any(Decimal(a['s_to'])>Decimal(b['s_from']) for a,b in zip(parts,parts[1:])):raise DomainError('同一原边局部区间不能重叠或重复计长')
            value=sum((Decimal(v['length']) for v in parts),Decimal(0));ast=op('add',*[v['formula_ast'] for v in parts])
        total+=value;nodes.append(ast)
        rows.append({**e,**d,'normalized_ranges':parts,'selected_length':str(value),'selected_formula_ast':ast})
    count=Decimal(s['multiplicity']);gross=quantity_number(total*count)
    quantity=quantity_number(gross.quantize(Decimal(1).scaleb(-digits),rounding=ROUND_HALF_UP))
    return {'quantity':str(quantity),'unit':'m','gross':str(gross),'deduction':'0','parameters':{},
            'formula':'Σ核定原边水平范围长度 × '+str(count)+'；仅总长修约'+str(digits)+'位',
            'formula_ast':{'op':'round','args':[op('multiply',op('add',*nodes),{'dimension':'multiplicity'})],'digits':digits,'mode':'HALF_UP'},
            'dimensions':dimensions,'source_evidence':refs,'unit_conversions':conversions,'coordinate_basis':s['coordinate_basis'],
            'source_geometry':snapshot(s),'source_component_id':s['id'],'source_component_hash':comp['source_component_hash'],
            'material':comp['material'],'installation_basis':comp['installation_basis'],'railing_edges':rows,
            'railing_summary':{'source_name':s['name'],'source_revision':s['revision'],'edge_count':len(rows),'available_length':str(available),
                               'single_length':str(total),'multiplicity':str(count),'unrounded_quantity':str(gross),
                               'rounding_digits':digits,'rounding_adjustment':str(quantity-gross)},
            'rule_version':VERSION,'rounding_rule':ROUNDING,'notice':NOTICE}


def validate(p,comp,saved=False):
    s=source_component(p,comp.get('source_component_id'));result=calculate(comp,s)
    sources=check_sources(p,result['source_evidence'])
    stamp=store.digest({'source_component_hash':comp['source_component_hash'],'sources':sources})
    if saved and (comp.get('source_stamp')!=stamp or comp.get('evidence')!=result['source_evidence'] or comp.get('preview')!=result or comp.get('source_geometry')!=snapshot(s)):
        raise HTTPException(409,'栏杆来源、原件、范围或计算记录已变化，请修订后重新复核')
    return result,stamp
