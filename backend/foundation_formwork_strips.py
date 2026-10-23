"""Rectangular vertical face strips in the foundation local signed XY frame."""
from decimal import Decimal
from fastapi import HTTPException
from . import store
from .compound_takeoff import op, text
from .engine import DomainError
from .measurement_units import LENGTH, VERSION as UNIT_VERSION
from .planar_takeoff import linear

VERSION='foundation-outer-formwork-1.2'
NOTICE='仅计据原件选入的基础竖直外侧实际接触范围。矩形可整宽或局部横向条带：X正负侧横向坐标沿局部Y，Y正负侧沿局部X，原点为基础中心，正向随局部坐标轴、不随观察面翻转。范围须在本层负半宽至正半宽内；局部XY与图纸的对应须有同一基准说明及原件并确认，同一来源基础项跨层跨记录不得混用。条带宽=止坐标−起坐标，乘所选层内实际接触高；侧边矩形开口按横向及标高两个方向的实际交集扣除，不受混凝土体积扣孔阈值影响。横向与标高均正交叠才重复，整宽也参与检查。圆柱整周或圆弧仍按共同角度基准、实际直径及标高计量。继承来源全部基础个数一次，未选范围不计、不推断支模工法，逐面不修约、仅总净面积修约。内部孔壁、孔底、顶底和台阶水平面、斜面、任意空间接触及支撑措施另核。材料做法、真实接触范围和修约须原件及独立复核，不与既有模板重复。'


def enabled(d): return isinstance(d,dict) and 'lateral_range' in d


def bounds(d):
    c=d['lateral_range'];return tuple(linear(c['parameters'][k],c['unit']) for k in ('lateral_start','lateral_end'))


def validate_config(c):
    if not isinstance(c,dict) or set(c)!={'parameters','unit','evidence','coordinate_basis','coordinate_evidence_id','scope_confirmed'}:
        raise DomainError('矩形条带须完整填写横向起止坐标、长度单位、逐坐标原件、共同局部坐标基准及确认')
    if not isinstance(c['parameters'],dict) or set(c['parameters'])!={'lateral_start','lateral_end'} or not isinstance(c['evidence'],dict) or set(c['evidence'])!={'lateral_start','lateral_end'}:
        raise DomainError('横向条带须且仅须起止坐标及逐坐标原件，不能用宽度或比例代替')
    lo,hi=bounds({'lateral_range':c})
    if hi<=lo:raise DomainError('横向条带须起坐标<止坐标，不自动调换方向或取绝对值')
    for eid in c['evidence'].values():text(eid,'横向坐标原件',200)
    text(c['coordinate_basis'],'局部XY与图纸对应的共同横向坐标基准');text(c['coordinate_evidence_id'],'横向坐标基准原件',200)
    if c['scope_confirmed'] is not True:raise DomainError('须据原件确认基础中心、局部XY正向与实际横向条带范围')


def check_frames(rows,others,conflict=False):
    for a in rows:
        if not enabled(a):continue
        for b in others:
            if not enabled(b) or a['material_index']!=b['material_index']:continue
            ca,cb=a['lateral_range'],b['lateral_range']
            if any(ca[k].strip()!=cb[k].strip() for k in ('coordinate_basis','coordinate_evidence_id')):
                message='同一来源基础项全部矩形条带须使用相同局部坐标基准说明及同一原件，跨层跨记录也不能混用'
                if conflict:raise HTTPException(409,message)
                raise DomainError(message)


def overlaps(a,b):
    if not (enabled(a) and enabled(b)):return True
    al,ah=bounds(a);bl,bh=bounds(b);return max(al,bl)<min(ah,bh)


def apply(d,key,dimensions,refs,conversions,full_width,full_ast):
    c=d['lateral_range'];lo,hi=bounds(d)
    if lo < -full_width/2 or hi > full_width/2:raise DomainError('横向条带须在本层外侧面负半宽至正半宽内，不自动裁切越界范围')
    asts={}
    for k,value in zip(('lateral_start','lateral_end'),(lo,hi)):
        dimensions[key+'.'+k]=str(c['parameters'][k]);refs[key+'.'+k]=c['evidence'][k];asts[k]=op('multiply',{'dimension':key+'.'+k},{'constant':LENGTH[c['unit']]})
        conversions.append(dict(parameter=key+'.'+k,original_value=str(c['parameters'][k]),source_unit=c['unit'],factor=LENGTH[c['unit']],canonical_value=str(value),canonical_unit='m',rule_version=UNIT_VERSION))
    refs[key+'.lateral_coordinate']=c['coordinate_evidence_id']
    return hi-lo,op('subtract',asts['lateral_end'],asts['lateral_start']),dict(lateral_start=str(lo),lateral_end=str(hi),lateral_axis='Y' if d['face'].startswith('x_') else 'X',full_face_width=str(full_width),lateral_frame_hash=store.digest(dict(basis=c['coordinate_basis'].strip(),evidence_id=c['coordinate_evidence_id'].strip()))),dict(**asts,full_face_width=full_ast)


def clip(d,h,perpendicular,fk,asts):
    """Return the actual side aperture horizontal intersection and its ASTs."""
    c=d['lateral_range'];lo,hi=bounds(d);axis='y' if perpendicular=='width' else 'x';hp={k:Decimal(v) for k,v in h['normalized_parameters'].items()};start=hp[axis]-hp[perpendicular]/2;end=hp[axis]+hp[perpendicular]/2
    a=max(lo,start);b=min(hi,end)
    if b<=a:return None
    hk=fk+'.holes.'+str(h['index']);center=op('multiply',{'dimension':hk+'.'+axis},{'constant':LENGTH[h['unit']]});half=op('divide',op('multiply',{'dimension':hk+'.'+perpendicular},{'constant':LENGTH[h['unit']]}),{'constant':'2'})
    a_ast=asts['lateral_start'] if lo>=start else op('subtract',center,half);b_ast=asts['lateral_end'] if hi<=end else op('add',center,half)
    return b-a,op('subtract',b_ast,a_ast),dict(lateral_start=str(a),lateral_end=str(b)),dict(lateral_start=a_ast,lateral_end=b_ast)


def interval(row):
    if enabled(row):return Decimal(row['lateral_start']),Decimal(row['lateral_end']),row['formula_asts']['lateral_start'],row['formula_asts']['lateral_end']
    w=Decimal(row['width']);half=op('divide',row['formula_asts']['width'],{'constant':'2'})
    return -w/2,w/2,op('multiply',{'constant':'-1'},half),half


def checks(rows):
    result=[]
    for i,a in enumerate(rows):
        for b in rows[:i]:
            if not all(a[k]==b[k] for k in ('material_index','level_index','face')) or not (enabled(a) or enabled(b)):continue
            al,ah,aln,ahn=interval(a);bl,bh,bln,bhn=interval(b);g=max(al,bl)-min(ah,bh)
            az=a['normalized_parameters'];bz=b['normalized_parameters'];z=max(Decimal(az['z_start']),Decimal(bz['z_start']))-min(Decimal(az['z_end']),Decimal(bz['z_end']));an=a['formula_asts'];bn=b['formula_asts']
            result.append(dict(left_index=b['index'],right_index=a['index'],left=b['name'],right=a['name'],lateral_gap=str(g),z_gap=str(z),state='vertically_disjoint' if z>0 else 'vertical_tangent' if z==0 else 'laterally_disjoint' if g>0 else 'lateral_tangent',formula_asts=dict(lateral_gap=op('subtract',aln if al>=bl else bln,ahn if ah<=bh else bhn),z_gap=op('subtract',an['z_start'] if Decimal(az['z_start'])>=Decimal(bz['z_start']) else bn['z_start'],an['z_end'] if Decimal(az['z_end'])<=Decimal(bz['z_end']) else bn['z_end']))))
    return result
