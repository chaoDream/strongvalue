"""Stored two-center geometry, strict containment and full-net overlap witnesses."""
from .foundation_eccentric_windows import METHOD

TITLES=('基础偏心内圆窗口参数','基础偏心内圆严格包含','基础偏心内圆分段积分','基础偏心内圆零接触校验','基础偏心内圆防重校验','基础偏心内圆逐边距离')
LABELS=dict(x='外圆心X',y='外圆心Y',outer_diameter='外径',inner_x='内圆心X',inner_y='内圆心Y',inner_diameter='内径')


def tables(snapshot):
    rows=[(t,d) for t in snapshot['takeoffs'] if t.get('horizontal_formwork_summary') for d in t['contact_ranges'] if d.get('eccentric_circular_window')]
    if not rows:return []
    result=[(TITLES[0],['模板 / 范围','外圆心XY / 外径（m）','内圆心XY / 内径（m）','外圆 / W / 窗口净区；真实毛 / 实扣 / 净（m²）','逐项原件及内圆排除'],[
        [t['name']+' / C'+str(d['index'])+' '+d['name'],d['window_circular_hole']['x']+'，'+d['window_circular_hole']['y']+' / '+d['window_circular_hole']['outer_diameter'],
         d['window_circular_hole']['inner_x']+'，'+d['window_circular_hole']['inner_y']+' / '+d['window_circular_hole']['inner_diameter'],
         d['window_outer_area']+' / '+d['window_exclusion_area']+' / '+d['window_area']+'；'+d['single_gross_area']+' / '+d['single_opening_area']+' / '+d['single_net_area'],
         '；'.join(LABELS[k]+'='+str(v)+' '+d['plan_range']['unit']+'；原件：'+d['plan_range']['evidence'][k] for k,v in d['plan_range']['parameters'].items())+'；W：'+d['plan_range']['exclusion_basis']+'；原件：'+d['plan_range']['exclusion_evidence_id']+'；两个圆心均相对基础共同原点，非相对偏移；W不创建实体孔。'] for t,d in rows]),
        (TITLES[1],['模板 / 范围','两圆心距平方（m²，精确有理数）','半径差平方（m²，精确有理数）','严格包含口径'],[
            [t['name']+' / C'+str(d['index']),d['window_inner_containment']['center_distance_squared'],d['window_inner_containment']['radius_gap_squared'],
             '内径为正且小于外径；圆心距平方严格小于半径差平方，内圆完整严格在外圆内，相切／越界拒绝；有序AST见JSON，原件仍须独立复核。'] for t,d in rows]),
        (TITLES[2],['模板 / 范围 / 对象','X起止（m）','有效下界 / 上界','分段面积（m²）','解析规则'],[
            [t['name']+' / C'+str(d['index'])+' / '+label,q['x_from']+'至'+q['x_to'],q['lower_boundary']+' / '+q['upper_boundary'],q['area'],m['integration_rule']+'；200位工作／80位保留；完整AST见JSON']
            for t,d in rows for label,m in [('净区真实毛交集',d['polygon_gross_trace']),('直接净接触',d['polygon_net_trace'])]+[('实际孔H'+str(h['hole_index']),h.get('polygon_cut_trace',h['curved_intersection'])) for h in d['apertures']] for q in m['terms']] or [['—','—','—','0','无正面积分段']])]
    proofs=[(t,d,d['polygon_zero_contact']) for t,d in rows if d.get('polygon_zero_contact')]
    if proofs:result.append((TITLES[3],['模板 / 范围','X起止 / 中点（m）','外圆候选下界 / 上界（m）','内圆W及实际孔H覆盖（m）','严格零接触证明'],[
        [t['name']+' / C'+str(d['index']),q['x_from']+'至'+q['x_to']+'；中点'+q['x_mid'],q['included']['lower']+' / '+q['included']['upper'],
         '；'.join(v['profile']+' '+v['lower_boundary']+'至'+v['upper_boundary']+'：'+v['lower']+' / '+v['upper'] for v in q['excluded']),
         m['method']+'；本段剩余'+str(len(q['remaining']))+'段；H'+str(m['canonical_hole_index'])+'=净区毛−其他实际孔扣；W不计作实体孔扣。'] for t,d,m in proofs for q in m['net_trace']['partitions']]))
    checks=[(t,c) for t in snapshot['takeoffs'] for c in t.get('horizontal_window_checks',[]) if c.get('method')==METHOD]
    if not checks:return result
    def disk(v):return '('+v[0]+','+v[1]+') / 半径'+v[2]
    result.append((TITLES[4],['模板 / 左右窗口 / 偏心窗口侧','外圆心与半径 / 内圆心与半径（m，精确有理数）','另一窗口或连通净块','距离平方及界（m²，精确有理数）','完整净区防重依据'],[
        [t['name']+' / C'+str(c['left_index'])+' / C'+str(c['right_index'])+' / '+('左' if c['window_side']=='left' else '右'),disk(c['outer'])+'；W '+disk(c['inner']),
         ('外圆 '+disk(c['other_outer'])+'；内圆 '+disk(c['other_inner']) if c['pair_kind']=='disks' else '直边净块B'+str(q['block_index'])+'；到内圆心最远外顶点('+q['farthest_x']+','+q['farthest_y']+')'),
         (c['distance_squared']+' '+c['operator']+' '+c['limit_squared'] if c['pair_kind']=='disks' else '到外圆心最小'+q['minimum_outer_squared']+'；外界'+q['outer_limit_squared']+'；到内圆心最大'+q['maximum_inner_squared']+'；内界'+q['inner_limit_squared']),
         ('边界接触或共享' if c['state']=='boundary_touch' else '分离')+'；'+({'outer_separation':'两外圆分离或外切','other_in_inner':'另一外圆完全在本窗口内圆内或与其共享／接触','self_in_other_inner':'本窗口外圆完全在另一内圆内或与其共享／接触'}[c['branch']] if c['pair_kind']=='disks' else ('本净块完全在外圆外或仅接触' if q['branch']=='outside_outer' else '本净块完全在内圆内或仅接触'))+'；两个圆心分别使用，不拼接不同净块极值；精确有理数无容差，未用内圆可另项。']
        for t,c in checks for q in ([c] if c['pair_kind']=='disks' else c['components'])]))
    polygons=[(t,c,q) for t,c in checks for q in c['components']]
    if polygons:result.append((TITLES[5],['模板 / 窗口对 / 净块 / 边','有限边段端点（m，精确有理数）','到外圆心最近点 / 边参数','距离平方（m²，精确有理数）','净块极值口径'],[
        [t['name']+' / C'+str(c['left_index'])+' / C'+str(c['right_index'])+' / B'+str(q['block_index'])+' / '+('外轮廓' if w['ring_index']==0 else 'W'+str(w['ring_index']))+'边'+str(w['edge_index']),
         '('+w['x_from']+','+w['y_from']+')至('+w['x_to']+','+w['y_to']+')','('+w['nearest_x']+','+w['nearest_y']+') / '+w['segment_parameter'],w['distance_squared'],
         ('外圆心在本净块内，最小距为0' if q['center_in_net'] else '到外圆心最小距取全部外／内边段最短距离')+'；到内圆心最大距取本块外轮廓顶点；两圆心不能混用，结论见防重表。'] for t,c,q in polygons for w in q['edge_witnesses']]))
    return result
