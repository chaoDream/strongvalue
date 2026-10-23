"""Exact rational intersection areas of horizontal polygon rings and XY windows."""
from decimal import Decimal, localcontext
from fractions import Fraction
from . import store
from .compound_takeoff import op, text
from .engine import DomainError, precise, quantity_number
from .measurement_units import LENGTH, VERSION as UNIT_VERSION
from .planar_takeoff import linear

VERSION = 'polygon-boundary-1.2'
NOTICE = '上下净面可按共同设计X/Y分割线划分窗口，实际计量为窗口内外轮廓扣孔后的净区域，不是整个矩形窗口；凹形窗口可包含多块互不连通净区，合为同一核定项。外侧及孔内侧仍用原边局部S/Z分区。孔洞、区域外及仅接触边界的零面积窗口不生成计量面。不推断施工做法、搭接、翻边或空间接触。'
ROUNDING = 'XY裁剪及逐孔扣减用精确有理数计算，分区净面积以派生分子/分母AST保存，按Decimal 80位转换显示；各子面不另行修约。精确有理数面积合计须等于原净面，记录80位子面合计与原面精度差，不调平子面；计入面合计后仅乘一次来源重复数量。侧面分区沿用80位边长及S/Z算法'
CLIP_RULE = 'xy-window-rational-1.0'


def editor_faces(faces):
    result=[]
    for f in faces:
        if f['geometry_type']=='polygon_cap':
            pts=[p for r in f['boundary_rings'] for p in r['points']]
            bounds={key:format(fn(Decimal(p[a]) for p in pts),'f') for key,a,fn in [('u_from','x',min),('u_to','x',max),('v_from','y',min),('v_to','y',max)]}
            result.append({**f,**bounds,'u_axis':'x','v_axis':'y'})
        else:result.append({**f,'u_axis':'s','v_axis':'z','u_from':'0','u_to':f['edge_length'],'v_from':'0','v_to':f['height']})
    return result


def checked(value):
    if max(value.numerator.bit_length(), value.denominator.bit_length()) > 8192:
        raise DomainError('多边形窗口交点有理数复杂度超过8192位，请拆分计量范围或核对原始坐标精度')
    return value


def decimal(value):
    return Decimal(value.numerator)/Decimal(value.denominator)


def rational(value):
    return {'numerator':str(value.numerator),'denominator':str(value.denominator)}


def clip(points, axis, value, greater):
    """Keep the directed boundary chain in a half-plane; bridges cancel in area."""
    if not points:return []
    result=[];a=points[-1];a_in=a[axis]>=value if greater else a[axis]<=value
    for b in points:
        b_in=b[axis]>=value if greater else b[axis]<=value
        if a_in!=b_in:
            ratio=checked((value-a[axis])/(b[axis]-a[axis]))
            q=tuple(value if k==axis else checked(a[k]+ratio*(b[k]-a[k])) for k in (0,1))
            if not result or result[-1]!=q:result.append(q)
        if b_in and (not result or result[-1]!=b):result.append(b)
        a,a_in=b,b_in
    if len(result)>1 and result[0]==result[-1]:result.pop()
    return result


def area(points):
    if len(points)<3:return Fraction(0)
    origin=points[0];total=Fraction(0)
    for a,b in zip(points,points[1:]+points[:1]):
        total=checked(total+checked((a[0]-origin[0])*(b[1]-origin[1])-(a[1]-origin[1])*(b[0]-origin[0])))
    return checked(abs(total)/2)


def window_area(points, box):
    for axis,value,greater in [(0,box[0],True),(0,box[1],False),(1,box[2],True),(1,box[3],False)]:
        points=clip(points,axis,value,greater)
    return area(points)


@precise
def partition(source, groups, result):
    from .polygon_side_partitions import partition as side_partition
    originals={f['face_id']:f for f in result['faces']}
    cap_groups=[];side_groups=[];seen=set()
    for g in groups:
        if not isinstance(g,dict) or set(g)!={'face_id','u_cuts','v_cuts'} or not isinstance(g['face_id'],str):
            raise DomainError('分区须明确原始面编号及两轴分割坐标')
        fid=g['face_id']
        if fid not in originals or fid in seen:raise DomainError('分区原始面不存在、已过期或重复')
        seen.add(fid)
        (cap_groups if originals[fid]['geometry_type']=='polygon_cap' else side_groups).append(g)
    if not cap_groups:
        return side_partition(source,groups,result)
    if side_groups:result=side_partition(source,side_groups,result)
    records=list(result.get('surface_subdivisions',[]));configs={}
    for gi,g in enumerate(cap_groups,1):
        f=originals[g['face_id']];rings=f['boundary_rings'];points=[p for r in rings for p in r['points']];bounds={}
        for axis,label in [('u','x'),('v','y')]:
            cuts=g[axis+'_cuts']
            if not isinstance(cuts,list) or len(cuts)>8:raise DomainError('每个上下净面每轴最多8条设计分割线')
            values={Decimal(p[label]) for p in points};low,high=min(values),max(values);values={low,high}
            for ci,c in enumerate(cuts,1):
                if not isinstance(c,dict) or set(c)!={'value','unit','evidence_id'}:raise DomainError('分割坐标须保留原值、长度单位和原始依据')
                value=linear(c['value'],c['unit'])
                if not low<value<high or value in values:raise DomainError('设计X/Y分割线须严格在原净面坐标范围内，不能重复、落在范围边界或越界')
                eid=text(c['evidence_id'],'净面分割原始依据',200);key=f'cap_cut.{gi}.{axis}.{ci}'
                result['dimensions'][key]=str(c['value']);result['source_evidence'][key]=eid
                conversion={'parameter':key,'original_value':str(c['value']),'source_unit':c['unit'],'factor':LENGTH[c['unit']],
                            'canonical_value':format(value,'f'),'canonical_unit':'m','rule_version':UNIT_VERSION}
                result['unit_conversions'].append(conversion)
                records.append({**conversion,'parent_face_id':g['face_id'],'parent_index':f['index'],'axis':axis,
                                'coordinate_axis':label,'coordinate_system':'design_xy','evidence_id':eid})
                values.add(value)
            bounds[axis]=sorted(values)
        if not g['u_cuts'] and not g['v_cuts']:raise DomainError('分区至少需要一条分割线；不分区请移除配置')
        configs[g['face_id']]=bounds
    if len(result['faces'])+sum((len(b['u'])-1)*(len(b['v'])-1)-1 for b in configs.values())>1000:
        raise DomainError('分区窗口与计量面合计超过1000项，请拆分计量范围')
    faces=[];checks=[]
    for f in result['faces']:
        if f['face_id'] not in configs:faces.append(f);continue
        b=configs[f['face_id']];rings=[[(Fraction(p['x']),Fraction(p['y'])) for p in r['points']] for r in f['boundary_rings']]
        whole=checked(area(rings[0])-sum((area(r) for r in rings[1:]),Fraction(0)))
        exact_sum=Fraction(0);children=[];zero=0
        for u0,u1 in zip(b['u'],b['u'][1:]):
            for v0,v1 in zip(b['v'],b['v'][1:]):
                box=[Fraction(x) for x in (u0,u1,v0,v1)];areas=[window_area(r,box) for r in rings]
                net=checked(areas[0]-sum(areas[1:],Fraction(0)));exact_sum=checked(exact_sum+net)
                if net<0:raise DomainError('窗口扣孔面积超过外轮廓裁剪面积，请核对来源及分割范围')
                if not net:zero+=1;continue
                index=len(children)+1;window={k:format(x,'f') for k,x in zip(('u_from','u_to','v_from','v_to'),(u0,u1,v0,v1))}
                frac=rational(net);value=quantity_number(decimal(net))
                children.append({**f,**window,'face_id':store.digest({'parent':f['face_id'],'window':{k:str(Fraction(v)) for k,v in window.items()},'rule':CLIP_RULE}),
                                 'parent_face_id':f['face_id'],'parent_index':f['index'],'sub_index':index,
                                 'label':f['label']+f' · 窗口净区{index}',
                                 'position':f'共同设计X {window["u_from"]}→{window["u_to"]}m；Y {window["v_from"]}→{window["v_to"]}m；局部Z={f["local_z"]}m',
                                 'calculation':f'窗口内外面积 {decimal(areas[0])} − 逐孔面积 {decimal(sum(areas[1:],Fraction(0)))}（按精确有理数扣减）',
                                 'area':str(value),'formula_ast':op('divide',{'constant':frac['numerator']},{'constant':frac['denominator']}),
                                 'cap_clip_rule':CLIP_RULE,'cap_rational_area':frac,
                                 'cap_clip_rings':[{'ring_index':i+1,'role':'outer' if i==0 else 'hole','area':str(decimal(a)),**rational(a)} for i,a in enumerate(areas)]})
        if exact_sum!=whole:raise DomainError('上下净面窗口的精确净面积不能完整闭合，请核对来源')
        with localcontext() as ctx:
            ctx.prec=200;display_sum=sum((Decimal(c['area']) for c in children),Decimal(0))
            checks.append({'parent_face_id':f['face_id'],'parent_index':f['index'],'child_count':len(children),'zero_window_count':zero,
                           'parent_area':f['area'],'children_area':str(display_sum),'area_difference':str(display_sum-Decimal(f['area'])),
                           'exact_rational_tiling':True,**rational(whole)})
        faces.extend(children)
    totals={k:Decimal(0) for k in result['normal_totals']}
    for f in faces:totals[f['normal']]+=Decimal(f['area'])
    result.update(faces=[{**f,'index':i+1} for i,f in enumerate(faces)],surface_subdivisions=records,surface_cap_partition_checks=checks,
                  total_area=str(sum((Decimal(f['area']) for f in faces),Decimal(0))),normal_totals={k:str(v) for k,v in totals.items()})
    return result
