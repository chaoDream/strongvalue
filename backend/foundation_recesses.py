"""Finite vertical circular void intervals, clipped to each actual foundation tier."""
from decimal import Decimal
from .compound_takeoff import PI,op,text
from .engine import DomainError
from .planar_takeoff import linear
from .measurement_units import LENGTH,VERSION as UNIT_VERSION

VERSION='railing-post-foundation-1.4'
NOTICE='每根已审最终立柱对应一个独立基础，来源重复仅乘一次。孔位采用各层共同中心局部XY，Z=0为本基础实体底面且向上；所有坐标及实际直径均须有原件依据，不从柱脚或锚栓推断。未填有限标高的孔贯穿全部基础；有限标高圆孔只在明确起止Z内扣减，逐层取正相交高度，仅校验实际相交层的轮廓。孔不得越出基础总高、反向、零高或越出相交层；有正高度交叠时圆截面不得正面积重叠或相互包含，标高相离或仅端面接触不重复扣减。相切及端面接触仅为几何判定，非结构净距或施工验收。台阶须同心同轴紧接、上层不外伸；每项所有基础采用相同孔表，各层同一材料，层数及孔数不增加基础数。逐孔逐层不修约，只对最终净体积修约。未列孔、斜孔、非圆凹槽、重叠孔并集、预埋实体、基础交叠、地基承载及定位、垫层土方钢筋模板另核，不重复既有计量。'
Z_FIELDS=('z_start','z_end')


def enabled(c):
    return isinstance(c,dict) and isinstance(c.get('holes'),list) and any(isinstance(h,dict) and 'vertical_range' in h for h in c['holes'])


def validate_config(c):
    from .foundation_openings import validate_config as legacy_validate
    if c.get('scope_confirmed') is not True:raise DomainError('须据原件确认各孔贯通或有限标高范围、基础底面Z=0向上、实际孔径及共同局部XY，不重复扣减')
    clean={**c,'holes':[{k:v for k,v in h.items() if k!='vertical_range'} if isinstance(h,dict) else h for h in c['holes']]}
    legacy_validate(clean)
    for h in c['holes']:
        if 'vertical_range' not in h:continue
        v=h['vertical_range']
        if not isinstance(v,dict) or set(v)!={'parameters','evidence'} or not isinstance(v['parameters'],dict) or set(v['parameters'])!=set(Z_FIELDS) or not isinstance(v['evidence'],dict) or set(v['evidence'])!=set(Z_FIELDS):raise DomainError('有限标高圆孔须明确起止Z及逐参数原件；Z以基础底面为零向上，单位同孔位')
        for k in Z_FIELDS:text(v['evidence'][k],'有限标高圆孔原始依据',200)


def apply(d,gross,gross_ast,tiers,parameters,key,dimensions,refs,conversions,profile=None):
    c=d['through_holes'];(profile.validate_config if profile else validate_config)(c);refs[key+'.holes.coordinate']=text(c['coordinate_evidence_id'],'基础孔位坐标原件',200)
    circular=d.get('shape') in ('circular','stepped_circular')
    layers=tiers if tiers is not None else [dict(index=1,normalized_parameters=parameters,single_level_volume=str(gross),formula_asts=dict(single_level_volume=gross_ast))]
    zero={'constant':'0'};z=Decimal(0);z_ast=zero;bounds=[]
    for layer in layers:
        lp=key+'.levels.'+str(layer['index']) if tiers is not None else key
        height=Decimal(layer['normalized_parameters']['height']);node=op('multiply',{'dimension':lp+'.height'},{'constant':LENGTH[d['length_unit']]});top=z+height;top_ast=op('add',z_ast,node)
        bounds.append((z,top,z_ast,top_ast,lp));z,z_ast=top,top_ast
    rows=[];checks=[];nodes=[];deduction=Decimal(0);layer_amounts=[Decimal(0) for _ in layers];layer_nodes=[[] for _ in layers]
    def square(n):return op('multiply',n,n)
    for index,h in enumerate(c['holes'],1):
        prefix=key+'.holes.'+str(index);values={};asts={}
        def parameter(k,raw,positive=False):
            n=linear(raw,c['unit'],positive);dimensions[prefix+'.'+k]=str(raw);node=op('multiply',{'dimension':prefix+'.'+k},{'constant':LENGTH[c['unit']]})
            conversions.append(dict(parameter=prefix+'.'+k,original_value=str(raw),source_unit=c['unit'],factor=LENGTH[c['unit']],canonical_value=str(n),canonical_unit='m',rule_version=UNIT_VERSION));return n,node
        for k in (profile.fields(h) if profile else ('x','y','diameter')):values[k],asts[k]=parameter(k,h['parameters'][k],k not in ('x','y'))
        for k,eid in h['evidence'].items():refs[prefix+'.'+k]=text(eid,'基础逐孔原件',200)
        start,end=Decimal(0),z;start_ast,end_ast=zero,z_ast
        if 'vertical_range' in h:
            v=h['vertical_range'];start,start_ast=parameter('z_start',v['parameters']['z_start']);end,end_ast=parameter('z_end',v['parameters']['z_end'],True)
            for k in Z_FIELDS:refs[prefix+'.'+k]=text(v['evidence'][k],'有限标高圆孔原件',200)
            if start<0 or start>=end or end>z:raise DomainError('基础孔“'+h['name']+'”起止Z须满足0≤起始Z<终止Z≤基础总高')
        containments=[];segments=[];hole_nodes=[];amount=Decimal(0)
        for j,layer in enumerate(layers):
            bottom,top,bottom_ast,top_ast,lp=bounds[j];lo=max(start,bottom);hi=min(end,top)
            if hi<=lo:continue
            lo_ast=start_ast if start>=bottom else bottom_ast;hi_ast=end_ast if end<=top else top_ast;depth=hi-lo;depth_ast=op('subtract',hi_ast,lo_ast)
            p={k:Decimal(v) for k,v in layer['normalized_parameters'].items()}
            def dim(k):return op('multiply',{'dimension':lp+'.'+k},{'constant':LENGTH[d['length_unit']]})
            if profile:
                margins,margin_asts=profile.containment(h,values,asts,p,dim,circular)
                if any(Decimal(v)<0 for v in margins.values()):raise DomainError('基础孔“'+h['name']+'”越出实际相交第'+str(layer['index'])+'层轮廓')
            elif circular:
                margin=((p['diameter']-values['diameter'])/2)**2-values['x']**2-values['y']**2
                if values['diameter']>=p['diameter'] or margin<0:raise DomainError('基础孔“'+h['name']+'”越出实际相交第'+str(layer['index'])+'层圆形轮廓或覆盖整层')
                margins={'radial_squared':str(margin)};margin_asts={'radial_squared':op('subtract',square(op('divide',op('subtract',dim('diameter'),asts['diameter']),{'constant':'2'})),op('add',square(asts['x']),square(asts['y'])))}
            else:
                margins={};margin_asts={}
                for axis,side in [('x','length'),('y','width')]:
                    margin=(p[side]-values['diameter'])/2-abs(values[axis])
                    if margin<0:raise DomainError('基础孔“'+h['name']+'”越出实际相交第'+str(layer['index'])+'层矩形轮廓')
                    margins[axis]=str(margin);center=asts[axis] if values[axis]>=0 else op('subtract',zero,asts[axis]);margin_asts[axis]=op('subtract',op('divide',op('subtract',dim(side),asts['diameter']),{'constant':'2'}),center)
            if not profile:node=op('multiply',op('divide',{'constant':PI},{'constant':'4'}),asts['diameter'],asts['diameter'],depth_ast);part=Decimal(PI)/4*values['diameter']*values['diameter']*depth
            if profile:
                cross,area_ast=profile.area(h,values,asts);node=op('multiply',area_ast,depth_ast);part=cross*depth
            layer_amounts[j]+=part;layer_nodes[j].append(node);hole_nodes.append(node);amount+=part
            containments.append(dict(level_index=layer['index'],margins=margins,state='tangent' if any(Decimal(v)==0 for v in margins.values()) else 'inside',formula_asts=margin_asts))
            segments.append(dict(level_index=layer['index'],z_start=str(lo),z_end=str(hi),height=str(depth),single_volume=str(part),formula_asts=dict(z_start=lo_ast,z_end=hi_ast,height=depth_ast,single_volume=node)))
        for previous in rows:
            pn=previous['_asts'];ps,pe=Decimal(previous['z_start']),Decimal(previous['z_end']);lo=max(start,ps);hi=min(end,pe);gap=lo-hi
            if profile:details=profile.pair(h,values,asts,previous);margin=Decimal(details['margin'])
            else:
                p=previous['normalized_parameters'];distance=(values['x']-Decimal(p['x']))**2+(values['y']-Decimal(p['y']))**2;threshold=((values['diameter']+Decimal(p['diameter']))/2)**2;margin=distance-threshold
                margin_ast=op('subtract',op('add',square(op('subtract',asts['x'],pn['x'])),square(op('subtract',asts['y'],pn['y']))),square(op('divide',op('add',asts['diameter'],pn['diameter']),{'constant':'2'})))
                details=dict(distance_squared=str(distance),radius_sum_squared=str(threshold),margin=str(margin),formula_ast=margin_ast)
            if gap<0 and margin<0:raise DomainError('基础孔“'+previous['name']+'”与“'+h['name']+'”在正相交高度内重叠或相互包含，不能重复扣减')
            gap_ast=op('subtract',start_ast if start>=ps else pn['z_start'],end_ast if end<=pe else pn['z_end'])
            checks.append(dict(left=previous['name'],right=h['name'],**details,vertical_gap=str(gap),vertical_gap_ast=gap_ast,state='vertically_disjoint' if gap>0 else 'vertical_tangent' if gap==0 else 'tangent' if margin==0 else 'disjoint'))
        node=op('add',*hole_nodes);nodes.append(node);deduction+=amount
        rows.append({**h,'index':index,'unit':c['unit'],'normalized_parameters':{k:str(v) for k,v in values.items()},'z_start':str(start),'z_end':str(end),'through_height':str(end-start),'single_volume':str(amount),'formula_ast':node,'height_ast':op('subtract',end_ast,start_ast),'vertical_formula_asts':dict(z_start=start_ast,z_end=end_ast),'segments':segments,'containments':containments,'_asts':{**asts,'z_start':start_ast,'z_end':end_ast}})
    for row in rows:row.pop('_asts')
    for j,layer in enumerate(layers):
        net=Decimal(layer['single_level_volume'])-layer_amounts[j]
        if net<=0:raise DomainError('圆孔扣减后每层净体积须大于零')
        if tiers is not None:layer.update(single_opening_volume=str(layer_amounts[j]),net_level_volume=str(net));layer['formula_asts'].update(single_opening_volume=op('add',*layer_nodes[j]),net_level_volume=op('subtract',layer['formula_asts']['single_level_volume'],op('add',*layer_nodes[j])))
    return gross-deduction,op('subtract',gross_ast,op('add',*nodes)),dict(single_gross_volume=str(gross),single_opening_volume=str(deduction),opening_holes=rows,opening_checks=checks,opening_formula_asts=dict(single_gross_volume=gross_ast,single_opening_volume=op('add',*nodes))),deduction
