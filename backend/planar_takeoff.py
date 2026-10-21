"""Exact union and difference of axis-aligned, evidence-backed design rectangles."""
from decimal import Decimal, InvalidOperation
from fastapi import HTTPException
from . import store
from .engine import DomainError, number, precise, quantity_number
from .measurement_units import LENGTH, VERSION as UNIT_VERSION
from .compound_takeoff import op, text

VERSION = 'rectilinear-1.0'
FIELDS = ['x','y','length','width']
NOTICE = '仅计算同一设计坐标平面的轴向矩形并集及扣减；示意图不能测量，不能代替专业计量边界、空间模型或施工图复核。两两相交面积用于定位，不可相加代替并集扣重。'


def linear(value, unit, positive=False):
    if not isinstance(unit,str) or unit not in LENGTH: raise DomainError('区域坐标和尺寸仅支持明确的长度单位')
    try: raw=Decimal(str(value))
    except InvalidOperation: raise DomainError('坐标或尺寸须为有效十进制数') from None
    number(raw.copy_abs(), positive)
    if positive and raw <= 0: raise DomainError('区域长、宽及板厚须大于零')
    converted=(raw*Decimal(LENGTH[unit])).normalize()
    try: number(converted.copy_abs(), positive)
    except DomainError: raise DomainError('区域单位换算后超出坐标或18位小数精度范围') from None
    return converted


def merged(intervals):
    """Each bound retains its exact generating expression for the calculation AST."""
    result=[]
    for start,end,left,right in sorted(intervals,key=lambda x:(x[0],x[1])):
        if not result or start > result[-1][1]: result.append([start,end,left,right])
        elif end > result[-1][1]: result[-1][1],result[-1][3]=end,right
    return result


def length_ast(intervals):
    return op('add',*[op('subtract',x[3],x[2]) for x in intervals]) if intervals else {'constant':'0'}


@precise
def calculate(comp):
    regions=comp.get('regions');mode=comp.get('quantity_type')
    if comp.get('parameters')!={} or mode not in ('area','volume') or not isinstance(regions,list) or not 1<=len(regions)<=30:
        raise DomainError('平面区域须指定面积/等厚体积，顶层参数为空，区域数量为1至30项')
    for key,label in [('name','构件名称'),('coordinate_basis','共同坐标与方向依据'),('measurement_basis','计量与扣减依据')]:text(comp.get(key),label)
    refs={key:text(comp.get(key+'_evidence_id'),'原始依据证据',200) for key in ['coordinate','basis','multiplicity']}
    count=number(comp.get('multiplicity'),True)
    if count!=count.to_integral_value() or count>1000000:raise DomainError('整组重复数量须为1至1000000的整数')
    dimensions={'multiplicity':str(comp['multiplicity'])};converted=[];shapes=[];names=set();boundaries={}
    def node(key,unit): return op('multiply',{'dimension':key},{'constant':LENGTH[unit]})
    for i,r in enumerate(regions):
        if not isinstance(r,dict) or set(r)!={'name','operation','parameters','unit','evidence','measurement_basis'}:
            raise DomainError('区域须包含名称、加扣、坐标尺寸、原始单位、逐参数证据和计量说明')
        name=text(r['name'],'区域名称',200)
        if name in names:raise DomainError('区域名称不能重复')
        names.add(name)
        if r['operation'] not in ('add','subtract') or not isinstance(r['parameters'],dict) or set(r['parameters'])!=set(FIELDS):raise DomainError('区域只支持x、y、长、宽及加项/扣减')
        if not isinstance(r['evidence'],dict) or set(r['evidence'])!=set(FIELDS)|{'measurement_basis'}:raise DomainError('每个区域的坐标、长宽和计量说明须分别关联原始依据')
        text(r['measurement_basis'],'区域计量说明')
        prefix=str(i+1)+'.';values={k:linear(r['parameters'][k],r['unit'],k in ('length','width')) for k in FIELDS}
        for k in FIELDS:
            dimensions[prefix+k]=str(r['parameters'][k])
            converted.append({'parameter':prefix+k,'original_value':str(r['parameters'][k]),'source_unit':r['unit'],'factor':LENGTH[r['unit']],
                              'canonical_value':format(values[k],'f'),'canonical_unit':'m','rule_version':UNIT_VERSION})
        for k,eid in r['evidence'].items():refs[prefix+k]=text(eid,'区域原始证据',200)
        x,y=values['x'],values['y'];right=x+values['length'];top=y+values['width']
        xn,yn=node(prefix+'x',r['unit']),node(prefix+'y',r['unit'])
        rn,tn=op('add',xn,node(prefix+'length',r['unit'])),op('add',yn,node(prefix+'width',r['unit']))
        boundaries.setdefault(x,xn);boundaries.setdefault(right,rn)
        shapes.append({'input':r,'index':i+1,'x':x,'y':y,'right':right,'top':top,'yn':yn,'tn':tn,'area':values['length']*values['width'],
                       'normalized':{k:format(v,'f') for k,v in values.items()}})
    if not any(s['input']['operation']=='add' for s in shapes):raise DomainError('至少需要一个加项区域')
    thickness=Decimal(1);thickness_node={'constant':'1'}
    if mode=='volume':
        thickness=linear(comp.get('thickness'),comp.get('thickness_unit'),True)
        dimensions['thickness']=str(comp['thickness']);refs['thickness']=text(comp.get('thickness_evidence_id'),'板厚原始依据',200)
        thickness_node=node('thickness',comp['thickness_unit'])
        converted.append({'parameter':'thickness','original_value':str(comp['thickness']),'source_unit':comp['thickness_unit'],'factor':LENGTH[comp['thickness_unit']],
                          'canonical_value':format(thickness,'f'),'canonical_unit':'m','rule_version':UNIT_VERSION})
    elif any(comp.get(k) not in (None,'') for k in ['thickness','thickness_unit','thickness_evidence_id']):raise DomainError('面积模式不能携带板厚，请清空或选择等厚体积')
    xs=sorted(boundaries);gross=Decimal(0);deducted=Decimal(0);strips=[];area_nodes=[]
    for left,right in zip(xs,xs[1:]):
        active=[s for s in shapes if s['x']<=left and s['right']>=right]
        intervals={kind:merged([(s['y'],s['top'],s['yn'],s['tn']) for s in active if s['input']['operation']==kind]) for kind in ['add','subtract']}
        add,sub=intervals['add'],intervals['subtract']
        if any(not any(a[0]<=d[0] and d[1]<=a[1] for a in add) for d in sub):raise DomainError('扣减区域超出加项并集，请核对共同设计坐标和计量边界')
        if not add:continue
        a=sum((x[1]-x[0] for x in add),Decimal(0));d=sum((x[1]-x[0] for x in sub),Decimal(0));width=right-left
        gross+=width*a;deducted+=width*d
        area_nodes.append(op('multiply',op('subtract',boundaries[right],boundaries[left]),op('subtract',length_ast(add),length_ast(sub))))
        strips.append({'x_from':str(left),'x_to':str(right),'width':str(width),'add_intervals':[[str(x[0]),str(x[1])] for x in add],
                       'deduct_intervals':[[str(x[0]),str(x[1])] for x in sub],'add_y_length':str(a),'deduct_y_length':str(d),'net_area':str(width*(a-d))})
    quantity_number(gross);quantity_number(deducted)
    naive_add=sum((s['area'] for s in shapes if s['input']['operation']=='add'),Decimal(0));naive_sub=sum((s['area'] for s in shapes if s['input']['operation']=='subtract'),Decimal(0))
    intersections=[]
    for i,a in enumerate(shapes):
        for b in shapes[i+1:]:
            if a['input']['operation']!=b['input']['operation']:continue
            width=min(a['right'],b['right'])-max(a['x'],b['x']);height=min(a['top'],b['top'])-max(a['y'],b['y'])
            if width>0 and height>0:intersections.append({'operation':a['input']['operation'],'left':a['input']['name'],'right':b['input']['name'],'area':str(width*height)})
    value=quantity_number((gross-deducted)*thickness*count);gross_value=quantity_number(gross*thickness*count);deduct_value=quantity_number(deducted*thickness*count)
    return {'quantity':str(value),'gross':str(gross_value),'deduction':str(deduct_value),'unit':'m²' if mode=='area' else 'm³','parameters':{},
            'formula':'(加项并集面积 '+str(gross)+' − 扣减并集面积 '+str(deducted)+')'+(' × 厚度 '+str(thickness) if mode=='volume' else '')+' × '+str(count),
            'formula_ast':op('multiply',op('add',*area_nodes),thickness_node,{'dimension':'multiplicity'}),'dimensions':dimensions,'source_evidence':refs,
            'coordinate_basis':comp['coordinate_basis'],'planar_regions':[{**s['input'],'index':s['index'],'normalized_parameters':s['normalized'],'rectangle_area':str(s['area'])} for s in shapes],
            'planar_strips':strips,'planar_intersections':intersections,'unit_conversions':converted,
            'planar_summary':{'naive_add_area':str(naive_add),'add_union_area':str(gross),'add_overlap_area':str(naive_add-gross),
                              'naive_deduct_area':str(naive_sub),'deduct_union_area':str(deducted),'deduct_overlap_area':str(naive_sub-deducted),
                              'net_area':str(gross-deducted),'thickness':str(thickness) if mode=='volume' else None,'multiplicity':str(count),'quantity_type':mode},
            'rule_version':VERSION,'rounding_rule':'Decimal 80位计算精度；坐标、面积与体积不另行修约','notice':NOTICE}


def validate(p,comp,saved=False):
    from .main import require_evidence,find
    from .task_execution import original_bytes
    result=calculate(comp);sources={};checked=set()
    for key,eid in result['source_evidence'].items():
        ev=require_evidence(p,eid);doc=find(p,'documents',ev['document_id'])
        if ev['document_hash']!=doc['hash']:raise HTTPException(409,'平面区域原始依据与文件校验值不一致')
        if doc['hash'] not in checked:original_bytes(doc);checked.add(doc['hash'])
        sources[key]={'evidence':ev,'document_hash':doc['hash']}
    stamp=store.digest(sources)
    if saved and (comp.get('source_stamp')!=stamp or comp.get('evidence')!=result['source_evidence'] or comp.get('preview')!=result):
        raise HTTPException(409,'平面区域来源或计算内容已变化，请修订后重新复核')
    return result,stamp
