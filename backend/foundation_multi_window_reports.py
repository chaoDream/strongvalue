"""Stored multi-block window coordinates, quantities and exact witnesses."""
TITLES=('基础多块窗口顶点','基础多块窗口净区','基础多块窗口分段积分','基础多块窗口拓扑校验','基础多块窗口零接触校验','基础多块窗口防重分段')


def tables(snapshot):
    rows=[(t,d) for t in snapshot['takeoffs'] if t.get('horizontal_formwork_summary') for d in t['contact_ranges'] if d.get('multi_polygon_window')]
    if not rows:return []
    rings=[]
    for t,d in rows:
        for part in d['window_parts']:
            configs=[part['plan_range']]+part['plan_range'].get('holes',[])
            normalized=part.get('window_rings',[dict(vertices=part['window_vertices'])])
            for i,(config,ring) in enumerate(zip(configs,normalized)):
                rings.append((t,d,part,i,config,ring))
    result=[
        (TITLES[0],['模板 / 范围 / 净块 / 轮廓','顶点 / 原始XY及单位','规范XY（m）','逐坐标原件 / 本块或内孔说明及原件'],[
            [t['name']+' / C'+str(d['index'])+' / B'+str(p['index'])+' '+p['name']+' / '+('外轮廓' if ri==0 else 'W'+str(ri)+' '+c['name']),
             'P'+str(i)+' / '+str(c['parameters']['x'+str(i)])+'，'+str(c['parameters']['y'+str(i)])+' '+d['plan_range']['unit'],
             v['x']+' / '+v['y'],c['evidence']['x'+str(i)]+' / '+c['evidence']['y'+str(i)]+'；'+(c['measurement_basis']+'；'+c['evidence_id'] if ri else p['measurement_basis']+'；'+p['evidence_id'])]
            for t,d,p,ri,c,ring in rings for i,v in enumerate(ring['vertices'],1)]),
        (TITLES[1],['模板 / 范围 / 净块','外轮廓 / 窗口内孔 / 窗口净区（m²）','每基础实际毛 / 孔扣 / 净（m²）','依据及数量口径'],[
            [t['name']+' / C'+str(d['index'])+' / B'+str(p['index'])+' '+p['name'],p.get('window_outer_area',p['window_area'])+' / '+p.get('window_exclusion_area','0')+' / '+p['window_area'],
             p['single_gross_area']+' / '+p['single_opening_area']+' / '+p['single_net_area'],p['measurement_basis']+'；原件：'+p['evidence_id']+'；全部'+d['total_count']+'个基础在逐块累计后继承一次，逐块不作显示位数修约；块间空区不计，内孔W不创建实体孔H。'] for t,d in rows for p in d['window_parts']]),
        (TITLES[2],['模板 / 范围 / 对象 / 净块','X起止（m）/ 窗口片段','有效下界 / 上界','分段面积（m²）','计算口径'],[
            [t['name']+' / C'+str(d['index'])+' / '+label+' / B'+str(q['block_index']),q['x_from']+'至'+q['x_to']+'；片段'+str(q['window_region_index']),
             q['lower_boundary']+' / '+q['upper_boundary'],q['area'],m['integration_rule']+'；完整AST见JSON']
            for t,d in rows for label,m in [('实际毛交集',d['polygon_gross_trace']),('直接净接触',d['polygon_net_trace'])]+[('实际孔H'+str(h['hole_index']),h.get('polygon_cut_trace',h['curved_intersection'])) for h in d['apertures']] for q in m['terms']] or [['—','—','—','0','全部净块无正面积分段']]),
        (TITLES[3],['模板 / 范围','校验对象','精确拓扑关系','计量边界'],[
            [t['name']+' / C'+str(d['index']),c['left']+' / '+c['right'],'外轮廓严格分离','不同净块不得接触、交叠或包含；嵌套岛区不在本模式内，不能填实块间空区'] for t,d in rows for c in d['window_part_checks']]+[
            [t['name']+' / C'+str(d['index'])+' / B'+str(p['index']),c['left']+' / '+c['right'],'严格在内' if c['relation']=='strictly_inside' else '相互分离','窗口内孔仅排除所属净块支模，不创建基础实体孔'] for t,d in rows for p in d['window_parts'] for c in p.get('window_ring_checks',[])])
    ]
    proofs=[(t,d,d['polygon_zero_contact']) for t,d in rows if d.get('polygon_zero_contact')]
    if proofs:result.append((TITLES[4],['模板 / 范围 / 净块','X起止 / 中点（m）/ 窗口片段','毛接触下界 / 上界（m）','覆盖轮廓及中点上下界（m）','严格零接触证明'],[
        [t['name']+' / C'+str(d['index'])+' / B'+str(q['block_index']),q['x_from']+'至'+q['x_to']+'；中点'+q['x_mid']+'；片段'+str(q['window_region_index']),q['included']['lower']+' / '+q['included']['upper'],
         '；'.join(v['profile']+'；'+v['lower_boundary']+'至'+v['upper_boundary']+'：'+v['lower']+' / '+v['upper'] for v in q['excluded']),m['method']+'；当前片段剩余'+str(len(q['remaining']))+'段；H'+str(m['canonical_hole_index'])+'=毛−其他孔扣；直接净='+d['polygon_direct_net_area']] for t,d,m in proofs for q in m['net_trace']['partitions']]))
    checks=[(t,c) for t in snapshot['takeoffs'] for c in t.get('horizontal_window_checks',[]) if c.get('method')=='multi_polygon_vertical_sweep']
    if checks:result.append((TITLES[5],['模板 / 窗口对','X起止 / 中点（m，精确有理数）','左窗口内部Y片段（m）','右窗口内部Y片段（m）','防重结论'],[
        [t['name']+' / C'+str(c['left_index'])+' / C'+str(c['right_index']),q['x_from']+'至'+q['x_to']+'；'+q['x_mid'],'；'.join(lo+'至'+hi for lo,hi in q['left']),'；'.join(lo+'至'+hi for lo,hi in q['right']),
         ('共享边界' if c['state']=='boundary_touch' else '分离')+'；完整窗口净区无正重叠，块间空区及窗口内孔空区不计；不按实体外部或实体孔内投影豁免'] for t,c in checks for q in (c['partitions'] or [dict(x_from='—',x_to='—',x_mid='无共同X内部区间',left=[],right=[])])]))
    return result
