"""Stored mixed hierarchy, original dimensions and complete block-pair witnesses."""
from .foundation_mixed_islands import METHOD
TITLES=('基础混合岛区净块','基础混合岛区尺寸原件','基础混合岛区严格拓扑','基础混合岛区分段积分','基础混合岛区整体覆盖','基础混合岛区防重总览','基础混合岛区防重距离','基础混合岛区防重逐边','基础混合岛区防重分段')


def tables(snapshot):
    rows=[(t,d) for t in snapshot['takeoffs'] if t.get('horizontal_formwork_summary') for d in t['contact_ranges'] if d.get('nested_mixed_window')]
    if not rows:return []
    def label(t,d,p=None):return t['name']+' / C'+str(d['index'])+(' / B'+str(p['index'])+' '+p['name'] if p else '')
    result=[(TITLES[0],['模板 / 范围 / 净块 / 类型 / 平面层级','最近父内孔','设计外轮廓 / W / 净区（m²）','每基础毛 / 实体孔扣 / 净（m²）','支模与关系原件及数量口径'],[
        [label(t,d,p)+' / '+('圆形' if p['block_shape']=='circular' else '直边')+' / '+str(p['nesting_depth'])+'级',('B'+str(p['parent']['block_index'])+' W'+str(p['parent']['hole_index']) if p['parent'] else '根块'),p.get('window_outer_area',p['window_area'])+' / '+p.get('window_exclusion_area','0')+' / '+p['window_area'],
         p['single_gross_area']+' / '+p['single_opening_area']+' / '+p['single_net_area'],p['measurement_basis']+'；原件：'+p['evidence_id']+'；全部'+d['total_count']+'基础在逐块累计后只继承一次；平面层级不代表Z或数量，W只排所属块，岛区另计，其余空区不填实。'] for t,d in rows for p in d['window_parts']])]
    dims=[];own=[]
    for t,d in rows:
        for p in d['window_parts']:
            cfg=p['plan_range'];configs=[cfg]+cfg.get('holes',[])
            normalized=[p['window_outer_circle']]+[h['normalized_parameters'] for h in p['window_circular_holes']] if p['block_shape']=='circular' else [r['vertices'] for r in p['window_rings']] if p.get('window_rings') else [p['window_vertices']]
            for hi,(c,n) in enumerate(zip(configs,normalized)):
                norm='；'.join(k+'='+str(v) for k,v in n.items()) if isinstance(n,dict) else '；'.join('P'+str(i)+'('+v['x']+','+v['y']+')' for i,v in enumerate(n,1))
                dims.append([label(t,d,p)+' / '+('W'+str(hi)+' '+c['name'] if hi else '外轮廓'),'；'.join(k+'='+str(v)+' '+cfg['unit']+'；原件：'+c['evidence'][k] for k,v in c['parameters'].items()),norm+' m',('不支模：'+c['measurement_basis']+'；原件：'+c['evidence_id'] if hi else '实际支模及父关系：'+p['measurement_basis']+'；原件：'+p['evidence_id'])+'；共同基准：'+cfg['coordinate_basis']+'；原件：'+cfg['coordinate_evidence_id']])
            for h in p.get('window_circular_holes',[]):
                q=h['containment'];own.append([label(t,d,p)+' / W'+str(h['index']),'本块内圆严格在外圆内',q['center_distance_squared']+' < '+q['radius_gap_squared']+' m²','内圆半径严格小于外圆；尺寸及AST见JSON，不按容差放行'])
            for q in p.get('window_hole_checks',[]):own.append([label(t,d,p)+' / W'+str(q['left_index'])+' / W'+str(q['right_index']),'本块内圆严格分离',q['center_distance_squared']+' > '+q['radius_sum_squared']+' m²','不接触、交叠或包含；完整AST见JSON'])
            for q in p.get('window_ring_checks',[]):own.append([label(t,d,p)+' / '+q['left']+' / '+q['right'],'本块直边内孔严格在内／分离',q['relation'],'真实边段无接触或交叉，顶点内外关系已核对；逐顶点原件及规范轮廓见尺寸表和JSON'])
    result.append((TITLES[1],['模板 / 范围 / 净块 / 轮廓','原始尺寸及单位 / 逐项原件','规范圆心直径或有序顶点（m）','用途及共同基准原件'],dims))
    topology=[]
    for t,d in rows:
        for q in d['window_part_checks']:
            name=label(t,d)+' / B'+str(q['left_index'])+(' W'+str(q['parent_hole_index']) if q['parent_hole_index'] else '')+' / B'+str(q['right_index']);p=q['proof'];state='严格包含，最近父另核；可含祖先见证' if q['relation']=='inside_window_hole' else '外轮廓严格分离'
            reason=('圆心严格在直边内部' if p.get('center_state')==1 else '圆心严格在直边外部' if p.get('center_state')==-1 else '')+('；全部外顶点严格在父孔内' if p.get('vertex_states') else '')+('；'+str(p['edge_pairs_checked'])+'对有限边段无交点' if p.get('no_boundary_intersection') else '')
            topology.append([name,state,reason or '核对以下逐项严格不等式','源轮廓与内外点关系、全部有序距离AST见JSON；不能用外包框或圆周抽样代替真实边界'])
            for v in p['conditions']:
                w=v.get('edge_witness');topology.append([name,v['label'],v['value']+' '+v['operator']+' '+v['limit']+' '+v['unit'],('有限边段参数'+w['segment_parameter']+'；最近点('+w['nearest_x']+','+w['nearest_y']+')m；' if w else '')+'精确有理数，无容差；公式与原始尺寸依赖见JSON'])
    result.append((TITLES[2],['模板 / 范围 / 拓扑对象','严格关系或校验项','精确数值及比较界','几何证明与独立复核'],topology+own))
    result.append((TITLES[3],['模板 / 范围 / 对象 / 块 / 平面层级','X起止（m）','有效下界 / 上界','分段面积（m²）','解析口径'],[
        [label(t,d)+' / '+name+' / B'+str(q['block_index'])+' / '+str(q['nesting_depth'])+'级',q['x_from']+'至'+q['x_to'],q['lower_boundary']+' / '+q['upper_boundary'],q['area'],m['integration_rule']+'；工作200位、保留80位；完整AST见JSON']
        for t,d in rows for name,m in [('真实毛交集',d['polygon_gross_trace']),('直接净接触',d['polygon_net_trace'])]+[('实际孔H'+str(h['hole_index']),h.get('polygon_cut_trace',h['curved_intersection'])) for h in d['apertures']] for q in m['terms']] or [['—','—','—','0','无正面积分段']]))
    proofs=[(t,d,d['polygon_zero_contact']) for t,d in rows if d.get('polygon_zero_contact')]
    if proofs:result.append((TITLES[4],['模板 / 范围 / 净块 / 平面层级','X起止 / 中点（m）','外轮廓候选下界 / 上界（m）','所属W、上层及实体孔H覆盖（m）','整体严格零接触'],[
        [label(t,d)+' / B'+str(q['block_index'])+' / '+str(q['nesting_depth'])+'级',q['x_from']+'至'+q['x_to']+'；中点'+q['x_mid'],q['included']['lower']+' / '+q['included']['upper'],
         '；'.join(v['profile']+' '+v['lower_boundary']+'至'+v['upper_boundary']+'：'+v['lower']+' / '+v['upper'] for v in q['excluded']),m['method']+'；本段剩余'+str(len(q['remaining']))+'段；整体H'+str(m['canonical_hole_index'])+'=全部块毛−其他实际孔扣，W不记作H。'] for t,d,m in proofs for q in m['net_trace']['partitions']]))
    checks=[(t,c,q) for t in snapshot['takeoffs'] for c in t.get('horizontal_window_checks',[]) if c.get('method')==METHOD for q in c['pairs']]
    if not checks:return result
    def pair_label(t,c,q):return t['name']+' / C'+str(c['left_index'])+(' B'+str(q['left_block_index']) if q['left_block_index'] else ' 旧窗口')+' / C'+str(c['right_index'])+(' B'+str(q['right_block_index']) if q['right_block_index'] else ' 旧窗口')
    def location(p):
        parts=[]
        for k,n in [('outer','本窗外圆'),('inner','本窗内圆'),('other_outer','另一外圆'),('other_inner','另一内圆')]:
            if k in p:parts.append(n+'('+','.join(p[k][:2])+') 半径'+p[k][2]+'m')
        for k,n in [('holes','本窗W'),('other_holes','另一W')]:
            for i,h in enumerate(p.get(k,[]),1):parts.append(n+str(i)+'('+','.join(h[:2])+') 半径'+h[2]+'m')
        if 'center_x' in p:parts.append('圆心('+p['center_x']+','+p['center_y']+')m；半径'+p.get('radius',p.get('outer_radius','—'))+'；内半径'+p.get('inner_radius','无')+'m')
        return '；'.join(parts) or '直边完整净区见分段表'
    branch={'outer_separation':'两外圆分离／外切','other_in_window_hole':'另一完整外圆在本窗某W内','window_in_other_hole':'本窗完整外圆在另一W内','other_in_inner':'另一完整外圆在本窗内圆中','self_in_other_inner':'本窗完整外圆在另一内圆中','outside_outer':'本连通净块在外圆外','inside_window_hole':'本连通净块完整在单个W内','inside_inner':'本连通净块完整在内圆中'}
    overview=[];distances=[];witnesses=[];scans=[]
    for t,c,q in checks:
        p=q['proof'];name=pair_label(t,c,q);state='边界接触或共享' if p['state']=='boundary_touch' else '分离';side=p.get('window_side',p.get('circle_side',p.get('annulus_side')))
        context=location(p)+('；圆形所在'+('左' if side=='left' else '右')+'侧' if side else '')
        overview.append([name,state,context,'本块对完整净区无正重叠；全部块对通过才放行，父孔未用空区可另项，岛区不能豁免重复；全部见证见JSON'])
        if 'distance_squared' in p:distances.append([name,context,p['distance_squared']+' '+p.get('operator','>=')+' '+p['limit_squared']+' m²',branch.get(p.get('branch'),'圆圆外切／分离')+('；采用W'+str(p['hole_index']) if p.get('hole_index') else '')])
        components=p.get('components',[])
        for comp in components:
            minima=comp.get('minimum_outer_squared',comp.get('minimum_squared'));maxima=comp.get('maximum_inner_squared',comp.get('maximum_squared'));text='最小距平方'+str(minima)+'；外界'+comp['outer_limit_squared']
            if maxima is not None:text+='；最大距平方'+str(maxima)+'；内界'+comp['inner_limit_squared']
            text+='；'+'；'.join('W'+str(v['hole_index'])+'最大距平方'+v['maximum_squared']+'；内界'+v['limit_squared']+'；最远顶点('+v['farthest_x']+','+v['farthest_y']+')m' for v in comp.get('inner_maxima',[]))
            distances.append([name+' / 连通净块'+str(comp['block_index']),context,text+' m²',branch.get(comp.get('branch'),'精确圆心及距离判定')+('；采用W'+str(comp['hole_index']) if comp.get('hole_index') else '')+'；不拼接不同块／孔极值'])
        for comp in components or ([p] if p.get('edge_witnesses') else []):
            for w in comp.get('edge_witnesses',[]):witnesses.append([name+' / 净块'+str(comp.get('block_index','—'))+' / 轮廓'+str(w['ring_index'])+' 边'+str(w['edge_index']),context,'('+w['x_from']+','+w['y_from']+')至('+w['x_to']+','+w['y_to']+')m','('+w['nearest_x']+','+w['nearest_y']+')m；边参数'+w['segment_parameter'],w['distance_squared']+' m²；取有限边段，不是无限直线'])
        for v in p.get('partitions',[]):scans.append([name,v['x_from']+'至'+v['x_to']+'；中点'+v['x_mid'],'；'.join(lo+'至'+hi for lo,hi in v['left']),'；'.join(lo+'至'+hi for lo,hi in v['right']),state+'；完整直边净区竖向有理数扫描，内部无正重叠；不按实体外部或实体孔投影豁免'])
    result.append((TITLES[5],['模板 / 左右范围及净块','状态','比较轮廓及所在侧（m，精确有理数）','完整净区结论'],overview))
    if distances:result.append((TITLES[6],['模板 / 范围 / 块对','圆形及各W（m，精确有理数）','距离平方与比较界（m²，精确有理数）','采用的几何分离依据'],distances))
    if witnesses:result.append((TITLES[7],['模板 / 范围 / 块对 / 边','圆形及各W（m）','有限边段端点（m）','最近点及边参数','距离平方及复核'],witnesses))
    if scans:result.append((TITLES[8],['模板 / 范围 / 块对','X起止 / 中点（m，精确有理数）','左净区Y片段（m）','右净区Y片段（m）','分段防重结论'],scans))
    return result
