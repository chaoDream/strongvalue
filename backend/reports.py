"""Application report exporter. All tables use the same immutable quote snapshot."""
import io
from .store import digest
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
    transcriptions=[e for e in snapshot['evidence'] if e.get('ocr_transcription')]
    if transcriptions:
        result.append(('扫描原行人工抄录',['人工证据 / 来源行','机器原文','人工核对文字','核对依据','核对人 / 时间','原件及位置'],
            [[e['id']+' / '+t['source_location']['evidence_id']+' 第'+str(t['source_location']['line'])+'行',
              t['original_line']['text'],e['text'],t['reason'],t['created_by']+' / '+t['created_at'],
              e['document_hash']+' / 第'+str(e['page'])+'页 / '+str(e['bbox'])]
             for e in transcriptions for t in [e['ocr_transcription']]]))
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
                           for t in snapshot['takeoffs'] if t.get('planar_summary') or t.get('spatial_summary') or t.get('surface_summary') or t.get('route_summary') or t.get('trench_summary') or t.get('pipe_summary') or t.get('bedding_summary') or t.get('chamber_summary') or t.get('plate_summary') for c in t['unit_conversions']])
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
    surfaces = [t for t in snapshot['takeoffs'] if t.get('surface_summary')]
    if surfaces:
        result.append(('表面计量汇总', ['构件', '用途', '来源空间构件 / 版本', '单组边界面积', '单组计入 / 排除面积', '重复数量 / 工程量', '计量依据', '来源版本哈希'],
                       [[t['name'], t['surface_summary']['purpose_label'], t['surface_summary']['source_name']+' / V'+str(t['surface_summary']['source_revision']),
                         t['surface_summary']['total_area']+' m²', t['surface_summary']['included_area']+' / '+t['surface_summary']['excluded_area']+' m²',
                         t['surface_summary']['multiplicity']+' / '+t['quantity']+' m²', t['measurement_basis'], t['source_component_hash']] for t in surfaces]))
        result.append(('逐面计量决定', ['构件 / 面', '朝向 / 平面位置（m）', '面内坐标范围（m）', '单组面积（m²）', '计量决定', '计量说明', '原始依据'],
                       [[t['name']+' / F'+str(f['index'])+(' / 原F'+str(f['parent_index'])+' 子'+str(f['sub_index']) if f.get('parent_index') else ''), f['normal']+' / '+f['plane'],
                         f['u_axis']+': '+f['u_from']+' → '+f['u_to']+'; '+f['v_axis']+': '+f['v_from']+' → '+f['v_to'],
                         f['area'], '计入' if f['included'] else '排除', f['basis'], f['evidence_id']] for t in surfaces for f in t['surface_faces']]))
        result.append(('表面几何来源', ['表面构件 / 来源长方体', '加扣 / 参数', '原始值', '原始单位', '原始依据'],
                       [[t['name']+' / '+r['name'], ('加项' if r['operation']=='add' else '扣减')+' / '+key, str(value), r['unit'], r['evidence'][key]]
                        for t in surfaces for r in t['source_geometry']['regions'] for key, value in r['parameters'].items()]))
        result.append(('表面计量规则', ['构件', '边界规则版本', '精度', '适用范围'],
                       [[t['name'], t['rule_version'], t['rounding_rule'], t['notice']] for t in surfaces]))
    cuts = [[t['name'], '原F'+str(c['parent_index']), c['coordinate_axis'], c['original_value']+' '+c['source_unit'],
             c['factor'], c['canonical_value']+' m', c['evidence_id'], c['rule_version']] for t in snapshot['takeoffs'] for c in t.get('surface_subdivisions', [])]
    if cuts:
        result.append(('表面分区坐标', ['表面构件','原始面','分割坐标轴','原值 / 单位','换算倍数','设计坐标','原始依据','单位规则'], cuts))
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
        if title not in ('图纸身份候选采用','图纸校核待办','图纸目录与版次','平立剖图纸互参','项目事实与冲突候选','清单映射适用范围','清单编码映射依据','井板逐孔定位与扣减','井板孔包含与分离校验','井底盖板净实体汇总','井板逐项尺寸与孔扣减','井板标高范围及原件','井板材料做法与规则','检查井井筒计量汇总','井筒逐井尺寸与数量','井筒定位及原始依据','井筒材料做法与规则','管网矩形垫层汇总','垫层逐段尺寸与体积','垫层材料施工与修约','垫层来源设计坐标','管道外包体积汇总','管道逐段外径与占位','管道外包口径与常量','管道外包来源坐标','关联回填平衡','回填逐项扣减','回填来源计算索引','回填范围与修约','变断面沟槽汇总','沟槽站点断面','沟槽分段体积','沟槽来源坐标','设计管线长度汇总','管线设计坐标','三维管段计算','表面分区坐标','表面计量汇总','逐面计量决定','表面几何来源','表面计量规则','空间并集与扣减','长方体原始坐标','空间分层计算','空间分层条带','空间相交定位','区域并集与扣减','平面区域与坐标','区域条带计算过程','区域相交定位','算量原始单位换算','分项算量与扣减明细','分项规则与精度','工程量差异诊断','批量调量台账','变更签证台账','待询价与匹配任务','材料替换台账','工程结构与归属','分级汇总','分级取费规则','费用与税金','定额候选采用台账','企业定额复用台账'): continue
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
                if table_data is not None or title in ('图纸身份候选采用','图纸校核待办','图纸目录与版次','平立剖图纸互参','项目事实与冲突候选','清单映射适用范围','清单编码映射依据','井板逐孔定位与扣减','井板孔包含与分离校验','井底盖板净实体汇总','井板逐项尺寸与孔扣减','井板标高范围及原件','井板材料做法与规则','检查井井筒计量汇总','井筒逐井尺寸与数量','井筒定位及原始依据','井筒材料做法与规则','管网矩形垫层汇总','垫层逐段尺寸与体积','垫层材料施工与修约','垫层来源设计坐标','管道外包体积汇总','管道逐段外径与占位','管道外包口径与常量','管道外包来源坐标','关联回填平衡','回填逐项扣减','回填来源计算索引','回填范围与修约','变断面沟槽汇总','沟槽站点断面','沟槽分段体积','沟槽来源坐标','设计管线长度汇总','管线设计坐标','三维管段计算','表面分区坐标','表面计量汇总','逐面计量决定','表面几何来源','表面计量规则','空间并集与扣减','长方体原始坐标','空间分层计算','空间分层条带','空间相交定位','区域并集与扣减','平面区域与坐标','区域条带计算过程','区域相交定位','算量原始单位换算','分项算量与扣减明细','分项规则与精度','工程量差异诊断','批量调量台账','变更签证台账','待询价与匹配任务','材料替换台账','工程结构与归属','分级汇总','分级取费规则','费用与税金','定额候选采用台账','企业定额复用台账'):
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
            t = Table(content, colWidths=[260*mm/len(headers)]*len(headers), repeatRows=1, splitInRow=1)
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
