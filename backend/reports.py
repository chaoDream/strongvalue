"""Application report exporter. All tables use the same immutable quote snapshot."""
import io
import json
import os
from pathlib import Path
from threading import Lock
from collections import defaultdict
from decimal import Decimal
from html import escape

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
               [[t['name'], t['formula'], json.dumps(t['parameters'], ensure_ascii=False), t['measurement_basis'], t['unit'], t['quantity'], ', '.join(t['evidence'].values())] for t in snapshot['takeoffs']]),
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
               [[f['name'], f['base_amount'], f['rate'], f['amount'], f.get('source', '')] for f in (q['fees'] if q else [])]),
              ('资源价格表', ['资源', '类型', '单价', '地区', '期次', '税制', '有效期', '价格来源 / 授权'],
               [[r['name'], r.get('type', ''), '待询价' if r.get('pricing_status')=='unpriced' else r['price'], r.get('region',''), r.get('period',''), r.get('tax_mode',''), r.get('valid_until',''), '尚无价格依据' if r.get('pricing_status')=='unpriced' else r['source']+' / '+r['license']] for b in items for quota in b.get('quotas', []) for r in quota['resources']]),
              ('依据与证据', ['证据 ID', '文件', '文件哈希', '页码', '提取方式', '原文 / 标注', '坐标约定', '归一化区域', '页面旋转及裁切'],
               [[e['id'], next((d['name'] for d in snapshot['documents'] if d['id'] == e['document_id']), ''), e['document_hash'], e['page'] or '无固定页码', e['method'], e['text'], e.get('coordinate_space') or '', json.dumps(e.get('bbox'),ensure_ascii=False) if e.get('bbox') is not None else '', json.dumps(e.get('page_geometry'),ensure_ascii=False) if e.get('page_geometry') else ''] for e in snapshot['evidence']]),
              ('审核记录', ['对象类型', '对象 ID', '决定', '复核人', '时间', '依据', '对象哈希'],
               [[r['collection'], r['object_id'], r['decision'], r['reviewer'], r['timestamp'], r['reason'], r['object_hash']] for r in snapshot['reviews']+report_reviews]),
              ('缺项与警示', ['事项'], [[x] for x in ((q['warnings'] if q else [])+(['当前版本已失效；源输入已变更'] if q and q['id'] in p['stale_quotes'] else []))])]
    if not q:
        from .main import blockers
        result[-1][2].extend([[x] for x in blockers(p)])
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
        if title not in ('工程量差异诊断','批量调量台账','变更签证台账','待询价与匹配任务','材料替换台账'): continue
        expanded = []
        for row in rows:
            cells = [str(v).replace('\r', '').replace('\n', '；') for v in row]
            count = max((len(v)+139)//140 for v in cells)
            for part in range(count):
                segment = [v[part*140:(part+1)*140] for v in cells]
                if part: segment[0] = cells[0]+'（续'+str(part+1)+'）'
                expanded.append(segment)
        result[i] = (title,headers,expanded)
    result = [section for section in result if section[0] != '定额换算明细' or section[2]]
    return watermark, result


def export_report(p, q, fmt, approved, template=None):
    label, sections = tables(p, q, approved)
    if template:
        sections[0][2].extend([['编制单位', template['organization']], ['企业模板', template['name']+' V'+str(template['version'])],
                               ['内部说明', template['cover_note']], ['模板性质', template['purpose']]])
    output = io.BytesIO()
    if fmt == 'json':
        data = {'project': {'id': p['id'], 'name': p['name'], 'scope': p['scope']}, 'status': label,
                'quote': q, 'report_reviews': [r for r in p['reviews'] if q and r['collection'] == 'quotes' and r['object_id'] == q['id']],
                'unpriced': None if q else {k: p[k] for k in ['documents', 'evidence', 'facts', 'components', 'takeoffs', 'boq', 'reviews', 'resource_substitutions', 'applied_resource_substitutions']},
                'tables': sections, 'report_template': template}
        return json.dumps(data, ensure_ascii=False, indent=2).encode(), 'application/json', 'json'
    if fmt == 'xlsx':
        wb = Workbook()
        wb.remove(wb.active)
        numeric_headers = {'总金额', '分部分项金额', '工程量', '单价', '原单价', '新单价', '合价', '基数', '费率', '金额', '消耗', '换算系数', '资源价格', '单价贡献'}
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
                if title in ('工程量差异诊断','批量调量台账','变更签证台账','待询价与匹配任务','材料替换台账'):
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
            story.extend([Paragraph(escape(title), title_style), para(label), Spacer(1, 6*mm)])
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
        doc = SimpleDocTemplate(output, pagesize=(297*mm,210*mm), leftMargin=18*mm,rightMargin=18*mm,topMargin=17*mm,bottomMargin=20*mm)
        doc.build(story, onFirstPage=page, onLaterPages=page)
        return output.getvalue(), 'application/pdf', 'pdf'
    raise HTTPException(422, '支持 pdf、xlsx、json；专有软件交换格式尚未验证')
