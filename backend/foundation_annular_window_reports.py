"""Stored annulus parameters, inner exclusions, analytic strips and radial witnesses."""
from .foundation_annular_windows import METHOD

TITLES=('基础圆环窗口参数','基础圆环窗口分段积分','基础圆环窗口零接触校验','基础圆环窗口防重校验','基础圆环窗口逐边距离')


def tables(snapshot):
    rows=[(t,d) for t in snapshot['takeoffs'] if t.get('horizontal_formwork_summary') for d in t['contact_ranges'] if d.get('annular_window')]
    if not rows:return []
    result=[(TITLES[0],['模板 / 范围','圆心XY / 外径 / 内径（m）','外圆 / 内圆W / 圆环净区（m²）','真实毛 / 实体孔扣 / 净接触（m²）','原始尺寸及原件 / 内圆排除依据'],[
        [t['name']+' / C'+str(d['index'])+' '+d['name'],d['window_annulus']['x']+'，'+d['window_annulus']['y']+' / '+d['window_annulus']['outer_diameter']+' / '+d['window_annulus']['inner_diameter'],
         d['window_outer_area']+' / '+d['window_exclusion_area']+' / '+d['window_area'],d['single_gross_area']+' / '+d['single_opening_area']+' / '+d['single_net_area'],
         '；'.join(k+'='+str(v)+' '+d['plan_range']['unit']+'；原件：'+d['plan_range']['evidence'][k] for k,v in d['plan_range']['parameters'].items())+'；内圆W：'+d['plan_range']['exclusion_basis']+'；原件：'+d['plan_range']['exclusion_evidence_id']+'；W只排除本窗支模，不创建实体孔H；全部'+d['total_count']+'基础继承一次。'] for t,d in rows]),
        (TITLES[1],['模板 / 范围 / 对象','X起止（m）','有效下界 / 上界','分段面积（m²）','解析口径'],[
            [t['name']+' / C'+str(d['index'])+' / '+label,q['x_from']+'至'+q['x_to'],q['lower_boundary']+' / '+q['upper_boundary'],q['area'],m['integration_rule']+'；200位工作／80位保留；完整AST见JSON']
            for t,d in rows for label,m in [('环带真实毛交集',d['polygon_gross_trace']),('直接净接触',d['polygon_net_trace'])]+[('实际孔H'+str(h['hole_index']),h.get('polygon_cut_trace',h['curved_intersection'])) for h in d['apertures']] for q in m['terms']] or [['—','—','—','0','无正面积分段']])]
    proofs=[(t,d,d['polygon_zero_contact']) for t,d in rows if d.get('polygon_zero_contact')]
    if proofs:result.append((TITLES[2],['模板 / 范围','X起止 / 中点（m）','外圆候选下界 / 上界（m）','内圆W及实际孔H覆盖（m）','严格零接触证明'],[
        [t['name']+' / C'+str(d['index']),q['x_from']+'至'+q['x_to']+'；中点'+q['x_mid'],q['included']['lower']+' / '+q['included']['upper'],
         '；'.join(v['profile']+' '+v['lower_boundary']+'至'+v['upper_boundary']+'：'+v['lower']+' / '+v['upper'] for v in q['excluded']),m['method']+'；本段剩余'+str(len(q['remaining']))+'段；实际孔H'+str(m['canonical_hole_index'])+'=环带毛−其他实际孔扣；W不计入实体孔扣。'] for t,d,m in proofs for q in m['net_trace']['partitions']]))
    checks=[(t,c) for t in snapshot['takeoffs'] for c in t.get('horizontal_window_checks',[]) if c.get('method')==METHOD]
    if not checks:return result
    result.append((TITLES[3],['模板 / 左右窗口 / 圆环侧','圆心XY / 外半径R / 内半径r（m，精确有理数）','校验对象','距离平方与界（m²，精确有理数）','防重依据'],[
        [t['name']+' / C'+str(c['left_index'])+' / C'+str(c['right_index'])+' / '+('左' if c['annulus_side']=='left' else '右'),c['center_x']+'，'+c['center_y']+' / '+c['outer_radius']+' / '+c['inner_radius'],
         ('另一圆／圆环：'+c['other_x']+'，'+c['other_y']+'；外半径'+c['other_outer_radius']+'；内半径'+c['other_inner_radius'] if c['pair_kind']=='radial' else '直边净块B'+str(q['block_index'])+'；最远外顶点('+q['farthest_x']+','+q['farthest_y']+')'),
         ('圆心距平方'+c['distance_squared']+'；分离界'+c['limit_squared'] if c['pair_kind']=='radial' else '最小'+q['minimum_squared']+'／最大'+q['maximum_squared']+'；外界'+q['outer_limit_squared']+'／内界'+q['inner_limit_squared']),
         ('边界接触或共享' if c['state']=='boundary_touch' else '分离')+'；'+({'outer_separation':'圆心距平方≥两外半径和平方','other_in_inner':'另一外圆完全在本窗内圆内或内切','self_in_other_inner':'本窗外圆完全在另一内圆内或内切'}[c['branch']] if c['pair_kind']=='radial' else ('本净块最小距平方≥外半径平方' if q['branch']=='outside_outer' else '本净块最大距平方≤内半径平方'))+'；逐连通净块判定，不能将不同块的最小／最大距离拼接；无容差，内圆空区可另项。']
        for t,c in checks for q in ([c] if c['pair_kind']=='radial' else c['components'])]))
    polygon_checks=[(t,c,q) for t,c in checks for q in c['components']]
    if polygon_checks:result.append((TITLES[4],['模板 / 窗口对 / 净块 / 轮廓边','有限边段端点（m，精确有理数）','最近点 / 边参数0至1','最短距离平方（m²，精确有理数）','最小及最大距离口径'],[
        [t['name']+' / C'+str(c['left_index'])+' / C'+str(c['right_index'])+' / B'+str(q['block_index'])+' / '+('外轮廓' if w['ring_index']==0 else 'W'+str(w['ring_index']))+'边'+str(w['edge_index']),
         '('+w['x_from']+','+w['y_from']+')至('+w['x_to']+','+w['y_to']+')','('+w['nearest_x']+','+w['nearest_y']+') / '+w['segment_parameter'],w['distance_squared'],
         ('圆心在本净块内，最小距为0' if q['center_in_net'] else '圆心不在本净块内部，最小距取全部外／内边段最短距离')+'；最大距取本块外轮廓顶点，内孔不填实；平方界及结论见防重表。'] for t,c,q in polygon_checks for w in q['edge_witnesses']]))
    return result
