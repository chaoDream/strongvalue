"""Stored circular hierarchy and per-block source-bound geometry and pair proofs."""
from .foundation_circular_islands import METHOD
from .foundation_circular_holes_reports import tables as circle_tables,TITLES as CIRCLE_TITLES
TITLES=('基础圆岛净块与父关系','基础圆岛严格父子拓扑','基础圆岛整体零接触')+tuple(t.replace('多内圆','圆岛') for t in CIRCLE_TITLES)


def tables(snapshot):
    rows=[(t,d) for t in snapshot['takeoffs'] if t.get('horizontal_formwork_summary') for d in t['contact_ranges'] if d.get('nested_circular_window')]
    if not rows:return []
    result=[(TITLES[0],['模板 / 范围 / 净块 / 平面层级','最近父块内圆','设计外圆 / W / 净区（m²）','每基础真实毛 / H扣 / 净（m²）','支模及父关系原件 / 数量口径'],[
        [t['name']+' / C'+str(d['index'])+' / B'+str(p['index'])+' '+p['name']+' / '+str(p['nesting_depth'])+'级',('B'+str(p['parent']['block_index'])+' W'+str(p['parent']['hole_index']) if p['parent'] else '根块'),
         p['window_outer_area']+' / '+p['window_exclusion_area']+' / '+p['window_area'],p['single_gross_area']+' / '+p['single_opening_area']+' / '+p['single_net_area'],p['measurement_basis']+'；原件：'+p['evidence_id']+'；平面层级不代表Z或数量，全部'+d['total_count']+'基础在逐块累计后继承一次，W仅从所属块排除，岛区另计，其余空区不计。'] for t,d in rows for p in d['window_parts']]),
        (TITLES[1],['模板 / 范围 / 块间关系','比较圆心距平方（m²，精确有理数）','严格比较界（m²，精确有理数）','几何关系与最近父核定'],[
            [t['name']+' / C'+str(d['index'])+' / B'+str(q['left_index'])+(' W'+str(q['parent_hole_index']) if q['parent_hole_index'] else '')+' / B'+str(q['right_index']),q['center_distance_squared'],q['operator']+' '+q['limit_squared'],
             ('子块外圆严格在该祖先块内圆中；圆心距平方小于半径差平方；最近父块必须另与净块表核对，不能跳层。' if q['relation']=='inside_window_hole' else '两块外圆严格分离；圆心距平方大于半径和平方，相接或交叠拒绝。')+'完整AST和逐尺寸原件见JSON。'] for t,d in rows for q in d['window_part_checks']])]
    proofs=[(t,d,d['polygon_zero_contact']) for t,d in rows if d.get('polygon_zero_contact')]
    if proofs:result.append((TITLES[2],['模板 / 范围 / 净块 / 平面层级','X起止 / 中点（m）','外圆候选下界 / 上界（m）','所属W及实际孔H覆盖（m）','整体严格零接触'],[
        [t['name']+' / C'+str(d['index'])+' / B'+str(q['block_index'])+' / '+str(q['nesting_depth'])+'级',q['x_from']+'至'+q['x_to']+'；中点'+q['x_mid'],q['included']['lower']+' / '+q['included']['upper'],
         '；'.join(v['profile']+' '+v['lower_boundary']+'至'+v['upper_boundary']+'：'+v['lower']+' / '+v['upper'] for v in q['excluded']),m['method']+'；本段剩余'+str(len(q['remaining']))+'段；整体H'+str(m['canonical_hole_index'])+'=全部块毛−其他实际孔扣，W不记作H。'] for t,d,m in proofs for q in m['net_trace']['partitions']]))
    flat=[]
    for t,d in rows:
        for p in d['window_parts']:
            f={k:v for k,v in d.items() if k!='polygon_zero_contact'};f.update(p);f.update(index=d['index'],name=d['name'])
            flat.append({**t,'name':t['name']+' / B'+str(p['index'])+' '+p['name'],'contact_ranges':[f],'horizontal_window_checks':[]})
    for t in snapshot['takeoffs']:
        for c in t.get('horizontal_window_checks',[]):
            if c.get('method')!=METHOD:continue
            for q in c['pairs']:
                p={**q['proof'],'left_index':c['left_index'],'right_index':c['right_index']}
                label=' / '+('左B'+str(q['left_block_index']) if q['left_block_index'] else '左旧窗口')+' / '+('右B'+str(q['right_block_index']) if q['right_block_index'] else '右旧窗口')
                flat.append({**t,'name':t['name']+label,'contact_ranges':[],'horizontal_window_checks':[p]})
    result.extend((title.replace('多内圆','圆岛'),headers,data) for title,headers,data in circle_tables({'takeoffs':flat}))
    return result
