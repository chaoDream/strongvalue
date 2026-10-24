"""Circular exposed ledges clipped to evidence-defined rectangular windows."""
from decimal import Decimal
from .engine import DomainError
from .compound_takeoff import op
from .foundation_formwork import layers
from .foundation_void_formwork import dim
from .foundation_hole_end_formwork import details
from . import foundation_step_formwork as steps
from . import foundation_step_windows as rectangular
from . import foundation_horizontal_windows as windows
from . import disk_window_intersection as disk_window
from .round_window_coverage import certificate

VERSION='foundation-horizontal-formwork-1.6'
NOTICE=rectangular.NOTICE.replace('矩形台阶平台','矩形或圆形台阶平台').replace('矩形平台毛面积','平台毛面积').replace('圆形台阶平台与孔底孔顶仍限既有整面，局部圆平台/孔端','孔底孔顶仍限既有整面，局部孔端')+' 圆平台上层与窗口先按圆矩形解析裁切；圆孔共同覆盖为两圆与窗口交集，按圆支撑、水平边交点及两圆交点分段，逐段选有效上下边界作圆弧解析积分，保留端点、边界、面积与公式，不用网格或容差。矩形孔先与窗口相交再与上层圆裁切。'
ZERO={'constant':'0'}
TWO={'constant':'2'}


def rectangle_common(hp,ha,up,ua,uv,un,values,asts):
    coords={}; nodes={}
    for i,(axis,side) in enumerate((('x','length'),('y','width'))):
        half=hp[side]/2; hn=op('divide',ha[side],TWO)
        coords[axis+'_start'],nodes[axis+'_start']=max([(values[2*i],asts[axis+'_start']),(hp[axis]-half,op('subtract',ha[axis],hn))],key=lambda v:v[0])
        coords[axis+'_end'],nodes[axis+'_end']=min([(values[2*i+1],asts[axis+'_end']),(hp[axis]+half,op('add',ha[axis],hn))],key=lambda v:v[0])
    if any(coords[a+'_start']>=coords[a+'_end'] for a in ('x','y')):return Decimal(0),ZERO,'矩形孔与窗口无正面积交集'
    p={};a={};rp={**up};ra={**ua}
    for axis,side in (('x','length'),('y','width')):
        p[side]=coords[axis+'_end']-coords[axis+'_start'];a[side]=op('subtract',nodes[axis+'_end'],nodes[axis+'_start'])
        rp[axis]=-(coords[axis+'_start']+coords[axis+'_end'])/2;ra[axis]=op('subtract',ZERO,op('divide',op('add',nodes[axis+'_start'],nodes[axis+'_end']),TWO))
    return steps.intersection(rp,ra,p,a,uv,un,p['length']*p['width'],op('multiply',a['length'],a['width']))


def geometry(d,item,tier,original,full_ast,holes,key,dimensions,refs,conversions):
    up,ua,uv,un=steps.section(item,layers(item)[d['level_index']]);lp,la,lv,ln=steps.section(item,tier)
    raw_keys=('hole_index','name','shape','parameters','unit','normalized_parameters','from_z','to_z')
    raw=[{**{k:h[k] for k in raw_keys},'single_area':h['full_area'],'formula_ast':h['formula_asts']['full_area']} for h in holes]
    lower,lower_node,clipped,extra,asts=windows.geometry(d,item,tier,lv,ln,raw,key,dimensions,refs,conversions)
    upper,upper_node,_,upper_extra,_=windows.geometry(d,item,layers(item)[d['level_index']],uv,un,[],key,{},{},[])
    gross=lower-upper
    if gross<0:raise DomainError('圆平台窗口上层覆盖超过下层交集，请核对来源精度')
    values=windows.bounds(d);x1,x2,y1,y2=values;R=up['diameter']/2
    upper_clip=(upper,upper_node,'上层与窗口交集',*([upper_extra['window_curved_intersection']] if 'window_curved_intersection' in upper_extra else []))
    for h,whole in zip(clipped,holes):
        hp={k:Decimal(v) for k,v in h['normalized_parameters'].items()};ha={k:dim(item,{'index':h['hole_index'],'unit':h['unit']},k) for k in hp}
        inside=Decimal(h['single_area']);inside_node=h['formula_ast'];window_curve=h.pop('curved_intersection',None)
        hole_clip=(inside,inside_node,'孔与窗口交集',*([window_curve] if window_curve else []))
        if not inside or not upper:result=(Decimal(0),ZERO,'孔或上层与窗口无正面积交集')
        elif 'diameter' not in hp:result=rectangle_common(hp,ha,up,ua,uv,un,values,asts)
        else:
            r=hp['diameter']/2;dist2=hp['x']**2+hp['y']**2
            if dist2>=(r+R)**2:result=(Decimal(0),ZERO,'两圆分离或外切')
            elif (R>=r and dist2<=(R-r)**2) or max(abs(x1),abs(x2))**2+max(abs(y1),abs(y2))**2<=R*R:result=hole_clip
            elif (r>=R and dist2<=(r-R)**2) or max(abs(x1-hp['x']),abs(x2-hp['x']))**2+max(abs(y1-hp['y']),abs(y2-hp['y']))**2<=r*r:result=upper_clip
            elif x1<=max(-R,hp['x']-r) and x2>=min(R,hp['x']+r) and y1<=max(-R,hp['y']-r) and y2>=min(R,hp['y']+r):
                result=(Decimal(whole['covered_area']),whole['formula_asts']['covered_area'],whole['intersection_rule'],*([whole['curved_intersection']] if 'curved_intersection' in whole else []))
            else:result=disk_window.calculate(hp,ha,up,ua,values,asts,hole_clip,upper_clip)
        covered,node,branch=result[:3];net=inside-covered
        coverage=certificate(lp,la,up,ua,hp,ha,values,asts)
        net_node=op('subtract',inside_node,node)
        if coverage is not None:
            net=gross;net_node=op('subtract',lower_node,upper_node);h['exposed_coverage']=coverage;branch+='；外露平台全部位于本孔内，按同一毛面积扣减'
        if net<0:raise DomainError('圆平台窗口共同覆盖超过窗口内孔交集，当前精度不能确认，请另核')
        h.update(window_hole_area=str(inside),covered_area=str(covered),single_area=str(net),formula_ast=net_node,intersection_rule='（孔∩窗口）−（孔∩上层圆∩窗口）；'+branch,
                 formula_asts={**h['formula_asts'],'window_hole_area':inside_node,'covered_area':node,'single_area':net_node})
        if window_curve:h['window_hole_curved_intersection']=window_curve
        if len(result)==4:h['curved_intersection']=details(result[3],'上层与窗口共同覆盖')
    extra.update(full_face_area=str(original),window_lower_area=str(lower),window_upper_area=str(upper),round_step_window=True,window_intersection_rule='（下层圆∩窗口）−（上层完整名义圆∩窗口）')
    if 'window_curved_intersection' in upper_extra:extra['window_upper_curved_intersection']=upper_extra['window_curved_intersection']
    asts.update(full_face_area=full_ast,window_lower_area=lower_node,window_upper_area=upper_node)
    return gross,op('subtract',lower_node,upper_node),clipped,extra,asts
