"""Hole-wall strips use the foundation frame, including each hole's offset."""
from decimal import Decimal
from . import store
from .compound_takeoff import op
from .measurement_units import LENGTH, VERSION as UNIT_VERSION
from .engine import DomainError
from . import foundation_formwork_strips as strips

VERSION='foundation-void-formwork-1.1'
NOTICE='仅计据原件选入的当前已审基础竖直孔壁。矩形孔可逐侧整宽或选局部横向条带，圆孔仅整周；X正负侧横向沿基础局部Y，Y正负侧沿局部X，基础中心为0，不以孔中心为0，不随观察面翻转。条带须在来源孔该侧的实际起止坐标内，不自动裁切；同一来源基础项各孔各层条带须用同一坐标基准说明及原件，并据原件确认。Z从基础底0向上，须在孔与层共同标高内，跨层分项。与矩形基础外边界重合的选入部分全扣；相邻矩形孔对向壁贴合仅扣横向与Z的真实交集，点/线相切无正面积扣减，体积扣孔阈值不填实物理孔。继承全部最终基础个数一次，不再乘来源重复。同孔同层同侧横向及Z均正交叠禁止，整宽与条带、跨记录也检查。未选面不计，不推断支模工法；逐面及扣减不修约，仅总净面积按有据位数HALF_UP修约。孔底、水平面、圆孔局部圆弧、斜孔、孔并集、任意空间接触、支撑措施及个体不同范围另核。材料、真实接触范围和修约须原件并独立复核，不与其他模板清单重复。'


def interval(item,h,face,dim):
    axis='y' if face.startswith('x_') else 'x';size='width' if axis=='y' else 'length'
    p={k:Decimal(v) for k,v in h['normalized_parameters'].items()};half=op('divide',dim(item,h,size),{'constant':'2'})
    return p[axis]-p[size]/2,p[axis]+p[size]/2,op('subtract',dim(item,h,axis),half),op('add',dim(item,h,axis),half)


def apply(d,item,h,key,dimensions,refs,conversions,full_width,full_ast,dim):
    c=d['lateral_range'];lo,hi=strips.bounds(d);left,right,left_ast,right_ast=interval(item,h,d['face'],dim)
    if lo<left or hi>right:raise DomainError('孔壁横向条带须在该孔所选侧实际起止坐标内；原点为基础中心，不是孔中心，不自动裁切')
    asts={}
    for k,value in zip(('lateral_start','lateral_end'),(lo,hi)):
        dimensions[key+'.'+k]=str(c['parameters'][k]);refs[key+'.'+k]=c['evidence'][k];asts[k]=op('multiply',{'dimension':key+'.'+k},{'constant':LENGTH[c['unit']]})
        conversions.append(dict(parameter=key+'.'+k,original_value=str(c['parameters'][k]),source_unit=c['unit'],factor=LENGTH[c['unit']],canonical_value=str(value),canonical_unit='m',rule_version=UNIT_VERSION))
    refs[key+'.lateral_coordinate']=c['coordinate_evidence_id']
    return hi-lo,op('subtract',asts['lateral_end'],asts['lateral_start']),dict(lateral_start=str(lo),lateral_end=str(hi),lateral_axis='Y' if d['face'].startswith('x_') else 'X',full_face_width=str(full_width),face_lateral_start=str(left),face_lateral_end=str(right),lateral_frame_hash=store.digest(dict(basis=c['coordinate_basis'].strip(),evidence_id=c['coordinate_evidence_id'].strip()))),dict(**asts,full_face_width=full_ast,face_lateral_start=left_ast,face_lateral_end=right_ast)


def checks(rows,items,dim):
    result=[]
    def get(d):
        if strips.enabled(d):return Decimal(d['lateral_start']),Decimal(d['lateral_end']),d['formula_asts']['lateral_start'],d['formula_asts']['lateral_end']
        item=items[d['material_index']];h=next(h for h in item['opening_holes'] if h['index']==d['hole_index']);return interval(item,h,d['face'],dim)
    for i,a in enumerate(rows):
        for b in rows[:i]:
            if not all(a[k]==b[k] for k in ('material_index','level_index','hole_index','face')) or not (strips.enabled(a) or strips.enabled(b)):continue
            al,ah,aln,ahn=get(a);bl,bh,bln,bhn=get(b);g=max(al,bl)-min(ah,bh);az=a['normalized_parameters'];bz=b['normalized_parameters'];z=max(Decimal(az['z_start']),Decimal(bz['z_start']))-min(Decimal(az['z_end']),Decimal(bz['z_end']));an=a['formula_asts'];bn=b['formula_asts']
            result.append(dict(left_index=b['index'],right_index=a['index'],left=b['name'],right=a['name'],lateral_gap=str(g),z_gap=str(z),state='vertically_disjoint' if z>0 else 'vertical_tangent' if z==0 else 'laterally_disjoint' if g>0 else 'lateral_tangent',formula_asts=dict(lateral_gap=op('subtract',aln if al>=bl else bln,ahn if ah<=bh else bhn),z_gap=op('subtract',an['z_start'] if Decimal(az['z_start'])>=Decimal(bz['z_start']) else bn['z_start'],an['z_end'] if Decimal(az['z_end'])<=Decimal(bz['z_end']) else bn['z_end']))))
    return result
