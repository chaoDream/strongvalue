"""Contained disjoint vertical circular through holes across every foundation tier."""
from decimal import Decimal
from .compound_takeoff import PI,op,text
from .engine import DomainError
from .planar_takeoff import linear
from .measurement_units import LENGTH,VERSION as UNIT_VERSION

VERSION='railing-post-foundation-1.3'
NOTICE='每根已审最终立柱对应一个独立基础，来源重复仅乘一次。仅扣明确列出的竖直圆形贯通孔，同一孔自基础底穿过全部层；孔位采用基础共同中心为局部XY原点，须有据核定轴向及实际孔径，不从立柱或锚栓推断。孔须完全位于每层轮廓内，孔与孔不得正面积重叠或相互包含；几何相切允许，不代表最小净距或结构安全验收。矩形及圆形台阶须同心同轴紧接且上层不外伸，层高非累计；层数和孔数不增加基础个数。各项各层同一材料；逐孔逐层不修约，累计数量后仅净体积修约。未列孔、盲孔/凹槽、斜孔、异形孔、孔并集、预埋实体扣减、基础交叠、定位承载、垫层土方钢筋模板另核，不与既有基础或清单重复。'
FIELDS=('x','y','diameter')


def validate_config(c):
    from . import foundation_recesses,foundation_voids
    if foundation_voids.enabled(c):return foundation_voids.validate_config(c)
    if foundation_recesses.enabled(c):return foundation_recesses.validate_config(c)
    if not isinstance(c,dict) or set(c)!={'unit','coordinate_basis','coordinate_evidence_id','scope_confirmed','holes'}:raise DomainError('基础贯通孔须明确局部XY单位、共同中心坐标依据、贯通范围确认及孔表')
    if c['scope_confirmed'] is not True:raise DomainError('须确认每个孔竖直贯穿本基础全部层，采用实际孔径和共同中心局部XY，不重复扣减')
    text(c['coordinate_basis'],'基础中心原点及局部XY方向',2000);text(c['coordinate_evidence_id'],'基础孔位坐标原件',200)
    if not isinstance(c['unit'],str) or c['unit'] not in LENGTH:raise DomainError('基础孔位及孔径须明确长度单位')
    if not isinstance(c['holes'],list) or not 1<=len(c['holes'])<=20:raise DomainError('每基础须明确1至20个贯通圆孔；无孔请关闭贯通孔扣减')
    names=set()
    for h in c['holes']:
        if not isinstance(h,dict) or set(h)!={'name','parameters','evidence','measurement_basis'} or not isinstance(h['parameters'],dict) or set(h['parameters'])!=set(FIELDS) or not isinstance(h['evidence'],dict) or set(h['evidence'])!=set(FIELDS)|{'measurement_basis'}:raise DomainError('每个基础孔须仅含孔名、中心XY、实际直径、逐参数原件及本孔贯通说明和依据')
        name=text(h['name'],'基础孔名称',200)
        if name in names:raise DomainError('同一基础孔名不可重复')
        names.add(name);text(h['measurement_basis'],'本孔贯通及扣减说明',2000)


def apply(d,gross,gross_ast,tiers,parameters,key,dimensions,refs,conversions):
    from . import foundation_recesses,foundation_voids
    if foundation_voids.enabled(d['through_holes']):return foundation_recesses.apply(d,gross,gross_ast,tiers,parameters,key,dimensions,refs,conversions,profile=foundation_voids)
    if foundation_recesses.enabled(d['through_holes']):return foundation_recesses.apply(d,gross,gross_ast,tiers,parameters,key,dimensions,refs,conversions)
    c=d['through_holes'];validate_config(c);refs[key+'.holes.coordinate']=text(c['coordinate_evidence_id'],'基础孔位坐标原件',200)
    round_shape=d.get('shape') in ('circular','stepped_circular');layers=tiers if tiers is not None else [dict(index=1,normalized_parameters=parameters,single_level_volume=str(gross),formula_asts=dict(single_level_volume=gross_ast))]
    height_nodes=[]
    for v in layers:
        prefix=key+'.levels.'+str(v['index']) if tiers is not None else key;height_nodes.append(op('multiply',{'dimension':prefix+'.height'},{'constant':LENGTH[d['length_unit']]}))
    height_ast=op('add',*height_nodes);height=sum((Decimal(v['normalized_parameters']['height']) for v in layers),Decimal(0));rows=[];checks=[];deduction=Decimal(0);nodes=[];layer_deductions=[Decimal(0) for _ in layers];layer_nodes=[[] for _ in layers]
    def square(n):return op('multiply',n,n)
    for i,h in enumerate(c['holes'],1):
        prefix=key+'.holes.'+str(i);values={};asts={}
        for k in FIELDS:
            raw=h['parameters'][k];n=linear(raw,c['unit'],k=='diameter');values[k]=n;dimensions[prefix+'.'+k]=str(raw);asts[k]=op('multiply',{'dimension':prefix+'.'+k},{'constant':LENGTH[c['unit']]})
            conversions.append(dict(parameter=prefix+'.'+k,original_value=str(raw),source_unit=c['unit'],factor=LENGTH[c['unit']],canonical_value=str(n),canonical_unit='m',rule_version=UNIT_VERSION))
        for k,eid in h['evidence'].items():refs[prefix+'.'+k]=text(eid,'基础逐孔原件',200)
        containments=[];radius=values['diameter']/2
        for j,v in enumerate(layers):
            p={k:Decimal(n) for k,n in v['normalized_parameters'].items()};lp=key+'.levels.'+str(v['index']) if tiers is not None else key
            def dim(k):return op('multiply',{'dimension':lp+'.'+k},{'constant':LENGTH[d['length_unit']]})
            if round_shape:
                distance=values['x']**2+values['y']**2;limit=(p['diameter']/2-radius)**2;margin=limit-distance
                if values['diameter']>=p['diameter'] or margin<0:raise DomainError('基础孔“'+h['name']+'”越出第'+str(v['index'])+'层圆形轮廓或覆盖整层')
                margins={'radial_squared':str(margin)};margin_asts={'radial_squared':op('subtract',square(op('divide',op('subtract',dim('diameter'),asts['diameter']),{'constant':'2'})),op('add',square(asts['x']),square(asts['y'])))}
            else:
                mx=(p['length']-values['diameter'])/2-abs(values['x']);my=(p['width']-values['diameter'])/2-abs(values['y'])
                if min(mx,my)<0:raise DomainError('基础孔“'+h['name']+'”越出第'+str(v['index'])+'层矩形轮廓')
                margins={'x':str(mx),'y':str(my)};margin_asts={}
                for axis,side in [('x','length'),('y','width')]:
                    center=asts[axis] if values[axis]>=0 else op('subtract',{'constant':'0'},asts[axis]);margin_asts[axis]=op('subtract',op('divide',op('subtract',dim(side),asts['diameter']),{'constant':'2'}),center)
            amount=Decimal(PI)/4*values['diameter']*values['diameter']*p['height'];node=op('multiply',op('divide',{'constant':PI},{'constant':'4'}),asts['diameter'],asts['diameter'],height_nodes[j]);layer_deductions[j]+=amount;layer_nodes[j].append(node)
            containments.append(dict(level_index=v['index'],margins=margins,state='tangent' if any(Decimal(n)==0 for n in margins.values()) else 'inside',formula_asts=margin_asts))
        for previous in rows:
            p=previous['normalized_parameters'];distance=(values['x']-Decimal(p['x']))**2+(values['y']-Decimal(p['y']))**2;threshold=((values['diameter']+Decimal(p['diameter']))/2)**2
            if distance<threshold:raise DomainError('基础孔“'+previous['name']+'”与“'+h['name']+'”重叠或相互包含，不能重复扣减')
            pn=previous['_asts'];margin_ast=op('subtract',op('add',square(op('subtract',asts['x'],pn['x'])),square(op('subtract',asts['y'],pn['y']))),square(op('divide',op('add',asts['diameter'],pn['diameter']),{'constant':'2'})))
            checks.append(dict(left=previous['name'],right=h['name'],distance_squared=str(distance),radius_sum_squared=str(threshold),margin=str(distance-threshold),state='tangent' if distance==threshold else 'disjoint',formula_ast=margin_ast))
        amount=sum((Decimal(PI)/4*values['diameter']*values['diameter']*Decimal(v['normalized_parameters']['height']) for v in layers),Decimal(0));node=op('add',*[ns[-1] for ns in layer_nodes]);deduction+=amount;nodes.append(node)
        rows.append({**h,'index':i,'unit':c['unit'],'normalized_parameters':{k:str(n) for k,n in values.items()},'through_height':str(height),'single_volume':str(amount),'formula_ast':node,'height_ast':height_ast,'containments':containments,'_asts':asts})
    for h in rows:h.pop('_asts')
    for j,v in enumerate(layers):
        net=Decimal(v['single_level_volume'])-layer_deductions[j]
        if net<=0:raise DomainError('贯通孔扣减后每层净体积须大于零')
        if tiers is not None:
            v.update(single_opening_volume=str(layer_deductions[j]),net_level_volume=str(net));v['formula_asts'].update(single_opening_volume=op('add',*layer_nodes[j]),net_level_volume=op('subtract',v['formula_asts']['single_level_volume'],op('add',*layer_nodes[j])))
    return gross-deduction,op('subtract',gross_ast,op('add',*nodes)),dict(single_gross_volume=str(gross),single_opening_volume=str(deduction),opening_holes=rows,opening_checks=checks,opening_formula_asts=dict(single_gross_volume=gross_ast,single_opening_volume=op('add',*nodes))),deduction
