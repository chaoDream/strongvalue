"""Export stored end-face geometry and deductions without recomputing areas."""
TITLES = ('基础顶底模板面积汇总','基础顶底逐面接触净量','基础顶底实际孔扣减','基础顶底接触范围原件','基础顶底模板做法与规则')


def legacy_tables(snapshot):
    ts = [t for t in snapshot['takeoffs'] if t.get('horizontal_formwork_summary')]
    if not ts: return []
    rows = [(t,d) for t in ts for d in t['contact_ranges']]
    return [
        (TITLES[0], ['模板 / 来源','选入整端面数','全部毛 / 孔扣减（m²）','未修约 / 修约净量（m²）','接触范围及修约原件'],
         [[t['name']+' / '+t['horizontal_formwork_summary']['source_name']+' V'+str(t['horizontal_formwork_summary']['source_revision']),str(t['horizontal_formwork_summary']['range_count']),t['gross']+' / '+t['deduction'],t['horizontal_formwork_summary']['unrounded_quantity']+' / '+t['quantity'],t['measurement_basis']+'；原件：'+t['source_evidence']['horizontal_formwork.basis']+'；'+t['rounding_rule']+'；差额'+t['horizontal_formwork_summary']['rounding_adjustment']+'m²；修约原件：'+t['source_evidence']['horizontal_formwork.rounding']] for t in ts]),
        (TITLES[1], ['模板 / 范围 / 来源端层','整体端面 / Z（m）','端层真实尺寸（m）','每基础毛 / 孔 / 净（m²）','全部个数 / 净面积（m²）'],
         [[t['name']+' / C'+str(d['index'])+' '+d['name']+' / M'+str(d['material_index'])+' L'+str(d['level_index']),d['face_label']+' / '+d['z'],'；'.join(k+'='+v for k,v in d['normalized_parameters'].items()),d['single_gross_area']+' / '+d['single_opening_area']+' / '+d['single_net_area'],d['total_count']+' / '+d['total_net_area']] for t,d in rows]),
        (TITLES[2], ['模板 / 端面 / 孔','孔原始尺寸及单位','孔底 / 顶Z（m）','每基础实际孔面积（m²）','端面扣减口径与孔原件'],
         [[t['name']+' / C'+str(d['index'])+' '+d['face_label']+' / H'+str(h['hole_index'])+' '+h['name'],'；'.join(k+'='+str(v) for k,v in h['parameters'].items())+' '+h['unit'],h['from_z']+' / '+h['to_z'],h['single_area'],('起Z<顶面Z≤止Z' if d['face']=='top' else '起Z≤底面Z<止Z')+'；物理孔全截面扣减，体积阈值不填实；'+ '；'.join(k+'：'+v for k,v in t['source_evidence'].items() if k.startswith('foundation.'+str(d['material_index'])+'.holes.'+str(h['hole_index'])+'.'))] for t,d in rows for h in d['apertures']] or [['—','—','—','0','选入端面无贯通孔；未到本端面的盲孔不扣']]),
        (TITLES[3], ['模板 / 整端面范围','实际接触说明及原件','标高及尺寸来源'],
         [[t['name']+' / C'+str(d['index'])+' '+d['name'],d['measurement_basis']+'；原件：'+d['evidence']['measurement_basis'],'尺寸、Z及孔直接来自已审基础V'+str(t['horizontal_formwork_summary']['source_revision'])+'；来源校验：'+t['source_component_hash']+'；不接受手填面积或重复数量'] for t,d in rows]),
        (TITLES[4], ['模板','材料做法、原件及计量规则'],
         [[t['name'],t['material']+'；做法原件：'+t['source_evidence']['horizontal_formwork.material']+'；'+t['notice']+'；'+t['rule_version']+('；π='+t['horizontal_formwork_pi_constant'] if 'horizontal_formwork_pi_constant' in t else '')] for t in ts])
    ]


STEP_TITLES = ('基础台阶平台截面差','基础台阶平台孔交集')
CURVED_TITLE = '基础平台曲线交集过程'


def step_tables(snapshot):
    result = legacy_tables(snapshot)
    rows = [(t,d) for t in snapshot['takeoffs'] if t.get('horizontal_formwork_summary') for d in t['contact_ranges'] if d['face']=='step']
    if not rows: return result
    # Keep the five existing sheets and stored numbers; broaden their labels only in ledge exports.
    result[0][1][1] = '选入水平整面数'
    result[1][1][1:3] = ['接触水平面 / Z（m）','所取下层真实尺寸（m）']
    result[2][1][3:5] = ['每基础本面孔扣减（m²）','本面扣减口径与孔原件']
    for row in result[2][2]:
        if '台阶中间整个平台' in row[0]:
            row[4] = row[4].replace('起Z≤底面Z<止Z；物理孔全截面扣减，体积阈值不填实','起Z<平台Z≤止Z；仅扣外露平台内孔面积，详见平台孔交集；体积阈值不填实')
    result.extend([
        (STEP_TITLES[0], ['模板 / 平台','下层 / 上层 / Z（m）','下层 / 上层尺寸（m）','单基础下层 / 上层 / 平台毛面积（m²）'],
         [[t['name']+' / C'+str(d['index'])+' '+d['name'],'L'+str(d['level_index'])+' / L'+str(d['upper_level_index'])+' / '+d['z'],'下层：'+'；'.join(k+'='+v for k,v in d['normalized_parameters'].items())+'；上层：'+'；'.join(k+'='+v for k,v in d['upper_normalized_parameters'].items()),d['single_lower_area']+' / '+d['single_upper_area']+' / '+d['single_gross_area']] for t,d in rows]),
        (STEP_TITLES[1], ['模板 / 平台 / 孔','全孔 / 上层覆盖交集（m²）','单基础平台内实际孔扣减（m²）','精确交集口径'],
         [[t['name']+' / C'+str(d['index'])+' / H'+str(h['hole_index'])+' '+h['name'],h['full_area']+' / '+h['covered_area'],h['single_area'],h['intersection_rule']+'；全孔减上层覆盖交集；起Z<平台Z≤止Z；上层名义截面内孔底不计入平台'] for t,d in rows for h in d['apertures']] or [['—','0 / 0','0','所选平台下侧无到达本标高的孔；不扣未到达的盲孔或从平台起孔']])
    ])
    curves = [(t,d,h) for t,d in rows for h in d['apertures'] if h.get('curved_intersection')]
    if curves:
        labels = dict(radius='圆半径',chord_distance='有向弦距',half_chord='半弦长',minor_half_angle='小弓形半角',x='相对X',y='相对Y',x_from='区间X起',x_to='区间X止')
        result.append((CURVED_TITLE,['模板 / 平台 / 孔','解析分项','参数（长度m / 角rad）','分项有向面积（m²）','合成口径及计算精度'],[
            [t['name']+' / C'+str(d['index'])+' / H'+str(h['hole_index']),q['name'],'；'.join(label+'='+q[k] for k,label in labels.items() if k in q),q['area'],h['curved_intersection']['method']+'；工作'+str(h['curved_intersection']['working_digits'])+'位，保留'+str(h['curved_intersection']['output_digits'])+'位；固定π见模板规则；'+h['curved_intersection'].get('integration_rule','弦距负取大弓形；象限分项按右上−左上−右下＋左下组合；')+'交集='+h['covered_area']+'m²'] for t,d,h in curves for q in h['curved_intersection']['terms']]))
    return result


END_TITLES = ('基础孔端接触截面','基础孔端相接孔交集')
END_CURVED_TITLE = '基础孔端曲线交集过程'


def end_tables(snapshot):
    result = step_tables(snapshot)
    rows = [(t,d) for t in snapshot['takeoffs'] if t.get('horizontal_formwork_summary') for d in t['contact_ranges'] if d['face'] in ('hole_bottom','hole_top')]
    if not rows: return result
    result[0][1][1] = '选入水平整面数'
    result[1][1][1:3] = ['接触水平面 / Z（m）','混凝土侧层真实尺寸（m）']
    result[2][1][3:5] = ['每基础本面孔交集（m²）','本面扣减口径与孔原件']
    apertures = [(t,d,h) for t in snapshot['takeoffs'] if t.get('horizontal_formwork_summary') for d in t['contact_ranges'] for h in d['apertures']]
    for row,(t,d,h) in zip(result[2][2],apertures):
        if d['face'] in ('hole_bottom','hole_top'):
            row[4] = '扣混凝土侧相接孔与所选孔交集，详见孔端相接孔交集；'+ '；'.join(k+'：'+v for k,v in t['source_evidence'].items() if k.startswith('foundation.'+str(d['material_index'])+'.holes.'+str(h['hole_index'])+'.'))
    result.extend([
        (END_TITLES[0], ['模板 / 范围 / 孔','孔端 / Z / 混凝土侧层','所选孔原始尺寸及标高','每基础全孔 / 与侧层交集 / 相接孔扣 / 净（m²）','裁切口径及孔原件'],
         [[t['name']+' / C'+str(d['index'])+' '+d['name']+' / H'+str(d['hole_index'])+' '+d['hole_name'],d['face_label']+' / Z='+d['z']+'m / '+('下侧' if d['concrete_side']=='below' else '上侧')+'L'+str(d['level_index']),
           '；'.join(k+'='+str(v) for k,v in d['hole_parameters'].items())+' '+d['hole_unit']+'；孔Z='+d['hole_from_z']+'至'+d['hole_to_z']+'m',
           d['single_full_hole_area']+' / '+d['single_gross_area']+' / '+d['single_opening_area']+' / '+d['single_net_area'],d['hole_end_intersection_rule']+('；本次毛面积另与XY窗口相交，整孔端交集见窗口表；' if d.get('hole_end_window') else '；毛面积仅取所选孔与混凝土侧层交集；')+ '；'.join(k+'：'+v for k,v in t['source_evidence'].items() if k.startswith('foundation.'+str(d['material_index'])+'.holes.'+str(d['hole_index'])+'.'))] for t,d in rows]),
        (END_TITLES[1], ['模板 / 孔端 / 相接孔','相接孔原始尺寸及Z','每基础相接孔全截面 / 实际交集（m²）','交集及混凝土侧规则'],
         [[t['name']+' / C'+str(d['index'])+' H'+str(d['hole_index'])+' / H'+str(h['hole_index'])+' '+h['name'],'；'.join(k+'='+str(v) for k,v in h['parameters'].items())+' '+h['unit']+'；Z='+h['from_z']+'至'+h['to_z']+'m',h['full_area']+' / '+h['single_area'],h['intersection_rule']+'；'+('相接孔起Z<孔底Z≤相接孔止Z' if d['face']=='hole_bottom' else '相接孔起Z≤孔顶Z<相接孔止Z')+'；扣与所选孔交集，不扣整层或整相接孔'] for t,d in rows for h in d['apertures']] or [['—','—','0 / 0','所选孔端混凝土侧无其他孔，不另扣减']])
    ])
    curves = [(t,d,'混凝土侧层裁切',d['full_face_area'] if d.get('hole_end_window') else d['single_gross_area'],d['hole_end_curved_intersection']) for t,d in rows if d.get('hole_end_curved_intersection')]
    curves += [(t,d,'相接孔H'+str(h['hole_index']),h.get('window_direct_area',h['single_area']),h['curved_intersection']) for t,d in rows for h in d['apertures'] if h.get('curved_intersection')]
    if curves:
        labels = dict(radius='圆半径',chord_distance='有向弦距',half_chord='半弦长',minor_half_angle='小弓形半角',x='相对X',y='相对Y',x_from='区间X起',x_to='区间X止')
        result.append((END_CURVED_TITLE,['模板 / 孔端 / 裁切对象','解析分项','参数（长度m / 角rad）','分项有向面积（m²）','合成及精度'],[
            [t['name']+' / C'+str(d['index'])+' H'+str(d['hole_index'])+' / '+target,q['name'],'；'.join(label+'='+q[k] for k,label in labels.items() if k in q),q['area'],m['method']+'；工作'+str(m['working_digits'])+'位，保留'+str(m['output_digits'])+'位；固定π见模板规则；交集='+covered+'m²'] for t,d,target,covered,m in curves for q in m['terms']]))
    return result


WINDOW_TITLES=('基础水平窗口裁切','基础水平窗口孔交集','基础水平窗口坐标原件','基础水平窗口分离校验')
WINDOW_CURVED_TITLE='基础水平窗口曲线过程'


def window_tables(snapshot):
    result=end_tables(snapshot)
    ts=[t for t in snapshot['takeoffs'] if t.get('horizontal_window_checks') is not None]
    if not ts:return result
    rows=[(t,d) for t in ts for d in t['contact_ranges'] if 'plan_range' in d]
    result[0][1][1]='选入整面或窗口数'
    result[1][1][1]='水平面 / Z（m）'
    # These are the original, unsplit stored rows; use face identity, never names.
    all_holes=[(t,d,h) for t in snapshot['takeoffs'] if t.get('horizontal_formwork_summary') for d in t['contact_ranges'] for h in d['apertures']]
    for row,(t,d,h) in zip(result[2][2],all_holes):
        if 'plan_range' in d:row[4]=('仅扣所选孔与同侧相接孔及XY窗口交集；详见孔端窗口表' if d.get('hole_end_window') else '仅扣到达本端面且与XY窗口相交的实际孔截面；详见窗口孔交集')+'；体积阈值不填实物理孔'
    result.extend([
        (WINDOW_TITLES[0],['模板 / 范围 / 端面','XY窗口范围（m）','每基础全端面 / 窗口 / 实际毛交集（m²）','每基础孔扣 / 净（m²）','实际裁切口径'],[
            [t['name']+' / C'+str(d['index'])+' '+d['name']+' / '+d['face_label'],'X '+d['x_start']+'至'+d['x_end']+'；Y '+d['y_start']+'至'+d['y_end'],d['full_face_area']+' / '+d['window_area']+' / '+d['single_gross_area'],d['single_opening_area']+' / '+d['single_net_area'],d['window_intersection_rule']+('；所选孔与混凝土侧层共同裁切，同侧相接孔再与所选孔及窗口相交' if d.get('hole_end_window') else '；只取窗口与真实端层交集，孔仅按窗口内部分扣减')] for t,d in rows]),
        (WINDOW_TITLES[1],['模板 / 窗口 / 孔','孔全截面 / 与窗口交集（m²）','交集口径'],[
            [t['name']+' / C'+str(d['index'])+' / H'+str(h['hole_index'])+' '+h['name'],h['full_area']+' / '+h['single_area'],h['intersection_rule']+'；按该端面起止Z条件取孔，再与窗口裁切'] for t,d in rows for h in d['apertures']] or [['—','0 / 0','所选端面无到达本标高的孔']]),
        (WINDOW_TITLES[2],['模板 / 窗口','原始坐标及单位','逐坐标原件','共用基础XY基准及原件'],[
            [t['name']+' / C'+str(d['index'])+' '+d['name'],('多块坐标逐点见基础多块窗口顶点表' if d.get('multi_polygon_window') else '；'.join(k+'='+str(v) for k,v in d['plan_range']['parameters'].items()))+' '+d['plan_range']['unit'],('逐块及逐孔原件见基础多块窗口顶点表' if d.get('multi_polygon_window') else '；'.join(k+'：'+v for k,v in d['plan_range']['evidence'].items())),d['plan_range']['coordinate_basis']+'；原件：'+d['plan_range']['coordinate_evidence_id']+'；校验：'+d['plan_frame_hash']] for t,d in rows]),
        (WINDOW_TITLES[3],['模板 / 两窗口','X / Y有向间隔（m）','状态及重复检查口径'],[
            [t['name']+' / C'+str(c['left_index'])+' '+c['left']+' / C'+str(c['right_index'])+' '+c['right'],('两圆心精确距离校验，详见偏心内圆防重及逐边表' if c.get('method')=='eccentric_window_exact_disk_distance' else '精确有理数径向距离校验，详见圆环窗口防重及逐边距离表' if c.get('method')=='annular_window_exact_radial_distance' else '圆圆心距／逐边最短距离平方精确有理数校验，详见圆形窗口防重表' if c.get('method')=='circular_window_exact_distance' else '精确有理数竖向扫描；'+str(len(c['partitions']))+'个共同X分段，逐段内部片段无正重叠' if c.get('method') in ('simple_polygon_vertical_sweep','perforated_polygon_vertical_sweep','multi_polygon_vertical_sweep','nested_polygon_vertical_sweep') else '分离轴('+c['axis_x']+','+c['axis_y']+')；投影间隔='+c['projection_gap']+'m²（未单位化轴）' if c.get('method')=='convex_separating_axis' else c['x_gap']+' / '+c['y_gap']),('分离' if c['state']=='separated' else '共享边界')+('；按两个圆心及完整净区检查，正重叠拒绝，内圆空区可另项' if c.get('method')=='eccentric_window_exact_disk_distance' else '；逐连通净块或圆形径向范围检查圆环净区，正重叠拒绝，内外切可分项' if c.get('method')=='annular_window_exact_radial_distance' else '；圆心奇偶内外及精确距离共同检查完整净区，正重叠拒绝，外切可分项' if c.get('method')=='circular_window_exact_distance' else '；按完整窗口全部直边交点分段，凹口空区不视作重叠，正重叠或包含拒绝' if c.get('method') in ('simple_polygon_vertical_sweep','perforated_polygon_vertical_sweep','multi_polygon_vertical_sweep','nested_polygon_vertical_sweep') else '；凸窗口按全部边法向分离轴判定，正重叠拒绝' if c.get('method')=='convex_separating_axis' else '；X/Y均正交叠即拒绝，整面与窗口也不能并计')] for t in ts for c in t['horizontal_window_checks']] or [['—','—','无同端面窗口对；已检查整面与窗口不重复']])
    ])
    curves=[(t,d,'下层与窗口' if d['face']=='step' else '端层截面',d['window_lower_area'] if d['face']=='step' else d['single_gross_area'],d['window_curved_intersection']) for t,d in rows if d.get('window_curved_intersection')]
    curves += [(t,d,('上层窗口内孔H' if d['face']=='step' else '实际孔H')+str(h['hole_index']),h['covered_area'] if d['face']=='step' else h.get('window_direct_area',h['single_area']),h['curved_intersection']) for t,d in rows for h in d['apertures'] if h.get('curved_intersection')]
    curves += [(t,d,'窗口内全孔H'+str(h['hole_index']),h['window_hole_area'],h['window_hole_curved_intersection']) for t,d in rows for h in d['apertures'] if h.get('window_hole_curved_intersection')]
    curves += [(t,d,'上层与窗口',d['window_upper_area'],d['window_upper_curved_intersection']) for t,d in rows if d.get('window_upper_curved_intersection')]
    if curves:
        labels=dict(radius='圆半径',x='相对X',y='相对Y',x_from='区间X起',x_to='区间X止')
        result.append((WINDOW_CURVED_TITLE,['模板 / 窗口 / 对象','解析分项' if any(m.get('integration_rule') for *_,m in curves) else '象限分项','参数（m）','分项有向面积（m²）','合成与精度'],[
            [t['name']+' / C'+str(d['index'])+' / '+target,q['name'],'；'.join(label+'='+q[k] for k,label in labels.items() if k in q),q['area'],m['method']+'；工作200位，保留80位，固定π见规则；交集='+covered+'m²'] for t,d,target,covered,m in curves for q in m['terms']]))
    return result


STEP_WINDOW_TITLES = ('基础平台窗口截面差','基础平台窗口孔裁切')


def step_window_tables(snapshot):
    result = window_tables(snapshot)
    rows = [(t,d) for t in snapshot['takeoffs'] if t.get('horizontal_formwork_summary') for d in t['contact_ranges'] if d['face']=='step' and 'plan_range' in d]
    if not rows: return result
    by_title = {t[0]:t for t in result}
    by_title[STEP_TITLES[0]][1][3] = '单基础整下层 / 整上层 / 选入平台毛面积（m²）'
    by_title[STEP_TITLES[1]][1][1] = '所取孔截面 / 上层共同覆盖（m²）'
    all_holes = [(t,d,h) for t in snapshot['takeoffs'] if t.get('horizontal_formwork_summary') for d in t['contact_ranges'] if d['face']=='step' for h in d['apertures']]
    for row,(_,d,h) in zip(by_title[STEP_TITLES[1]][2],all_holes):
        if 'plan_range' in d:
            row[1] = h['window_hole_area']+' / '+h['covered_area']
            row[3] = h['intersection_rule']+'；起Z<平台Z≤止Z；上层名义截面内孔底不计平台；详见平台窗口孔裁切'
    by_title[WINDOW_TITLES[0]][1][2] = '每基础整端面或平台 / 窗口 / 实际毛交集（m²）'
    by_title[WINDOW_TITLES[1]][1][1] = '孔全截面 / 选入面窗口内实际扣减（m²）'
    result.extend([
        (STEP_WINDOW_TITLES[0],['模板 / 平台 / 窗口','下层∩窗口 / 上层∩窗口（m²）','窗口内平台毛 / 实际孔扣 / 净（m²）','整个平台 / 口径'],[
            [t['name']+' / C'+str(d['index'])+' '+d['name']+' / L'+str(d['level_index'])+'→L'+str(d['upper_level_index']),d['window_lower_area']+' / '+d['window_upper_area'],d['single_gross_area']+' / '+d['single_opening_area']+' / '+d['single_net_area'],d['full_face_area']+'；'+d['window_intersection_rule']] for t,d in rows]),
        (STEP_WINDOW_TITLES[1],['模板 / 平台窗口 / 孔','全孔 / 孔∩窗口（m²）','孔∩上层∩窗口 / 平台窗口实际孔扣（m²）','共同交集口径'],[
            [t['name']+' / C'+str(d['index'])+' / H'+str(h['hole_index'])+' '+h['name'],h['full_area']+' / '+h['window_hole_area'],h['covered_area']+' / '+h['single_area'],h['intersection_rule']+'；体积扣孔阈值不填实物理孔'] for t,d in rows for h in d['apertures']] or [['—','0 / 0','0 / 0','平台下侧无到达本标高的孔']])
    ])
    return result


ROUND_WINDOW_TITLE = '基础圆平台窗口分段积分'


def tables(snapshot):
    result=step_window_tables(snapshot)
    rows=[(t,d,h,h['curved_intersection']) for t in snapshot['takeoffs'] if t.get('horizontal_formwork_summary') for d in t['contact_ranges'] if d.get('round_step_window') for h in d['apertures'] if h.get('curved_intersection',{}).get('integration_rule')]
    result=hole_end_window_tables(snapshot,coverage_tables(snapshot,result))
    result=polygon_window_tables(snapshot,result)
    result=simple_window_check_tables(snapshot,polygon_window_tables(snapshot,result,simple=True))
    result=perforated_window_tables(snapshot,result)
    from .foundation_multi_window_reports import tables as multi_tables
    result.extend(multi_tables(snapshot))
    result.extend(multi_tables(snapshot,nested=True))
    from .foundation_circular_window_reports import tables as circular_tables
    result.extend(circular_tables(snapshot))
    from .foundation_annular_window_reports import tables as annular_tables
    result.extend(annular_tables(snapshot))
    from .foundation_eccentric_window_reports import tables as eccentric_tables
    result.extend(eccentric_tables(snapshot))
    if not rows:return result
    result.append((ROUND_WINDOW_TITLE,['模板 / 平台窗口 / 孔','区间 / X起止（m）','有效上边界 / 下边界','区间共同面积（m²）','共同交集及精度'],[
        [t['name']+' / C'+str(d['index'])+' / H'+str(h['hole_index']),q['name']+' / '+q['x_from']+'至'+q['x_to'],q['upper_boundary']+' / '+q['lower_boundary'],q['area'],h['covered_area']+'；'+m['integration_rule']+'；工作200位、保留80位、固定π见规则'] for t,d,h,m in rows for q in m['terms']]))
    return result


POLYGON_TITLES = ('基础凸窗口顶点','基础凸窗口分段积分','基础凸窗口零接触校验')
SIMPLE_POLYGON_TITLES = ('基础凹窗口顶点','基础凹窗口分段积分','基础凹窗口零接触校验')
SIMPLE_CHECK_TITLE = '基础凹窗口防重分段'


def polygon_window_tables(snapshot,result,simple=False,perforated=False):
    titles=PERFORATED_TITLES if perforated else SIMPLE_POLYGON_TITLES if simple else POLYGON_TITLES
    rows=[(t,d) for t in snapshot['takeoffs'] if t.get('horizontal_formwork_summary') for d in t['contact_ranges'] if d.get('polygon_window') and not d.get('multi_polygon_window') and bool(d.get('perforated_polygon_window'))==perforated and (perforated or bool(d.get('simple_polygon_window'))==simple)]
    if not rows:return result
    if perforated:
        result.append((titles[0],['模板 / 范围 / 轮廓','顶点 / 原始XY及单位','规范XY（m）','逐坐标原件 / 排除说明及原件'],[
            [t['name']+' / C'+str(d['index'])+' / '+('外轮廓' if ring['index']==0 else 'W'+str(ring['index'])+' '+ring['name']),
             'P'+str(i)+' / '+str(config['parameters']['x'+str(i)])+'，'+str(config['parameters']['y'+str(i)])+' '+d['plan_range']['unit'],
             v['x']+' / '+v['y'],config['evidence']['x'+str(i)]+' / '+config['evidence']['y'+str(i)]+'；'+(config['measurement_basis']+'；'+config['evidence_id'] if ring['index'] else d['plan_range']['coordinate_basis']+'；'+d['plan_range']['coordinate_evidence_id'])]
            for t,d in rows for ring in d['window_rings'] for config in [d['plan_range'] if ring['index']==0 else d['plan_range']['holes'][ring['index']-1]] for i,v in enumerate(ring['vertices'],1)]))
    else:
        result.append((titles[0],['模板 / 范围','顶点 / 原始XY及单位','规范XY（m）','逐坐标原件 / 共同基准'],[
            [t['name']+' / C'+str(d['index'])+' '+d['name'],'P'+str(i)+' / '+str(d['plan_range']['parameters']['x'+str(i)])+'，'+str(d['plan_range']['parameters']['y'+str(i)])+' '+d['plan_range']['unit'],v['x']+' / '+v['y'],d['plan_range']['evidence']['x'+str(i)]+' / '+d['plan_range']['evidence']['y'+str(i)]+'；'+d['plan_range']['coordinate_basis']+'；'+d['plan_range']['coordinate_evidence_id']] for t,d in rows for i,v in enumerate(d['window_vertices'],1)]))
    traces=[(t,d,'窗口实际毛交集',d['polygon_gross_trace']) for t,d in rows]
    traces += [(t,d,'逐孔实际扣减H'+str(h['hole_index']),h.get('polygon_cut_trace',h['curved_intersection'])) for t,d in rows for h in d['apertures']]
    traces += [(t,d,'直接净接触校验',d['polygon_net_trace']) for t,d in rows]
    result.append((titles[1],['模板 / 范围 / 对象','X起止（m）','有效下界 / 上界','分段面积（m²）','规则'],[
        [t['name']+' / C'+str(d['index'])+' / '+label+(' / 窗口片段'+str(q['window_region_index']) if 'window_region_index' in q else ''),q['x_from']+'至'+q['x_to'],q['lower_boundary']+' / '+q['upper_boundary'],q['area'],m['integration_rule']+'；200位工作/80位保留；公式AST见JSON'] for t,d,label,m in traces for q in m['terms']] or [['—','—','—','0','无正面积分段']]))
    proofs=[(t,d,d['polygon_zero_contact']) for t,d in rows if d.get('polygon_zero_contact')]
    if proofs:result.append((titles[2],['模板 / 范围','区间X起止 / 中点（m）','毛接触下界 / 上界（m）','覆盖轮廓及中点上下界（m）','完整覆盖及分割余项'],[
        [t['name']+' / C'+str(d['index']),q['x_from']+'至'+q['x_to']+('；窗口片段'+str(q['window_region_index']) if 'window_region_index' in q else '')+'；中点'+q['x_mid'],q['included']['lower']+' / '+q['included']['upper'],
         '；'.join(v['profile']+'；'+v['lower_boundary']+'至'+v['upper_boundary']+'：'+v['lower']+' / '+v['upper'] for v in q['excluded']),
         m['method']+'；本区间剩余'+str(len(q['remaining']))+'段；H'+str(m['canonical_hole_index'])+'=毛−其他孔扣；直接净='+d['polygon_direct_net_area']] for t,d,m in proofs for q in m['net_trace']['partitions']]))
    return result


ROUND_COVERAGE_TITLE = '基础圆平台窗口全覆校验'


def coverage_tables(snapshot,result):
    proofs=[(t,d,h,h['exposed_coverage']) for t in snapshot['takeoffs'] if t.get('horizontal_formwork_summary') for d in t['contact_ranges'] if d.get('round_step_window') for h in d['apertures'] if h.get('exposed_coverage') is not None]
    if proofs:
        result.append((ROUND_COVERAGE_TITLE,['模板 / 平台窗口 / 孔','区间X起止（m）','平台下边界 / 上边界','孔下 / 上包含余量（m）','几何包含口径'],[
            [t['name']+' / C'+str(d['index'])+' / H'+str(h['hole_index']),q['x_from']+'至'+q['x_to'],q['lower_boundary']+' / '+q['upper_boundary'],q['lower_margin']+' / '+q['upper_margin'],m['method']+'；工作200位；每个区间无边界交点，中点只作包含见证，原始公式见JSON'] for t,d,h,m in proofs for q in (m['terms'] or [dict(x_from=d['x_start'],x_to=d['x_end'],lower_boundary='无外露平台',upper_boundary='无外露平台',lower_margin='0',upper_margin='0')])]))
    return result


HOLE_WINDOW_TITLES = ('基础孔端窗口交集','基础孔端窗口分段积分','基础孔端窗口覆盖校验')


def hole_end_window_tables(snapshot,result):
    rows=[(t,d) for t in snapshot['takeoffs'] if t.get('horizontal_formwork_summary') for d in t['contact_ranges'] if d.get('hole_end_window')]
    if not rows:return result
    result.append((HOLE_WINDOW_TITLES[0],['模板 / 孔端窗口','所选孔 / 混凝土侧层 / Z','整孔端交集 / 窗口毛 / 实扣 / 净（m²）','交集与相接孔口径'],[
        [t['name']+' / C'+str(d['index'])+' '+d['name'],'H'+str(d['hole_index'])+' / '+('下侧' if d['concrete_side']=='below' else '上侧')+'L'+str(d['level_index'])+' / '+d['z'],d['full_face_area']+' / '+d['single_gross_area']+' / '+d['single_opening_area']+' / '+d['single_net_area'],'毛=所选孔∩混凝土侧层∩窗口；扣=所选孔∩同侧相接孔∩窗口；相接孔在侧层内且互不重叠；同孔同端面重复校验'] for t,d in rows]))
    traces=[(t,d,'所选孔与侧层窗口毛交集',d['single_gross_area'],d['window_curved_intersection']) for t,d in rows]
    traces += [(t,d,'相接孔H'+str(h['hole_index'])+'；实际扣='+h['single_area'],h['window_direct_area'],h['curved_intersection']) for t,d in rows for h in d['apertures']]
    result.append((HOLE_WINDOW_TITLES[1],['模板 / 孔端窗口 / 对象','区间X起止（m）','有效下边界 / 上边界','分段面积 / 直接积分合计（m²）','解析规则与精度'],[
        [t['name']+' / C'+str(d['index'])+' / '+target,q['x_from']+'至'+q['x_to'],q['lower_boundary']+' / '+q['upper_boundary'],q['area']+' / '+total,m['integration_rule']+'；工作200位、保留80位；固定π见规则；覆盖恒等式实际扣另列，原始AST见JSON'] for t,d,target,total,m in traces for q in m['terms']] or [['—','—','—','0 / 0','无正面积交集']]))
    proofs=[(t,d,d['hole_end_window_coverage']) for t,d in rows if d.get('hole_end_window_coverage')]
    if proofs:
        result.append((HOLE_WINDOW_TITLES[2],['模板 / 孔端窗口','区间X起止 / 中点（m）','毛交集下边界 / 上边界','完整覆盖的同侧相接孔','几何分割口径'],[
            [t['name']+' / C'+str(d['index']),q['x_from']+'至'+q['x_to']+' / '+q['x_mid'],q['lower_boundary']+' / '+q['upper_boundary'],'；'.join('H'+str(v['hole_index'])+' '+v['lower_boundary']+'至'+v['upper_boundary']+'；中点下/上='+v['lower']+'/'+v['upper']+'；接续间隙='+v['join_gap'] for v in q['pieces']),m['method']+'；分割余项H'+str(m['canonical_hole_index'])+'=毛−其他孔扣；各孔直接积分另存，原始AST见JSON'] for t,d,m in proofs for q in m['terms']]))
    return result


def simple_window_check_tables(snapshot,result,perforated=False):
    method='perforated_polygon_vertical_sweep' if perforated else 'simple_polygon_vertical_sweep'
    rows=[(t,c) for t in snapshot['takeoffs'] for c in t.get('horizontal_window_checks',[]) if c.get('method')==method]
    if rows:result.append((PERFORATED_CHECK_TITLE if perforated else SIMPLE_CHECK_TITLE,['模板 / 窗口对','X起止 / 中点（m，精确有理数）','左窗口内部Y片段（m）','右窗口内部Y片段（m）','防重结论'],[
        [t['name']+' / C'+str(c['left_index'])+' / C'+str(c['right_index']),q['x_from']+'至'+q['x_to']+'；'+q['x_mid'],
         '；'.join(lo+'至'+hi for lo,hi in q['left']),'；'.join(lo+'至'+hi for lo,hi in q['right']),
         ('共享边界' if c['state']=='boundary_touch' else '分离')+'；内部片段无正重叠；完整窗口检查，不按实体或孔投影豁免'] for t,c in rows for q in (c['partitions'] or [dict(x_from='—',x_to='—',x_mid='无共同X内部区间',left=[],right=[])])]))
    return result


PERFORATED_TITLES = ('基础带孔窗口顶点','基础带孔窗口分段积分','基础带孔窗口零接触校验')
PERFORATED_NET_TITLE = '基础带孔窗口净区与校验'
PERFORATED_CHECK_TITLE = '基础带孔窗口防重分段'


def perforated_window_tables(snapshot,result):
    result=polygon_window_tables(snapshot,result,perforated=True)
    rows=[(t,d) for t in snapshot['takeoffs'] if t.get('horizontal_formwork_summary') for d in t['contact_ranges'] if d.get('perforated_polygon_window')]
    if rows:result.append((PERFORATED_NET_TITLE,['模板 / 范围','外轮廓 / 窗口内孔 / 窗口净面积（m²）','逐轮廓面积及拓扑校验','计量边界'],[
        [t['name']+' / C'+str(d['index']),d['window_outer_area']+' / '+d['window_exclusion_area']+' / '+d['window_area'],
         '；'.join(('外轮廓' if ring['index']==0 else 'W'+str(ring['index'])+' '+ring['name'])+'='+ring['area'] for ring in d['window_rings'])+'；'+'；'.join(c['left']+' / '+c['right']+'：'+('严格在内' if c['relation']=='strictly_inside' else '相互分离') for c in d['window_ring_checks']),
         '窗口内孔W仅排除本项支模，非基础实体孔H，不扣混凝土或生成孔壁；先取窗口净区，再与真实接触相交及扣实际孔。全部个数继承一次；完整公式AST见JSON'] for t,d in rows]))
    return simple_window_check_tables(snapshot,result,perforated=True)
