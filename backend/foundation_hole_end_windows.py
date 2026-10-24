"""Local windows on reviewed internal void bottoms and tops."""
from decimal import Decimal
from . import store
from .compound_takeoff import op
from .measurement_units import LENGTH,VERSION as UNIT_VERSION
from .foundation_void_formwork import dim
from . import foundation_hole_end_formwork as ends
from . import foundation_horizontal_windows as windows
from . import foundation_step_formwork as steps
from . import profile_window_intersections as profiles
from .engine import DomainError

VERSION='foundation-horizontal-formwork-1.7'
NOTICE= ('整体顶底、台阶平台及内部孔底/孔顶可据原件选XY矩形窗口；窗口共用来源基础中心及孔位XY正向，不以所选孔圆心为原点，不随俯仰视翻转。'
        '孔端Z及混凝土侧层只读继承：孔底取起Z下侧，孔顶取止Z上侧；外部孔口仍阻断。毛面积为所选孔∩混凝土侧层∩窗口，实际孔扣为所选孔∩同侧相接孔∩窗口；该侧相接孔已由来源验证完全在混凝土侧层内且互不重叠。'
        '所有直边/圆弧及窗口边界事件作X分段解析积分，工作200位Decimal、保留80位和记录固定π，不用网格或容差；不能确认界限则阻断。相接孔完整覆盖时保留逐段几何证明，按同一毛面积分割恒等式扣减，直接积分另存，不作容差归零。'
        '整面和同孔同端X/Y正重叠窗口不能并计，跨记录同样检查；边界相接可分项，不同孔/孔端分开计量但共用XY依据。体积阈值不填实物理孔，未选范围不计，零接触保留零面积。'
        '全部基础个数继承一次，逐面逐孔不修约，仅总净面积按有据位数HALF_UP修约；真实接触、工法、材料及修约须原件和独立复核，不推断施工支模。旋转/斜线窗口、任意水平分区、斜孔/异形并集、空间接触、个体差异、支撑及专业计量例外另核。')


def geometry(d,item,tier,original,full_ast,holes,key,dimensions,refs,conversions):
    c=d['plan_range'];values=windows.bounds(d);asts={}
    for k,v in zip(windows.FIELDS,values):
        dimensions[key+'.'+k]=str(c['parameters'][k]);refs[key+'.'+k]=c['evidence'][k];asts[k]=op('multiply',{'dimension':key+'.'+k},{'constant':LENGTH[c['unit']]})
        conversions.append(dict(parameter=key+'.'+k,original_value=str(c['parameters'][k]),source_unit=c['unit'],factor=LENGTH[c['unit']],canonical_value=str(v),canonical_unit='m',rule_version=UNIT_VERSION))
    refs[key+'.plan_coordinate']=c['coordinate_evidence_id']
    selected=ends.hole(item,d['hole_index']);hp,ha,_,_=ends.profile(item,selected)
    cp,ca,_,_=steps.section(item,tier) if 'foundation_levels' in item else ends.single_section(item,tier)
    shapes=[profiles.profile(hp,ha,'所选孔'),profiles.profile(cp,ca,'混凝土侧层')]
    for h in holes:
        p={k:Decimal(v) for k,v in h['normalized_parameters'].items()};a={k:dim(item,{'index':h['hole_index'],'unit':h['unit']},k) for k in p}
        shapes.append(profiles.profile(p,a,'相接孔H'+str(h['hole_index'])))
    (gross,node,trace),cuts,proof=profiles.compute(shapes,values,asts)
    clipped=[]
    for h,(v,n,m) in zip(holes,cuts):
        clipped.append(dict(**{k:v for k,v in h.items() if k not in ('curved_intersection','formula_asts','single_area','formula_ast','intersection_rule')},full_end_intersection_area=h['single_area'],single_area=str(v),window_direct_area=str(v),formula_ast=n,
                        formula_asts=dict(full_area=h['formula_asts']['full_area'],full_end_intersection_area=h['formula_ast'],single_area=n,window_direct_area=n),
                        intersection_rule='所选孔∩同侧相接孔∩窗口；相接孔已审包含于混凝土侧层',curved_intersection=m))
    if proof:
        index=proof.pop('canonical_hole');others=[h for i,h in enumerate(clipped) if i!=index]
        remaining=gross-sum((Decimal(h['single_area']) for h in others),Decimal(0))
        if remaining<0:raise DomainError('孔端窗口覆盖分割余量非正，当前精度不能确认，请另核')
        h=clipped[index];n=op('subtract',node,op('add',*[h['formula_ast'] for h in others]));h['single_area']=str(remaining);h['formula_ast']=n;h['formula_asts']['single_area']=n;h['intersection_rule']+='；按完整覆盖分割恒等式计入毛面积减其他孔扣，直接积分另存'
        proof['canonical_hole_index']=h['hole_index']
        for q in proof['terms']:
            q['holes']=[holes[i-1]['hole_index'] for i in q['holes']]
            for piece in q['pieces']:piece['hole_index']=holes[piece['hole_index']-1]['hole_index']
    if gross<0 or any(Decimal(h['single_area'])<0 for h in clipped) or sum((Decimal(h['single_area']) for h in clipped),Decimal(0))>gross:
        raise DomainError('孔端窗口实际孔扣超过毛交集，当前精度不能确认，请另核')
    wn=op('multiply',op('subtract',asts['x_end'],asts['x_start']),op('subtract',asts['y_end'],asts['y_start']))
    extra=dict(**dict(zip(windows.FIELDS,map(str,values))),plan_bounds=dict(zip(windows.FIELDS,map(str,values))),full_face_area=str(original),window_area=str((values[1]-values[0])*(values[3]-values[2])),
               hole_end_window=True,window_intersection_rule='所选孔∩混凝土侧层∩窗口',plan_frame_hash=store.digest(dict(basis=c['coordinate_basis'].strip(),evidence_id=c['coordinate_evidence_id'].strip())),window_curved_intersection=trace,
               **({'hole_end_window_coverage':proof} if proof else {}))
    return gross,node,clipped,extra,dict(**asts,full_face_area=full_ast,window_area=wn)
