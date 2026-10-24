"""Selected vertical void walls, excluding exterior and shared-void interfaces."""
from decimal import Decimal, ROUND_HALF_UP
from fastapi import HTTPException
from . import store
from .compound_takeoff import PI, op, text
from .engine import DomainError, precise, quantity_number
from .measurement_units import LENGTH, VERSION as UNIT_VERSION
from .planar_takeoff import linear
from .post_foundation import calculate as foundation_quantity
from .foundation_formwork import source_component, layers, bounds
from .railing_material import snapshot
from .surface_takeoff import check_sources

VERSION='foundation-void-formwork-1.0'
FACES={'x_minus':'X负侧孔壁','x_plus':'X正侧孔壁','y_minus':'Y负侧孔壁','y_plus':'Y正侧孔壁','circumference':'圆孔整周内壁'}
NOTICE='仅计明确选入的当前已审基础竖直孔壁，不从孔体积、立柱或锚栓推断支模。矩形孔逐侧选整宽，圆孔只选整周；方向沿来源基础共同局部XY，不随观察面翻转。Z以基础底为0向上，须在所选层与所选孔的共同标高内，跨层分项。矩形孔壁与本层矩形基础外边界重合的部分无混凝土接触，全部扣除；与另一矩形孔对向边界相接时，按真实横向及Z交集扣除。圆孔及圆形外轮廓的点相切无正面积扣减。体积扣孔阈值不改变真实孔壁及无接触面。继承本基础项全部最终个数一次，不再乘来源重复；未选范围不计，同孔同层同侧正高度交叠及跨记录重复禁止。逐面及扣减不修约，仅总净面积按有据位数HALF_UP修约。孔底、顶底、台阶水平面、局部孔壁条带/圆弧、斜孔、孔并集、任意空间接触、支撑措施及个体不同范围另核，不与其他模板清单重复。真实施工工法、材料、接触范围和修约须原件并独立复核。'
ZERO={'constant':'0'}


def vertical(h):
    return Decimal(h.get('z_start','0')),Decimal(h.get('z_end',h['through_height'])),h.get('vertical_formula_asts',dict(z_start=ZERO,z_end=h['height_ast']))


def faces(h):return ['x_minus','x_plus','y_minus','y_plus'] if h.get('shape')=='rectangular' else ['circumference']


@precise
def candidates(p,cid):
    s=source_component(p,cid);r=foundation_quantity(s);items=[]
    for d in r['foundation_items']:
        holes=[]
        for h in d.get('opening_holes',[]):
            lo,hi,_=vertical(h);intersections=[]
            for v in layers(d):
                a=max(lo,Decimal(v['from_z']));b=min(hi,Decimal(v['to_z']))
                if b>a:intersections.append(dict(level_index=v['index'],from_z=str(a),to_z=str(b)))
            holes.append(dict(index=h['index'],name=h['name'],shape=h.get('shape','circular'),unit=h['unit'],normalized_parameters=h['normalized_parameters'],from_z=str(lo),to_z=str(hi),faces=faces(h),level_intersections=intersections))
        if holes:items.append(dict(material_index=d['material_index'],material_name=d['material_name'],total_count=d['total_count'],levels=[dict(index=v['index'],from_z=v['from_z'],to_z=v['to_z']) for v in layers(d)],holes=holes))
    return dict(source_component_id=s['id'],source_component_hash=store.digest(snapshot(s)),source_name=s['name'],source_revision=s['revision'],foundation_items=items,notice=NOTICE)


def overlap(a,b):return all(a[k]==b[k] for k in ('material_index','level_index','hole_index','face')) and max(bounds(a)[0],bounds(b)[0])<min(bounds(a)[1],bounds(b)[1])


def check_claims(p,c):
    for other in p['components']:
        if other['kind']=='foundation_void_formwork' and other['id']!=c.get('id') and other['source_component_id']==c['source_component_id'] and any(overlap(a,b) for a in c['contact_ranges'] for b in other['contact_ranges']):
            raise HTTPException(409,'孔壁模板范围与记录“'+other['name']+'”重复，请修订原记录或核定不重叠范围')


def dim(item,h,k):return op('multiply',{'dimension':'foundation.'+str(item['material_index'])+'.holes.'+str(h['index'])+'.'+k},{'constant':LENGTH[h['unit']]})


def deductions(item,tier,h,face,lo,hi,zasts,width,width_ast):
    if h.get('shape')!='rectangular':return [],[],Decimal(0)
    hp={k:Decimal(v) for k,v in h['normalized_parameters'].items()};tp={k:Decimal(v) for k,v in tier['normalized_parameters'].items()};axis='x' if face.startswith('x_') else 'y';other_axis='y' if axis=='x' else 'x';outer='length' if axis=='x' else 'width';perp='width' if axis=='x' else 'length';sign=Decimal(-1) if face.endswith('minus') else Decimal(1);plane=hp[axis]+sign*hp[outer]/2
    half=op('divide',dim(item,h,perp),{'constant':'2'});left=hp[other_axis]-hp[perp]/2;right=hp[other_axis]+hp[perp]/2;left_ast=op('subtract',dim(item,h,other_axis),half);right_ast=op('add',dim(item,h,other_axis),half)
    def row(kind,index,name,a,b,aa,bb,x,y,xa,ya):
        w=y-x;height=b-a;node=op('multiply',op('subtract',ya,xa),op('subtract',bb,aa));return dict(kind=kind,hole_index=index,name=name,lateral_start=str(x),lateral_end=str(y),z_start=str(a),z_end=str(b),width=str(w),height=str(height),single_area=str(w*height),formula_asts=dict(lateral_start=xa,lateral_end=ya,z_start=aa,z_end=bb,width=op('subtract',ya,xa),height=op('subtract',bb,aa),single_area=node))
    # An exterior face has no concrete on its outward side; do not add other cuts.
    if 'diameter' not in tp and sign*plane==tp[outer]/2:
        d=row('exterior_boundary',h['index'],'本层基础外边界，无混凝土接触',lo,hi,zasts['z_start'],zasts['z_end'],left,right,left_ast,right_ast);return [d],[d['formula_asts']['single_area']],Decimal(d['single_area'])
    rows=[];nodes=[];cut=Decimal(0)
    for n in item.get('opening_holes',[]):
        if n['index']==h['index'] or n.get('shape')!='rectangular':continue
        np={k:Decimal(v) for k,v in n['normalized_parameters'].items()}
        if np[axis]-sign*np[outer]/2!=plane:continue
        nl,nr=np[other_axis]-np[perp]/2,np[other_axis]+np[perp]/2;x,y=max(left,nl),min(right,nr);zl,zh,za=vertical(n);a,b=max(lo,zl),min(hi,zh)
        if y<=x or b<=a:continue
        nhalf=op('divide',dim(item,n,perp),{'constant':'2'});nleft=op('subtract',dim(item,n,other_axis),nhalf);nright=op('add',dim(item,n,other_axis),nhalf)
        d=row('adjacent_void',n['index'],n['name'],a,b,zasts['z_start'] if lo>=zl else za['z_start'],zasts['z_end'] if hi<=zh else za['z_end'],x,y,left_ast if left>=nl else nleft,right_ast if right<=nr else nright);rows.append(d);nodes.append(d['formula_asts']['single_area']);cut+=Decimal(d['single_area'])
    return rows,nodes,cut


@precise
def calculate(comp,source=None):
    source=source if source is not None else comp['source_geometry']
    if comp.get('parameters')!={} or comp.get('quantity_type')!='area' or comp.get('scope_confirmed') is not True:raise DomainError('孔壁模板须为空顶层参数、面积类型，并确认真实接触范围及不重复计量')
    if 'multiplicity' in comp:raise DomainError('孔壁模板继承全部基础个数，不能再填重复数量')
    if source.get('kind')!='post_foundation':raise DomainError('孔壁模板只能引用已审立柱基础')
    if comp.get('source_component_id')!=source['id'] or comp.get('source_component_hash')!=store.digest(snapshot(source)):raise HTTPException(409,'基础来源已修订，请重新读取孔洞、层及全部个数')
    for k,label in [('name','孔壁模板名称'),('material','孔壁模板材料及做法'),('measurement_basis','孔壁实际接触及不重复计量说明')]:text(comp.get(k),label,200 if k=='name' else 2000)
    digits=comp.get('rounding_digits')
    if type(digits) is not int or not 0<=digits<=12:raise DomainError('孔壁总净面积修约位数须为0至12整数')
    selected=comp.get('contact_ranges')
    if not isinstance(selected,list) or not 1<=len(selected)<=200:raise DomainError('须选择1至200项孔壁接触范围，未选面不计')
    r=foundation_quantity(source);items={d['material_index']:d for d in r['foundation_items']};dimensions=dict(r['dimensions']);refs=dict(r['source_evidence']);conversions=[];rows=[];names=set();gross=Decimal(0);cut=Decimal(0);nodes=[]
    for k in ('basis','material','rounding'):refs['void_formwork.'+k]=text(comp.get(k+'_evidence_id'),'孔壁模板原件',200)
    for i,d in enumerate(selected,1):
        if not isinstance(d,dict) or set(d)!={'name','material_index','level_index','hole_index','face','parameters','unit','evidence','measurement_basis'}:raise DomainError('孔壁范围须仅含名称、基础项、层、孔、内壁面、起止Z、单位及逐参数原件和说明；局部条带或圆弧另核')
        if any(type(d[k]) is not int for k in ('material_index','level_index','hole_index')) or d['material_index'] not in items:raise DomainError('孔壁基础项、层和孔须为已审来源中存在的整数序号')
        name=text(d['name'],'孔壁范围名称',200)
        if name in names:raise DomainError('孔壁范围名称不能重复')
        names.add(name);item=items[d['material_index']];tiers=layers(item);h=next((h for h in item.get('opening_holes',[]) if h['index']==d['hole_index']),None)
        if not 1<=d['level_index']<=len(tiers) or h is None:raise DomainError('所选来源层或孔不存在；不能新增未审孔洞')
        tier=tiers[d['level_index']-1]
        if not isinstance(d['face'],str) or d['face'] not in faces(h):raise DomainError('矩形孔须选X/Y正负内壁，圆孔仅支持整周竖直内壁')
        if not isinstance(d['parameters'],dict) or set(d['parameters'])!={'z_start','z_end'} or not isinstance(d['evidence'],dict) or set(d['evidence'])!={'z_start','z_end','measurement_basis'}:raise DomainError('孔壁须完整填写起止Z及逐参数原件和范围说明')
        text(d['measurement_basis'],'所选孔壁真实接触和无接触扣减说明');lo,hi=bounds(d);hl,hh,_=vertical(h);lower=max(hl,Decimal(tier['from_z']));upper=min(hh,Decimal(tier['to_z']))
        if not lower<=lo<hi<=upper:raise DomainError('孔壁起止Z须在所选层与孔的共同标高内，满足共同底≤起始Z<终止Z≤共同顶；跨层分项')
        if any(overlap(d,v) for v in selected[:i-1]):raise DomainError('同基础项同层同孔同内壁面的标高范围正高度重叠，不能重复计量')
        key='void_formwork.'+str(i);asts={}
        for k,value in zip(('z_start','z_end'),(lo,hi)):
            dimensions[key+'.'+k]=str(d['parameters'][k]);asts[k]=op('multiply',{'dimension':key+'.'+k},{'constant':LENGTH[d['unit']]});conversions.append(dict(parameter=key+'.'+k,original_value=str(d['parameters'][k]),source_unit=d['unit'],factor=LENGTH[d['unit']],canonical_value=str(value),canonical_unit='m',rule_version=UNIT_VERSION))
        refs.update({key+'.'+k:text(v,'孔壁接触原件',200) for k,v in d['evidence'].items()})
        hp={k:Decimal(v) for k,v in h['normalized_parameters'].items()};circular=h.get('shape')!='rectangular';size='diameter' if circular else 'width' if d['face'].startswith('x_') else 'length';width=hp[size]*(Decimal(PI) if circular else 1);width_ast=op('multiply',dim(item,h,size),*([{'constant':PI}] if circular else []));height=hi-lo;height_ast=op('subtract',asts['z_end'],asts['z_start']);single_gross=width*height;gross_ast=op('multiply',width_ast,height_ast)
        ds,ns,single_cut=deductions(item,tier,h,d['face'],lo,hi,asts,width,width_ast);single_net=single_gross-single_cut
        if single_net<0:raise DomainError('孔壁无接触扣减超过毛面积，请核对已审几何')
        count=Decimal(item['total_count']);count_ast=item['formula_asts']['total_count'];cut_ast=op('add',*ns);net_ast=op('subtract',gross_ast,cut_ast);total_ast=op('multiply',net_ast,count_ast);gross+=single_gross*count;cut+=single_cut*count;nodes.append(total_ast)
        rows.append({**d,'index':i,'material_name':item['material_name'],'hole_name':h['name'],'hole_shape':h.get('shape','circular'),'hole_unit':h['unit'],'hole_parameters':h['parameters'],'hole_normalized_parameters':h['normalized_parameters'],'hole_from_z':str(hl),'hole_to_z':str(hh),'layer_from_z':tier['from_z'],'layer_to_z':tier['to_z'],'available_from_z':str(lower),'available_to_z':str(upper),'face_label':FACES[d['face']],'normalized_parameters':dict(z_start=str(lo),z_end=str(hi)),'width':str(width),'height':str(height),'total_count':str(count),'single_gross_area':str(single_gross),'single_deduction_area':str(single_cut),'single_net_area':str(single_net),'total_gross_area':str(single_gross*count),'total_deduction_area':str(single_cut*count),'total_net_area':str(single_net*count),'deductions':ds,'formula_asts':dict(z_start=asts['z_start'],z_end=asts['z_end'],width=width_ast,height=height_ast,total_count=count_ast,single_gross_area=gross_ast,single_deduction_area=cut_ast,single_net_area=net_ast,total_gross_area=op('multiply',gross_ast,count_ast),total_deduction_area=op('multiply',cut_ast,count_ast),total_net_area=total_ast)})
    quantity_number(gross);quantity_number(cut);net=quantity_number(gross-cut);q=quantity_number(net.quantize(Decimal(1).scaleb(-digits),rounding=ROUND_HALF_UP))
    return dict(quantity=str(q),gross=str(gross),deduction=str(cut),unit='m²',parameters={},material=comp['material'],formula='Σ[(已选孔壁整宽或整周长×实际接触高−外边界及相邻孔实际无接触交集)×已审全部基础个数]；仅总净面积修约'+str(digits)+'位',formula_ast=dict(op='round',args=[op('add',*nodes)],digits=digits,mode='HALF_UP'),dimensions=dimensions,source_evidence=refs,unit_conversions=conversions,source_unit_conversions=r['unit_conversions'],source_geometry=snapshot(source),source_component_id=source['id'],source_component_hash=comp['source_component_hash'],contact_ranges=rows,void_formwork_summary=dict(source_name=source['name'],source_revision=source['revision'],range_count=len(rows),unrounded_quantity=str(net),rounding_digits=digits,rounding_adjustment=str(q-net)),**({'void_formwork_pi_constant':PI} if any(d['face']=='circumference' for d in selected) else {}),rounding_rule='Decimal 80位及记录的固定π，来源未修约孔尺寸与最终全部基础个数；逐面与扣减不修约，不再乘来源重复，仅总面积HALF_UP修约',rule_version=VERSION,notice=NOTICE)


def validate(p,comp,saved=False):
    s=source_component(p,comp.get('source_component_id'));r=calculate(comp,s);check_claims(p,comp);proofs=check_sources(p,r['source_evidence']);stamp=store.digest(dict(source_component_hash=comp['source_component_hash'],sources=proofs))
    if saved and (comp.get('source_stamp')!=stamp or comp.get('evidence')!=r['source_evidence'] or comp.get('preview')!=r or comp.get('source_geometry')!=snapshot(s)):raise HTTPException(409,'孔壁来源、接触范围、无接触扣减或计算已变化，请修订并重新复核')
    return r,stamp
