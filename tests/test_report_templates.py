import io
import json

import pytest
from openpyxl import load_workbook
from pypdf import PdfReader
from backend import store
from tests.test_workflow import create, get, priced_project


def template_body(**extra):
    return {'name':'合成内部模板','organization':'测试编制单位','cover_note':'仅用于模板软件测试，不具备专业效力',
            'footer':'内部审阅 <样式> & 版本','accent':'244B78',**extra}


def test_template_permissions_isolation_versions_and_stale_revision(clients):
    admin=clients['admin'];e=clients['estimator'];r=clients['reviewer'];other=clients['other']
    path='/api/v1/organization/report-templates'
    assert e.post(path,json=template_body()).status_code==403
    assert r.post(path,json=template_body()).status_code==403
    first=admin.post(path,json=template_body()).json()
    assert first['version']==1
    assert len(e.get(path).json())==1
    assert other.get(path).json()==[]
    assert other.post(path,json=template_body(previous_id=first['id'])).status_code==404
    second=admin.post(path,json=template_body(previous_id=first['id'],cover_note='新版内部说明')).json()
    assert second['version']==2 and second['family']==first['family']
    assert admin.post(path,json=template_body(previous_id=first['id'])).status_code==409
    assert admin.get(path).json()[1]==first
    assert admin.post(path,json=template_body(accent='FFFFFF')).status_code==422
    assert admin.post(path,json=template_body(accent={})).status_code==422
    assert admin.post(path,json=template_body(footer='x'*41)).status_code==422
    with store.connect() as c:
        with pytest.raises(Exception,match='immutable'):
            c.execute('DELETE FROM report_templates')


def test_template_exports_preserve_quote_watermark_history_and_formula_safety(clients):
    e,r,pid,comp,q=priced_project(clients);admin=clients['admin'];base=f'/api/v1/projects/{pid}'
    first=admin.post('/api/v1/organization/report-templates',json=template_body(organization='=HYPERLINK("https://example.invalid")')).json()
    quote=json.dumps(get(e,pid)['quotes'],sort_keys=True)
    url=base+'/exports?template_id='+first['id']+'&format='
    response=e.get(url+'xlsx');assert response.status_code==200,response.text
    wb=load_workbook(io.BytesIO(response.content));sheet=wb.worksheets[0]
    cells=[cell for row in sheet for cell in row if isinstance(cell.value,str)]
    value=next(cell for cell in cells if cell.value.startswith('=HYPERLINK'))
    assert value.data_type=='s'
    assert '未完成成果草稿' in sheet['A1'].value
    assert sheet['A1'].fill.fgColor.rgb.endswith(first['accent'])
    pdf=e.get(url+'pdf');assert pdf.status_code==200,pdf.text[:300] if pdf.status_code!=200 else ''
    text=''.join(page.extract_text() for page in PdfReader(io.BytesIO(pdf.content)).pages)
    assert first['cover_note'] in text and first['footer'] in text
    assert '未完成成果草稿' in text and q['total'] in text
    result=e.get(url+'json').json()
    assert result['report_template']==first and result['quote']['id']==q['id']
    assert json.dumps(get(e,pid)['quotes'],sort_keys=True)==quote
    artifact=next(a for a in e.get(base+'/artifacts').json() if a['format']=='pdf')
    assert artifact['template']==first and artifact['template_hash']==store.digest(first)
    admin.post('/api/v1/organization/report-templates',json=template_body(previous_id=first['id'],cover_note='后来修订'))
    assert e.get(base+'/artifacts/'+artifact['id']+'/file').content==pdf.content
    other=clients['other'];other_project=create(other)['id']
    assert other.get(f'/api/v1/projects/{other_project}/exports?format=json&template_id='+first['id']).status_code==404
