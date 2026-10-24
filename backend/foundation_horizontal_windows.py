"""Evidence-defined XY windows on whole exterior horizontal foundation faces."""
from decimal import Decimal
from fastapi import HTTPException
from . import store
from .compound_takeoff import op,text
from .engine import DomainError
from .measurement_units import LENGTH,VERSION as UNIT_VERSION
from .planar_takeoff import linear
from .foundation_voids import area
from .foundation_void_formwork import dim
from .foundation_hole_end_formwork import single_section,details
from .foundation_formwork import layers
from . import foundation_step_formwork as steps

VERSION='foundation-horizontal-formwork-1.4'
FIELDS=('x_start','x_end','y_start','y_end')
NOTICE='整体最顶面、最底面可选整面或有据XY矩形窗口。窗口以来源基础中心为原点，X/Y方向与已审孔位共用且不随俯视/仰视翻转；同来源同基础项全部窗口须相同基准说明与原件。窗口可越过外轮廓，仅计窗口与真实端层截面的交集；实际孔只扣到达该端面且落在窗口内的部分，不受体积扣孔阈值影响。矩形逐轴裁切，圆矩形局部交集沿用200位Decimal解析过程后保留80位及记录固定π，不能确认则阻止计量。同端面两个窗口在X/Y均正交叠即拒绝，即使交叠仅在孔或外轮廓之外；整面与任意窗口也不能并计，防止以空区或基准混用绕过重复检查。共享边界可分项，未选范围不计，零接触保留零面积。数量继承全部基础个数一次，仅总净面积HALF_UP修约。实际接触、工法、材料、坐标及修约须原件并独立复核，不推断土面支模。台阶平台与孔底孔顶仍限既有整面，局部平台/孔端、旋转或斜线窗口、任意水平分区、斜面及空间接触、个体差异、支撑措施和专业计量例外另核。'
ZERO={'constant':'0'}
TWO={'constant':'2'}


def enabled(d):return isinstance(d,dict) and 'plan_range' in d


def bounds(d):
    c=d['plan_range'];return tuple(linear(c['parameters'][k],c['unit']) for k in FIELDS)


def validate_config(c):
    if not isinstance(c,dict) or set(c)!={'parameters','unit','evidence','coordinate_basis','coordinate_evidence_id','scope_confirmed'} or not isinstance(c['parameters'],dict) or set(c['parameters'])!=set(FIELDS) or not isinstance(c['evidence'],dict) or set(c['evidence'])!=set(FIELDS):
        raise DomainError('水平窗口须完整填写XY起止坐标、长度单位、逐坐标原件、共同局部坐标基准及确认，不能用面积或比例代替')
    x1,x2,y1,y2=bounds(dict(plan_range=c))
    if not x1<x2 or not y1<y2:raise DomainError('水平窗口须X起<X止且Y起<Y止，不自动倒向或取绝对值')
    for eid in c['evidence'].values():text(eid,'水平窗口逐坐标原件',200)
    text(c['coordinate_basis'],'窗口与基础孔位共用的局部XY基准');text(c['coordinate_evidence_id'],'窗口坐标基准原件',200)
    if c['scope_confirmed'] is not True:raise DomainError('须据原件确认基础中心、共同局部XY正向和实际窗口范围')


def check_frames(rows,others,conflict=False):
    for a in rows:
        if not enabled(a):continue
        for b in others:
            if not enabled(b) or a['material_index']!=b['material_index']:continue
            ca,cb=a['plan_range'],b['plan_range']
            if any(ca[k].strip()!=cb[k].strip() for k in ('coordinate_basis','coordinate_evidence_id')):
                message='同一来源基础项所有水平窗口须使用相同局部XY基准说明和同一原件，不能跨端面或记录混用'
                if conflict:raise HTTPException(409,message)
                raise DomainError(message)


def overlaps(a,b):
    if not (enabled(a) and enabled(b)):return True
    ax,ae,ay,af=bounds(a);bx,be,by,bf=bounds(b)
    return max(ax,bx)<min(ae,be) and max(ay,by)<min(af,bf)


def geometry(d,item,tier,original,full_ast,holes,key,dimensions,refs,conversions):
    c=d['plan_range'];values=bounds(d);asts={}
    for k,v in zip(FIELDS,values):
        dimensions[key+'.'+k]=str(c['parameters'][k]);refs[key+'.'+k]=c['evidence'][k];asts[k]=op('multiply',{'dimension':key+'.'+k},{'constant':LENGTH[c['unit']]})
        conversions.append(dict(parameter=key+'.'+k,original_value=str(c['parameters'][k]),source_unit=c['unit'],factor=LENGTH[c['unit']],canonical_value=str(v),canonical_unit='m',rule_version=UNIT_VERSION))
    refs[key+'.plan_coordinate']=c['coordinate_evidence_id']
    x1,x2,y1,y2=values;cx,cy=(x1+x2)/2,(y1+y2)/2
    center={axis:op('divide',op('add',asts[axis+'_start'],asts[axis+'_end']),TWO) for axis in ('x','y')}
    wp=dict(length=x2-x1,width=y2-y1)
    wa={axis:op('subtract',asts[lo],asts[hi]) for axis,lo,hi in [('length','x_end','x_start'),('width','y_end','y_start')]}
    window,wn=area(dict(shape='rectangular'),wp,wa)
    p,a,_,_=steps.section(item,tier) if 'foundation_levels' in item else single_section(item,tier)
    relative={**p,'x':-cx,'y':-cy};ra={**a,**{axis:op('subtract',ZERO,center[axis]) for axis in ('x','y')}}
    clip=steps.intersection(relative,ra,wp,wa,original,full_ast,window,wn);gross,node,rule=clip[:3]
    clipped=[]
    for h in holes:
        hp={k:Decimal(v) for k,v in h['normalized_parameters'].items()};ha={k:dim(item,{'index':h['hole_index'],'unit':h['unit']},k) for k in hp};rel={**hp,'x':hp['x']-cx,'y':hp['y']-cy};rasts={**ha,**{axis:op('subtract',ha[axis],center[axis]) for axis in ('x','y')}}
        result=steps.intersection(rel,rasts,wp,wa,Decimal(h['single_area']),h['formula_ast'],window,wn)
        v,n,branch=result[:3]
        clipped.append({**h,'full_area':h['single_area'],'single_area':str(v),'intersection_rule':branch.replace('上层覆盖','窗口'),
                        'formula_ast':n,'formula_asts':dict(full_area=h['formula_ast'],single_area=n),**({'curved_intersection':details(result[3],'窗口')} if len(result)==4 else {})})
    extra=dict(**dict(zip(FIELDS,map(str,values))),plan_bounds=dict(zip(FIELDS,map(str,values))),full_face_area=str(original),window_area=str(window),window_intersection_rule=rule.replace('上层覆盖','窗口').replace('孔','基础截面'),
               plan_frame_hash=store.digest(dict(basis=c['coordinate_basis'].strip(),evidence_id=c['coordinate_evidence_id'].strip())),
               **({'window_curved_intersection':details(clip[3],'窗口')} if len(clip)==4 else {}))
    return gross,node,clipped,extra,dict(**asts,full_face_area=full_ast,window_area=wn)


def checks(rows):
    result=[]
    for i,a in enumerate(rows):
        if not enabled(a):continue
        for b in rows[:i]:
            if not enabled(b) or a['material_index']!=b['material_index'] or a['face']!=b['face']:continue
            if a['face']=='step' and a['level_index']!=b['level_index']:continue
            if a['face'] in ('hole_bottom','hole_top') and a['hole_index']!=b['hole_index']:continue
            values={};nodes={}
            for axis in ('x','y'):
                al,ah=(Decimal(a['plan_bounds'][axis+'_'+k]) for k in ('start','end'));bl,bh=(Decimal(b['plan_bounds'][axis+'_'+k]) for k in ('start','end'))
                values[axis+'_gap']=str(max(al,bl)-min(ah,bh));nodes[axis+'_gap']=op('subtract',(a if al>=bl else b)['formula_asts'][axis+'_start'],(a if ah<=bh else b)['formula_asts'][axis+'_end'])
            result.append(dict(left_index=b['index'],right_index=a['index'],left=b['name'],right=a['name'],**({'level_index':a['level_index']} if a['face']=='step' else {}),**values,state='separated' if any(Decimal(v)>0 for v in values.values()) else 'boundary_touch',formula_asts=nodes))
    return result
