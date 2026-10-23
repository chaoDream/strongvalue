"""Bounded ASCII DXF model-space WCS paths; never expands blocks or measures pixels."""
import re
from decimal import Decimal
from .engine import DomainError
from .route_takeoff import calculate

VERSION='dxf-wcs-path-1.0'
UNITS={4:'mm',5:'cm',6:'m'}


def one(groups,code,default=None):
    values=[v for k,v in groups if k==code]
    if len(values)>1:raise DomainError('DXF关键组码重复：'+str(code))
    return values[0] if values else default


def integer(groups,code,default='0'):
    value=one(groups,code,default)
    if not re.fullmatch(r'[+-]?\d{1,9}',value):raise DomainError('DXF整数标记无效：'+str(code))
    return int(value)


def common(groups):
    if integer(groups,67)!=0 or one(groups,410,'Model').casefold()!='model':raise DomainError('图纸空间或非Model布局不作为设计坐标')
    if integer(groups,60)!=0:raise DomainError('不可见对象须人工核查，不自动采用')
    for k,default in [(39,'0'),(210,'0'),(220,'0'),(230,'1')]:
        try:value=Decimal(one(groups,k,default))
        except Exception:raise DomainError('DXF厚度或法向数值无效') from None
        if not value.is_finite() or value!=Decimal(default):raise DomainError('非零厚度或非默认挤出方向不在本版坐标读取范围')


def point(groups,offset=0):
    result={k:one(groups,code+offset) for k,code in [('x',10),('y',20),('z',30)]}
    if any(v is None for v in result.values()):raise DomainError('XYZ坐标须明确完整；不从缺失高程推定设计Z值')
    return result


def route_body(row,eid,**metadata):
    return {'name':metadata.get('name','DXF坐标候选'),'kind':'design_route','parameters':{},'quantity_type':'length',
            'route_points':[{'name':'P'+str(i+1),'parameters':p,'unit':row['unit'],'evidence':{k:eid for k in ('x','y','z')}} for i,p in enumerate(row['points'])],
            'coordinate_basis':metadata.get('coordinate_basis','WCS坐标候选，尚未确认设计用途'),'coordinate_evidence_id':metadata.get('basis_evidence_id',eid),
            'measurement_basis':metadata.get('measurement_basis','候选只读计算，不形成工程量'),'basis_evidence_id':metadata.get('basis_evidence_id',eid),
            'multiplicity':metadata.get('multiplicity','1'),'multiplicity_evidence_id':metadata.get('basis_evidence_id',eid),
            'rounding_digits':metadata.get('rounding_digits',6),'rounding_evidence_id':metadata.get('basis_evidence_id',eid)}


def parse(raw):
    if raw.startswith(b'AutoCAD Binary DXF'):raise DomainError('本入口仅支持ASCII DXF；二进制DXF及DWG须授权解析器')
    try:source=raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        try:source=raw.decode('gb18030')
        except UnicodeDecodeError:raise DomainError('DXF文本编码不支持') from None
    lines=source.splitlines()
    if len(lines)%2 or len(lines)>500000:raise DomainError('DXF代码对不完整或超过250000对上限')
    pairs=[]
    for i in range(0,len(lines),2):
        if not re.fullmatch(r'\d{1,4}',lines[i].strip()):raise DomainError('DXF组码须为整数')
        pairs.append((int(lines[i].strip()),lines[i+1].strip()))
    sections={};i=0;eof=False
    while i<len(pairs):
        if pairs[i]==(0,'EOF'):
            if i!=len(pairs)-1:raise DomainError('DXF结束标记后存在多余数据')
            eof=True;break
        if pairs[i][0]==999:i+=1;continue
        if pairs[i]!=(0,'SECTION') or i+1>=len(pairs) or pairs[i+1][0]!=2:raise DomainError('DXF段结构无效')
        name=pairs[i+1][1]
        if name in sections:raise DomainError('DXF段重复，不能判定唯一来源')
        i+=2;content=[]
        while i<len(pairs) and pairs[i]!=(0,'ENDSEC'):
            if pairs[i] in [(0,'SECTION'),(0,'EOF')]:raise DomainError('DXF段未完整结束')
            content.append(pairs[i]);i+=1
        if i==len(pairs):raise DomainError('DXF缺少段结束标记')
        sections[name]=content;i+=1
    if not eof or 'ENTITIES' not in sections:raise DomainError('DXF须有完整ENTITIES段及EOF')
    header=sections.get('HEADER',[]);units=[]
    for j,(k,v) in enumerate(header):
        if (k,v)==(9,'$INSUNITS'):
            if j+1>=len(header) or header[j+1][0]!=70:raise DomainError('DXF单位标记无效')
            units.append(integer([header[j+1]],70))
    if len(units)>1:raise DomainError('DXF绘图单位声明重复')
    unit=UNITS.get(units[0]) if units else None
    records=[];current=None
    for n,(code,value) in enumerate(sections['ENTITIES']):
        if code==0:
            if current:records.append(current)
            current={'kind':value,'groups':[],'ordinal':len(records)+1}
        elif current is None:
            if code!=999:raise DomainError('DXF实体头缺失')
        else:current['groups'].append((code,value))
    if current:records.append(current)
    if len(records)>20000:raise DomainError('DXF模型空间实体超过20000条，本版不截断读取')
    handles=[one(r['groups'],5) for r in records if one(r['groups'],5)]
    if len(handles)!=len(set(h.upper() for h in handles)):raise DomainError('DXF实体句柄重复，不能定位唯一原对象')
    result=[];ignored={};index=0
    while index<len(records):
        r=records[index];g=r['groups'];kind=r['kind'];index+=1
        if kind not in ('LINE','POLYLINE','LWPOLYLINE','INSERT','ARC','SPLINE','CIRCLE','ELLIPSE'):
            ignored[kind]=ignored.get(kind,0)+1;continue
        row={'ordinal':r['ordinal'],'handle':one(g,5,''),'layer':one(g,8,''),'kind':kind,'unit':unit,'points':[],'issue':''}
        vertices=[]
        if kind=='POLYLINE':
            while index<len(records) and records[index]['kind']=='VERTEX':vertices.append(records[index]);index+=1
            ended=index<len(records) and records[index]['kind']=='SEQEND'
            if ended:index+=1
        try:
            common(g)
            if kind not in ('LINE','POLYLINE'):raise DomainError('本版不读取此类对象，不展开块、不将曲线或二维OCS多段线当作三维折线')
            if not unit:raise DomainError('INSUNITS未明确为mm、cm或m；不能猜测绘图单位')
            if not re.fullmatch(r'[0-9A-Fa-f]{1,32}',row['handle']):raise DomainError('缺少可定位的有效DXF句柄')
            if kind=='LINE':row['points']=[point(g),point(g,1)]
            else:
                if integer(g,70) not in (8,136) or not ended:raise DomainError('只支持完整、开放、无拟合的3D POLYLINE；闭合、网格和拟合对象不采用')
                if not 2<=len(vertices)<=101:raise DomainError('3D折线须有2至101个明确顶点，不截断坐标')
                for vertex in vertices:
                    vg=vertex['groups'];common(vg)
                    if integer(vg,70)!=32 or one(vg,8,row['layer'])!=row['layer']:raise DomainError('三维顶点类型或图层与主对象不一致')
                    try:bulge=Decimal(one(vg,42,'0'))
                    except Exception:raise DomainError('顶点曲率无效') from None
                    if not bulge.is_finite() or bulge!=0:raise DomainError('带曲率顶点不作为直线段计量')
                    row['points'].append(point(vg))
            result_calculated=calculate(route_body(row,'candidate-preview'))
            row['single_length_m']=result_calculated['route_summary']['single_length']
        except DomainError as e:row['issue']=str(e)
        row['eligible']=not row['issue'];result.append(row)
    return {'objects':result,'unit':unit,'insunits':units[0] if units else None,'ignored':ignored,
            'notice':'仅读取ENTITIES中模型空间的显式WCS直线及开放3D折线。INSUNITS只作单位声明，须以已知长度校核；不推断管线用途、缺失高程、块变换或施工附加量。'}
