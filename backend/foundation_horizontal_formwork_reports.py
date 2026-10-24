"""Export stored end-face geometry and deductions without recomputing areas."""
TITLES = ('基础顶底模板面积汇总','基础顶底逐面接触净量','基础顶底实际孔扣减','基础顶底接触范围原件','基础顶底模板做法与规则')


def tables(snapshot):
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
