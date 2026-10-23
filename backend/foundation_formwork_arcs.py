"""Degree intervals in one evidenced local XY frame, with periodic seam checks."""
from decimal import Decimal
from fastapi import HTTPException
from . import store
from .compound_takeoff import op, text
from .engine import DomainError, number

VERSION = 'foundation-outer-formwork-1.1'
NOTICE = '模板仅计据原件选入的基础竖直外侧实际接触范围，矩形逐侧、圆柱可整周或明确圆弧。圆弧采用同一基础局部XY圆心，0°为+X，从+Z向原点俯视、从+X向+Y逆时针为正；局部XY与图纸的对应须有原件并确认。同一来源基础项全部圆弧须引用完全相同的角度基准说明和原件，跨层、跨记录也不能混用。起止角度须0≤起角<止角≤360°，不自动求余、倒向或取小弧，跨0°须拆成两项；0°与360°为同一周期边界。展开宽=π×本层实际直径×(止角−起角)/360，面积乘本层内实际接触高及来源全部基础个数，逐弧不修约。仅角度或仅标高分离可共存，两方向均正交叠则拒绝，整周也参与重复检查。内孔及点相切不产生圆柱外侧正面积扣减。未选范围不计，不推断是否支模、不额外乘重复；矩形侧开口仍按真实几何扣，不受混凝土体积阈值影响。材料、接触范围和修约须原件及独立复核。顶底、台阶水平面、孔壁孔底、斜面、任意空间相交和支撑措施另核，不与既有模板清单重复。'
ZERO={'constant':'0'}
FULL={'constant':'360'}


def enabled(d): return isinstance(d,dict) and 'arc_range' in d


def validate_config(c):
    if not isinstance(c,dict) or set(c)!={'parameters','unit','evidence','coordinate_basis','coordinate_evidence_id','scope_confirmed'}:
        raise DomainError('局部圆弧须完整填写起止角度、度单位、逐角原件、共同角度基准及确认')
    if c['unit']!='°': raise DomainError('圆弧角度仅接受明确的度（°），不能混用弧度或长度单位')
    if not isinstance(c['parameters'],dict) or set(c['parameters'])!={'angle_start','angle_end'} or not isinstance(c['evidence'],dict) or set(c['evidence'])!={'angle_start','angle_end'}:
        raise DomainError('圆弧须且仅须起角、止角及逐角原件，不得另填圆弧长度或比例')
    lo,hi=(number(c['parameters'][k]) for k in ('angle_start','angle_end'))
    if not 0<=lo<hi<=360: raise DomainError('圆弧角度须0≤起角<止角≤360°，跨0°请拆分，不自动求余或取小弧')
    for eid in c['evidence'].values(): text(eid,'圆弧逐角原件',200)
    text(c['coordinate_basis'],'基础局部XY与图纸对应的共同角度基准');text(c['coordinate_evidence_id'],'共同角度基准原件',200)
    if c['scope_confirmed'] is not True: raise DomainError('须据原件确认共同圆心、+X零角、+Z俯视逆时针及起止角度的实际接触范围')


def angles(d):
    if not enabled(d): return Decimal(0),Decimal(360)
    c=d['arc_range'];return number(c['parameters']['angle_start']),number(c['parameters']['angle_end'])


def gap(a,b):
    lo,hi=angles(a);start,end=angles(b)
    return min((max(lo,start+s)-min(hi,end+s),s) for s in map(Decimal,('0','-360','360')))[0]


def same_frame(a,b):
    ca,cb=a['arc_range'],b['arc_range']
    return ca['coordinate_basis'].strip()==cb['coordinate_basis'].strip() and ca['coordinate_evidence_id'].strip()==cb['coordinate_evidence_id'].strip()


def check_frames(rows, others, conflict=False):
    for a in rows:
        if not enabled(a): continue
        for b in others:
            if enabled(b) and a['material_index']==b['material_index'] and not same_frame(a,b):
                message='同一来源基础项的全部圆弧须使用相同角度基准说明及同一原件，不能混用方向基准绕过重复检查'
                if conflict: raise HTTPException(409,message)
                raise DomainError(message)


def apply(d,key,dimensions,refs,full_width,full_ast):
    c=d['arc_range'];lo,hi=angles(d);span=hi-lo;asts={}
    for k in ('angle_start','angle_end'):
        dimensions[key+'.'+k]=str(c['parameters'][k]);refs[key+'.'+k]=c['evidence'][k];asts[k]={'dimension':key+'.'+k}
    refs[key+'.arc_coordinate']=c['coordinate_evidence_id'];span_ast=op('subtract',asts['angle_end'],asts['angle_start']);width=full_width*span/360
    return width,op('divide',op('multiply',full_ast,span_ast),FULL),dict(angle_start=str(lo),angle_end=str(hi),angle_span=str(span),angle_unit='°',full_circumference=str(full_width),arc_frame_hash=store.digest(dict(basis=c['coordinate_basis'].strip(),evidence_id=c['coordinate_evidence_id'].strip()))),dict(**asts,angle_span=span_ast,full_circumference=full_ast)


def checks(rows):
    result=[]
    for i,a in enumerate(rows):
        if a['face']!='circumference': continue
        for b in rows[:i]:
            if b['face']!='circumference' or a['material_index']!=b['material_index'] or a['level_index']!=b['level_index'] or not (enabled(a) or enabled(b)): continue
            az={k:Decimal(v) for k,v in a['normalized_parameters'].items()};bz={k:Decimal(v) for k,v in b['normalized_parameters'].items()};z=max(az['z_start'],bz['z_start'])-min(az['z_end'],bz['z_end'])
            aa,ae=angles(a);ba,be=angles(b);g,shift=min((max(aa,ba+s)-min(ae,be+s),s) for s in map(Decimal,('0','-360','360')))
            an=a['formula_asts'];bn=b['formula_asts'];bs=op('add',bn.get('angle_start',ZERO),{'constant':str(shift)});en=op('add',bn.get('angle_end',FULL),{'constant':str(shift)})
            g_ast=op('subtract',an.get('angle_start',ZERO) if aa>=ba+shift else bs,an.get('angle_end',FULL) if ae<=be+shift else en)
            z_ast=op('subtract',an['z_start'] if az['z_start']>=bz['z_start'] else bn['z_start'],an['z_end'] if az['z_end']<=bz['z_end'] else bn['z_end'])
            result.append(dict(left_index=b['index'],right_index=a['index'],left=b['name'],right=a['name'],angle_gap=str(g),periodic_shift=str(shift),z_gap=str(z),state='vertically_disjoint' if z>0 else 'vertical_tangent' if z==0 else 'angular_disjoint' if g>0 else 'angular_tangent',formula_asts=dict(angle_gap=g_ast,z_gap=z_ast)))
    return result
