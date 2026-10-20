"""Local, non-AI extraction. Uncertain drawing data is never auto-approved."""
import io
import re
import zipfile
from decimal import Decimal
from xml.etree import ElementTree
from pypdf import PdfReader
from openpyxl import load_workbook

EXTENSIONS = {'.pdf', '.docx', '.xlsx', '.txt', '.dxf', '.dwg', '.ifc'}


def validate_file(ext, raw):
    if ext not in EXTENSIONS:
        raise ValueError('支持 PDF、DOCX、XLSX、TXT、DWG、DXF、IFC')
    if ext == '.pdf' and not raw.startswith(b'%PDF-'):
        raise ValueError('文件内容不是 PDF')
    if ext in ('.docx', '.xlsx'):
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            if sum(i.file_size for i in z.infolist()) > 100*1024*1024:
                raise ValueError('压缩文档展开后过大')
            if any('vbaProject' in n for n in z.namelist()):
                raise ValueError('不支持含宏文件')


def extract(ext, raw):
    if ext == '.pdf':
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted:
            raise ValueError('加密 PDF 请解密后重新上传')
        if len(reader.pages) > 500:
            raise ValueError('单个 PDF 超过 500 页，请拆分后上传')
        return [(i+1, (page.extract_text() or '')[:100000]) for i, page in enumerate(reader.pages)]
    if ext == '.txt':
        return [(1, raw.decode('utf-8-sig'))]
    if ext == '.dxf':
        objects, units = dxf_objects(raw)
        return [(None, '\n'.join(o['text'] for o in objects if o.get('text'))+'\nCAD绘图单位：'+str(units))]
    if ext == '.docx':
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            root = ElementTree.fromstring(z.read('word/document.xml'))
        # DOCX has no reliable page geometry; page null explicitly means paragraph index.
        ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        return [(None, '\n'.join(''.join(p.itertext()) for p in root.findall('.//w:p', ns)))]
    if ext == '.xlsx':
        w = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        return [(None, s.title+'\n'+'\n'.join('\t'.join(str(v or '') for v in row) for row in s.iter_rows(values_only=True))) for s in w]
    return []


def dxf_objects(raw):
    """Read text and actual DIMENSION measurements from uncompressed ASCII DXF only."""
    if raw.startswith(b'AutoCAD Binary DXF'):
        raise ValueError('当前支持 ASCII DXF；二进制 DXF/DWG 需授权解析器')
    try: text = raw.decode('utf-8-sig')
    except UnicodeDecodeError: text = raw.decode('gb18030')
    lines = text.splitlines()
    if len(lines) % 2 or len(lines) > 500000:
        raise ValueError('DXF代码对不完整或超过当前解析上限')
    pairs = []
    for i in range(0, len(lines), 2):
        try: pairs.append((int(lines[i].strip()), lines[i+1].strip()))
        except ValueError: raise ValueError('不是有效 ASCII DXF 代码对')
    units = None
    for i, pair in enumerate(pairs[:-1]):
        if pair == (9, '$INSUNITS') and pairs[i+1][0] == 70:
            units = {0: '未指定', 1: 'inch', 4: 'mm', 5: 'cm', 6: 'm'}.get(int(pairs[i+1][1]), '其他单位')
    entities, entity = [], None
    for code, value in pairs:
        if code == 0:
            if entity: entities.append(entity)
            entity = {'type': value, 'groups': []} if value in ('TEXT', 'MTEXT', 'DIMENSION') else None
        elif entity is not None:
            entity['groups'].append((code, value))
    if entity: entities.append(entity)
    result = []
    for e in entities:
        values = dict(e['groups'])
        item = {'kind': e['type'], 'layer': values.get(8, ''), 'x': values.get(10), 'y': values.get(20), 'unit': units}
        if e['type'] in ('TEXT', 'MTEXT'):
            item['text'] = ''.join(v for k, v in e['groups'] if k in (1, 3)).replace('\\P', '\n')
        else:
            item.update(text=values.get(1, ''), measured_value=values.get(42), text_override=values.get(1) not in (None, '', '<>'))
            if values.get(42): item['text'] += ' [CAD DIMENSION measured='+values[42]+', unit='+str(units)+']'
        result.append(item)
    return result, units


def pdf_text_anchors(raw):
    reader = PdfReader(io.BytesIO(raw))
    objects = []
    for i, page in enumerate(reader.pages):
        width, height = float(page.mediabox.width), float(page.mediabox.height)
        def visit(text, cm, tm, font, size):
            if text.strip() and len(objects) < 20000:
                x, y = tm[4]*cm[0]+tm[5]*cm[2]+cm[4], tm[4]*cm[1]+tm[5]*cm[3]+cm[5]
                bbox = [max(0,min(1,x/width)), max(0,min(1,1-y/height)), min(1,len(text)*float(size)*.6/width), min(1,float(size)/height)] if not page.rotation and width and height else None
                objects.append({'page': i+1, 'text': text[:2000], 'bbox': bbox, 'coordinate_space': 'normalized_page_approximate',
                                'method': 'pdf_text_anchor', 'notice': '坐标仅用于定位文字，不能作为工程尺寸'})
        page.extract_text(visitor_text=visit)
    return objects


def dimension_candidates(text):
    out = []
    names = {'长': 'length', '长度': 'length', 'L': 'length', '宽': 'width', '宽度': 'width', 'W': 'width',
             '厚': 'thickness', '厚度': 'thickness', 'T': 'thickness', '高': 'height', '高度': 'height', 'H': 'height', '埋深': 'depth'}
    pattern = r'(?<![A-Za-z])(?P<label>长度|宽度|厚度|高度|埋深|长|宽|厚|高|L|W|T|H)\s*[=:：]?\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>mm|cm|m|毫米|厘米|米)(?![A-Za-z³²23])'
    for m in re.finditer(pattern, text):
        prefix = text[max(0,m.start()-120):m.start()]
        labels = re.findall(r'构件(?:名称)?[：:]\s*([^\n，,；;：:]{1,50})', prefix)
        unit = m['unit']
        factor = Decimal('.001') if unit in ('mm','毫米') else Decimal('.01') if unit in ('cm','厘米') else Decimal(1)
        out.append({'component_name': labels[-1].strip() if labels else '', 'parameter': names[m['label']],
                    'value': str(Decimal(m['value'])*factor), 'unit': 'm', 'original_value': m['value'], 'original_unit': unit,
                    'raw_text': m.group(0)})
    return out


def fact_candidates(text):
    patterns = [
        ('capacity', '处理规模', r'(\d+(?:\.\d+)?)\s*(?:m[³3]|立方米)\s*[/／每]\s*(?:d|日|天)', 'm³/日'),
        ('duration', '施工工期', r'(\d+)\s*(?:日历天|天工期)', '日历天'),
        ('supervision_rate', '监理报价费率', r'监理[^\n]{0,35}?(\d+(?:\.\d+)?)\s*[%％]', '%'),
        ('construction_amount', '施工标金额', r'(?:施工[^\n]{0,25}?(?:中标价|合同价|金额)|施工中标价)[：:\s人民币]*([\d,，]+(?:\.\d{1,2})?)\s*元', '元'),
    ]
    out = []
    for key, label, pattern, unit in patterns:
        for match in re.finditer(pattern, text, re.I):
            out.append({'key': key, 'label': label, 'value': match.group(1).replace(',', '').replace('，', ''),
                        'unit': unit, 'raw_text': text[max(0, match.start()-20):match.end()+30]})
    return out
