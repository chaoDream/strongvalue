"""Application report exporter. All tables use the same immutable quote snapshot."""
import io
from .store import digest,canonical
import json
import os
from pathlib import Path
from threading import Lock
from collections import defaultdict
from decimal import Decimal
from html import escape

from .engine import GEOMETRIES
from fastapi import HTTPException
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.pagebreak import Break
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak

FONT_LOCK = Lock()


def chinese_font():
    # Embed a subset: relying on a viewer's Adobe CJK font pack yields blank PDFs.
    name = 'QiangChinese'
    with FONT_LOCK:
        if name not in pdfmetrics.getRegisteredFontNames():
            candidates = [os.getenv('QIANG_PDF_FONT', ''), '/Library/Fonts/Arial Unicode.ttf',
                          '/System/Library/Fonts/Supplemental/Arial Unicode.ttf',
                          '/System/Library/Fonts/STHeiti Light.ttc',
                          '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc']
            font_path = next((p for p in candidates if p and Path(p).is_file()), None)
            if not font_path:
                raise HTTPException(422, '未找到可嵌入中文字体；请设置 QIANG_PDF_FONT 为合法中文 TTF/TTC 字体路径')
            pdfmetrics.registerFont(TTFont(name, font_path))
    return name


def uppercase_money(value):
    digits = '零壹贰叁肆伍陆柒捌玖'
    n = Decimal(value).quantize(Decimal('0.01'))
    integer = int(n)
    def group(x):
        s, zero = '', False
        for divisor, unit in [(1000, '仟'), (100, '佰'), (10, '拾'), (1, '')]:
            d, x = divmod(x, divisor)
            if d:
                if zero:
                    s += '零'
                s += digits[d]+unit
                zero = False
            elif s:
                zero = True
        return s
    if integer == 0:
        out = '零'
    else:
        out = ''
        parts = []
        while integer:
            parts.append(integer % 10000)
            integer //= 10000
        pending_zero = False
        for i in range(len(parts)-1, -1, -1):
            if not parts[i]:
                pending_zero = bool(out)
                continue
            if out and (pending_zero or parts[i] < 1000):
                out += '零'
            out += group(parts[i])+(['', '万', '亿', '兆'][i])
            pending_zero = False
    out += '元'
    cents = int(n*100) % 100
    jiao, fen = divmod(cents, 10)
    if not cents:
        return out+'整'
    if jiao:
        out += digits[jiao]+'角'
    if fen:
        out += ('' if jiao else '零')+digits[fen]+'分'
    return out


def tables(p, q, approved):
    snapshot = q['snapshot'] if q else p
    items = q['lines'] if q else snapshot['boq']
    watermark = '试点范围审核成果（无签章效力）' if approved else '未完成成果草稿 · 未经完整审核'
    total = q['total'] if q else '未计价'
    scope = '、'.join(q.get('scope', p['scope']) if q else p['scope'])
    report_reviews = [r for r in p['reviews'] if q and r['collection'] == 'quotes' and r['object_id'] == q['id']]
    summary = [['工程名称', q['project_name'] if q else p['name']], ['成果状态', watermark], ['本次范围', scope],
               ['报价版本', q['version'] if q else '无价草稿'], ['总金额', total], ['人民币大写', uppercase_money(total) if q else '未计价'],
               ['快照哈希', q['hash'] if q else '未形成报价快照'], ['适用标准', snapshot['rules'][0]['standard'] if snapshot['rules'] else '待配置'],
               ['说明', '仅覆盖所列试点范围，签章由有权专业人员依法完成。']]
    summary.extend([['计价适用日期', snapshot.get('pricing_date') or p.get('pricing_date') or '当前日期'],
                    ['数量来源', '包含已有清单兼容导入，非原图自动算量验收' if q and q.get('source_mode') == 'imported_or_mixed' else '构件计算书 / 人工审定证据']])
    dates = snapshot.get('pricing_bases', [])
    if dates:
        b = dates[-1]
        summary.extend([['项目日期裁决', 'V'+str(b['version'])+' / '+b['state']], ['日期选择与冲突裁决依据', b['reason']], ['日期裁决来源证据', b['evidence_id']]])
        labels = {'tender':'招标','contract':'合同签订','design':'设计版次','construction':'施工','settlement':'结算'}
        summary.extend([[labels[k]+'日期', m['date']+(' / '+m['edition'] if m.get('edition') else '')+' / 证据 '+m['evidence_id']] for k,m in b['milestones'].items()])
    else:
        summary.append(['项目日期裁决', '未登记多时间节点专业裁决'])
    hierarchy = defaultdict(Decimal)
    for b in items:
        if q:
            hierarchy[b['node']] += Decimal(b['amount'])
    result = [('封面与说明', ['字段', '内容'], summary),
              ('分级汇总', ['单项 / 单位工程', '分部分项金额'], [[k, str(v)] for k, v in hierarchy.items()]),
              ('分部分项清单', ['编码', '名称', '项目特征', '单位', '工程量', '单价', '合价'],
               [[b['code'] or '待匹配', b['name'], b['features'], b['unit'], b['quantity'], b.get('unit_price', ''), b.get('amount', '')] for b in items]),
              ('工程量计算书', ['构件', '公式', '参数', '扣减/计量依据', '单位', '工程量', '来源证据'],
               [[t['name'], t['formula'], json.dumps(t['parameters'], ensure_ascii=False), t['measurement_basis'], t['unit'], t['quantity'], ', '.join(sorted(set(t['evidence'].values())))] for t in snapshot['takeoffs']]),
              ('综合单价分析', ['清单项', '定额', '资源', '类型', '消耗', '换算系数', '资源价格', '单价贡献'],
               [[b['name'], r['quota_code'], r['name'], r.get('type', ''), r['consumption'], r['coefficient'], r['price'], r['cost']] for b in items for r in b.get('resources_snapshot', [])]),
              ('定额换算明细', ['清单项', '定额', '字段', '值'],
               [[b['name'], quota['code'], label, str(value)[start:start+140]]
                for b in items for quota in b.get('quotas', []) for conv in ([quota['conversion']] if 'conversion' in quota else [])
                for label, value in [('定额单位', conv['quota_unit']), ('单位换算率', conv['unit_factor']), ('通用系数', quota.get('coefficient', '1')),
                                     *[(k+'系数', conv['category_factors'][k]) for k in ['人工', '材料', '机械', '设备']],
                                     ('换算来源', conv['source']), ('授权依据', conv['license']), ('原始证据', conv['evidence_id']), ('换算说明', conv['reason'])]
                for start in range(0, max(1,len(str(value))), 140)]),
              ('费用与税金', ['费用', '基数', '费率', '金额', '依据'],
               [[(f.get('scope_name','')+' / ' if f.get('scope_name') else '')+f['name'], f['base_amount'], f['rate'], f['amount'], f.get('source', '')] for f in (q['fees'] if q else [])]),
              ('资源价格表', ['资源', '类型', '单价', '地区', '期次', '税制', '有效期', '价格来源 / 授权'],
               [[r['name'], r.get('type', ''), '待询价' if r.get('pricing_status')=='unpriced' else r['price'], r.get('region',''), r.get('period',''), r.get('tax_mode',''), r.get('valid_until',''), '尚无价格依据' if r.get('pricing_status')=='unpriced' else r['source']+' / '+r['license']] for b in items for quota in b.get('quotas', []) for r in quota['resources']]),
              ('依据与证据', ['证据 ID', '文件', '文件哈希', '页码', '提取方式', '原文 / 标注', '坐标约定', '归一化区域', '页面旋转及裁切'],
               [[e['id'], next((d['name'] for d in snapshot['documents'] if d['id'] == e['document_id']), ''), e['document_hash'], e['page'] or '无固定页码', e['method'], e['text'], e.get('coordinate_space') or '', json.dumps(e.get('bbox'),ensure_ascii=False) if e.get('bbox') is not None else '', json.dumps(e.get('page_geometry'),ensure_ascii=False) if e.get('page_geometry') else ''] for e in snapshot['evidence']]),
              ('审核记录', ['对象类型', '对象 ID', '决定', '复核人', '时间', '依据', '对象哈希'],
               [[r['collection'], r['object_id'], r['decision'], r['reviewer'], r['timestamp'], r['reason'], r['object_hash']] for r in snapshot['reviews']+report_reviews]),
              ('缺项与警示', ['事项'], [[x] for x in ((q['warnings'] if q else [])+(['当前版本已失效；源输入已变更'] if q and q['id'] in p['stale_quotes'] else []))])]
    dxf_routes=snapshot.get('dxf_route_adoptions',[])
    from .dxf_revision_diff import report_tables as dxf_revision_tables
    result.extend(dxf_revision_tables(dxf_routes))
    if dxf_routes:
        result.append(('DXF坐标采用与单位校核',['管线 / 修订','原CAD对象','已知两点距离 / CAD距离','容差 / 偏差','采用核对依据','操作人 / 时间'],
            [[x.get('component_name',x['component_id'])+' / V'+str(x['component_revision']),t['candidate']['document_id']+' / '+t['candidate']['kind']+' '+t['candidate']['handle']+' / '+t['candidate']['layer']+' / '+t['candidate']['unit'],
              'P'+str(t['calibration']['from_point'])+'–P'+str(t['calibration']['to_point'])+'：'+t['calibration']['known_distance_m']+'m / '+t['calibration']['cad_distance_m']+'m',
              t['calibration']['tolerance_m']+'m / '+t['calibration']['delta_m']+'m',t['reason']+' / 校核证据 '+t['proofs']['calibration']['evidence_id'],
              t['author']+' / '+t['created_at']] for x in dxf_routes for t in [x['trace']]]))
    fragments=[(x,x['trace']['candidate']) for x in dxf_routes if x['trace']['candidate'].get('kind')=='FRAGMENT']
    if fragments:
        result.append(('DXF原对象取段范围',['管线 / 修订','原对象 / 图层','原始范围 / 方向','取段点 → 原始点','采用起点 → 终点','单组长度 / 原件哈希'],
            [[x.get('component_name',x['component_id'])+' / V'+str(x['component_revision']),r['source_object']['kind']+' '+r['handle']+' / '+r['layer'],
              'P'+str(r['fragment']['from_point'])+'—P'+str(r['fragment']['to_point'])+' / '+('反向' if r['fragment']['reverse'] else '原方向'),
              '；'.join('P'+str(i+1)+'→原P'+str(v) for i,v in enumerate(r['source_point_indices'])),
              canonical(r['points'][0])+' → '+canonical(r['points'][-1])+' '+r['unit'],r['single_length_m']+'m / '+r['document_hash']] for x,r in fragments]))
    assembled=[(x,x['trace']['candidate']) for x in dxf_routes if x['trace']['candidate'].get('kind')=='ASSEMBLY']
    if assembled:
        result.append(('DXF多对象组装来源',['管线 / 修订','顺序 / 方向','原对象 / 图层','组装点范围','原始起点 → 终点','原单位 / 文件哈希'],
            [[x.get('component_name',x['component_id'])+' / V'+str(x['component_revision']),str(i+1)+' / '+('反向' if part['reverse'] else '原方向'),
              src['kind']+' '+src['handle']+' / '+src['layer'],'P'+str(part['start_point'])+'—P'+str(part['end_point']),
              canonical(src['points'][0])+' → '+canonical(src['points'][-1]),src['unit']+' / '+src['document_hash']]
             for x,row in assembled for i,(src,part) in enumerate(zip(row['source_objects'],row['source_ranges']))]))
    ranged_assemblies=[(x,row) for x,row in assembled if any(s.get('kind')=='FRAGMENT' for s in row['source_objects'])]
    if ranged_assemblies:
        result.append(('DXF组装取段顶点映射',['管线 / 修订','顺序 / 原对象 / 图层','原始范围 / 方向','组装点 → 原始点','采用起点 → 终点','原单位 / 原件哈希'],
            [[x.get('component_name',x['component_id'])+' / V'+str(x['component_revision']),str(i+1)+' / '+raw['kind']+' '+src['handle']+' / '+src['layer'],
              ('原P'+str(src['fragment']['from_point'])+'—原P'+str(src['fragment']['to_point']) if src.get('fragment') else '完整原对象')+' / '+('反向' if part['reverse'] else '原方向'),
              '；'.join('P'+str(part['start_point']+j)+'→原P'+str(n) for j,n in enumerate(indices)),
              canonical(oriented[0])+' → '+canonical(oriented[-1]),src['unit']+' / '+src['document_hash']]
             for x,row in ranged_assemblies for i,(src,part) in enumerate(zip(row['source_objects'],row['source_ranges']))
             for raw in [src.get('source_object',src)] for oriented in [src['points'] if src.get('fragment') or not part['reverse'] else list(reversed(src['points']))]
             for indices in [src['source_point_indices'] if src.get('fragment') else list(range(len(src['points']),0,-1)) if part['reverse'] else list(range(1,len(src['points'])+1))]]))
    offers=snapshot.get('supplier_quotes',[])
    if offers:
        result.append(('供应商报价登记',['资源 / 清单','供应商 / 报价单','单价 / 税制 / 地区','期次 / 有效期','供货条件 / 登记依据','状态 / 原始证据'],
            [[o['target']['resource']['name']+' / '+o['target']['boq_name'],o['supplier']+' / '+o['reference'],
              o['metadata']['price']+'元/'+o['target']['resource']['unit']+' / '+o['metadata']['tax_mode']+' / '+o['metadata']['region'],
              o['metadata']['period']+' / '+o['metadata']['valid_from']+' 至 '+o['metadata']['valid_until'],
              o['terms']+' / '+o['reason'],{'draft':'待复核','approved':'已审定','rejected':'已拒绝','needs_info':'待补件'}.get(o['state'],o['state'])+' / '+o['evidence_id']]
             for o in offers]))
    adoptions=snapshot.get('supplier_quote_adoptions',[])
    revisions=[o for o in offers if o.get('previous_quote_id')]
    if revisions:
        result.append(('供应商报价修订',['供应商 / 资源','报价版本 / 原版本','原单价 / 新单价','修订核对依据','登记人 / 时间','原始证据'],
            [[o['supplier']+' / '+o['target']['resource']['name'],'R'+str(o['revision'])+' / '+o['previous_quote_id'],
              next((p['metadata']['price'] for p in offers if p['id']==o['previous_quote_id']),'历史原价')+' → '+o['metadata']['price']+'元/'+o['target']['resource']['unit']+' / 不作为价差',
              o['reason'],o['author']+' / '+o['created_at'],o['evidence_id']] for o in revisions]))
    reopenings=snapshot.get('supplier_inquiry_reopenings',[])
    if reopenings:
        result.append(('供应商重新询价',['资源 / 清单','原报价 / 采用记录','原价 / 当前操作','重新询价依据','操作人 / 时间','原始证据'],
            [[x['before']['name']+' / '+x['boq_name'],x['quote_id']+' / '+x['adoption_id'],x['before']['price']+'元/'+x['before']['unit']+' → 待询价（不是零价）',
              x['reason'],x['author']+' / '+x['created_at'],x['evidence_id']] for x in reopenings]))
    if adoptions:
        result.append(('供应商报价采用',['资源 / 清单','报价及版本校验','采用单价 / 原状态','选择依据','采用人 / 时间','原始证据'],
            [[x['after']['name']+' / '+next((b['name'] for b in snapshot['boq'] if b['id']==x['boq_id']),x['boq_id']),
              x['quote_id']+' / '+x['quote_hash'],x['after']['price']+'元/'+x['after']['unit']+' / 原待询价，无原价差额',
              x['reason'],x['author']+' / '+x['created_at'],x['after']['evidence_id']] for x in adoptions]))
    transcriptions=[e for e in snapshot['evidence'] if e.get('ocr_transcription')]
    if transcriptions:
        result.append(('扫描原行人工抄录',['人工证据 / 来源行','机器原文','人工核对文字','核对依据','核对人 / 时间','原件及位置'],
            [[e['id']+' / '+t['source_location']['evidence_id']+' 第'+str(t['source_location']['line'])+'行',
              t['original_line']['text'],e['text'],t['reason'],t['created_by']+' / '+t['created_at'],
              e['document_hash']+' / 第'+str(e['page'])+'页 / '+str(e['bbox'])]
             for e in transcriptions for t in [e['ocr_transcription']]]))
    adopted_facts=[f for f in snapshot['facts'] if f.get('transcription_adoption')]
    if adopted_facts:
        result.append(('人工抄录事实采用',['事实字段 / 候选','原始值 / 单位','标准值 / 单位','采用依据','采用人 / 时间','来源证据 / 原文'],
            [[f['label']+' / '+a['candidate']['id'],f['original_value']+' '+f['original_unit'],f['value']+' '+f['unit'],
              a['reason'],a['created_by']+' / '+a['created_at'],f['evidence_id']+' / '+f['raw_text']]
             for f in adopted_facts for a in [f['transcription_adoption']]]))
    if snapshot.get('facts'):
        fact_states={'draft':'待复核','approved':'已审定','rejected':'不采纳','superseded':'历史替代','conflict':'存在冲突','stale':'需重新复核','needs_info':'待补依据'}
        result.insert(1,('项目事实与冲突候选', ['字段 / 候选ID','原始值 / 单位','标准值 / 单位','候选类别 / 审核状态','来源文件 / 页码','证据ID / 原文'],
            [[f['label']+' / '+f['id'],f.get('original_value',f['value'])+' '+f.get('original_unit',f.get('unit','')),
              f['value']+' '+f.get('unit',''),f.get('confidence_category','历史候选')+' / '+fact_states.get(f['state'],f['state']),
              next((d['name'] for d in snapshot['documents'] if d['id']==e.get('document_id')),'')+' / '+str(e.get('page') or '无固定页码'),
              f['evidence_id']+' / '+f.get('raw_text',e.get('text',''))]
             for f in snapshot['facts'] for e in [next((x for x in snapshot['evidence'] if x['id']==f['evidence_id']),{})]]))
    from .drawing_register import report_tables as drawing_tables
    result.extend(drawing_tables(snapshot,current_checks=q is None))
    compound_rows = [[t['name'], ('加项' if part['operation']=='add' else '扣减')+' / '+part['name'],
                      part['formula'], json.dumps(part['parameters'], ensure_ascii=False),
                      part['single_quantity']+' × '+str(part['multiplicity'])+' = '+part['quantity']+' '+part['unit'],
                      part['measurement_basis'], ', '.join(sorted(set(part['evidence'].values())))]
                     for t in snapshot['takeoffs'] for part in t.get('measurement_parts', [])]
    if compound_rows:
        result.append(('分项算量与扣减明细', ['构件','分项','公式','原始尺寸','单项 × 数量 = 分项量','计量依据','来源证据'], compound_rows))
        result.append(('分项规则与精度', ['构件','规则版本','净量 / 单位','精度规则'],
                       [[t['name'], t['rule_version'], t['quantity']+' '+t['unit'], t['rounding_rule']]
                        for t in snapshot['takeoffs'] if t.get('measurement_parts')]))
    conversion_rows = [[t['name'], part['name']+' / '+GEOMETRIES[part['kind']]['fields'][c['parameter']].split('（')[0], c['original_value'], c['source_unit'], c['factor'],
                        c['canonical_value']+' '+c['canonical_unit'], c['rule_version'], part['evidence'][c['parameter']]]
                       for t in snapshot['takeoffs'] for part in t.get('measurement_parts', []) for c in part.get('unit_conversions', [])]
    conversion_rows.extend([[t['name'],c['parameter'],c['original_value'],c['source_unit'],c['factor'],c['canonical_value']+' '+c['canonical_unit'],c['rule_version'],t['evidence'][c['parameter']]]
                           for t in snapshot['takeoffs'] if t.get('formwork_summary') or t.get('material_summary') or t.get('railing_summary') or t.get('polygon_summary') or t.get('planar_summary') or t.get('spatial_summary') or t.get('surface_summary') or t.get('route_summary') or t.get('trench_summary') or t.get('pipe_summary') or t.get('bedding_summary') or t.get('chamber_summary') or t.get('plate_summary') for c in t['unit_conversions']])
    if conversion_rows:
        result.append(('算量原始单位换算', ['构件','分项 / 参数','原始值','原始单位','换算倍数','标准值 / 单位','单位规则版本','原始证据'], conversion_rows))
    plates = [t for t in snapshot['takeoffs'] if t.get('plate_summary')]
    if plates:
        result.append(('井底盖板净实体汇总', ['井板 / 材料','用途 / 来源井筒版本','选定项数 / 继承板数','原体积 / 孔扣减（m³）','未修约净量 / 工程量（m³）','计量依据与精度'],
                       [[t['name']+' / '+t['material'],t['plate_summary']['role_label']+' / '+t['plate_summary']['source_name']+' V'+str(t['plate_summary']['source_revision']),
                         str(t['plate_summary']['well_item_count'])+' / '+t['plate_summary']['plate_count'],t['gross']+' / '+t['deduction'],t['plate_summary']['unrounded_quantity']+' / '+t['quantity'],t['measurement_basis']+'；'+t['rounding_rule']] for t in plates]))
        result.append(('井板逐项尺寸与孔扣减', ['井板 / 名称 / 来源井号','原始参数 / 单位','板径 / 孔径或孔数 / 厚度（m）','板底 / 板顶标高（m）','继承井数','单板原体积 / 孔扣减 / 净体积（m³）','本项净体积（m³）'],
                       [[t['name']+' / '+d['name']+' / '+d['source_well_name'],json.dumps(d['parameters'],ensure_ascii=False)+' / '+d['unit'],
                         ' / '.join(d['normalized_parameters'].get(k,str(len(d.get('openings',[])))+'孔') for k in ['diameter','opening_diameter','thickness']),d['normalized_parameters']['bottom_z']+' / '+d['top_z'],str(d['source_multiplicity']),
                         d['single_gross_volume']+' / '+d['single_opening_volume']+' / '+d['single_net_volume'],d['volume']] for t in plates for d in t['plate_sections']]))
        result.append(('井板标高范围及原件', ['井板 / 名称','来源井号 / 关联站点','独立标高及板孔范围说明','板孔厚标高及范围证据'],
                       [[t['name']+' / '+d['name'],d['source_well_name']+' / P'+str(d['point_index'])+' '+d['location']['name'],d['measurement_basis'],json.dumps(d['evidence'],ensure_ascii=False)] for t in plates for d in t['plate_sections']]))
        result.append(('井板材料做法与规则', ['井板','材料施工及依据','π / 修约差额（m³）','规则版本','计量边界'],
                       [[t['name'],t['material']+'；'+t['construction_basis']+'；'+t['evidence']['plate_material']+'；'+t['evidence']['plate_construction'],t['plate_summary']['pi_constant']+' / '+t['plate_summary']['rounding_adjustment'],t['rule_version'],t['notice']] for t in plates]))
    offset_plates = [(t,d) for t in plates for d in t['plate_sections'] if 'openings' in d]
    if offset_plates:
        result.append(('井板逐孔定位与扣减', ['井板 / 圆孔','局部坐标口径及依据','原始XY直径 / 单位','标准XY直径（m）','单板孔扣减（m³） / 继承井数','贯通定位说明及证据'],
                       [[t['name']+' / '+d['name']+' / '+h['name'],d['coordinate_basis']+'；'+d['evidence']['coordinate_basis'],json.dumps(h['parameters'],ensure_ascii=False)+' / '+h['unit'],
                         ' / '.join(h['normalized_parameters'][k] for k in ['x','y','diameter']),h['single_volume']+' / '+str(d['source_multiplicity']),h['measurement_basis']+'；'+json.dumps(h['evidence'],ensure_ascii=False)]
                        for t,d in offset_plates for h in d['openings']]))
        check_rows = [[t['name']+' / '+d['name']+' / '+h['name'],'板内包含',h['containment']['limit_distance_squared'],h['containment']['center_distance_squared'],h['containment']['margin'],
                       '与板边界相切' if h['containment']['state']=='tangent' else '完全在板内'] for t,d in offset_plates for h in d['openings']]
        check_rows.extend([[t['name']+' / '+d['name']+' / '+c['left']+' / '+c['right'],'孔间分离',c['center_distance_squared'],c['radius_sum_squared'],c['margin'],
                            '相切' if c['state']=='tangent' else '互不重叠'] for t,d in offset_plates for c in d['opening_checks']])
        result.append(('井板孔包含与分离校验', ['板及孔关系','判定类型','比较项A（m²）','比较项B（m²）','判定差A−B（m²）≥0','几何结论（非结构安全结论）'],check_rows))
    chambers = [t for t in snapshot['takeoffs'] if t.get('chamber_summary')]
    if chambers:
        result.append(('检查井井筒计量汇总', ['井筒 / 材料','用途 / 工程量（m³）','关联管线 / 版本','定位项数 / 核定井数','未修约材料 / 外包（m³）','计量依据及精度'],
                       [[t['name']+' / '+t['material'],t['chamber_summary']['purpose_label']+' / '+t['quantity'],t['chamber_summary']['source_name']+' / V'+str(t['chamber_summary']['source_revision']),
                         str(t['chamber_summary']['location_count'])+' / '+t['chamber_summary']['well_count'],t['chamber_summary']['wall_volume']+' / '+t['chamber_summary']['outer_volume'],t['measurement_basis']+'；'+t['rounding_rule']] for t in chambers]))
        result.append(('井筒逐井尺寸与数量', ['构件 / 井号','关联站点','原始尺寸 / 单位','标准外径内径高度（m）','独立井数','单井材料 / 外包（m³）','所选用途体积（m³）'],
                       [[t['name']+' / '+w['name'],'P'+str(w['point_index'])+' '+w['location']['name'],json.dumps(w['parameters'],ensure_ascii=False)+' / '+w['unit'],
                         ' / '.join(w['normalized_parameters'][k] for k in ['outer_diameter','inner_diameter','height']),str(w['multiplicity']),w['single_wall_volume']+' / '+w['single_outer_volume'],w['volume']] for t in chambers for w in t['chamber_wells']]))
        result.append(('井筒定位及原始依据', ['井筒 / 井号','站点原始XYZ / 单位','站点XYZ证据','定位及数量说明','尺寸数量及范围证据'],
                       [[t['name']+' / '+w['name'],json.dumps(w['location']['parameters'],ensure_ascii=False)+' / '+w['location']['unit'],json.dumps(w['location']['evidence'],ensure_ascii=False),
                         w['measurement_basis'],json.dumps(w['evidence'],ensure_ascii=False)] for t in chambers for w in t['chamber_wells']]))
        result.append(('井筒材料做法与规则', ['井筒 / 用途','材料及施工依据','π / 未修约量 / 修约差额','规则 / 管线数量不继承','计量边界'],
                       [[t['name']+' / '+t['chamber_summary']['purpose_label'],t['material']+'；'+t['construction_basis']+'；'+t['evidence']['chamber_material']+'；'+t['evidence']['chamber_construction'],
                         t['chamber_summary']['pi_constant']+' / '+t['chamber_summary']['unrounded_quantity']+' / '+t['chamber_summary']['rounding_adjustment'],
                         t['rule_version']+'；来源管线重复数量'+t['chamber_summary']['route_multiplicity_not_inherited']+'不参与井数计算',t['notice']] for t in chambers]))
    beddings = [t for t in snapshot['takeoffs'] if t.get('bedding_summary')]
    if beddings:
        result.append(('管网矩形垫层汇总', ['垫层 / 材料','来源管线 / 版本','单组未修约（m³）','重复数量 / 工程量（m³）','范围依据','规则及精度'],
                       [[t['name']+' / '+t['material'],t['bedding_summary']['source_name']+' / V'+str(t['bedding_summary']['source_revision']),t['bedding_summary']['single_volume'],
                         t['bedding_summary']['multiplicity']+' / '+t['quantity'],t['measurement_basis'],t['rule_version']+'；'+t['rounding_rule']] for t in beddings]))
        result.append(('垫层逐段尺寸与体积', ['垫层 / 段','起点 → 终点','原始宽厚 / 单位','标准宽厚（m）','水平长 / 三维长（m）','断面积 / 单组体积','范围及证据'],
                       [[t['name']+' / S'+str(d['segment_index']),d['from_name']+' → '+d['to_name'],json.dumps(d['parameters'],ensure_ascii=False)+' / '+d['unit'],
                         d['normalized_parameters']['width']+' / '+d['normalized_parameters']['thickness'],d['horizontal_length']+' / '+d['spatial_length'],
                         d['cross_section_area']+' m² / '+d['single_volume']+' m³',d['measurement_basis']+'；'+json.dumps(d['evidence'],ensure_ascii=False)] for t in beddings for d in t['bedding_sections']]))
        result.append(('垫层材料施工与修约', ['垫层','材料规格及依据','施工做法及依据','修约差额及依据','边界说明'],
                       [[t['name'],t['material']+'；'+t['evidence']['bedding_material'],t['construction_basis']+'；'+t['evidence']['bedding_construction'],
                         t['bedding_summary']['rounding_adjustment']+' m³；'+t['evidence']['bedding_rounding'],t['notice']] for t in beddings]))
        result.append(('垫层来源设计坐标', ['垫层 / 来源点','原始XYZ','单位','XYZ证据'],
                       [[t['name']+' / P'+str(i+1)+' '+p['name'],json.dumps(p['parameters'],ensure_ascii=False),p['unit'],json.dumps(p['evidence'],ensure_ascii=False)]
                        for t in beddings for i,p in enumerate(t['source_geometry']['route_points'])]))
    pipes = [t for t in snapshot['takeoffs'] if t.get('pipe_summary')]
    if pipes:
        result.append(('管道外包体积汇总', ['管道占位','来源管线 / 版本','单组未修约（m³）','重复数量 / 工程量（m³）','范围依据','规则及精度'],
                       [[t['name'],t['pipe_summary']['source_name']+' / V'+str(t['pipe_summary']['source_revision']),t['pipe_summary']['single_volume'],
                         t['pipe_summary']['multiplicity']+' / '+t['quantity'],t['measurement_basis'],t['rule_version']+'；'+t['rounding_rule']] for t in pipes]))
        result.append(('管道逐段外径与占位', ['管道 / 段','起点 → 终点','实际外径 / 标准值','三维长 / 水平长（m）','外包截面积（m²）','单组体积（m³）','范围 / 外径及计量证据'],
                       [[t['name']+' / S'+str(d['segment_index']),d['from_name']+' → '+d['to_name'],str(d['outer_diameter'])+' '+d['diameter_unit']+' / '+d['normalized_outer_diameter']+' m',
                         d['spatial_length']+' / '+d['horizontal_length'],d['cross_section_area'],d['single_volume'],d['measurement_basis']+'；'+d['evidence_id']+'；'+d['basis_evidence_id']]
                        for t in pipes for d in t['pipe_sections']]))
        result.append(('管道外包口径与常量', ['管道占位','π常量','修约差额（m³）','边界说明'],
                       [[t['name'],t['pipe_summary']['pi_constant'],t['pipe_summary']['rounding_adjustment'],t['notice']] for t in pipes]))
        result.append(('管道外包来源坐标', ['管道 / 来源点','原始XYZ','单位','XYZ证据'],
                       [[t['name']+' / P'+str(i+1)+' '+p['name'],json.dumps(p['parameters'],ensure_ascii=False),p['unit'],json.dumps(p['evidence'],ensure_ascii=False)]
                        for t in pipes for i,p in enumerate(t['source_geometry']['route_points'])]))
    balances = [t for t in snapshot['takeoffs'] if t.get('balance_summary')]
    if balances:
        result.append(('关联回填平衡', ['回填','已审开挖 / 版本','开挖 / 扣减（m³）','未修约净量 / 工程量（m³）','计量范围依据','规则与精度'],
                       [[t['name'],t['excavation_reference']['name']+' / V'+str(t['excavation_reference']['revision']),t['gross']+' / '+t['deduction'],
                         t['balance_summary']['unrounded_quantity']+' / '+t['quantity'],t['measurement_basis'],t['rule_version']+'；'+t['rounding_rule']] for t in balances]))
        result.append(('回填逐项扣减', ['回填 / 项','已审来源 / 版本','来源量（m³）','核定比例','扣减体积（m³）','比例及范围说明','原始依据'],
                       [[t['name']+' / D'+str(d['index']),d['name']+' / V'+str(d['revision']),d['quantity'],d['factor'],d['deduction'],d['basis'],d['evidence_id']]
                        for t in balances for d in t['balance_deductions']]))
        result.append(('回填来源计算索引', ['回填','用途 / 来源','来源版本哈希','已审体积（m³）','原公式 / 规则','原参数与证据'],
                       [[t['name'],label+' / '+s['name']+' / V'+str(s['revision']),digest(s),s['preview']['quantity'],
                         s['preview']['formula']+' / '+s['preview']['rule_version'],(json.dumps(s['parameters'],ensure_ascii=False) if s['parameters'] else '结构化参数见来源计算书')+'；证据：'+', '.join(sorted(set(s['evidence'].values())))]
                        for t in balances for label,s in [('开挖',t['source_geometry']['excavation'])]+[('扣减',v) for v in t['source_geometry']['occupations']]]))
        result.append(('回填范围与修约', ['回填','范围核定','修约差额（m³）','能力边界'],
                       [[t['name'],'已明确确认范围一致且扣减不重复；详见依据和复核意见',t['balance_summary']['rounding_adjustment'],t['notice']] for t in balances]))
    trenches = [t for t in snapshot['takeoffs'] if t.get('trench_summary')]
    if trenches:
        labels = {'method_label':'断面法','single_volume':'单组未修约体积（m³）','multiplicity':'来源重复数量','station_count':'断面数量',
                  'unrounded_quantity':'总量修约前（m³）','rounding_digits':'总量修约小数位','rounding_adjustment':'修约差额（m³）'}
        result.append(('变断面沟槽汇总', ['沟槽','字段','内容'],
                       [[t['name'],label,str(t['trench_summary'][key])] for t in trenches for key,label in labels.items()]+
                       [[t['name'],key,value] for t in trenches for key,value in [('来源',t['trench_summary']['source_name']+' / V'+str(t['trench_summary']['source_revision'])),
                         ('工程量',t['quantity']+' m³'),('坐标依据',t['coordinate_basis']),('计量依据',t['measurement_basis']),('地质场地',t['geology_basis']),
                         ('施工工作面',t['construction_basis']),('算法与精度',t['rule_version']+'；'+t['rounding_rule']),('适用边界',t['notice'])]]))
        result.append(('沟槽站点断面', ['沟槽 / 来源点','原始底宽顶宽挖深','原始单位','标准值（m）','断面积（m²）','断面说明与证据'],
                       [[t['name']+' / P'+str(s['point_index'])+' '+s['point_name'],json.dumps(s['parameters'],ensure_ascii=False),s['unit'],
                         json.dumps(s['normalized_parameters'],ensure_ascii=False),s['area'],s['measurement_basis']+'；'+json.dumps(s['evidence'],ensure_ascii=False)] for t in trenches for s in t['trench_stations']]))
        result.append(('沟槽分段体积', ['沟槽 / 段','起点 → 终点','水平长 / 空间长（m）','起 / 终面积（m²）','线性中断面 / 计算均值（m²）','未修约体积（m³）'],
                       [[t['name']+' / S'+str(s['index']),s['from_name']+' → '+s['to_name'],s['horizontal_length']+' / '+s['spatial_length'],s['start_area']+' / '+s['end_area'],
                         s['mid_area']+' / '+s['mean_area'],s['volume']] for t in trenches for s in t['trench_segments']]))
        result.append(('沟槽来源坐标', ['沟槽 / 来源点','原始XYZ','原始单位','XYZ证据'],
                       [[t['name']+' / P'+str(i+1)+' '+p['name'],json.dumps(p['parameters'],ensure_ascii=False),p['unit'],json.dumps(p['evidence'],ensure_ascii=False)]
                        for t in trenches for i,p in enumerate(t['source_geometry']['route_points'])]))
    routes = [t for t in snapshot['takeoffs'] if t.get('route_summary')]
    if routes:
        labels = {'point_count':'转折点数量','segment_count':'管段数量','single_length':'单组未修约长度（m）','multiplicity':'整组重复数量',
                  'unrounded_quantity':'总量修约前（m）','rounding_digits':'总量修约小数位','rounding_adjustment':'修约差额（m）'}
        result.append(('设计管线长度汇总', ['管线','字段','内容'],
                       [[t['name'], label, str(t['route_summary'][key])] for t in routes for key,label in labels.items()]+
                       [[t['name'], key, value] for t in routes for key,value in [('工程量',t['quantity']+' m'),('设计坐标依据',t['coordinate_basis']),
                         ('计量依据',t['measurement_basis']),('算法版本',t['rule_version']),('精度',t['rounding_rule']),('适用边界',t['notice'])]]))
        result.append(('管线设计坐标', ['管线 / 点','原始XYZ','原始单位','标准XYZ（m）','逐坐标证据'],
                       [[t['name']+' / P'+str(p['index'])+' '+p['name'], json.dumps(p['parameters'],ensure_ascii=False),p['unit'],
                         json.dumps(p['normalized_parameters'],ensure_ascii=False),json.dumps(p['evidence'],ensure_ascii=False)] for t in routes for p in t['route_points']]))
        result.append(('三维管段计算', ['管线 / 段','起点 → 终点','ΔXYZ（m）','长度平方（m²）','三维段长（m）','水平投影长（m）'],
                       [[t['name']+' / S'+str(r['index']),r['from_name']+' → '+r['to_name'],json.dumps(r['delta'],ensure_ascii=False),r['squared_length'],r['length'],r['horizontal_length']]
                        for t in routes for r in t['route_segments']]))
    from .polygon_reports import tables as polygon_tables
    result.extend(polygon_tables(snapshot['takeoffs']))
    planar=[t for t in snapshot['takeoffs'] if t.get('planar_summary')]
    if planar:
        labels={'naive_add_area':'加项累加面积（含重叠）','add_union_area':'加项并集面积','add_overlap_area':'重叠扣重面积','naive_deduct_area':'扣减累加面积（含重叠）',
                'deduct_union_area':'扣减并集面积','deduct_overlap_area':'重复扣减面积','net_area':'净面积','thickness':'统一厚度（m）','multiplicity':'整组重复数量','quantity_type':'计算类型'}
        result.append(('区域并集与扣减',['构件','字段','内容'],
                       [[t['name'],label,('不适用' if t['planar_summary'][key] is None else ('平面面积' if t['planar_summary'][key]=='area' else '等厚体积') if key=='quantity_type' else str(t['planar_summary'][key])+(' m²' if key.endswith('_area') else ''))] for t in planar for key,label in labels.items()]+
                       [[t['name'],key,value] for t in planar for key,value in [('工程量',t['quantity']+' '+t['unit']),('设计坐标依据',t['coordinate_basis']),('计量依据',t['measurement_basis']),('算法规则',t['rule_version']),('适用边界',t['notice'])]]))
        result.append(('平面区域与坐标',['构件','区域 / 加扣','原始坐标与尺寸','标准坐标与尺寸（m）','矩形面积（m²）','计量说明','原始依据'],
                       [[t['name'],'R'+str(r['index'])+' / '+r['name']+' / '+('加项' if r['operation']=='add' else '扣减'),json.dumps(r['parameters'],ensure_ascii=False)+' '+r['unit'],
                         json.dumps(r['normalized_parameters'],ensure_ascii=False),r['rectangle_area'],r['measurement_basis'],', '.join(sorted(set(r['evidence'].values())))] for t in planar for r in t['planar_regions']]))
        result.append(('区域条带计算过程',['构件','X区间（m）','条带宽（m）','加项Y并集（m）','扣减Y并集（m）','本段净面积（m²）'],
                       [[t['name'],s['x_from']+' → '+s['x_to'],s['width'],json.dumps(s['add_intervals']),json.dumps(s['deduct_intervals']),s['net_area']] for t in planar for s in t['planar_strips']]))
        intersections=[[t['name'],'加项' if i['operation']=='add' else '扣减',i['left'],i['right'],i['area'],'仅定位；不可相加代替并集扣重'] for t in planar for i in t['planar_intersections']]
        if intersections:result.append(('区域相交定位',['构件','加扣','区域A','区域B','两两相交面积（m²）','说明'],intersections))
    spatial = [t for t in snapshot['takeoffs'] if t.get('spatial_summary')]
    if spatial:
        labels = {'naive_add_volume': '加项累加体积（含重叠）', 'add_union_volume': '加项空间并集体积', 'add_overlap_volume': '重叠扣重体积',
                  'naive_deduct_volume': '扣减累加体积（含重叠）', 'deduct_union_volume': '扣减空间并集体积', 'deduct_overlap_volume': '重复扣减体积',
                  'net_volume': '单组净体积', 'multiplicity': '整组重复数量'}
        result.append(('空间并集与扣减', ['构件', '字段', '内容'],
                       [[t['name'], label, str(t['spatial_summary'][key])+(' m³' if key.endswith('_volume') else '')] for t in spatial for key, label in labels.items()]+
                       [[t['name'], key, value] for t in spatial for key, value in [('工程量', t['quantity']+' '+t['unit']), ('设计坐标依据', t['coordinate_basis']),
                         ('计量依据', t['measurement_basis']), ('算法规则', t['rule_version']), ('适用边界', t['notice'])]]))
        result.append(('长方体原始坐标', ['构件', '长方体 / 加扣', '原始XYZ与长宽高', '标准XYZ与长宽高（m）', '体积（m³）', '计量说明', '原始依据'],
                       [[t['name'], 'B'+str(r['index'])+' / '+r['name']+' / '+('加项' if r['operation']=='add' else '扣减'),
                         json.dumps(r['parameters'], ensure_ascii=False)+' '+r['unit'], json.dumps(r['normalized_parameters'], ensure_ascii=False),
                         r['cuboid_volume'], r['measurement_basis'], ', '.join(sorted(set(r['evidence'].values())))] for t in spatial for r in t['spatial_regions']]))
        result.append(('空间分层计算', ['构件', 'Z区间（m）', '层高（m）', '涉及长方体', '加项截面并集（m²）', '扣减截面并集（m²）', '净截面（m²）', '本层净体积（m³）'],
                       [[t['name'], z['z_from']+' → '+z['z_to'], z['height'], ', '.join('B'+str(i) for i in z['region_indices']),
                         z['add_area'], z['deduct_area'], z['net_area'], z['net_volume']] for t in spatial for z in t['spatial_layers']]))
        result.append(('空间分层条带', ['构件', 'Z区间（m）', 'X区间（m）', '加项Y并集（m）', '扣减Y并集（m）', '条带净面积（m²）'],
                       [[t['name'], z['z_from']+' → '+z['z_to'], r['x_from']+' → '+r['x_to'], json.dumps(r['add_intervals']), json.dumps(r['deduct_intervals']), r['net_area']]
                        for t in spatial for z in t['spatial_layers'] for r in z['strips']]))
        intersections = [[t['name'], '加项' if i['operation']=='add' else '扣减', i['left'], i['right'], i['volume'], '仅定位；不可相加代替并集扣重']
                         for t in spatial for i in t['spatial_intersections']]
        if intersections:
            result.append(('空间相交定位', ['构件', '加扣', '长方体A', '长方体B', '两两相交体积（m³）', '说明'], intersections))
    def material_proofs(item):
        labels={'quantity':'每组数量','unit_weight':'单位质量','length':'展开长度','width':'展开宽度','x':'X起距','y':'Y起距','cut_length':'下料长度','measurement_basis':'说明','coordinate_basis':'展开坐标口径','spacing':'最大间距','start_post':'起端归属','end_post':'终端归属','direction':'定距锚向'}
        grouped={}
        for key,eid in item['evidence'].items():grouped.setdefault(eid,[]).append('固定间距' if key=='spacing' and 'direction' in item else labels[key])
        return '\n'.join('、'.join(names)+'：'+eid for eid,names in grouped.items())
    from .foundation_void_formwork_reports import tables as void_formwork_tables
    result.extend(void_formwork_tables(snapshot))
    formworks=[t for t in snapshot['takeoffs'] if t.get('formwork_summary')]
    if formworks:
        result.append(('基础模板接触面积汇总',['模板构件 / 来源基础','选入范围数','全部毛 / 实际侧开口 / 净面积（m²）','未修约 / 修约净面积（m²）','范围及总量修约依据'],
                       [[t['name']+' / '+t['formwork_summary']['source_name']+' V'+str(t['formwork_summary']['source_revision']),str(t['formwork_summary']['range_count']),t['gross']+' / '+t['deduction']+' / '+t['formwork_summary']['unrounded_quantity'],t['formwork_summary']['unrounded_quantity']+' / '+t['quantity'],t['measurement_basis']+'；原件：'+t['source_evidence']['formwork.basis']+'；'+t['rounding_rule']+'；差额'+t['formwork_summary']['rounding_adjustment']+'m²；原件：'+t['source_evidence']['formwork.rounding']] for t in formworks]))
        result.append(('基础模板逐面标高与净量',['模板 / 接触范围 / 来源项层面','本层底 / 顶Z（m）','接触起始 / 终止Z（m）','展开宽 / 接触高（m）','每基础毛 / 开口 / 净（m²）','全部基础个数 / 全部净面积（m²）'],
                       [[t['name']+' / '+d['name']+' / M'+str(d['material_index'])+' L'+str(d['level_index'])+' '+d['face_label'],d['layer_from_z']+' / '+d['layer_to_z'],d['normalized_parameters']['z_start']+' / '+d['normalized_parameters']['z_end'],d['width']+' / '+d['height'],d['single_gross_area']+' / '+d['single_opening_area']+' / '+d['single_net_area'],d['total_count']+' / '+d['total_net_area']] for t in formworks for d in t['contact_ranges']]))
        result.append(('基础模板侧开口实际扣减',['模板 / 接触范围 / 侧开口','开口实际相交起止Z（m）','开口宽 / 相交高（m）','每基础实际扣面积（m²）','计量口径'],
                       [[t['name']+' / '+d['name']+' / H'+str(h['hole_index'])+' '+h['name'],h['z_start']+' / '+h['z_end'],h['width']+' / '+h['height'],h['single_area'],'仅此实际外侧开口；不受混凝土体积阈值影响，不计内部孔壁及孔底'] for t in formworks for d in t['contact_ranges'] for h in d['apertures']] or [['—','—','—','0','所选范围无正面积外侧开口；未选面不计']]))
        result.append(('基础模板原始范围与依据',['模板 / 接触范围','原始起止Z及单位','逐参数原件','实际接触范围及依据'],
                       [[t['name']+' / '+d['name'],str(d['parameters']['z_start'])+' / '+str(d['parameters']['z_end'])+' '+d['unit'],'起始Z：'+d['evidence']['z_start']+'；终止Z：'+d['evidence']['z_end'],d['measurement_basis']+'；原件：'+d['evidence']['measurement_basis']] for t in formworks for d in t['contact_ranges']]))
        result.append(('基础模板做法与计算规则',['模板构件','材料、做法原件及范围规则'],[[t['name'],t['material']+'；原件：'+t['source_evidence']['formwork.material']+'；'+t['notice']+'；'+t['rule_version']+('；π='+t['formwork_pi_constant'] if 'formwork_pi_constant' in t else '；矩形面无需π常量')] for t in formworks]))
    formwork_strips=[(t,d) for t in formworks for d in t['contact_ranges'] if 'lateral_range' in d]
    if formwork_strips:
        result.append(('基础模板横向条带',['模板 / 接触范围 / 来源项层面','原始起 / 止及单位','局部横向轴 / 起止（m）','本层整宽 / 条带宽（m）','接触高（m）/ 全部净面积（m²）'],
                       [[t['name']+' / C'+str(d['index'])+' '+d['name']+' / M'+str(d['material_index'])+' L'+str(d['level_index'])+' '+d['face_label'],str(d['lateral_range']['parameters']['lateral_start'])+' / '+str(d['lateral_range']['parameters']['lateral_end'])+' '+d['lateral_range']['unit'],d['lateral_axis']+' / '+d['lateral_start']+' / '+d['lateral_end'],d['full_face_width']+' / '+d['width'],d['height']+' / '+d['total_net_area']] for t,d in formwork_strips]))
        result.append(('基础模板条带基准与原件',['模板 / 接触范围','逐坐标原件','共同坐标基准及原件','校准及计算边界'],
                       [[t['name']+' / C'+str(d['index'])+' '+d['name'],'起坐标：'+d['lateral_range']['evidence']['lateral_start']+'；止坐标：'+d['lateral_range']['evidence']['lateral_end'],d['lateral_range']['coordinate_basis']+'；原件：'+d['lateral_range']['coordinate_evidence_id'],'基准哈希：'+d['lateral_frame_hash']+'；中心0，X侧沿Y、Y侧沿X，正向不随观察面翻转；同来源项全部条带基准一致，不自动校准方向；'+t['rule_version']] for t,d in formwork_strips]))
        strip_states={'laterally_disjoint':'横向分离','lateral_tangent':'横向边界相接','vertically_disjoint':'标高分离','vertical_tangent':'标高端面相接'}
        result.append(('基础模板条带分离校验',['模板 / 比较范围','横向间隔（m）','标高间隔（m）','校验结论'],
                       [[t['name']+' / C'+str(c['left_index'])+' '+c['left']+' / C'+str(c['right_index'])+' '+c['right'],c['lateral_gap'],c['z_gap'],strip_states[c['state']]+'；负间隔为该方向正交叠，两个方向均正交叠拒绝'] for t in formworks for c in t.get('lateral_checks',[])] or [['—','—','—','无同项同层同面比较对；跨记录仍检查共同基准与重复']]))
        result.append(('基础模板条带开口交集',['模板 / 范围 / 开口','实际横向起 / 止（m）','实际Z起 / 止（m）','实际宽 / 高（m）','单基础扣减（m²）'],
                       [[t['name']+' / C'+str(d['index'])+' '+d['name']+' / H'+str(h['hole_index'])+' '+h['name'],h['lateral_start']+' / '+h['lateral_end'],h['z_start']+' / '+h['z_end'],h['width']+' / '+h['height'],h['single_area']] for t,d in formwork_strips for h in d['apertures']] or [['—','—','—','—','0：所选条带无正面积侧开口交集']]))
    formwork_arcs=[(t,d) for t in formworks for d in t['contact_ranges'] if 'arc_range' in d]
    if formwork_arcs:
        result.append(('基础模板圆弧角度',['模板 / 接触范围 / 来源项层','原始起角 / 止角（°）','起角 / 止角 / 弧角（°）','本层整周长 / 选弧展开宽（m）','接触高（m）/ 全部净面积（m²）'],
                       [[t['name']+' / C'+str(d['index'])+' '+d['name']+' / M'+str(d['material_index'])+' L'+str(d['level_index']),str(d['arc_range']['parameters']['angle_start'])+' / '+str(d['arc_range']['parameters']['angle_end']),d['angle_start']+' / '+d['angle_end']+' / '+d['angle_span'],d['full_circumference']+' / '+d['width'],d['height']+' / '+d['total_net_area']] for t,d in formwork_arcs]))
        result.append(('基础模板圆弧基准与原件',['模板 / 接触范围','逐角原件','共同局部XY与角度基准及原件','基准哈希 / 规则'],
                       [[t['name']+' / C'+str(d['index'])+' '+d['name'],'起角：'+d['arc_range']['evidence']['angle_start']+'；止角：'+d['arc_range']['evidence']['angle_end'],d['arc_range']['coordinate_basis']+'；原件：'+d['arc_range']['coordinate_evidence_id'],'基准哈希：'+d['arc_frame_hash']+'；0°为+X，+Z向原点俯视从+X向+Y逆时针，跨0°须拆分，不自动求余或取小弧；同来源基础项全部圆弧基准一致；'+t['rule_version']] for t,d in formwork_arcs]))
        arc_states={'angular_disjoint':'角度分离','angular_tangent':'角度边界相接','vertically_disjoint':'标高分离','vertical_tangent':'标高端面相接'}
        result.append(('基础模板圆弧分离校验',['模板 / 比较范围','周期角间隔（°）/ 对比角平移（°）','标高间隔（m）','校验结论及边界'],
                       [[t['name']+' / C'+str(c['left_index'])+' '+c['left']+' / C'+str(c['right_index'])+' '+c['right'],c['angle_gap']+' / '+c['periodic_shift'],c['z_gap'],arc_states[c['state']]+'；负间隔表示该方向正交叠，两个方向均正交叠拒绝；0°与360°为同一周期边界'] for t in formworks for c in t.get('arc_checks',[])] or [['—','—','—','无同项同层比较对；跨记录重复及角度基准仍在保存、复核和重算时检查']]))
    foundations=[t for t in snapshot['takeoffs'] if t.get('foundation_summary')]
    def round_foundation(d):return d.get('shape') in ('circular','stepped_circular')
    def foundation_dims(d,v,raw=False):
        p=v['parameters' if raw else 'normalized_parameters']
        return ('Φ'+str(p['diameter'])+' × '+str(p['height'])) if round_foundation(d) else ' × '.join(str(p[k]) for k in ('length','width','height'))
    def foundation_proofs(d,v):
        labels=[('diameter','实际直径'),('height','净高')] if round_foundation(d) else [('length','长'),('width','宽'),('height','高')]
        return '；'.join(label+'：'+v['evidence'][k] for k,label in labels)
    ruled_foundations=any(t.get('rule_version')=='railing-post-foundation-1.6' for t in foundations)
    if foundations:
        result.append(('立柱基础体积汇总',['基础构件','来源材料表 / 版本','选入 / 未选立柱项','单组 / 重复 / 全部基础数','毛 / 计量（修约）体积（m³）' if ruled_foundations else '毛 / 净（修约）体积（m³）' if any(t.get('foundation_opening_summary') for t in foundations) else '未修约 / 修约体积（m³）'],
                       [[t['name'],t['foundation_summary']['source_name']+' / V'+str(t['foundation_summary']['source_revision']),str(t['foundation_summary']['selected_item_count'])+' / '+str(t['foundation_summary']['unselected_post_item_count']),str(t['foundation_summary']['single_group_count'])+' / '+t['foundation_summary']['multiplicity']+' / '+t['foundation_summary']['total_count'],t['gross']+' / '+t['quantity']] for t in foundations]))
        result.append(('立柱基础逐项体积',['基础构件 / 立柱项','单体尺寸（m，圆形Φ直径）/ 台阶层数' if any(round_foundation(d) for t in foundations for d in t['foundation_items']) else '单体长宽高（m）/ 台阶层数' if any(d.get('foundation_levels') for t in foundations for d in t['foundation_items']) else '基础长×宽×高（m）','单组 / 全部根数','单基础计量体积（m³）' if ruled_foundations else '单基础净体积（m³）' if any(t.get('foundation_opening_summary') for t in foundations) else '单基础体积（m³）','单组 / 全部计量体积（m³）' if ruled_foundations else '单组 / 全部净体积（m³）' if any(t.get('foundation_opening_summary') for t in foundations) else '单组 / 全部体积（m³）'],
                       [[t['name']+' / M'+str(d['material_index'])+' '+d['material_name'],str(len(d['foundation_levels']))+'层，见逐层表' if d.get('foundation_levels') else foundation_dims(d,d),str(d['single_group_count'])+' / '+d['total_count'],d['single_foundation_volume'],d['single_group_volume']+' / '+d['total_volume']] for t in foundations for d in t['foundation_items']]))
        result.append(('立柱基础原始尺寸与依据',['基础构件 / 立柱项','原始尺寸及单位（圆形Φ直径）' if any(round_foundation(d) for t in foundations for d in t['foundation_items']) else '原始长宽高及单位','尺寸原件 / 适用范围及依据'],
                       [[t['name']+' / M'+str(d['material_index'])+' '+d['material_name'],str(len(d['foundation_levels']))+'层，见逐层原件表' if d.get('foundation_levels') else foundation_dims(d,d,True)+' '+d['length_unit'],('逐层尺寸原件见台阶表' if d.get('foundation_levels') else foundation_proofs(d,d))+'；'+d['measurement_basis']+' 原件：'+d['evidence']['measurement_basis']] for t in foundations for d in t['foundation_items']]))
        result.append(('立柱基础材料范围与修约',['基础构件','材料、范围、修约及原件'],
                       [[t['name'],t['material']+' 原件：'+t['source_evidence']['foundation.material']+'；'+t['measurement_basis']+' 原件：'+t['source_evidence']['foundation.basis']+'；'+t['rounding_rule']+'；修约差额'+t['foundation_summary']['rounding_adjustment']+'m³ 原件：'+t['source_evidence']['foundation.rounding']+'；'+t['notice']+'；'+t['rule_version']] for t in foundations]))
    tiers=[(t,d,v) for t in foundations for d in t['foundation_items'] for v in d.get('foundation_levels',[])]
    if tiers:
        result.append(('台阶基础逐层体积',['基础构件 / 立柱项','自下而上层号','尺寸×净层高（m，圆形Φ直径）' if any(round_foundation(d) for t,d,v in tiers) else '长×宽×净层高（m）','相对底→顶Z（m）','单基础本层 / 全部计量体积（m³）' if ruled_foundations else '单基础本层 / 全部净体积（m³）' if any(t.get('foundation_opening_summary') for t,d,v in tiers) else '单基础本层 / 全部体积（m³）'],
                       [[t['name']+' / M'+str(d['material_index'])+' '+d['material_name'],'L'+str(v['index']),foundation_dims(d,v),v['from_z']+' → '+v['to_z'],v.get('net_level_volume',v['single_level_volume'])+' / '+v['total_volume']] for t,d,v in tiers]))
        result.append(('台阶基础逐层原件',['基础构件 / 立柱项 / 层号','原始尺寸及单位（圆形Φ直径）' if any(round_foundation(d) for t,d,v in tiers) else '原始长宽净层高及单位','逐参数尺寸原件' if any(round_foundation(d) for t,d,v in tiers) else '三项尺寸原件'],
                       [[t['name']+' / M'+str(d['material_index'])+' / L'+str(v['index']),foundation_dims(d,v,True)+' '+d['length_unit'],foundation_proofs(d,v) if round_foundation(d) else '；'.join(label+'：'+v['evidence'][k] for k,label in [('length','长'),('width','宽'),('height','净层高')])] for t,d,v in tiers]))
    circular_foundations=[t for t in foundations if any(round_foundation(d) for d in t['foundation_items'])]
    if circular_foundations:
        result.append(('圆形基础圆周率与口径',['基础构件','圆周率与计算、适用范围'],[[t['name'],'π='+t['foundation_pi_constant']+('；圆柱毛体积π/4×实际直径²×净高，实际孔体积与按阈值计量扣减分别保留，仅计量总体积修约。' if t.get('rule_version')=='railing-post-foundation-1.6' else '；圆柱毛体积π/4×实际直径²×净高，台阶逐层相加，减明确列入的有效圆孔或矩形孔；仅净总体积修约。' if t.get('rule_version')=='railing-post-foundation-1.5' else '；圆柱毛体积π/4×实际直径²×净高，台阶逐层相加，减明确列入的有效圆孔；仅净总体积修约。' if t.get('rule_version')=='railing-post-foundation-1.4' else '；圆柱毛体积π/4×实际直径²×净高，台阶逐层相加，减明确列入的贯通孔；仅净总体积修约。' if t.get('foundation_opening_summary') else '；圆柱π/4×实际直径²×净高，台阶逐层相加；仅总体积修约。')+t['notice']] for t in circular_foundations]))
    opened_foundations=[t for t in foundations if t.get('foundation_opening_summary')]
    opening_rows=[(t,d) for t in opened_foundations for d in t['foundation_items'] if d.get('opening_holes')]
    if opened_foundations:
        balances=[];proofs=[];checks=[];scopes=[];segments=[];vertical_proofs=[]
        mixed_openings=any(t.get('rule_version') in ('railing-post-foundation-1.5','railing-post-foundation-1.6') for t in opened_foundations)
        finite_openings=any(t.get('rule_version') in ('railing-post-foundation-1.4','railing-post-foundation-1.5','railing-post-foundation-1.6') for t in opened_foundations)
        opening_title='基础孔洞' if mixed_openings else '基础圆孔' if finite_openings else '基础贯通孔'
        for t,d in opening_rows:
            name=t['name']+' / M'+str(d['material_index']);count=Decimal(d['total_count'])
            balances.append([name+' / 整个基础',d['single_gross_volume']+' / '+d['single_opening_volume']+' / '+d['single_foundation_volume'],d['total_count'],d['total_gross_volume']+' / '+d['total_opening_volume']+' / '+d['total_volume'],str(len(d['opening_holes']))])
            for v in d.get('foundation_levels',[]):balances.append([name+' / L'+str(v['index']),v['single_level_volume']+' / '+v['single_opening_volume']+' / '+v['net_level_volume'],d['total_count'],v['total_gross_volume']+' / '+v['total_opening_volume']+' / '+v['total_volume'],'仅扣实际相交高度，不另加孔数' if finite_openings else '同一批孔贯穿本层，不另加孔数'])
            scopes.append([name,d['through_holes']['coordinate_basis']+'；坐标原件：'+d['through_holes']['coordinate_evidence_id']+('；π='+t['foundation_pi_constant'] if t.get('foundation_pi_constant') else '；本记录仅矩形几何，面积按长×宽')+'；'+t['notice']+'；'+t['rule_version']])
            for h in d['opening_holes']:
                hn=name+' / H'+str(h['index'])+' '+h['name'];rect=h.get('shape')=='rectangular';hole_labels={'x':'孔心X','y':'孔心Y',**({'length':'孔长','width':'孔宽'} if rect else {'diameter':'实际孔径'}),'measurement_basis':'扣减范围说明' if finite_openings else '贯通说明'}
                dimensions='X='+str(h['parameters']['x'])+'，Y='+str(h['parameters']['y'])+('，轴向矩形长×宽='+str(h['parameters']['length'])+'×'+str(h['parameters']['width'])+'（长沿X，宽沿Y）' if rect else '，实际Φ='+str(h['parameters']['diameter']))+' '+h['unit']
                proofs.append([hn,dimensions,h['through_height']+' / '+h['single_volume'],'；'.join(label+'：'+h['evidence'][k] for k,label in hole_labels.items())+'；'+h['measurement_basis']])
                if h.get('segments'):
                    vertical_proofs.append([hn,h['z_start']+' → '+h['z_end']+' m',h['through_height']+' m','；'.join(('起始Z' if k=='z_start' else '终止Z')+'='+str(h['vertical_range']['parameters'][k])+' '+h['unit']+'；原件：'+h['vertical_range']['evidence'][k] for k in ('z_start','z_end')) if 'vertical_range' in h else '贯通全部基础；起止Z由已审各层高度累计，不另填深度'])
                    for v in h['segments']:segments.append([hn+' / L'+str(v['level_index']),v['z_start']+' → '+v['z_end'],v['height'],v['single_volume']])
                for c in h['containments']:checks.append([hn+' / L'+str(c['level_index']),'逐层包含','；'.join({'x':'X方向','y':'Y方向','radial_squared':'径向平方','corner_squared':'最远角点平方'}[k]+'='+v+('m²' if k.endswith('_squared') else 'm') for k,v in c['margins'].items()),'相切' if c['state']=='tangent' else '完全在内'])
            for c in d['opening_checks']:
                state={'tangent':'截面相切','disjoint':'截面分离','vertically_disjoint':'标高相离','vertical_tangent':'仅端面接触'}.get(c['state'],c['state']) if 'vertical_gap' in c else '相切' if c['state']=='tangent' else '分离'
                checks.append([name+' / '+c['left']+' / '+c['right'],{'rectangle_rectangle':'矩形轴向分离余量','circle_rectangle':'圆心至矩形最近点距离平方−半径平方'}.get(c.get('separation_kind'),'孔心距平方−半径和平方'),c['margin']+c.get('margin_unit','m²'),state])
                if 'vertical_gap' in c:checks.append([name+' / '+c['left']+' / '+c['right'],'标高分离余量（负值为正高度相交）',c['vertical_gap']+'m',state])
        result.append((opening_title+'毛扣净',['基础构件 / 立柱项 / 基础或层','每基础毛 / 计量扣 / 计量（m³）' if ruled_foundations else '每基础毛 / 扣 / 净（m³）','全部基础个数','全部毛 / 计量扣 / 计量（m³）' if ruled_foundations else '全部毛 / 扣 / 净（m³）','每基础孔数或说明'],balances))
        result.append((opening_title+'原始依据',['基础构件 / 立柱项 / 孔','原始孔心XY及实际截面尺寸 / 单位' if mixed_openings else '原始孔心XY及实际孔径 / 单位','有效高度（m）/ 每基础实际孔体积（m³）' if ruled_foundations else '有效高度（m）/ 每基础本孔扣减（m³）' if finite_openings else '贯通高（m）/ 每基础本孔扣减（m³）','逐参数原件及扣减范围说明' if finite_openings else '逐参数原件及贯通说明'],proofs))
        result.append((opening_title+'校验',['基础构件 / 立柱项 / 孔或孔对','校验类别','精确余量（非结构净距验收）','几何状态'],checks))
        result.append((opening_title+'范围与常量',['基础构件 / 立柱项','共同中心坐标、原件、圆周率与适用范围'],scopes))
        if finite_openings:
            result.append((opening_title+'标高依据',['基础构件 / 立柱项 / 孔','实际起止Z（m，基础底为0向上）','有效高度','原始标高、单位及原件'],vertical_proofs))
            result.append((opening_title+'逐层相交',['基础构件 / 立柱项 / 孔 / 层','实际相交起止Z（m）','有效相交高度（m）','每基础本孔本层实际体积（m³）' if ruled_foundations else '每基础本孔本层扣减（m³）'],segments))
        if ruled_foundations:
            policies=[];actuals=[]
            metric_labels={'section_area':'每基础单孔截面积','single_volume':'每基础单孔实际体积'};comparisons={'gt':'严格大于（等于不扣）','gte':'大于等于（等于也扣）'}
            for t,d in opening_rows:
                r=d.get('opening_deduction_rule')
                if not r:continue
                name=t['name']+' / M'+str(d['material_index'])
                policies.append([name,metric_labels[r['metric']]+'；'+comparisons[r['comparison']],str(r['threshold'])+' '+r['unit'],r['normalized_threshold']+' '+r['canonical_unit'],r['basis']+'；原件：'+r['evidence_id']+'；每基础'+str(r['eligible_count'])+'孔扣减、'+str(r['excluded_count'])+'孔不扣。'])
                actuals.append([name+' / 每基础',d['single_geometric_opening_volume'],d['single_geometric_net_volume'],d['single_opening_volume'],d['single_foundation_volume'],'计量体积与实际实体净体积分开保留'])
                actuals.append([name+' / 全部基础',d['total_geometric_opening_volume'],d['total_geometric_net_volume'],d['total_opening_volume'],d['total_volume'],d['total_count']+'个基础'])
                for v in d.get('foundation_levels',[]):actuals.append([name+' / 每基础L'+str(v['index']),v['geometric_opening_volume'],v['geometric_net_volume'],v['single_opening_volume'],v['net_level_volume'],'本层实际孔体积不因计量阈值消失'])
                for h in d['opening_holes']:
                    decision=h['deduction_decision'];actuals.append([name+' / 每基础H'+str(h['index'])+' '+h['name'],h['single_volume'],'—',h['deducted_volume'],'—',decision['metric_value']+' '+decision['unit']+'；'+{'gt':'大于','eq':'等于','lt':'小于'}[decision['relation']]+'阈值；'+('扣全部实际体积' if decision['eligible'] else '不扣，实际孔体积保留')])
            result.append(('基础孔洞扣减阈值',['基础构件 / 立柱项','单孔指标与等值边界','原始阈值 / 单位','换算阈值 / 单位','计量适用范围及原件'],policies))
            result.append(('基础孔洞实际与计量',['基础构件 / 立柱项 / 范围','实际孔体积（m³）','实际实体净体积（m³）','计量扣减（m³）','未修约计量体积（m³）','范围或逐孔决定'],actuals))
    materials=[t for t in snapshot['takeoffs'] if t.get('material_summary')]
    if materials:
        labels={'source_single_length':'来源单组未修约计入长度（m）','single_group_mass':'单组材料质量（kg）','multiplicity':'来源重复数量','unrounded_quantity':'未修约总质量（kg）','rounding_digits':'总质量修约位数','rounding_adjustment':'总质量修约差额（kg）'}
        result.append(('栏杆材料质量汇总',['材料表','字段','内容'],
                       [[t['name'],label,str(t['material_summary'][key])] for t in materials for key,label in labels.items()]+
                       [[t['name'],key,value] for t in materials for key,value in [('来源',t['material_summary']['source_name']+' / V'+str(t['material_summary']['source_revision'])),('材料类别',t['material']),('计长口径',t['material_summary']['source_length_basis']),('工程量',t['quantity']+' kg'),('计量依据',t['measurement_basis']),('算法及精度',t['rule_version']+'；'+t['rounding_rule']),('适用边界',t['notice'])]]))
        result.append(('栏杆材料逐项计算',['材料表 / 项目','类型 / 每组数量','单道或单件长度（m）','每米质量（kg/m）','单组线材总长（m）','单组质量（kg）','说明及依据'],
                       [[t['name']+' / '+v['name'],('连续道数' if v['mode']=='continuous' else '定长件数')+' / '+v['normalized_quantity'],v['single_member_length'],v['normalized_unit_weight'],v['single_group_stock_length'],v['single_group_mass'],v['measurement_basis']+'；'+material_proofs(v)] for t in materials for v in t['material_items'] if v['mode'] in ('continuous','cut_pieces')]))
        result.append(('栏杆材料原始参数',['材料表 / 项目','每组道数或件数','每米质量原值及单位','单件下料原值及单位','逐参数依据'],
                       [[t['name']+' / '+v['name'],str(v['quantity']),str(v['unit_weight'])+' '+v['weight_unit'],str(v['cut_length'])+' '+v['length_unit'] if v['mode']=='cut_pieces' else '采用来源单组未修约计入长度',material_proofs(v)] for t in materials for v in t['material_items'] if v['mode'] in ('continuous','cut_pieces')]))
        result.append(('栏杆材料计长来源',['材料表 / 来源原段','原边或原段 / 决定','来源计入长（m）','局部区间（m）','范围说明及依据'],
                       [[t['name']+' / '+('S'+str(e['edge_index']) if e['role']=='route' else 'R'+str(e['ring_index'])+' E'+str(e['edge_index'])),('设计线段' if e['role']=='route' else '外轮廓' if e['role']=='outer' else '孔')+' / '+{'exclude':'排除','full':'整段','ranges':'局部区间'}[e['mode']],e['selected_length'],'；'.join(v['s_from']+'→'+v['s_to'] for v in e['normalized_ranges']) if e['mode']=='ranges' else '整段' if e['mode']=='full' else '无计入',e['basis']+'；'+e['evidence_id']] for t in materials for e in t['source_railing']['railing_edges']]))
    allowance_rows=[(t,v) for t in materials for v in t['material_items'] if 'stock_planning' in v]
    if allowance_rows:
        stock_labels={'net':'净用量','lap':'搭接增量','loss':'损耗增量','procurement':'备料量'}
        result.append(('线材备料汇总',['材料表','已编 / 未编备料项数','组成','合计长度（m）','合计质量（kg）'],
                       [[t['name'],str(t['stock_summary']['item_count'])+' / '+str(t['stock_summary']['unplanned_item_count'])+'（含不适用项）',label,t['stock_summary']['total'][key+'_length'],t['stock_summary']['total'][key+'_mass']] for t in materials if 'stock_summary' in t for key,label in stock_labels.items()]))
        stock_proofs={'joint_count':'每组接头总数','lap_length':'每处搭接长度','loss_percent':'损耗率','measurement_basis':'范围及不重复说明'}
        result.append(('线材搭接损耗备料',['材料表 / 材料项','组成','单组长度（m） / 质量（kg）','来源重复','全部长度（m） / 质量（kg）'],
                       [[t['name']+' / '+v['name'],label,v['stock_planning']['single_group'][key+'_length']+' / '+v['stock_planning']['single_group'][key+'_mass'],t['material_summary']['multiplicity'],v['stock_planning']['total'][key+'_length']+' / '+v['stock_planning']['total'][key+'_mass']] for t,v in allowance_rows for key,label in stock_labels.items()]))
        result.append(('线材备料参数依据',['材料表 / 材料项','每组接头总数 / 每处搭接','损耗率（%）','范围、净量确认及逐参数原件'],
                       [[t['name']+' / '+v['name'],str(v['stock_allowance']['joint_count'])+'处 / '+str(v['stock_allowance']['lap_length'])+' '+v['stock_allowance']['length_unit'],str(v['stock_allowance']['loss_percent']),v['stock_allowance']['measurement_basis']+'；已确认原净下料不含本次搭接与损耗；'+'；'.join(stock_proofs[k]+': '+eid for k,eid in v['stock_allowance']['evidence'].items())] for t,v in allowance_rows]))
        result.append(('线材备料规则与范围',['材料表 / 材料项','公式、适用范围及版本'],
                       [[t['name']+' / '+v['name'],v['stock_planning']['notice']+'；'+v['stock_planning']['rule_version']] for t,v in allowance_rows]))
    cutting_rows=[(t,v) for t in materials for v in t['material_items'] if 'stock_cutting' in v]
    if cutting_rows:
        cutting_labels={'net':'净下料','kerf':'锯缝','trim':'两端切除','scrap':'报废余料','retained':'待留用余料','loss':'损耗合计（锯缝＋切除＋报废）','purchased':'原材购入'}
        cutting_parameters={'stock_length':'原材定尺','kerf_per_piece':'每件锯缝','trim_start':'起端切除','trim_end':'终端切除','reusable_min_length':'可留用余料下限','measurement_basis':'范围口径说明'}
        result.append(('定尺下料汇总',['材料表 / 项目 / 本表覆盖','全部需求件数','每根最大容量','原材根数','净质量（kg）','购入质量（kg）'],
                       [[t['name']+' / '+v['name']+'；已编'+str(t['cutting_summary']['item_count'])+'项、未编'+str(t['cutting_summary']['unplanned_item_count'])+'项',str(v['stock_cutting']['required_piece_count']),v['stock_cutting']['capacity'],str(v['stock_cutting']['stock_count']),v['stock_cutting']['total']['net_mass'],v['stock_cutting']['total']['purchased_mass']] for t,v in cutting_rows]))
        result.append(('定尺下料模式',['材料表 / 项目','原材编号 / 根数','每根件数 / 共出件数','每根净长 / 锯缝 / 切除（m）','每根余料（m）','余料归属'],
                       [[t['name']+' / '+v['name'],'#'+str(p['first_stock'])+'→#'+str(p['last_stock'])+' / '+str(p['stock_count']),str(p['pieces_per_stock'])+' / '+str(p['produced_count']),p['per_stock']['net_length']+' / '+p['per_stock']['kerf_length']+' / '+p['per_stock']['trim_length'],p['offcut_length'],{'none':'无余料','retain':'待留用（未入库）','scrap':'报废'}[p['offcut_disposition']]] for t,v in cutting_rows for p in v['stock_cutting']['patterns']]))
        result.append(('定尺长度质量台账',['材料表 / 项目','全部重复后组成','长度（m）','质量（kg）'],
                       [[t['name']+' / '+v['name'],label,v['stock_cutting']['total'][key+'_length'],v['stock_cutting']['total'][key+'_mass']] for t,v in cutting_rows for key,label in cutting_labels.items()]))
        result.append(('定尺取整与原件',['材料表 / 项目','取整证明、原始参数及范围依据'],
                       [[t['name']+' / '+v['name'],'容量：'+g['capacity_certificate']['used_at_capacity']+' ≤ 可用'+g['capacity_certificate']['usable_length']+' < 多一件需'+g['capacity_certificate']['used_at_next']+'m；少一根容量'+g['stock_count_certificate']['previous_capacity']+' < 需求'+str(g['required_piece_count'])+' ≤ 本次容量'+g['stock_count_certificate']['current_capacity']+'件；购入'+g['balance']['purchased_length']+'m = 净下料＋总损耗＋待留用'+g['balance']['net_plus_loss_plus_retained']+'m；差额0m；'+g['measurement_basis']+'；'+'；'.join(label+('：'+str(g[key])+g['length_unit'] if key!='measurement_basis' else '')+' 原件：'+g['evidence'][key] for key,label in cutting_parameters.items())+'；已确认单件下料完整、每件一次锯缝（含最后一件）及两端切除未重复；'+g['notice']+'；'+g['rule_version']] for t,v in cutting_rows for g in [v['stock_cutting']]]))
    mixed_rows=[(t,g) for t in materials for g in t.get('mixed_stock_cutting',{}).get('groups',[])]
    if mixed_rows:
        mixed_status={'length_optimal_bound':'已证明购长/根数双目标最优（达到下界）','length_optimal_search':'已证明购长/根数双目标最优（搜索穷尽）','length_feasible_unproven':'多定尺可行但尚未证明双目标最优','optimal_bound':'已证明最少根数（达到长度下界）','optimal_search':'已证明最少根数（搜索穷尽）','feasible_unproven':'可行但尚未证明最优'}
        mixed_labels={'net':'净下料','kerf':'锯缝','trim':'两端切除','scrap':'报废余料','retained':'待留用余料','loss':'损耗合计（锯缝＋切除＋报废）','purchased':'原材购入'}
        mixed_params={'stock_length':'原材定尺','kerf_per_piece':'每件锯缝','trim_start':'起端切除','trim_end':'终端切除','reusable_min_length':'留用余料下限','material_basis':'材质截面兼容说明','measurement_basis':'混排下料范围说明'}
        def mixed_proof(g):
            p=g['proof']
            return ('先最小购长再最少根数；购长下界'+p['length_lower_bound']+'m、初始'+(p['initial_purchased_length']+'m / '+str(p['initial_stock_count'])+'根' if p['initial_purchased_length'] is not None else '未找到可行方案')+'、当前'+p['purchased_length']+'m / '+str(g['stock_count'])+'根；购长未证差距'+p['unproven_length_gap']+'m'+('；规格及目标说明：' if g['stock_selection'].get('supply_limited') else '；按需供应说明：')+g['stock_choices']['measurement_basis']+' 原件：'+g['stock_choices']['evidence_id']+('；有限供应范围：'+g['stock_choices']['supply_limits']['measurement_basis']+' 原件：'+g['stock_choices']['supply_limits']['evidence_id'] if g['stock_selection'].get('supply_limited') else '')) if 'stock_selection' in g else '长度下界'+str(p['lower_bound'])+'、当前'+str(g['stock_count'])+'根'
        result.append(('混合下料汇总',['材料表 / 混排组','需求件数 / 原材根数','下界 / 初始（多定尺为购长m）' if any('stock_selection' in g for _,g in mixed_rows) else '根数下界 / 初始方案','搜索节点 / 上限','最优性结论','购入长度（m） / 质量（kg）'],
                       [[t['name']+' / '+g['name'],str(g['required_piece_count'])+' / '+str(g['stock_count']),(g['proof']['length_lower_bound']+'m / '+(g['proof']['initial_purchased_length']+'m' if g['proof']['initial_purchased_length'] is not None else '未找到') if 'stock_selection' in g else str(g['proof']['lower_bound'])+' / '+str(g['proof']['initial_stock_count'])),str(g['proof']['visited_nodes'])+' / '+str(g['proof']['node_limit']),mixed_status[g['proof']['status']],g['total']['purchased_length']+' / '+g['total']['purchased_mass']] for t,g in mixed_rows]))
        result.append(('混合下料需求',['材料表 / 混排组','原材料项','全部重复后件数','单件完整下料长（m）'],
                       [[t['name']+' / '+g['name'],'M'+str(d['item_index'])+' '+d['name'],str(d['piece_count']),d['cut_length']] for t,g in mixed_rows for d in g['demands']]))
        result.append(('混合下料原材明细',['材料表 / 混排组','原材号 / 出件数','所下材料项及项内件号','每根净长 / 锯缝 / 切除（m）','每根余料（m） / 归属'],
                       [[t['name']+' / '+g['name'],'#'+str(b['index'])+(' S'+str(b['stock_choice_index'])+' '+b['values']['purchased_length']+'m' if 'stock_choice_index' in b else '')+' / '+str(b['piece_count']),'；'.join('M'+str(p['item_index'])+' #'+str(p['piece_index']) for p in b['pieces']),b['values']['net_length']+' / '+b['values']['kerf_length']+' / '+b['values']['trim_length'],b['offcut_length']+' / '+{'none':'无余料','retain':'待留用（未入库）','scrap':'报废'}[b['offcut_disposition']]] for t,g in mixed_rows for b in g['bars']]))
        result.append(('混合下料逐件位置',['材料表 / 混排组','原材号','材料项 / 项内件号','实际下料长（m）','原材起端S起→终（m）','本件锯缝终S（m）'],
                       [[t['name']+' / '+g['name'],'#'+str(b['index']),'M'+str(p['item_index'])+' '+p['name']+' #'+str(p['piece_index']),p['cut_length'],p['from_s']+' → '+p['to_s'],p['kerf_to_s']] for t,g in mixed_rows for b in g['bars'] for p in b['pieces']]))
        result.append(('混合下料长度质量',['材料表 / 混排组','全部重复后组成','长度（m）','质量（kg）'],
                       [[t['name']+' / '+g['name'],label,g['total'][key+'_length'],g['total'][key+'_mass']] for t,g in mixed_rows for key,label in mixed_labels.items()]))
        result.append(('混合下料依据与边界',['材料表 / 混排组','兼容与下料依据、搜索结论及适用范围'],
                       [[t['name']+' / '+g['name'],mixed_status[g['proof']['status']]+'；'+mixed_proof(g)+'；已确认同材质同截面、单件下料完整及锯缝切除未重复；'+g['material_basis']+'；'+g['measurement_basis']+'；'+'；'.join(('S1 ' if (g.get('stock_selection',{}).get('processing_independent') and key in ('trim_start','trim_end','reusable_min_length') or g.get('stock_selection',{}).get('kerf_independent') and key=='kerf_per_piece') else '')+label+(' '+str(g[key])+g['length_unit'] if key in ('stock_length','kerf_per_piece','trim_start','trim_end','reusable_min_length') else '')+' 原件：'+g['evidence'][key] for key,label in mixed_params.items())+'；长度平衡差额'+g['balance_difference']+'m；'+g['notice']+'；'+g['rule_version']] for t,g in mixed_rows]))
    multi_rows=[(t,g,o) for t,g in mixed_rows for o in g.get('stock_selection',{}).get('options',[])]
    if multi_rows:
        result.append(('多定尺候选与选用',['材料表 / 混排组','候选编号','定尺（m）','选用根数','定尺原件','目标 / 供应依据'],
                       [[t['name']+' / '+g['name'],'S'+str(o['index']),o['stock_length'],str(o['stock_count']),g['evidence']['stock_length'] if o['index']==1 else g['stock_choices']['alternatives'][o['index']-2]['evidence_id'],'先最小购长，再最少根数；'+('规格与目标原件：' if g['stock_selection'].get('supply_limited') else '按需供应原件：')+g['stock_choices']['evidence_id']] for t,g,o in multi_rows]))
    limited_rows=[(t,g,o) for t,g,o in multi_rows if g['stock_selection'].get('supply_limited')]
    if limited_rows:
        result.append(('有限供应选用台账',['材料表 / 混排组','候选 / 定尺（m）','可供上限（根）','选用（根）','未选用供应（根）','上限原件'],
                       [[t['name']+' / '+g['name'],'S'+str(o['index'])+' / '+o['stock_length'],str(o['supply_limit']),str(o['stock_count']),str(o['unselected_supply']),g['stock_choices']['supply_limits']['quantities'][o['index']-1]['evidence_id']] for t,g,o in limited_rows]))
    processing_rows=[(t,g,o) for t,g,o in multi_rows if g['stock_selection'].get('processing_independent')]
    if processing_rows:
        result.append(('逐规格加工参数',['材料表 / 混排组 / 规格','起端 / 终端切除（m）','实际可用长（m）','留用下限（m）','逐参数原件'],
                       [[t['name']+' / '+g['name']+' / S'+str(o['index'])+' '+o['stock_length']+'m',o['processing']['trim_start']+' / '+o['processing']['trim_end'],o['processing']['usable_length'],o['processing']['reusable_min_length'],'；'.join(label+'：'+(g['evidence'] if o['index']==1 else g['stock_choices']['processing']['alternatives'][o['index']-2]['evidence'])[key] for key,label in [('trim_start','起端'),('trim_end','终端'),('reusable_min_length','留用下限')])] for t,g,o in processing_rows]))
        result.append(('逐规格加工范围依据',['材料表 / 混排组','范围、确认及原件'],
                       [[t['name']+' / '+g['name'],('已确认S1沿用本组、备选逐规格核定、每件锯缝以逐规格锯缝表为准且端切不重复；' if g.get('stock_selection',{}).get('kerf_independent') else '已确认S1沿用本组、备选逐规格核定、每件统一锯缝且端切不重复；')+g['stock_choices']['processing']['measurement_basis']+' 原件：'+g['stock_choices']['processing']['evidence_id']+'；'+g['notice']] for t,g in mixed_rows if g.get('stock_selection',{}).get('processing_independent')]))
    kerf_rows=[(t,g,o) for t,g,o in multi_rows if g['stock_selection'].get('kerf_independent')]
    if kerf_rows:
        result.append(('逐规格每件锯缝',['材料表 / 混排组','规格 / 定尺（m）','每件锯缝（m）','锯缝原件'],
                       [[t['name']+' / '+g['name'],'S'+str(o['index'])+' / '+o['stock_length'],o['kerf']['kerf_per_piece'],g['evidence']['kerf_per_piece'] if o['index']==1 else g['stock_choices']['kerfs']['alternatives'][o['index']-2]['evidence_id']] for t,g,o in kerf_rows]))
        result.append(('逐规格锯缝范围依据',['材料表 / 混排组','范围、确认及原件'],
                       [[t['name']+' / '+g['name'],'已确认S1沿用本组、S2起锯缝逐规格核定，每件含最后一件，未重复计入净下料或端切；本表优先于通用锯缝说明；'+g['stock_choices']['kerfs']['measurement_basis']+' 原件：'+g['stock_choices']['kerfs']['evidence_id']+'；'+g['notice']] for t,g in mixed_rows if g.get('stock_selection',{}).get('kerf_independent')]))
    panels=[(t,v) for t in materials for v in t['material_items'] if v['mode']=='rect_panels']
    if panels:
        result.append(('板网片净面积与质量',['材料表 / 板网片','外矩形 / 开口并集（m²）','开口直加 / 消除重复（m²）','单片净面积（m²）','每组片数 / 净面积（m²）','面密度（kg/m²）','单组质量（kg）','口径及逐项依据'],
                       [[t['name']+' / '+v['name'],v['panel_geometry']['gross']+' / '+v['panel_geometry']['deduction'],v['panel_geometry']['planar_summary']['naive_deduct_area']+' / '+v['panel_geometry']['planar_summary']['deduct_overlap_area'],v['single_panel_net_area'],v['normalized_quantity']+' / '+v['single_group_net_area'],v['normalized_unit_weight'],v['single_group_mass'],v['measurement_basis']+'；'+material_proofs(v)] for t,v in panels]))
        result.append(('板网片原始尺寸与开口',['材料表 / 板网片 / 区域','加扣','原始XY / 长宽','原始单位','原始面密度 / 每组片数','局部坐标口径及区域说明','逐参数依据'],
                       [[t['name']+' / '+v['name']+' / '+a['name'],'外矩形' if a['operation']=='add' else '扣开口',str(a['parameters']['x'])+' / '+str(a['parameters']['y'])+' / '+str(a['parameters']['length'])+' / '+str(a['parameters']['width']),a['unit'],str(v['unit_weight'])+' '+v['weight_unit']+' / '+str(v['quantity']),v['panel_geometry']['coordinate_basis']+'；'+a['measurement_basis'],material_proofs(a)] for t,v in panels for a in v['panel_geometry']['planar_regions']]))
        result.append(('板网片面积分条闭合',['材料表 / 板网片','X起终（m）','条宽（m）','外轮廓Y区间（m）','扣开口Y并集（m）','条带净面积（m²）'],
                       [[t['name']+' / '+v['name'],a['x_from']+' → '+a['x_to'],a['width'],json.dumps(a['add_intervals']),json.dumps(a['deduct_intervals']),a['net_area']] for t,v in panels for a in v['panel_geometry']['planar_strips']]))
    polygon_panels=[(t,v) for t in materials for v in t['material_items'] if v['mode']=='polygon_panels']
    if polygon_panels:
        result.append(('异形板网片面积与质量',['材料表 / 板片','外轮廓 / 独立孔（m²）','单片净面积（m²）','每组片数 / 净面积（m²）','面密度（kg/m²）','单组质量（kg）','原始面密度 / 片数','展开口径、计量及依据'],
                       [[t['name']+' / '+v['name'],v['panel_geometry']['gross']+' / '+v['panel_geometry']['deduction'],v['single_panel_net_area'],v['normalized_quantity']+' / '+v['single_group_net_area'],v['normalized_unit_weight'],v['single_group_mass'],str(v['unit_weight'])+' '+v['weight_unit']+' / '+str(v['quantity']),v['coordinate_basis']+'；'+v['measurement_basis']+'；'+material_proofs(v)] for t,v in polygon_panels]))
        result.append(('异形板网片原始顶点',['材料表 / 板片 / 轮廓','顶点','原始XY / 单位','标准XY（m）','逐坐标依据'],
                       [[t['name']+' / '+v['name']+' / R'+str(r['index'])+' '+r['name'],'P'+str(p['index'])+' '+p['name'],str(p['parameters']['x'])+' / '+str(p['parameters']['y'])+' '+p['unit'],p['normalized_parameters']['x']+' / '+p['normalized_parameters']['y'],'X：'+p['evidence']['x']+'；Y：'+p['evidence']['y']] for t,v in polygon_panels for r in v['panel_geometry']['polygon_rings'] for p in r['points']]))
        result.append(('异形板网片逐边面积',['材料表 / 板片 / 轮廓','类型 / 方向','起终点','有向叉积（m²）','轮廓面积（m²）','范围及依据'],
                       [[t['name']+' / '+v['name']+' / R'+str(r['index'])+' '+r['name'],('外轮廓' if r['role']=='outer' else '扣孔')+' / '+('顺时针' if r['orientation']=='clockwise' else '逆时针'),'P'+str(e['from_index'])+' → P'+str(e['to_index']),e['cross_product'],r['area'],r['measurement_basis']+'；'+r['evidence_id']] for t,v in polygon_panels for r in v['panel_geometry']['polygon_rings'] for e in r['edges']]))
        result.append(('异形板网片孔边界校验',['材料表 / 板片','轮廓或孔A / 孔B','关系','结论'],
                       [[t['name']+' / '+v['name'],c['left']+' / '+c['right'],'严格在内且不接触外边界' if c['relation']=='strictly_inside' else '孔间分离，无接触、重叠或包含','通过'] for t,v in polygon_panels for c in v['panel_geometry']['polygon_checks']]+
                       [[t['name']+' / '+v['name'],'无孔','明确仅外轮廓','简单外轮廓通过'] for t,v in polygon_panels if not v['panel_geometry']['polygon_checks']]))
    posts=[(t,v) for t in materials for v in t['material_items'] if v['mode'] in ('spaced_posts','spaced_added_posts','spaced_adjusted_posts','spaced_added_adjusted_posts')]
    if posts:
        has_equal_adjustments=any(v['mode'] in ('spaced_adjusted_posts','spaced_added_adjusted_posts') for t,v in posts)
        result.append(('立柱均分布置质量',['材料表 / 材料项','来源S口径','单组根数','每根下料长（m）','每米质量（kg/m）','单组总长 / 质量','原始下料长及每米质量','规则与依据'],
                       [[t['name']+' / '+v['name'],t['material_summary']['source_length_basis'],v['normalized_quantity'],v['single_member_length'],v['normalized_unit_weight'],v['single_group_stock_length']+' m / '+v['single_group_mass']+' kg',str(v['cut_length'])+' '+v['length_unit']+' / '+str(v['unit_weight'])+' '+v['weight_unit'],v['measurement_basis']+'；'+material_proofs(v)+'；'+('原最大间距及跨数仅描述移前布置；完整规则见均分移柱规则与范围' if v['mode']=='spaced_adjusted_posts' else '原规则及加柱共同构成移前布置；完整规则见含加柱移柱规则与范围' if v['mode']=='spaced_added_adjusted_posts' else v['post_layout']['notice'])] for t,v in posts]))
        result.append(('立柱逐区间间距与归属',['材料表 / 材料项 / 区间','起终S（m）','原最大间距原值' if has_equal_adjustments else '最大间距原值','原标准最大 / 原均分跨间距（m）' if has_equal_adjustments else '标准最大 / 均分跨间距（m）','原跨间 / 原内部柱' if has_equal_adjustments else '跨间 / 内部柱','起端 / 终端计入','本区间根数','说明及逐参数依据'],
                       [[t['name']+' / '+v['name']+' / '+r['label'],r['s_from']+' → '+r['s_to'],str(r['spacing'])+' '+r['spacing_unit'],r['normalized_spacing']+' / '+r['actual_spacing'],str(r['interval_count'])+' / '+str(r['interior_count']),('计入' if r['start_post'] else '不计')+' / '+('计入' if r['end_post'] else '不计'),str(r['post_count']),r['measurement_basis']+'；'+material_proofs(r)] for t,v in posts for r in v['post_layout']['post_ranges']]))
        def fraction_text(r):return r['numerator'] if r['denominator']=='1' else r['numerator']+' / '+r['denominator']
        result.append(('立柱跨间数精确校验',['材料表 / 材料项 / 区间','最小正整数N','下界(N−1)²×间距²','未修约区间长度²','上界N²×间距²','严格关系及规则'],
                       [[t['name']+' / '+v['name']+' / '+r['label'],str(r['interval_count']),fraction_text(r['ceil_certificate']['lower_spacing_square']),fraction_text(r['ceil_certificate']['range_length_square']),fraction_text(r['ceil_certificate']['upper_spacing_square']),'下界 < 区间长度² ≤ 上界；'+v['post_layout']['rule_version']] for t,v in posts for r in v['post_layout']['post_ranges']]))
        result.append(('立柱逐根端距',['材料表 / 材料项','立柱编号','来源原段区间','均分节点号','原段起点端距S（m）','位置'],
                       [[t['name']+' / '+v['name'],str(r['index']),r['range_label'],str(r.get('bay_index','-')),r['s'],('移柱'+((' 原加柱 #'+str(r['original_node_index'])+' '+r['additional_label']) if r.get('original_node_type')=='additional' else (' 原规则 j='+str(r['original_node_index'])) if 'original_node_type' in r else '')+'，原S='+r['original_s']) if r['position_kind']=='moved' else ('构造加柱：'+r['additional_label']) if r['position_kind']=='additional' else {'start':'区间起端','end':'区间终端','interior':'内部均分柱'}[r['position_kind']]] for t,v in posts for r in v['post_layout']['post_stations']]))
    fixed_posts=[(t,v) for t in materials for v in t['material_items'] if v['mode'] in ('fixed_posts','fixed_added_posts','fixed_adjusted_posts','fixed_added_adjusted_posts')]
    if fixed_posts:
        has_adjusted_posts=any(v['mode'] in ('fixed_adjusted_posts','fixed_added_adjusted_posts') for t,v in fixed_posts)
        fixed_directions={'from_start':'从区间起端向终端','from_end':'从区间终端向起端'}
        def fixed_fraction(r):return r['numerator'] if r['denominator']=='1' else r['numerator']+' / '+r['denominator']
        result.append(('定距立柱质量汇总',['材料表 / 材料项','来源S口径','单组根数','每根下料长（m）','每米质量（kg/m）','单组总长 / 质量','原始下料长及每米质量','规则与依据'],
                       [[t['name']+' / '+v['name'],t['material_summary']['source_length_basis'],v['normalized_quantity'],v['single_member_length'],v['normalized_unit_weight'],v['single_group_stock_length']+' m / '+v['single_group_mass']+' kg',str(v['cut_length'])+' '+v['length_unit']+' / '+str(v['unit_weight'])+' '+v['weight_unit'],v['measurement_basis']+'；'+material_proofs(v)+'；'+('原规则及加柱共同构成移前布置；完整规则见含加柱移柱规则与范围' if v['mode']=='fixed_added_adjusted_posts' else v['post_layout']['notice'])] for t,v in fixed_posts]))
        result.append(('定距立柱锚向余跨与归属',['材料表 / 材料项 / 区间','原段起终S（m）','原固定间距原值 / 标准m' if has_adjusted_posts else '固定间距原值 / 标准m','定距锚向','原常规跨数 / 原余跨（m）' if has_adjusted_posts else '常规跨数 / 余跨（m）','余跨所在端','两端归属 / 根数','说明及逐参数依据'],
                       [[t['name']+' / '+v['name']+' / '+r['label'],r['s_from']+' → '+r['s_to'],str(r['spacing'])+' '+r['spacing_unit']+' / '+r['regular_spacing'],fixed_directions[r['direction']],str(r['regular_interval_count'])+' / '+r['residual_spacing'],{'none':'整除无余跨','start':'区间起端','end':'区间终端'}[r['residual_side']],('起端计' if r['start_post'] else '起端不计')+' / '+('终端计' if r['end_post'] else '终端不计')+' / '+str(r['post_count']),r['measurement_basis']+'；'+material_proofs(r)] for t,v in fixed_posts for r in v['post_layout']['post_ranges']]))
        result.append(('定距立柱跨数精确校验',['材料表 / 材料项 / 区间','最小正整数N / 常规跨数','下界(N−1)²×间距²','未修约区间长度²','上界N²×间距²','严格关系及规则'],
                       [[t['name']+' / '+v['name']+' / '+r['label'],str(r['interval_count'])+' / '+str(r['regular_interval_count']),fixed_fraction(r['ceil_certificate']['lower_spacing_square']),fixed_fraction(r['ceil_certificate']['range_length_square']),fixed_fraction(r['ceil_certificate']['upper_spacing_square']),'下界 < 区间长度² ≤ 上界；整除由精确上界相等判定；'+v['post_layout']['rule_version']] for t,v in fixed_posts for r in v['post_layout']['post_ranges']]))
        result.append(('定距立柱逐根端距',['材料表 / 材料项','立柱编号','来源原段区间','定距锚向','原段起点端距S（m）','位置'],
                       [[t['name']+' / '+v['name'],str(r['index']),r['range_label'],fixed_directions[r['direction']],r['s'],('移柱'+((' 原加柱 #'+str(r['original_node_index'])+' '+r['additional_label']) if r.get('original_node_type')=='additional' else (' 原规则 j='+str(r['original_node_index'])) if 'original_node_type' in r else '')+'，原S='+r['original_s']) if r['position_kind']=='moved' else ('构造加柱：'+r['additional_label']) if r['position_kind']=='additional' else {'start':'区间起端','end':'区间终端','interior':'内部定距柱'}[r['position_kind']]] for t,v in fixed_posts for r in v['post_layout']['post_stations']]))
    added_posts=[(t,v) for t in materials for v in t['material_items'] if v['mode'] in ('fixed_added_posts','spaced_added_posts','fixed_added_adjusted_posts','spaced_added_adjusted_posts')]
    if added_posts:
        result.append(('构造加柱数量组成',['材料表 / 材料项 / 区间','原规则柱数' if any(v['mode'] in ('spaced_added_posts','spaced_added_adjusted_posts') for t,v in added_posts) else '原定距柱数','构造加柱数','合计根数','规则'],
                       [[t['name']+' / '+v['name']+' / '+r['label'],str(r['base_post_count']),str(r['additional_post_count']),str(r['post_count']),('原规则柱与构造加柱数量保持，位置见移前移后表；' if v['mode'] in ('fixed_added_adjusted_posts','spaced_added_adjusted_posts') else '保留原均分位置，不移柱或重新分段均分；' if v['mode']=='spaced_added_posts' else '保留原定距及余跨，不移柱；')+v['post_layout']['rule_version']] for t,v in added_posts for r in v['post_layout']['post_ranges']]))
        result.append(('构造加柱端距及依据',['材料表 / 材料项 / 区间','加柱名称','原始S / 单位','标准端距S（m）','构造说明','原始依据'],
                       [[t['name']+' / '+v['name']+' / '+r['label'],('#'+str(a['index'])+' '+a['label']) if v['mode'] in ('fixed_added_adjusted_posts','spaced_added_adjusted_posts') else a['label'],str(a['s'])+' '+a['s_unit'],a['normalized_s'],a['measurement_basis'],'端距S：'+a['evidence']['s']+'；说明：'+a['evidence']['measurement_basis']] for t,v in added_posts for r in v['post_layout']['post_ranges'] for a in r['additional_posts']]))
    adjusted_posts=[(t,v) for t in materials for v in t['material_items'] if v['mode'] in ('fixed_adjusted_posts','spaced_adjusted_posts','fixed_added_adjusted_posts','spaced_added_adjusted_posts')]
    if adjusted_posts:
        result.append(('立柱禁布范围核查',['材料表 / 材料项 / 区间','禁布名称','原始起终S / 单位','标准禁布闭区间S（m）','原有 / 移后根数','说明及依据'],
                       [[t['name']+' / '+v['name']+' / '+r['label'],a['label'],str(a['from'])+' → '+str(a['to'])+' '+a['unit'],'['+a['normalized_from']+', '+a['normalized_to']+']',str(a['original_post_count'])+' / '+str(a['final_post_count']),a['measurement_basis']+'；起距：'+a['evidence']['from']+'；终距：'+a['evidence']['to']+'；说明：'+a['evidence']['measurement_basis']] for t,v in adjusted_posts for r in v['post_layout']['post_ranges'] for a in r['avoidance_ranges']]))
        result.append(('立柱移前移后端距',['材料表 / 材料项 / 区间','原柱类型及编号' if any(v['mode'] in ('fixed_added_adjusted_posts','spaced_added_adjusted_posts') for t,v in adjusted_posts) else '原内部节点j','原端距S（m）','移后原值 / 单位','移后S（m）','说明及依据'],
                       [[t['name']+' / '+v['name']+' / '+r['label'],(('原加柱 #' if a.get('node_type')=='additional' else '原规则 j=')+str(a['node_index'])) if 'node_type' in a else str(a['node_index']),a['original_s'],str(a['s'])+' '+a['s_unit'],a['normalized_s'],a['measurement_basis']+('；原柱类型：'+a['evidence']['node_type'] if 'node_type' in a else '')+'；节点：'+a['evidence']['node_index']+'；新S：'+a['evidence']['s']+'；说明：'+a['evidence']['measurement_basis']] for t,v in adjusted_posts for r in v['post_layout']['post_ranges'] for a in r['post_moves']]))
    equal_adjustments=[(t,v) for t in materials for v in t['material_items'] if v['mode']=='spaced_adjusted_posts']
    if equal_adjustments:
        result.append(('均分移柱规则与范围',['材料表 / 材料项','完整规则、适用边界及版本'],
                       [[t['name']+' / '+v['name'],v['post_layout']['notice']+'；'+v['post_layout']['rule_version']+'；原柱数保持'+str(v['post_layout']['post_count'])+'根，移柱'+str(v['post_layout']['moved_post_count'])+'根，禁布范围'+str(v['post_layout']['avoidance_range_count'])+'个'] for t,v in equal_adjustments]))
    combined_adjustments=[(t,v) for t in materials for v in t['material_items'] if v['mode'] in ('fixed_added_adjusted_posts','spaced_added_adjusted_posts')]
    if combined_adjustments:
        result.append(('含加柱移柱规则与范围',['材料表 / 材料项','完整规则、适用边界及版本'],
                       [[t['name']+' / '+v['name'],v['post_layout']['notice']+'；'+v['post_layout']['rule_version']+'；原规则柱'+str(v['post_layout']['base_post_count'])+'根＋原构造加柱'+str(v['post_layout']['additional_post_count'])+'根，移柱'+str(v['post_layout']['moved_post_count'])+'根，禁布范围'+str(v['post_layout']['avoidance_range_count'])+'个'] for t,v in combined_adjustments]))
    checked_posts=[(t,v) for t in materials for v in t['material_items'] if 'post_spacing' in v and v['post_spacing'].get('scope')!='connected_path']
    if checked_posts:
        from .post_spacing import expression as post_distance_expression
        result.append(('立柱区间实际柱距',['材料表 / 材料项 / 区间','计入柱 / 相邻对','区间最大实际柱距（m）','核定上限（m）','起端 / 终端未归属距离（m）','局部结论'],
                       [[t['name']+' / '+v['name']+' / '+r['label'],str(r['post_count'])+' / '+str(r['pair_count']),r['max_distance'] or '无相邻对',v['post_spacing']['normalized_limit'],'整区间无计入柱' if r['fully_unposted'] else r['unposted_start_distance']+' / '+r['unposted_end_distance'],'已列相邻对不超过上限；端部及跨区间、跨边另核' if r['pair_count'] else '无相邻对，须另核'] for t,v in checked_posts for r in v['post_spacing']['ranges']]))
        result.append(('立柱相邻柱距明细',['材料表 / 材料项 / 区间','相邻柱及身份','起终端距S（m）','实际柱距（m）','核定上限（m）','精确距离表达式'],
                       [[t['name']+' / '+v['name']+' / '+r['label'],'柱'+str(a['from_post'])+' '+a['from_identity']+' → 柱'+str(a['to_post'])+' '+a['to_identity'],a['from_s']+' → '+a['to_s'],a['distance'],v['post_spacing']['normalized_limit'],post_distance_expression(a['exact_distance'])] for t,v in checked_posts for r in v['post_spacing']['ranges'] for a in r['pairs']]))
        result.append(('立柱柱距核查依据',['材料表 / 材料项','上限原值、范围说明及依据'],
                       [[t['name']+' / '+v['name'],str(v['spacing_check']['max_spacing'])+' '+v['spacing_check']['spacing_unit']+'；'+v['spacing_check']['measurement_basis']+'；上限依据：'+v['spacing_check']['evidence']['max_spacing']+'；范围依据：'+v['spacing_check']['evidence']['measurement_basis']+'；'+v['post_spacing']['notice']+'；'+v['post_spacing']['rule_version']] for t,v in checked_posts]))
    path_posts=[(t,v) for t in materials for v in t['material_items'] if v.get('post_spacing',{}).get('scope')=='connected_path']
    if path_posts:
        from .path_post_spacing import expression as path_expression
        result.append(('立柱连续路径柱距',['材料表 / 材料项 / 路径','连接的来源区间','开闭 / 柱数 / 对数','最大沿路径柱距（m）','上限（m）','未归属起端 / 终端距离（m）'],
                       [[t['name']+' / '+v['name']+' / '+p['label'],' → '.join(p['range_labels']),('闭环' if p['closed'] else '开路径')+' / '+str(p['post_count'])+' / '+str(p['pair_count']),p['max_distance'] or '无相邻对',v['post_spacing']['normalized_limit'],'整路径无柱，须另核' if p['fully_unposted'] else '闭环无开端' if p['closed'] else p['unposted_start_distance']+' / '+p['unposted_end_distance']] for t,v in path_posts for p in v['post_spacing']['paths']]))
        result.append(('立柱路径相邻柱距',['材料表 / 材料项 / 路径','相邻柱 / 来源S（m）','连接类型 / 沿路径距离（m）','上限（m）','精确距离 / 途经范围'],
                       [[t['name']+' / '+v['name']+' / '+p['label'],'柱'+str(a['from_post'])+' '+a['from_identity']+' '+a['from_range']+' S='+a['from_s']+' → 柱'+str(a['to_post'])+' '+a['to_identity']+' '+a['to_range']+' S='+a['to_s'],('闭环首尾' if a['wrap'] else '跨区间或跨边' if a['cross_range'] else '同区间')+'；'+a['distance'],v['post_spacing']['normalized_limit'],path_expression(a['exact_distance'])+'；'+' → '.join(s['label'] for s in a['segments'])] for t,v in path_posts for p in v['post_spacing']['paths'] for a in p['pairs']]))
        result.append(('立柱路径核查依据',['材料表 / 材料项','上限、范围、依据及规则'],
                       [[t['name']+' / '+v['name'],str(v['spacing_check']['max_spacing'])+' '+v['spacing_check']['spacing_unit']+'；'+v['spacing_check']['measurement_basis']+'；上限依据：'+v['spacing_check']['evidence']['max_spacing']+'；范围依据：'+v['spacing_check']['evidence']['measurement_basis']+'；'+v['post_spacing']['notice']+'；'+v['post_spacing']['rule_version']] for t,v in path_posts]))
    def railing_edge_label(e):
        return 'S'+str(e['edge_index']) if e['role']=='route' else 'R'+str(e['ring_index'])+' E'+str(e['edge_index'])
    railings = [t for t in snapshot['takeoffs'] if t.get('railing_summary')]
    if railings:
        labels={'available_length':'候选原边总长（m）','single_length':'单组核定长度（m）','multiplicity':'来源重复数量','unrounded_quantity':'修约前总长（m）','rounding_digits':'总长修约位数','rounding_adjustment':'修约差额（m）'}
        result.append(('栏杆边线长度汇总',['栏杆','字段','内容'],
                       [[t['name'],label,str(t['railing_summary'][key])] for t in railings for key,label in labels.items()]+
                       [[t['name'],key,value] for t in railings for key,value in [('来源',t['railing_summary']['source_name']+' / V'+str(t['railing_summary']['source_revision'])),('工程量',t['quantity']+' m'),('材料做法',t['material']),('安装依据',t['installation_basis']),('计量依据',t['measurement_basis']),('算法及精度',t['rule_version']+'；'+t['rounding_rule']),('适用边界',t['notice'])]]))
        result.append(('栏杆逐边范围核定',['栏杆 / 原边','轮廓及原点序','起终XY（m）','原边长（m）','决定 / 计入长（m）','计量说明','原始依据'],
                       [[t['name']+' / '+railing_edge_label(e),('设计线段' if e['role']=='route' else '外轮廓' if e['role']=='outer' else '孔')+' / P'+str(e['from_index'])+'→P'+str(e['to_index']),json.dumps(e['from_xy'])+' → '+json.dumps(e['to_xy']),e['length'],{'exclude':'排除','full':'整边','ranges':'局部区间'}[e['mode']]+' / '+e['selected_length'],e['basis'],e['evidence_id']] for t in railings for e in t['railing_edges']]))
        ranges=[[t['name']+' / '+railing_edge_label(e),str(v['from'])+' → '+str(v['to'])+' '+v['unit'],v['s_from']+' → '+v['s_to'],v['length'],v['from_evidence_id']+' / '+v['to_evidence_id']] for t in railings for e in t['railing_edges'] for v in e['normalized_ranges']]
        if ranges:result.append(('栏杆局部计长区间',['栏杆 / 原边','原始起终距','标准S区间（m）','长度（m）','起终距依据'],ranges))
        if any(t['source_geometry']['kind']=='polygon_region' for t in railings):
            result.append(('栏杆来源原始坐标',['栏杆 / 轮廓','原图顶点','原始XY / 单位','逐坐标依据'],
                           [[t['name']+' / R'+str(i),p['name'],json.dumps(p['parameters'])+' '+p['unit'],json.dumps(p['evidence'])] for t in railings for i,r in enumerate(t['source_geometry'].get('polygon_rings',[]),1) for p in r['points']]))
    route_railings=[t for t in railings if t['railing_summary'].get('source_kind')=='design_route']
    if route_railings:
        result.append(('坐标线栏杆口径与段长',['栏杆 / 原段','计长口径','三维长 / 水平长（m）','本口径原段长 / 计入长（m）','起终XYZ（m）','口径依据'],
                       [[t['name']+' / S'+str(e['edge_index']),t['railing_summary']['length_basis_label'],e['spatial_length']+' / '+e['horizontal_length'],e['length']+' / '+e['selected_length'],json.dumps(e['from_xyz'])+' → '+json.dumps(e['to_xyz']),t['source_evidence']['railing.length_basis']] for t in route_railings for e in t['railing_edges']]))
        result.append(('坐标线栏杆原始XYZ',['栏杆 / 来源点','原始XYZ','原始单位','逐坐标依据','共同坐标与高程基准'],
                       [[t['name']+' / P'+str(i)+' '+p['name'],json.dumps(p['parameters']),p['unit'],json.dumps(p['evidence']),t['coordinate_basis']] for t in route_railings for i,p in enumerate(t['source_geometry']['route_points'],1)]))
    surfaces = [t for t in snapshot['takeoffs'] if t.get('surface_summary')]
    if surfaces:
        result.append(('表面计量汇总', ['构件', '用途', '来源空间构件 / 版本', '单组边界面积', '单组计入 / 排除面积', '重复数量 / 工程量', '计量依据', '来源版本哈希'],
                       [[t['name'], t['surface_summary']['purpose_label'], t['surface_summary']['source_name']+' / V'+str(t['surface_summary']['source_revision']),
                         t['surface_summary']['total_area']+' m²', t['surface_summary']['included_area']+' / '+t['surface_summary']['excluded_area']+' m²',
                         t['surface_summary']['multiplicity']+' / '+t['quantity']+' m²', t['measurement_basis'], t['source_component_hash']] for t in surfaces]))
        result.append(('逐面计量决定', ['构件 / 面', '朝向 / 平面位置（m）', '面内坐标范围（m）', '单组面积（m²）', '计量决定', '计量说明', '原始依据'],
                       [[t['name']+' / F'+str(f['index'])+(' / 原F'+str(f['parent_index'])+' 子'+str(f['sub_index']) if f.get('parent_index') else ''), f['label'] if f.get('geometry_type') else f['normal']+' / '+f['plane'],
                         f['position']+'；'+f['calculation'] if f.get('geometry_type') else f['u_axis']+': '+f['u_from']+' → '+f['u_to']+'; '+f['v_axis']+': '+f['v_from']+' → '+f['v_to'],
                         f['area'], '计入' if f['included'] else '排除', f['basis'], f['evidence_id']] for t in surfaces for f in t['surface_faces']]))
        result.append(('表面几何来源', ['表面构件 / 来源实体', '类别 / 参数', '原始值', '原始单位', '原始依据'],
                       [[t['name']+' / '+r['name'], ('加项' if r['operation']=='add' else '扣减')+' / '+key, str(value), r['unit'], r['evidence'][key]]
                        for t in surfaces for r in t['source_geometry'].get('regions', []) for key, value in r['parameters'].items()]+
                       [[t['name']+' / '+t['source_geometry']['name'], '等厚多边形 / '+label, str(t['source_geometry'][key]), unit, t['source_geometry'][proof]]
                        for t in surfaces if t['source_geometry']['kind']=='polygon_region'
                        for key,label,unit,proof in [('thickness','竖向厚度',t['source_geometry']['thickness_unit'],'thickness_evidence_id'),('multiplicity','重复数量','组','multiplicity_evidence_id')]]))
        polygon_surfaces = [t for t in surfaces if t['source_geometry']['kind']=='polygon_region']
        if polygon_surfaces:
            result.append(('多边形表面原始坐标', ['表面构件 / 来源轮廓', '原顶点', '原始XY / 单位', '逐坐标原始依据'],
                           [[t['name']+' / R'+str(ri)+' '+r['name'], 'P'+str(pi)+' '+pt['name'],
                             'X='+str(pt['parameters']['x'])+'；Y='+str(pt['parameters']['y'])+' / '+pt['unit'],
                             'X：'+pt['evidence']['x']+'；Y：'+pt['evidence']['y']]
                            for t in polygon_surfaces for ri,r in enumerate(t['source_geometry']['polygon_rings'],1) for pi,pt in enumerate(r['points'],1)]))
            result.append(('多边形表面来源口径', ['表面构件', '来源厚度 / 原始依据', '来源数量 / 原始依据', '坐标与计量口径'],
                           [[t['name'], str(t['source_geometry']['thickness'])+' '+t['source_geometry']['thickness_unit']+' / '+t['source_geometry']['thickness_evidence_id'],
                             str(t['source_geometry']['multiplicity'])+' / '+t['source_geometry']['multiplicity_evidence_id'],
                             t['source_geometry']['coordinate_basis']+'；'+t['source_geometry']['measurement_basis']] for t in polygon_surfaces]))
        result.append(('表面计量规则', ['构件', '边界规则版本', '精度', '适用范围'],
                       [[t['name'], t['rule_version'], t['rounding_rule'], t['notice']] for t in surfaces]))
    cuts = [[t['name'], '原F'+str(c['parent_index']), (('原边局部S' if c['coordinate_axis']=='s' else '底面局部Z') if c.get('coordinate_system')=='edge_local' else c['coordinate_axis']), c['original_value']+' '+c['source_unit'],
             c['factor'], c['canonical_value']+' m', c['evidence_id'], c['rule_version']] for t in snapshot['takeoffs'] for c in t.get('surface_subdivisions', [])]
    if cuts:
        result.append(('表面分区坐标', ['表面构件','原始面','分割坐标轴','原值 / 单位','换算倍数','换算后位置','原始依据','单位规则'], cuts))
    partition_checks = [[t['name'], '原F'+str(c['parent_index'])+' / '+str(c['child_count'])+'子面', c['parent_area'], c['children_area'], c['area_difference'], '有限十进制展开范围完整闭合；80位子面不调平，差额为计算精度差']
                        for t in snapshot['takeoffs'] for c in t.get('surface_partition_checks', [])]
    if partition_checks:
        result.append(('多边形侧面分区校验', ['构件', '原面 / 子面数', '原面积（m²）', '子面合计（m²）', '精度差（m²）', '校验说明'], partition_checks))
    cap_checks = [[t['name'], '原F'+str(c['parent_index']), str(c['child_count'])+'净区 / '+str(c['zero_window_count'])+'零窗口',
                   c['parent_area'], c['children_area'], c['area_difference'], '精确有理数裁剪与扣孔面积完整闭合；零净量窗口不生成面，80位面积不调平']
                  for t in snapshot['takeoffs'] for c in t.get('surface_cap_partition_checks', [])]
    if cap_checks:
        result.append(('净面窗口分区校验', ['构件','原面','计量/零窗口','原面积（m²）','子区合计（m²）','精度差（m²）','校验说明'], cap_checks))
        result.append(('净面窗口逐孔扣减', ['构件 / 净区','共同XY窗口 / 局部Z','原轮廓','窗口内面积（m²）','净区面积（m²）','算法'],
                       [[t['name']+' / F'+str(f['index']), f['position'], ('派生' if f.get('cap_component_rings') else '')+'R'+str(c['ring_index'])+(' 外轮廓' if c['role']=='outer' else ' 孔'),
                         c['area'], f['area'], f['cap_clip_rule']]
                        for t in snapshot['takeoffs'] for f in t.get('surface_faces',[]) if f.get('cap_clip_rule') for c in f['cap_clip_rings']]))
    components = [(t,c) for t in snapshot['takeoffs'] for c in t.get('surface_cap_component_checks',[])]
    if components:
        result.append(('净块分离与闭合校验', ['构件 / 原面 / 裁剪区','独立块数','原区净量（m²）','块合计（m²）','显示精度差（m²）','说明'],
                       [[t['name']+' / 原F'+str(c['parent_index'])+' / 区'+str(c['clip_index']),c['component_count'],c['clip_area'],c['components_area'],c['area_difference'],
                         '原裁剪区内各净块精确有理数面积闭合；不调平某块，不重复乘来源数量'] for t,c in components]))
        result.append(('独立净块派生边界', ['构件 / 净块','派生轮廓 / 顶点','XY坐标（m，80位显示）','派生轮廓面积（m²）','净块面积（m²）','说明'],
                       [[t['name']+' / F'+str(f['index'])+' / 块'+str(f['cap_component_index']),
                         '派生R'+str(r['ring_index'])+(' 外' if r['role']=='outer' else ' 孔')+' / P'+str(i),
                         '('+p['x']+', '+p['y']+')',r['area'],f['area'],'实际净块边界；首尾闭合。派生点不是原图点号，精确分数见JSON']
                        for t in snapshot['takeoffs'] for f in t.get('surface_faces',[]) for r in f.get('cap_component_rings',[]) for i,p in enumerate(r['points'],1)]))
        result.append(('分离前原区逐孔扣减', ['构件 / 原面 / 裁剪区','原始轮廓','裁剪面积（m²）','原区净量（m²）','原分割算法','范围'],
                       [[t['name']+' / 原F'+str(c['parent_index'])+' / 区'+str(c['clip_index']),
                         '原R'+str(r['ring_index'])+(' 外轮廓' if r['role']=='outer' else ' 孔'),r['area'],c['area'],c['cap_clip_rule'],c['position']]
                        for t in snapshot['takeoffs'] for c in t.get('surface_cap_clip_sources',[]) for r in c['cap_clip_rings']]))
    point_contacts=[(t,c) for t in snapshot['takeoffs'] for c in t.get('surface_point_contact_checks',[])]
    if point_contacts:
        result.append(('点接触处理依据', ['构件 / 原面 / 裁剪区','口径','接点 / 净块 / 重访边界数','处理说明','原始依据','规则'],
                       [[t['name']+' / 原F'+str(c['parent_index'])+' / 区'+str(c['clip_index']),'净区内部连通；零宽接点不合并面积块',
                         str(c['contact_count'])+' / '+str(c['component_count'])+' / '+str(c['repeated_vertex_walks']),c['setting']['basis'],c['setting']['evidence_id'],c['rule_version']] for t,c in point_contacts]))
        result.append(('接点边界配对记录', ['构件 / 原面 / 裁剪区','接点XY（m）','边界组','进入邻点XY（m）','离开邻点XY（m）','说明'],
                       [[t['name']+' / 原F'+str(c['parent_index'])+' / 区'+str(c['clip_index']),
                         '('+p['point']['x']+', '+p['point']['y']+')',str(j)+' / '+str(p['degree']),
                         '('+pair['incoming_from']['x']+', '+pair['incoming_from']['y']+')','('+pair['outgoing_to']['x']+', '+pair['outgoing_to']['y']+')',
                         '净区在左侧的精确边界追踪；不推断工程连接，精确分数见JSON']
                        for t,c in point_contacts for p in c['contacts'] for j,pair in enumerate(p['pairings'],1)]))
    line_cuts = [(t, line) for t in snapshot['takeoffs'] for line in t.get('surface_partition_lines', [])]
    if line_cuts:
        result.append(('净面斜线分割依据', ['构件 / 原面 / 斜线', '两点原始坐标 / 单位', '逐坐标依据', '规范式（X/Y单位m）', '分割做法 / 原始依据', '规则'],
                       [[t['name']+' / 原F'+str(c['parent_index'])+' / L'+str(c['line_index']),
                         '('+c['original']['x1']+', '+c['original']['y1']+') → ('+c['original']['x2']+', '+c['original']['y2']+') '+c['original']['unit']+'；无限延伸',
                         '；'.join(k.upper()+': '+v for k,v in c['original']['evidence'].items()),
                         c['coefficients'][0]+'×X + ('+c['coefficients'][1]+')×Y + ('+c['coefficients'][2]+') = 0；正负侧由此式确定',
                         c['original']['basis']+' / '+c['original']['evidence_id'], c['rule_version']] for t,c in line_cuts]))
        result.append(('净面斜线裁剪范围', ['构件 / 净区', '斜线规范侧', '凸框顶点', 'XY坐标（m，80位显示）', '净面积（m²）', '说明'],
                       [[t['name']+' / F'+str(f['index']), '、'.join('L'+str(c['line_index'])+('正侧' if c['positive'] else '负侧') for c in f['cap_cut_sides']),
                         'P'+str(i), '('+p['x']+', '+p['y']+')', f['area'], '凸框仅界定裁剪范围，须与原外轮廓及孔相交；不是净区外轮廓。精确有理数见JSON']
                        for t in snapshot['takeoffs'] for f in t.get('surface_faces', []) if f.get('cap_clip_domain') for i,p in enumerate(f['cap_clip_domain'],1)]))
    if not q:
        from .main import blockers
        next(table[2] for table in result if table[0] == '缺项与警示').extend([[x] for x in blockers(p)])
    if snapshot.get('document_requests'):
        result.append(('补件任务台账', ['任务', '专业', '负责人', '期限', '状态', '未关闭时阻断本次范围', '缺项说明', '补充说明', '证据'],
                       [[t['name'], t['specialty'], t['assignee'], t['due_date'], t['state'], '是' if t['blocking'] and t['specialty'] in (q['scope'] if q else p['scope']) else '否', t['detail'], (t['submissions'][-1]['note'] if t['submissions'] else ''), ', '.join(t['evidence_ids'])] for t in snapshot['document_requests']]))
    changes = [x for x in snapshot.get('changes', []) if x['id'] in snapshot.get('applied_changes', [])]
    if changes:
        result.append(('变更签证台账', ['清单', '原工程量', '变更后', '增减量', '单位', '原因及依据', '来源证据'],
                       [[x['name'], x['before_quantity'], x['quantity'], x['delta'], x['unit'], x['reason'], x['evidence_id']] for x in changes]))
    if snapshot.get('quantity_batches'):
        batch_states = {'draft':'待复核应用','applied':'已整批应用','cancelled':'已取消'}
        result.append(('批量调量台账', ['调量单','状态 / 子项数','原因','提交时条件性试算','子项记录'],
                       [[b['name'],batch_states[b['state']]+' / '+str(len(b['change_ids'])),b['reason']+(('；取消：'+b['cancel_reason']) if b['state']=='cancelled' else ''),
                         ('原总价 '+b['impact']['before_total']+' / 调整后 '+b['impact']['after_total']+' / 差额 '+b['impact']['delta']+'；'+b['impact']['notice']) if b['impact']['available'] else b['impact']['notice'],
                         ', '.join(b['change_ids'])] for b in snapshot['quantity_batches']]))
    from .pricing_tasks import tasks
    pending_tasks = tasks(snapshot)
    if pending_tasks:
        result.append(('待询价与匹配任务', ['清单', '任务', '资源编码 / 名称', '规格 / 单位 / 原消耗', '费用归属', '原始依据 / 基线'],
                       [[t['boq_name'], '待询价' if t['kind']=='price_inquiry' else '待定额匹配',
                         (t['resource']['resource_code']+' / '+t['resource']['name']) if t['resource'] else '',
                         (t['resource']['specification']+' / '+t['resource']['unit']+' / '+str(t['resource']['consumption'])) if t['resource'] else '',
                         t['resource']['fee_allocation'] if t['resource'] else '',
                         ((t['resource']['evidence_id']+' / ') if t['resource'] else '')+t['boq_hash']] for t in pending_tasks]))
    price_changes = [x for x in snapshot.get('price_changes', []) if x['id'] in snapshot.get('applied_price_changes', [])]
    if price_changes:
        result.append(('资源调价台账', ['清单', '资源', '原单价', '新单价', '单位', '价格期次及税制', '来源及许可', '调价依据', '证据'],
                       [[entry['boq_name'], entry['after']['name'], entry['before']['price'], entry['after']['price'], entry['after']['unit'],
                         entry['after']['period']+' / '+entry['after']['tax_mode'], entry['after']['source']+' / '+entry['after']['license'],
                         change['reason'], entry['after']['evidence_id']] for change in price_changes for entry in change['entries']]))
    structures = snapshot.get('engineering_structures', [])
    if structures:
        structure = structures[-1]
        nodes = {n['id']: n for n in structure['nodes']}
        summary.extend([['工程结构版本', 'V'+str(structure['version'])+' / '+structure['state']],
                        *[['工程划分与归属依据'+('（续）' if start else ''), structure['reason'][start:start+140]] for start in range(0,len(structure['reason']),140)], ['工程结构来源证据', structure['evidence_id']],
                        ['项目费用归属', '按适用节点规则逐级取费，下级费用只汇总一次，不自动分摊' if snapshot['rules'] and snapshot['rules'][0].get('node_fees') else '项目级费用单列；不按比例分摊至单项/单位工程']])
        result.append(('工程结构与归属', ['单项工程', '单位工程', '清单编码', '清单', '单位', '原始依据'],
                       [[nodes.get(nodes.get(structure['assignments'].get(b['id']),{}).get('parent_id'),{}).get('name','未归属'),
                         nodes.get(structure['assignments'].get(b['id']),{}).get('name','未归属'), b['code'], b['name'], b['unit'], structure['evidence_id']] for b in items]))
        if not q:
            from .engineering_structure import issues as structure_issues
            summary.extend([['工程结构当前警示', issue] for issue in structure_issues(snapshot)])
    engineering = q.get('engineering_summary') if q else None
    if engineering:
        grouped = []
        for single in [n for n in engineering['nodes'] if n['kind']=='single']:
            grouped.append(['单项工程',single['name'],q['project_name'],single['line_count'],single['subtotal'],'',single['subtotal']])
            grouped.extend([['单位工程',u['name'],single['name'],u['line_count'],u['subtotal'],'',u['subtotal']]
                           for u in engineering['nodes'] if u['parent_id']==single['id']])
        grouped.append(['建设项目',q['project_name'],'',len(engineering['assignments']),engineering['subtotal'],engineering['project_fees'],engineering['total']])
        result[1] = ('分级汇总', ['层级','工程名称','上级工程','清单项数','分部分项金额','项目级费用','汇总金额'],grouped)
        if engineering.get('scoped_fees'):
            grouped=[]
            for single in [n for n in engineering['nodes'] if n['kind']=='single']:
                for n in [single]+[u for u in engineering['nodes'] if u['parent_id']==single['id']]:
                    grouped.append(['单项工程' if n['kind']=='single' else '单位工程',n['name'],q['project_name'] if n['kind']=='single' else single['name'],n['line_count'],n['subtotal'],n['lower_fees'],n['own_fees'],n['total']])
            grouped.append(['建设项目',q['project_name'],'',len(engineering['assignments']),engineering['subtotal'],engineering['descendant_fees'],engineering['project_fees'],engineering['total']])
            result[1]=('分级汇总',['层级','工程名称','上级工程','清单项数','分部分项金额','下级费用','本级费用','汇总金额'],grouped)
        summary.append(['逐级金额核对', '清单合价 + 全部各级费用 = 报价总价；下级费用逐层只汇总一次；闭合'])
    scoped_rules = snapshot['rules'][0] if snapshot['rules'] else {}
    if scoped_rules.get('node_fees'):
        labels={n['id']:n for n in structures[-1]['nodes']} if structures else {}
        result.append(('分级取费规则',['工程节点','顺序 / 费用','基数表达式','费率','取费依据 / 授权','原始证据'],
                       [[labels.get(g['node_id'],{}).get('name','节点已移除'),str(i+1)+' / '+f['name'],json.dumps(f['basis'],ensure_ascii=False),f['rate'],f['source']+' / '+scoped_rules['license'],f['evidence_id']]
                        for g in scoped_rules['node_fees'] for i,f in enumerate(g['fees'])]))
    substitutions = snapshot.get('resource_substitutions', [])
    if substitutions:
        states = {'draft':'待复核','approved':'已复核','rejected':'已拒绝','needs_info':'待补充'}
        result.append(('材料替换台账', ['替换单 / 清单', '状态', '替换前材料与消耗', '替换后材料与消耗', '价格 / 归属', '替换原因 / 来源 / 授权', '原始依据', '提交时条件性试算'],
                       [[s['name']+' / '+s['boq_name'], states[s['state']]+(' / 已应用' if s['id'] in snapshot.get('applied_resource_substitutions',[]) else ''),
                         s['before']['name']+' / '+str(s['before']['consumption'])+' '+s['before']['unit'],
                         str(i+1)+'. '+r['resource_code']+' / '+r['name']+' / '+r['specification']+' / '+str(r['consumption'])+' '+r['unit'],
                         ('待询价' if r.get('pricing_status')=='unpriced' else str(r['price'])+' / '+r['source_type']+' / '+r['period']+' / '+r['tax_mode'])+'；'+r['fee_allocation'],
                         s['reason']+' / '+s['source']+' / '+s['license'],
                         s['evidence_id']+' / 原材料 '+s['before']['evidence_id']+' / 新材料 '+r['evidence_id'],
                         (s['impact']['before_total']+' → '+s['impact']['after_total']+' / 差额 '+s['impact']['delta']+'；'+s['impact']['notice']) if s['impact']['available'] else s['impact']['notice']]
                        for s in substitutions for i,r in enumerate(s['after'])]))
    mapped = [b for b in items if b.get('mapping_ref')]
    if mapped:
        result.append(('清单编码映射依据', ['清单编码','名称','标准','版本','专业','映射版本校验值','来源证据'],
                       [[b['code'],b['name'],b['code_standard'],b['code_edition'],b['discipline'],b['mapping_ref']['hash'],b['code_evidence_id']] for b in mapped]))
        from .mapping_types import TYPES, selected_profile
        from .extensions import links
        mapping_rows = []
        for b in mapped:
            rule = next((r for r in snapshot.get('boq_mappings', []) if r['id'] == b['mapping_ref']['id']), None)
            profile = selected_profile(rule) if rule else None
            for link in links(b):
                book = next((t for t in snapshot.get('takeoffs', []) if t['id'] == link['takeoff_id']), {})
                comp = next((c for c in snapshot.get('components', []) if c['id'] == book.get('component_id')), {})
                mapping_rows.append([b['code']+' / '+b['name'], book.get('name', link['takeoff_id'])+' / '+comp.get('name', '来源构件需核对')+' / V'+str(comp.get('revision', '—')),
                                     TYPES.get(rule['kind'], {}).get('label', rule['kind'])+' / '+profile['label'] if rule and profile else '映射口径需核对',
                                     str(link['quantity'])+' '+b['unit'], rule['name']+' / V'+str(rule['version']) if rule else b['mapping_ref']['id']])
        result.append(('清单映射适用范围', ['编码 / 清单', '来源计算书 / 构件版本', '构件类型 / 计量口径', '本清单贡献量', '映射规则版本'], mapping_rows))
    library_rows = []
    for b in items:
        for quota in b.get('quotas',[]):
            for resource in quota['resources']:
                ref=resource.get('library_ref')
                if not ref: continue
                saved=next((s['entry'] for s in snapshot.get('library_snapshots',[]) if s['hash']==ref['hash']),None)
                if saved:
                    library_rows.append([b['name'],resource['name'],resource['price'],saved['source_project_name'],
                                         saved['reuse_basis'],saved['resource']['period'],ref['hash'],resource['evidence_id'],ref['reuse_evidence_id']])
    if library_rows:
        result.append(('企业价格复用依据',['清单','资源','采用单价','原项目','复用授权范围','期次','企业条目校验值','价格依据','复用许可依据'],library_rows))
    if snapshot.get('quota_adoptions'):
        result.append(('定额候选采用台账',['目标清单','候选 / 原清单','专业 / 定额年度','适用条件 / 复用许可','采用理由','采用人 / 时间','候选校验值','原始证据'],
                       [[x['boq_name'],x['candidate']['name']+' / '+x['candidate']['boq_id'],x['candidate']['discipline']+' / '+x['candidate']['quota_edition'],
                         x['candidate']['applicability']+' / '+x['candidate']['reuse_basis'],x['reason'],x['author']+' / '+x['created_at'],x['candidate_hash'],
                         ', '.join(sorted({x['candidate']['reuse_evidence_id']}|{q['evidence_id'] for q in x['candidate']['quotas']}))] for x in snapshot['quota_adoptions']]))
    if snapshot.get('enterprise_quota_adoptions'):
        result.append(('企业定额复用台账',['目标清单','原项目 / 定额候选','专业 / 定额年度','适用条件 / 跨项目许可','目标价格处理','采用理由 / 人员 / 时间','企业条目校验值','目标原件证据'],
                       [[x['boq_name'],x['entry']['source_project_name']+' / '+x['entry']['candidate']['name'],x['entry']['candidate']['discipline']+' / '+x['entry']['candidate']['quota_edition'],
                         x['entry']['candidate']['applicability']+' / '+x['entry']['reuse_basis'],'采用时全部转为待询价；当前价格以本报价资源表及审核为准',
                         x['reason']+' / '+x['author']+' / '+x['created_at'],x['entry_hash'],', '.join(x['proof_map'].values())] for x in snapshot['enterprise_quota_adoptions']]))
    if snapshot.get('quantity_diagnoses'):
        from .quantity_diagnosis import CATEGORIES
        from .reconciliation import context_hash
        report_context = context_hash(snapshot)
        states = {'draft':'待复核','approved':'诊断已复核','rejected':'诊断已拒绝','needs_info':'待补充','stale':'已失效'}
        result.append(('工程量差异诊断', ['构件 / 底稿行','类别 / 版本 / 状态','对账记录值','系统侧记录','人工基准记录','分析及处理建议','原始依据'],
                       [[d['target']['name']+' / '+d['target_key'], CATEGORIES[d['category']]+' / V'+str(d['version'])+' / '+states.get(d['state'],d['state'])+(' / 历史计算口径' if d['context_hash']!=report_context else ''),
                         ('系统 '+d['target']['quantity']+' '+d['target']['unit']+'；底稿未覆盖' if d['target']['kind']=='missing_takeoff' else '系统 '+d['target'].get('actual','未匹配')+' / 基准 '+d['target']['expected']+' '+d['target']['unit']+' / 差量 '+d['target'].get('delta','无法计算')+' / 允许 '+d['target'].get('allowed_delta','无法计算')),
                         d['observed'], d['reference'], d['reason']+'；处理：'+d['action']+'；诊断不改变容差及数量对照结论',
                         d['baseline_evidence_id']+', '+d['evidence_id']] for d in snapshot['quantity_diagnoses']]))
    # Long analyses must remain printable in Excel's bounded row height.
    for i,(title,headers,rows) in enumerate(result):
        if not ((circular_foundations or opened_foundations) and title in ('立柱基础体积汇总','立柱基础逐项体积','立柱基础原始尺寸与依据','立柱基础材料范围与修约','台阶基础逐层体积','台阶基础逐层原件','圆形基础圆周率与口径','基础贯通孔毛扣净','基础贯通孔原始依据','基础贯通孔校验','基础贯通孔范围与常量','基础圆孔毛扣净','基础圆孔原始依据','基础圆孔校验','基础圆孔范围与常量','基础圆孔标高依据','基础圆孔逐层相交','基础孔洞毛扣净','基础孔洞原始依据','基础孔洞校验','基础孔洞范围与常量','基础孔洞标高依据','基础孔洞逐层相交','基础孔洞扣减阈值','基础孔洞实际与计量')) and title not in ('基础孔壁模板面积汇总','基础孔壁逐面接触净量','基础孔壁无接触扣减','基础孔壁原始范围与依据','基础孔壁来源孔与原件','基础孔壁模板做法与规则','基础模板横向条带','基础模板条带基准与原件','基础模板条带分离校验','基础模板条带开口交集','基础模板圆弧角度','基础模板圆弧基准与原件','基础模板圆弧分离校验','基础模板接触面积汇总','基础模板逐面标高与净量','基础模板侧开口实际扣减','基础模板原始范围与依据','基础模板做法与计算规则','多边形净量汇总','多边形外轮廓与孔','多边形原始顶点','多边形逐边计算','多边形扣孔边界校验','图纸身份候选采用','图纸校核待办','图纸目录与版次','平立剖图纸互参','项目事实与冲突候选','清单映射适用范围','清单编码映射依据','井板逐孔定位与扣减','井板孔包含与分离校验','井底盖板净实体汇总','井板逐项尺寸与孔扣减','井板标高范围及原件','井板材料做法与规则','检查井井筒计量汇总','井筒逐井尺寸与数量','井筒定位及原始依据','井筒材料做法与规则','管网矩形垫层汇总','垫层逐段尺寸与体积','垫层材料施工与修约','垫层来源设计坐标','管道外包体积汇总','管道逐段外径与占位','管道外包口径与常量','管道外包来源坐标','关联回填平衡','回填逐项扣减','回填来源计算索引','回填范围与修约','变断面沟槽汇总','沟槽站点断面','沟槽分段体积','沟槽来源坐标','设计管线长度汇总','管线设计坐标','三维管段计算','表面分区坐标','表面计量汇总','逐面计量决定','表面几何来源','表面计量规则','空间并集与扣减','长方体原始坐标','空间分层计算','空间分层条带','空间相交定位','区域并集与扣减','平面区域与坐标','区域条带计算过程','区域相交定位','算量原始单位换算','分项算量与扣减明细','分项规则与精度','工程量差异诊断','批量调量台账','变更签证台账','待询价与匹配任务','材料替换台账','工程结构与归属','分级汇总','分级取费规则','费用与税金','定额候选采用台账','企业定额复用台账'): continue
        expanded = []
        for row in rows:
            cells = [str(v).replace('\r', '').replace('\n', '；') for v in row]
            count = max((len(v)+139)//140 for v in cells)
            for part in range(count):
                segment = [v[part*140:(part+1)*140] for v in cells]
                if part: segment[0] = (segment[0] or cells[0][:80])+'（续'+str(part+1)+'）'
                expanded.append(segment)
        result[i] = (title,headers,expanded)
    result = [section for section in result if section[0] != '定额换算明细' or section[2]]
    return watermark, result


def export_report(p, q, fmt, approved, template=None, table_data=None, pagination=None):
    label, sections = table_data if table_data is not None else tables(p, q, approved)
    if template:
        sections[0][2].extend([['编制单位', template['organization']], ['企业模板', template['name']+' V'+str(template['version'])],
                               ['内部说明', template['cover_note']], ['模板性质', template['purpose']]])
    output = io.BytesIO()
    if fmt == 'json':
        from .facts import conflict_groups
        data = {'project': {'id': p['id'], 'name': p['name'], 'scope': p['scope']}, 'status': label,
                'fact_conflicts':conflict_groups({**q['snapshot'],'id':p['id']} if q else p),
                'quote': q, 'report_reviews': [r for r in p['reviews'] if q and r['collection'] == 'quotes' and r['object_id'] == q['id']],
                'unpriced': None if q else {k: p[k] for k in ['documents', 'evidence', 'facts', 'components', 'takeoffs', 'boq', 'reviews', 'resource_substitutions', 'applied_resource_substitutions', 'engineering_structures', 'quota_adoptions', 'catalog', 'enterprise_quota_adoptions','drawing_sheets','drawing_references']},
                'tables': sections, 'report_template': template}
        return json.dumps(data, ensure_ascii=False, indent=2).encode(), 'application/json', 'json'
    if fmt == 'xlsx':
        wb = Workbook()
        wb.remove(wb.active)
        numeric_headers = {'总金额', '分部分项金额', '项目级费用', '下级费用', '本级费用', '汇总金额', '工程量', '单价', '原单价', '新单价', '合价', '基数', '费率', '金额', '消耗', '换算系数', '资源价格', '单价贡献'}
        for title, headers, rows in sections:
            s = wb.create_sheet(title)
            s.append([title+' — '+label])
            s.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max(2, len(headers)))
            s.append(headers)
            for row in rows:
                s.append([float(Decimal(str(v))) if headers[i] in numeric_headers and v not in ('', '待询价') else str(v) for i, v in enumerate(row)])
            if not rows:
                s.append(['未提供 / 待编制'])
            for cell in s[1]+s[2]:
                cell.font = Font(name='Microsoft YaHei', color='FFFFFF', bold=True, size=11)
                cell.fill = PatternFill('solid', fgColor=template['accent'] if template else '173B36')
            for row in s.iter_rows(min_row=3):
                for cell in row:
                    # Force strings to text to prevent spreadsheet formula injection.
                    if isinstance(cell.value, str):
                        cell.data_type = 's'
                    cell.alignment = Alignment(vertical='top', wrap_text=True)
                    cell.font = Font(name='Microsoft YaHei', size=10)
                    if cell.row % 2:
                        cell.fill = PatternFill('solid', fgColor='F0F5F3')
                    if isinstance(cell.value, (int, float)):
                        header = headers[cell.column-1]
                        unit_digits = (q['snapshot']['rules'][0].get('rounding_config', {}).get('unit_price_digits', 2) if q else 2)
                        cell.number_format = ('#,##0'+('.'+'0'*unit_digits if unit_digits else '')) if header == '单价' and title == '分部分项清单' else '#,##0.##################' if header in ('工程量', '消耗', '换算系数', '资源价格', '单价') else '#,##0.00'
                s.row_dimensions[row[0].row].height = 48
                if table_data is not None or any(t[0] in ('圆形基础圆周率与口径','基础贯通孔毛扣净','基础圆孔毛扣净','基础孔洞毛扣净') for t in sections) and title in ('立柱基础体积汇总','立柱基础逐项体积','立柱基础原始尺寸与依据','立柱基础材料范围与修约','台阶基础逐层体积','台阶基础逐层原件','圆形基础圆周率与口径','基础贯通孔毛扣净','基础贯通孔原始依据','基础贯通孔校验','基础贯通孔范围与常量','基础圆孔毛扣净','基础圆孔原始依据','基础圆孔校验','基础圆孔范围与常量','基础圆孔标高依据','基础圆孔逐层相交','基础孔洞毛扣净','基础孔洞原始依据','基础孔洞校验','基础孔洞范围与常量','基础孔洞标高依据','基础孔洞逐层相交','基础孔洞扣减阈值','基础孔洞实际与计量') or title in ('基础孔壁模板面积汇总','基础孔壁逐面接触净量','基础孔壁无接触扣减','基础孔壁原始范围与依据','基础孔壁来源孔与原件','基础孔壁模板做法与规则','基础模板横向条带','基础模板条带基准与原件','基础模板条带分离校验','基础模板条带开口交集','基础模板圆弧角度','基础模板圆弧基准与原件','基础模板圆弧分离校验','基础模板接触面积汇总','基础模板逐面标高与净量','基础模板侧开口实际扣减','基础模板原始范围与依据','基础模板做法与计算规则','多边形净量汇总','多边形外轮廓与孔','多边形原始顶点','多边形逐边计算','多边形扣孔边界校验','图纸身份候选采用','图纸校核待办','图纸目录与版次','平立剖图纸互参','项目事实与冲突候选','清单映射适用范围','清单编码映射依据','井板逐孔定位与扣减','井板孔包含与分离校验','井底盖板净实体汇总','井板逐项尺寸与孔扣减','井板标高范围及原件','井板材料做法与规则','检查井井筒计量汇总','井筒逐井尺寸与数量','井筒定位及原始依据','井筒材料做法与规则','管网矩形垫层汇总','垫层逐段尺寸与体积','垫层材料施工与修约','垫层来源设计坐标','管道外包体积汇总','管道逐段外径与占位','管道外包口径与常量','管道外包来源坐标','关联回填平衡','回填逐项扣减','回填来源计算索引','回填范围与修约','变断面沟槽汇总','沟槽站点断面','沟槽分段体积','沟槽来源坐标','设计管线长度汇总','管线设计坐标','三维管段计算','表面分区坐标','表面计量汇总','逐面计量决定','表面几何来源','表面计量规则','空间并集与扣减','长方体原始坐标','空间分层计算','空间分层条带','空间相交定位','区域并集与扣减','平面区域与坐标','区域条带计算过程','区域相交定位','算量原始单位换算','分项算量与扣减明细','分项规则与精度','工程量差异诊断','批量调量台账','变更签证台账','待询价与匹配任务','材料替换台账','工程结构与归属','分级汇总','分级取费规则','费用与税金','定额候选采用台账','企业定额复用台账'):
                    import math, unicodedata
                    width = min(55,max(15,110/len(headers)))-2
                    lines = max(sum(max(1,math.ceil(sum(2 if unicodedata.east_asian_width(ch) in 'WF' else 1 for ch in line)/width)) for line in str(cell.value or '').split('\n')) for cell in row)
                    s.row_dimensions[row[0].row].height = min(409,max(48,lines*14+10))
            for col in s.columns:
                s.column_dimensions[col[1].column_letter].width = min(55, max(15, 110/len(headers)))
            s.freeze_panes = 'A3'
            s.print_title_rows = '1:2'
            s.sheet_properties.pageSetUpPr.fitToPage = True
            s.page_setup.orientation = 'landscape'
            s.page_setup.paperSize = s.PAPERSIZE_A4
            s.page_setup.fitToWidth = 1
            s.page_setup.fitToHeight = 0
            s.oddHeader.center.text = label
            s.oddFooter.center.text = '第 &P 页 / 共 &N 页'
            if template:
                s.oddFooter.left.text = template['footer'].replace('&', '&&')
        wb.save(output)
        return output.getvalue(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'xlsx'
    if fmt == 'pdf':
        font = chinese_font()
        normal = ParagraphStyle('body', fontName=font, fontSize=8, leading=12, wordWrap='CJK')
        title_style = ParagraphStyle('title', parent=normal, fontSize=18, leading=26, spaceAfter=14)
        header_style = ParagraphStyle('table-header', parent=normal, textColor=colors.white if template else colors.black)
        def para(v):
            return Paragraph(escape(str(v)).replace('\n', '<br/>'), normal)
        story = []
        for title, headers, rows in sections:
            if story:
                story.append(PageBreak())
            heading = Paragraph(escape(title), title_style)
            heading.report_section = title
            story.extend([heading, para(label), Spacer(1, 6*mm)])
            # Bound huge evidence excerpts and wrap unbroken hashes.
            content = [[Paragraph(escape(h), header_style) for h in headers]]+[[para(str(v)) for v in row] for row in rows]
            if len(content) == 1:
                content.append([para('未提供 / 待编制')]+[para('') for _ in headers[1:]])
            # Keep a post's identifier and complete 80-digit station on one page.
            # Other sections still permit oversized evidence rows to split.
            t = Table(content, colWidths=[260*mm/len(headers)]*len(headers), repeatRows=1,
                      splitInRow=0 if title in ('立柱逐根端距','定距立柱逐根端距','立柱相邻柱距明细','立柱路径相邻柱距') else 1)
            t.setStyle(TableStyle([('BACKGROUND', (0,0),(-1,0),colors.HexColor('#'+template['accent'] if template else '#DFEBE6')),
                                  ('GRID',(0,0),(-1,-1),.3,colors.HexColor('#C7D6D0')),
                                  ('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),5),
                                  ('RIGHTPADDING',(0,0),(-1,-1),5),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7)]))
            story.append(t)
        def page(canvas, doc):
            canvas.saveState()
            canvas.setFont(font, 8)
            canvas.setFillColor(colors.HexColor('#7A8C85'))
            canvas.drawString(18*mm, 12*mm, label)
            if template and template['footer']:
                canvas.drawString(18*mm, 8*mm, template['footer'])
            canvas.drawRightString(278*mm, 12*mm, str(doc.page))
            canvas.restoreState()
        entries = []
        class ReportDocument(SimpleDocTemplate):
            def afterFlowable(self, flowable):
                title = getattr(flowable, 'report_section', None)
                if title:
                    key = 'section-'+str(len(entries)+1)
                    self.canv.bookmarkPage(key)
                    self.canv.addOutlineEntry(title, key, level=0, closed=False)
                    entries.append({'title': title, 'page': self.page})
        doc = ReportDocument(output, pagesize=(297*mm,210*mm), leftMargin=18*mm,rightMargin=18*mm,topMargin=17*mm,bottomMargin=20*mm)
        doc.build(story, onFirstPage=page, onLaterPages=page)
        if pagination is not None:
            pagination.update(schema='report-pagination-v1', page_count=doc.page,
                              sections=[{**v, 'end_page': entries[i+1]['page']-1 if i+1<len(entries) else doc.page} for i,v in enumerate(entries)])
        return output.getvalue(), 'application/pdf', 'pdf'
    raise HTTPException(422, '支持 pdf、xlsx、json；专有软件交换格式尚未验证')
