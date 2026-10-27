"""Stored circular-hole sources, topology, quantities and exact whole-window proofs."""
from .foundation_circular_holes_windows import METHOD

TITLES=('基础多内圆窗口参数','基础多内圆严格拓扑','基础多内圆分段积分','基础多内圆零接触校验','基础多内圆防重校验','基础多内圆逐边距离')


def tables(snapshot):
    rows=[(t,d) for t in snapshot['takeoffs'] if t.get('horizontal_formwork_summary') for d in t['contact_ranges'] if d.get('circular_holes_window')]
    if not rows:return []
    result=[(TITLES[0],['模板 / 范围 / 轮廓','圆心XY / 实际直径（m）','设计面积（m²）','原始尺寸及逐项原件','用途及实际接触口径'],[
        [t['name']+' / C'+str(d['index'])+' '+d['name']+' / '+('外圆' if p is None else 'W'+str(p['index'])+' '+p['name']),
         n['x']+'，'+n['y']+' / '+n['diameter'],d['window_outer_area'] if p is None else p['area'],
         '；'.join({'x':'圆心X','y':'圆心Y','diameter':'实际直径'}[k]+'='+str(v)+' '+d['plan_range']['unit']+'；原件：'+cfg['evidence'][k] for k,v in cfg['parameters'].items()),
         ('外圆−全部W='+d['window_outer_area']+'−'+d['window_exclusion_area']+'='+d['window_area']+'m²；每基础真实毛／孔扣／净='+d['single_gross_area']+' / '+d['single_opening_area']+' / '+d['single_net_area']+'；全部'+d['total_count']+'基础只继承一次。' if p is None else cfg['measurement_basis']+'；原件：'+cfg['evidence_id']+'；W仅从本窗口支模排除，不创建实体孔H、不扣混凝土或生成孔壁／孔端；所有圆心以基础共同原点填写。')]
        for t,d in rows for p,cfg,n in [(None,d['plan_range'],d['window_outer_circle'])]+[(p,d['plan_range']['holes'][p['index']-1],p['normalized_parameters']) for p in d['window_circular_holes']]]),
        (TITLES[1],['模板 / 范围 / 校验对象','圆心距平方（m²，精确有理数）','比较界（m²，精确有理数）','严格拓扑关系及复核'],[
            [t['name']+' / C'+str(d['index'])+' / W'+str(p['index'])+' 对外圆',p['containment']['center_distance_squared'],p['containment']['radius_gap_squared'],
             '内圆半径小于外圆；圆心距平方严格小于半径差平方，内圆严格在外圆内；相切或越界拒绝，完整AST见JSON。'] for t,d in rows for p in d['window_circular_holes']]+[
            [t['name']+' / C'+str(d['index'])+' / W'+str(q['left_index'])+' 对W'+str(q['right_index']),q['center_distance_squared'],q['radius_sum_squared'],
             '圆心距平方严格大于半径和平方，两内圆严格分离，接触、交叠或包含拒绝；完整AST见JSON，原件用途仍须独立复核。'] for t,d in rows for q in d['window_hole_checks']]),
        (TITLES[2],['模板 / 范围 / 对象','X起止（m）','有效下界 / 上界','分段面积（m²）','解析口径'],[
            [t['name']+' / C'+str(d['index'])+' / '+label,q['x_from']+'至'+q['x_to'],q['lower_boundary']+' / '+q['upper_boundary'],q['area'],m['integration_rule']+'；200位工作／80位保留；完整AST见JSON']
            for t,d in rows for label,m in [('净区真实毛交集',d['polygon_gross_trace']),('直接净接触',d['polygon_net_trace'])]+[('实际孔H'+str(h['hole_index']),h.get('polygon_cut_trace',h['curved_intersection'])) for h in d['apertures']] for q in m['terms']] or [['—','—','—','0','无正面积分段']])]
    proofs=[(t,d,d['polygon_zero_contact']) for t,d in rows if d.get('polygon_zero_contact')]
    if proofs:result.append((TITLES[3],['模板 / 范围','X起止 / 中点（m）','外圆候选下界 / 上界（m）','窗口内圆W及实体孔H覆盖（m）','严格零接触证明'],[
        [t['name']+' / C'+str(d['index']),q['x_from']+'至'+q['x_to']+'；中点'+q['x_mid'],q['included']['lower']+' / '+q['included']['upper'],
         '；'.join(v['profile']+' '+v['lower_boundary']+'至'+v['upper_boundary']+'：'+v['lower']+' / '+v['upper'] for v in q['excluded']),
         m['method']+'；本段剩余'+str(len(q['remaining']))+'段；H'+str(m['canonical_hole_index'])+'=净区毛−其他实际孔扣；所有W不记作实体孔扣减。'] for t,d,m in proofs for q in m['net_trace']['partitions']]))
    checks=[(t,c) for t in snapshot['takeoffs'] for c in t.get('horizontal_window_checks',[]) if c.get('method')==METHOD]
    if not checks:return result
    def disk(v):return '('+v[0]+','+v[1]+') / 半径'+v[2]
    def holes(v):return '；'.join('W'+str(i)+' '+disk(h) for i,h in enumerate(v,1)) or '无内圆'
    result.append((TITLES[4],['模板 / 左右窗口 / 多内圆侧','本窗外圆及各W（m，精确有理数）','另一窗口或连通净块','距离平方与界（m²，精确有理数）','完整净区防重依据'],[
        [t['name']+' / C'+str(c['left_index'])+' / C'+str(c['right_index'])+' / '+('左' if c['window_side']=='left' else '右'),disk(c['outer'])+'；'+holes(c['holes']),
         ('外圆 '+disk(c['other_outer'])+'；'+holes(c['other_holes']) if c['pair_kind']=='disks' else '直边净块B'+str(q['block_index'])),
         (c['distance_squared']+' '+c['operator']+' '+c['limit_squared'] if c['pair_kind']=='disks' else '到外圆心最小'+q['minimum_outer_squared']+'；外界'+q['outer_limit_squared']+'；'+'；'.join('到W'+str(m['hole_index'])+'最大'+m['maximum_squared']+'／内界'+m['limit_squared']+'；最远顶点('+m['farthest_x']+','+m['farthest_y']+')' for m in q['inner_maxima'])),
         ('边界接触或共享' if c['state']=='boundary_touch' else '分离')+'；'+(('两外圆分离或外切' if c['branch']=='outer_separation' else '另一完整外圆在本窗W'+str(c['hole_index'])+'内' if c['branch']=='other_in_window_hole' else '本窗完整外圆在另一W'+str(c['hole_index'])+'内') if c['pair_kind']=='disks' else '本块完全在外圆外' if q['branch']=='outside_outer' else '本块完整在本窗W'+str(q['hole_index'])+'内')+'；每个W严格内部且互相分离，不拼接不同净块／不同孔极值；完整窗口精确有理数判定，无容差。']
        for t,c in checks for q in ([c] if c['pair_kind']=='disks' else c['components'])]))
    polys=[(t,c,q) for t,c in checks for q in c['components']]
    if polys:result.append((TITLES[5],['模板 / 窗口对 / 净块 / 边','有限边段端点（m，精确有理数）','到外圆心最近点 / 边参数','距离平方（m²，精确有理数）','净块极值口径'],[
        [t['name']+' / C'+str(c['left_index'])+' / C'+str(c['right_index'])+' / B'+str(q['block_index'])+' / '+('外轮廓' if w['ring_index']==0 else 'W'+str(w['ring_index']))+'边'+str(w['edge_index']),
         '('+w['x_from']+','+w['y_from']+')至('+w['x_to']+','+w['y_to']+')','('+w['nearest_x']+','+w['nearest_y']+') / '+w['segment_parameter'],w['distance_squared'],
         ('外圆心在本净块内，最小距为0' if q['center_in_net'] else '到外圆心最小距取全部外／内有限边段')+'；到每个窗口W的最大距离各取本净块外轮廓顶点，详见防重表，不能用不同块或不同W拼出一条包含证明。'] for t,c,q in polys for w in q['edge_witnesses']]))
    return result
