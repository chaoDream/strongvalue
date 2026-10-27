"""Stored circular geometry, analytic strips and exact distance witnesses."""
from .foundation_circular_windows import METHOD

TITLES=('基础圆形窗口参数','基础圆形窗口分段积分','基础圆形窗口零接触校验','基础圆形窗口防重校验')


def tables(snapshot):
    rows=[(t,d) for t in snapshot['takeoffs'] if t.get('horizontal_formwork_summary') for d in t['contact_ranges'] if d.get('circular_window')]
    if not rows:return []
    result=[(TITLES[0],['模板 / 范围','圆心XY / 实际直径（m）','窗口 / 真实毛 / 实扣 / 净（m²）','圆心直径原件及口径'],[
        [t['name']+' / C'+str(d['index'])+' '+d['name'],d['window_circle']['x']+'，'+d['window_circle']['y']+' / '+d['window_circle']['diameter'],
         d['window_area']+' / '+d['single_gross_area']+' / '+d['single_opening_area']+' / '+d['single_net_area'],
         '；'.join(k+'='+str(v)+' '+d['plan_range']['unit']+'；原件：'+d['plan_range']['evidence'][k] for k,v in d['plan_range']['parameters'].items())+'；'+d['window_intersection_rule']+'；全部'+d['total_count']+'个基础只继承一次；圆窗不创建基础实体孔。'] for t,d in rows]),
        (TITLES[1],['模板 / 范围 / 对象','X起止（m）','有效下界 / 上界','分段面积（m²）','解析口径'],[
            [t['name']+' / C'+str(d['index'])+' / '+label,q['x_from']+'至'+q['x_to'],q['lower_boundary']+' / '+q['upper_boundary'],q['area'],m['integration_rule']+'；200位工作／80位输出；完整AST见JSON']
            for t,d in rows for label,m in [('实际毛交集',d['polygon_gross_trace']),('直接净接触',d['polygon_net_trace'])]+[('实际孔H'+str(h['hole_index']),h.get('polygon_cut_trace',h['curved_intersection'])) for h in d['apertures']] for q in m['terms']] or [['—','—','—','0','无正面积分段']])]
    proofs=[(t,d,d['polygon_zero_contact']) for t,d in rows if d.get('polygon_zero_contact')]
    if proofs:result.append((TITLES[2],['模板 / 范围','X起止 / 中点（m）','毛接触下界 / 上界（m）','实际孔覆盖上下界（m）','严格零接触证明'],[
        [t['name']+' / C'+str(d['index']),q['x_from']+'至'+q['x_to']+'；中点'+q['x_mid'],q['included']['lower']+' / '+q['included']['upper'],
         '；'.join(v['profile']+' '+v['lower_boundary']+'至'+v['upper_boundary']+'：'+v['lower']+' / '+v['upper'] for v in q['excluded']),
         m['method']+'；本区间剩余'+str(len(q['remaining']))+'段；H'+str(m['canonical_hole_index'])+'=毛−其他孔扣；直接净='+d['polygon_direct_net_area']] for t,d,m in proofs for q in m['net_trace']['partitions']]))
    checks=[(t,c) for t in snapshot['takeoffs'] for c in t.get('horizontal_window_checks',[]) if c.get('method')==METHOD]
    if checks:result.append((TITLES[3],['模板 / 左右窗口 / 圆窗所在侧','圆心XY / 半径（m，精确有理数）','校验对象','距离平方 / 比较界（m²，精确有理数）','防重依据'],[
        [t['name']+' / C'+str(c['left_index'])+' / C'+str(c['right_index'])+' / '+('左' if c['circle_side']=='left' else '右'),c['center_x']+'，'+c['center_y']+' / '+c['radius'],
         ('另一圆心'+c['other_x']+'，'+c['other_y']+'；半径'+c['other_radius'] if c['pair_kind']=='circle_circle' else '直边净区轮廓'+str(q['ring_index'])+'边'+str(q['edge_index'])+'；端点('+q['x_from']+','+q['y_from']+')至('+q['x_to']+','+q['y_to']+')；最近点('+q['nearest_x']+','+q['nearest_y']+')；边参数'+q['segment_parameter']),
         q['distance_squared']+' / '+q['limit_squared'],('边界外切' if c['state']=='boundary_touch' else '分离')+'；'+('圆心距平方≥半径和平方' if c['pair_kind']=='circle_circle' else '圆心不在直边净区内部，全部边最短距离平方≥圆半径平方；孔与岛区按完整净区奇偶关系检查')+'；严格有理数比较，无容差，不按实体或孔投影豁免。']
        for t,c in checks for q in ([c] if c['pair_kind']=='circle_circle' else c['edge_witnesses'])]))
    return result
