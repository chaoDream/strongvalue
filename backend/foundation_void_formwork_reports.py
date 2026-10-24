"""Stored high precision hole-wall values; no report-side recalculation."""
TITLES=('基础孔壁模板面积汇总','基础孔壁逐面接触净量','基础孔壁无接触扣减','基础孔壁原始范围与依据','基础孔壁来源孔与原件','基础孔壁模板做法与规则')


def tables(snapshot):
    ts=[t for t in snapshot['takeoffs'] if t.get('void_formwork_summary')]
    if not ts:return []
    result=[]
    result.append((TITLES[0],['模板 / 来源基础','选入范围数','全部毛 / 无接触扣减 / 净（m²）','未修约 / 修约净面积（m²）','范围与修约依据'],[[t['name']+' / '+t['void_formwork_summary']['source_name']+' V'+str(t['void_formwork_summary']['source_revision']),str(t['void_formwork_summary']['range_count']),t['gross']+' / '+t['deduction']+' / '+t['void_formwork_summary']['unrounded_quantity'],t['void_formwork_summary']['unrounded_quantity']+' / '+t['quantity'],t['measurement_basis']+'；原件：'+t['source_evidence']['void_formwork.basis']+'；'+t['rounding_rule']+'；差额'+t['void_formwork_summary']['rounding_adjustment']+'m²；修约原件：'+t['source_evidence']['void_formwork.rounding']] for t in ts]))
    rows=[(t,d) for t in ts for d in t['contact_ranges']]
    result.append((TITLES[1],['模板 / 范围 / 来源项层孔面','孔与层共同底 / 顶（m）','接触Z起 / 止（m）','孔壁宽或周长 / 高（m）','每基础毛 / 扣 / 净（m²）','全部个数 / 净面积（m²）'],[[t['name']+' / C'+str(d['index'])+' '+d['name']+' / M'+str(d['material_index'])+' L'+str(d['level_index'])+' H'+str(d['hole_index'])+' '+d['face_label'],d['available_from_z']+' / '+d['available_to_z'],d['normalized_parameters']['z_start']+' / '+d['normalized_parameters']['z_end'],d['width']+' / '+d['height'],d['single_gross_area']+' / '+d['single_deduction_area']+' / '+d['single_net_area'],d['total_count']+' / '+d['total_net_area']] for t,d in rows]))
    result.append((TITLES[2],['模板 / 范围 / 扣减对象','无接触原因','实际横向起 / 止（m）','实际Z起 / 止（m）','实际宽 / 高（m）','每基础扣减（m²）'],[[t['name']+' / C'+str(d['index'])+' '+d['name']+' / H'+str(x['hole_index'])+' '+x['name'],'本层基础外边界，无混凝土接触' if x['kind']=='exterior_boundary' else '相邻矩形孔对向壁贴合，无混凝土接触',x['lateral_start']+' / '+x['lateral_end'],x['z_start']+' / '+x['z_end'],x['width']+' / '+x['height'],x['single_area']] for t,d in rows for x in d['deductions']] or [['—','所选孔壁无正面积无接触扣减','—','—','—','0']]))
    result.append((TITLES[3],['模板 / 范围','原始起止Z及单位','逐参数原件','接触说明及原件'],[[t['name']+' / C'+str(d['index'])+' '+d['name'],str(d['parameters']['z_start'])+' / '+str(d['parameters']['z_end'])+' '+d['unit'],'起始Z：'+d['evidence']['z_start']+'；终止Z：'+d['evidence']['z_end'],d['measurement_basis']+'；原件：'+d['evidence']['measurement_basis']] for t,d in rows]))
    def proof(t,d):
        prefix='foundation.'+str(d['material_index'])+'.holes.'+str(d['hole_index']);return '；'.join(k+'：'+str(v)+' '+d['hole_unit']+'；原件：'+t['source_evidence'][prefix+'.'+k] for k,v in d['hole_parameters'].items())+'；共同坐标原件：'+t['source_evidence']['foundation.'+str(d['material_index'])+'.holes.coordinate']
    result.append((TITLES[4],['模板 / 范围 / 来源孔','孔位与实际尺寸及原件','孔底 / 顶Z（m）','计量边界'],[[t['name']+' / C'+str(d['index'])+' / H'+str(d['hole_index'])+' '+d['hole_name'],proof(t,d),d['hole_from_z']+' / '+d['hole_to_z'],'孔位沿来源基础局部XY；圆孔整周、矩形逐侧整宽；体积扣孔阈值不改变物理接触面，外边界及相邻孔实际贴合扣除'] for t,d in rows]))
    result.append((TITLES[5],['模板','材料做法、原件及计算范围'],[[t['name'],t['material']+'；原件：'+t['source_evidence']['void_formwork.material']+'；'+t['notice']+'；'+t['rule_version']+('；π='+t['void_formwork_pi_constant'] if 'void_formwork_pi_constant' in t else '；本孔壁矩形计算无需π')] for t in ts]))
    return result
