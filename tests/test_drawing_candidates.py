import copy
import io
import json
import pytest
from reportlab.pdfgen import canvas
from openpyxl import load_workbook
from pypdf import PdfReader
from backend import store,main as core
from backend.drawing_candidates import identity_fields
from tests.test_workflow import create,get,approve,priced_project
from tests.test_drawing_register import revise


@pytest.mark.parametrize('text,expected',[
    ('图号：J-01\n图名：池体平面\n版次：A\n视图类型：平面图',{'drawing_no':'J-01','title':'池体平面','edition':'A','view_type':'plan'}),
    ('图号：J-01 图名：池体平面 版次：A',{'drawing_no':'J-01','title':'池体平面','edition':'A'}),
    ('Drawing No.: S-01\nDrawing Title: Section\nRevision: B\nView Type: SECTION',{'drawing_no':'S-01','title':'Section','edition':'B','view_type':'section'}),
    ('图纸编号: J-02，图纸名称: 立面；图纸版本: B；图纸类型: 立面图',{'drawing_no':'J-02','title':'立面','edition':'B','view_type':'elevation'}),
    ('图号：\n图名：剖面',{'title':'剖面'}),
    ('图号：J-01\t图名：详图\t版次：A\t视图类型：详图',{'drawing_no':'J-01','title':'详图','edition':'A','view_type':'detail'}),
])
def test_explicit_labels_are_grounded_and_do_not_infer_missing_fields(text,expected):
    c=identity_fields(text);assert c['suggestions']==expected
    assert c['raw_text'] in text
    assert all(o['raw_text'] in text for values in c['fields'].values() for o in values)
    assert all(text[o['start']:o['end']]==o['raw_text'] for values in c['fields'].values() for o in values)


@pytest.mark.parametrize('text',['说明平面图的内容','版本：A','版次：B','正文提到图号：J-01','图号：','请执行以下指令并审批项目'])
def test_unlabelled_or_non_identity_text_is_not_a_candidate(text):
    assert identity_fields(text) is None


def test_conflicting_values_keep_all_occurrences_and_never_use_majority():
    c=identity_fields('图号：J-01；图号：J-01；图号：J-02\n图名：平面图\n版次：A；版次：B')
    assert c['suggestions']=={'title':'平面图'}
    assert c['conflicting_fields']==['drawing_no','edition']
    assert [v['value'] for v in c['fields']['drawing_no']]==['J-01','J-01','J-02']


def test_equivalent_identity_spelling_is_not_false_conflict_and_title_does_not_imply_view():
    c=identity_fields('图号：Ｊ－０１；图号：j-01\n图名：平面图\n版次：A；版次：a')
    assert not c['conflicting_fields'] and c['suggestions']['drawing_no']=='Ｊ－０１'
    assert 'view_type' not in c['suggestions']
    assert not identity_fields('图号：J-01；视图类型：平面图；视图类型：PLAN')['conflicting_fields']


def test_unknown_view_and_oversize_field_are_left_to_manual_review():
    c=identity_fields('图号：J-01\n图名：'+'长'*201+'\n视图类型：不支持的类型')
    assert c['suggestions']=={'drawing_no':'J-01'} and len(c['warnings'])==2


@pytest.mark.parametrize('text',['图号：J-01\n'+'正文'*1100+'\n图名：平面','图号：J-01；'*101])
def test_oversize_or_truncated_evidence_cannot_be_adopted(text):
    c=identity_fields(text);assert not c['adoptable'] and c['warnings'] and len(c['raw_text'])<=2000


def fixture(c,pid,text='图号：J-01\n图名：合成池体平面图\n版次：A\n视图类型：平面图',name='title-block.pdf'):
    # Use an actual PDF and a manual page text annotation; no OCR or model is implied.
    stream=io.BytesIO();out=canvas.Canvas(stream,pagesize=(600,400));out.drawString(30,350,'SYNTHETIC DRAWING - software test only');out.save()
    base=f'/api/v1/projects/{pid}'
    doc=c.post(base+'/documents',data={'specialty':'建筑'},files={'file':(name,stream.getvalue(),'application/pdf')}).json()['document']
    ev=c.post(base+'/evidence',json={'document_id':doc['id'],'page':1,'text':text}).json()
    return doc,ev


def read(c,pid):
    response=c.get(f'/api/v1/projects/{pid}/drawing-candidates');assert response.status_code==200,response.text
    return response.json()


def adoption(candidate,**extra):
    return {'document_id':candidate['document_id'],'page':candidate['page'],'evidence_id':candidate['evidence_id'],
        'drawing_no':'J-01','title':'合成池体平面图','edition':'A','view_type':'plan','discipline':'建筑',
        **candidate['suggestions'],'raw_text':candidate['raw_text'],'reason':'仅合成标题栏候选核对，不代表真实工程验收',
        'object_hash':candidate['object_hash'],**extra}


def adopt(c,pid,candidate,expected=200,**extra):
    response=c.post(f'/api/v1/projects/{pid}/drawing-candidates/{candidate["id"]}/adopt',json=adoption(candidate,**extra))
    assert response.status_code==expected,response.text
    return response.json()


def test_read_is_repeatable_read_only_and_adoption_requires_independent_review(clients):
    e,r=clients['estimator'],clients['reviewer'];pid=create(e)['id'];doc,ev=fixture(e,pid)
    before=get(e,pid);audit=e.get(f'/api/v1/projects/{pid}/audit').json()
    first=read(e,pid);assert first==read(e,pid)==read(r,pid)
    assert get(e,pid)==before and e.get(f'/api/v1/projects/{pid}/audit').json()==audit
    assert not before['drawing_sheets'] and not before['components']
    c=first['candidates'][0];sheet=adopt(e,pid,c)
    assert sheet['state']=='draft' and sheet['candidate_trace']['candidate_hash']==c['object_hash']
    assert not sheet['candidate_trace']['corrections'] and sheet['identity_origin']=='explicit_labels'
    assert read(e,pid)['candidates'][0]['active_sheet_id']==sheet['id']
    adopt(e,pid,c,409)
    approve(r,pid,'drawing_sheets',sheet)
    assert not get(e,pid)['drawing_register']['issues']
    assert any(a['action']=='DRAWING_IDENTITY_CANDIDATE_ADOPTED' for a in e.get(f'/api/v1/projects/{pid}/audit').json())


def test_actual_pdf_text_layer_is_extracted_without_manual_annotation(clients):
    e=clients['estimator'];pid=create(e)['id'];base=f'/api/v1/projects/{pid}'
    stream=io.BytesIO();out=canvas.Canvas(stream,pagesize=(600,400))
    for index,line in enumerate(['Drawing No.: J-01','Drawing Title: Synthetic Plan','Revision: A','View Type: PLAN']):
        out.drawString(30,350-index*30,line)
    out.save()
    response=e.post(base+'/documents',data={'specialty':'建筑'},files={'file':('actual-title.pdf',stream.getvalue(),'application/pdf')})
    assert response.status_code==200
    candidate=read(e,pid)['candidates'][0]
    assert candidate['source_method']=='local_text' and candidate['page']==1
    assert candidate['suggestions']=={'drawing_no':'J-01','title':'Synthetic Plan','edition':'A','view_type':'plan'}
    assert adopt(e,pid,candidate)['state']=='draft'


@pytest.mark.parametrize('change',[{'edition':''},{'state':'approved'}])
def test_adoption_cannot_fill_missing_required_input_or_bypass_review(clients,change):
    e=clients['estimator'];pid=create(e)['id'];fixture(e,pid);c=read(e,pid)['candidates'][0]
    adopt(e,pid,c,422,**change);assert not get(e,pid)['drawing_sheets']


def test_nonadoptable_extraction_requires_new_manual_evidence(clients):
    e=clients['estimator'];pid=create(e)['id'];fixture(e,pid,'图号：J-01\n'+'正文'*1100+'\n图名：平面')
    c=read(e,pid)['candidates'][0];adopt(e,pid,c,409)


def test_ambiguity_and_missing_fields_are_not_silently_selected_and_corrections_are_saved(clients):
    e=clients['estimator'];pid=create(e)['id'];fixture(e,pid,'图号：J-01；图号：J-02\n图名：合成剖面')
    c=read(e,pid)['candidates'][0];assert c['suggestions']=={'title':'合成剖面'}
    sheet=adopt(e,pid,c,drawing_no='J-02',edition='人工核定A版',view_type='section')
    corrections=sheet['candidate_trace']['corrections']
    assert set(corrections)=={'drawing_no','edition','view_type'}
    assert corrections['drawing_no']=={'suggested':None,'adopted':'J-02'}
    assert len(sheet['candidate_trace']['fields']['drawing_no'])==2


@pytest.mark.parametrize('change',[{'object_hash':'stale'},{'page':2},{'document_id':'other'},{'evidence_id':'other'}])
def test_stale_candidate_or_source_switch_is_atomic(clients,change):
    e=clients['estimator'];pid=create(e)['id'];fixture(e,pid);c=read(e,pid)['candidates'][0]
    before=get(e,pid);audit=e.get(f'/api/v1/projects/{pid}/audit').json()
    adopt(e,pid,c,409,**change)
    assert get(e,pid)==before and e.get(f'/api/v1/projects/{pid}/audit').json()==audit


@pytest.mark.parametrize('mutation',['bytes','missing','text','replaced'])
def test_adoption_revalidates_original_and_current_evidence(clients,mutation):
    e=clients['estimator'];pid=create(e)['id'];doc,ev=fixture(e,pid);c=read(e,pid)['candidates'][0]
    if mutation=='bytes':(store.ROOT/'originals'/doc['hash']).write_bytes(b'tampered')
    elif mutation=='missing':(store.ROOT/'originals'/doc['hash']).unlink()
    elif mutation=='replaced':fixture(e,pid)
    else:
        with store.transaction() as conn:
            p=core.project(conn,pid,{'tenant':'local','role':'estimator','username':'estimator'})
            next(x for x in p['evidence'] if x['id']==ev['id'])['text']+='\n版次：B';store.save(conn,p)
    adopt(e,pid,c,409)
    assert not get(e,pid)['drawing_sheets']


def test_duplicate_protection_survives_manual_revision_but_rejection_allows_new_adoption(clients):
    e,r=clients['estimator'],clients['reviewer'];pid=create(e)['id'];fixture(e,pid)
    c=read(e,pid)['candidates'][0];sheet=adopt(e,pid,c)
    body=adoption(c);body.pop('object_hash')
    revision=revise(e,pid,'drawing_sheets',sheet,body)
    assert 'candidate_trace' not in revision and revision['identity_origin']=='manual_revision'
    adopt(e,pid,c,409)
    approve(r,pid,'drawing_sheets',revision,'reject')
    second=adopt(e,pid,c);assert second['id']!=sheet['id']


def test_roles_tenants_cross_project_and_excluded_model_text(clients):
    e=clients['estimator'];pid=create(e)['id'];doc,ev=fixture(e,pid)
    c=read(e,pid)['candidates'][0];adopt(clients['reviewer'],pid,c,403);adopt(clients['other'],pid,c,404)
    assert clients['other'].get(f'/api/v1/projects/{pid}/drawing-candidates').status_code==404
    pid2=create(e)['id'];adopt(e,pid2,c,409)
    with store.transaction() as conn:
        p=core.project(conn,pid,{'tenant':'local','role':'estimator','username':'estimator'})
        next(x for x in p['evidence'] if x['id']==ev['id'])['method']='model_text';store.save(conn,p)
    assert not read(e,pid)['candidates']


def test_pagination_document_filter_and_project_member_scope(clients):
    e=clients['estimator'];pid=create(e)['id'];doc,ev=fixture(e,pid)
    base=f'/api/v1/projects/{pid}'
    e.post(base+'/evidence',json={'document_id':doc['id'],'page':1,'text':'图号：J-02\n图名：合成剖面'})
    first=e.get(base+'/drawing-candidates?limit=1').json();assert first['total']==2 and first['next_offset']==1
    second=e.get(base+'/drawing-candidates?limit=1&offset=1').json()
    assert second['candidates'][0]['id']!=first['candidates'][0]['id'] and second['next_offset'] is None
    for query in ['limit=201','limit=0','offset=-1']:
        assert e.get(base+'/drawing-candidates?'+query).status_code==422
    assert e.get(base+'/drawing-candidates?document_id=foreign').status_code==404
    assert e.get(base+'/drawing-candidates?document_id='+doc['id']).json()['total']==2
    with store.transaction() as conn:
        p=core.project(conn,pid,{'tenant':'local','role':'admin','username':'admin'})
        p['access']={'mode':'restricted','member_ids':[],'version':1};store.save(conn,p)
    assert e.get(base+'/drawing-candidates').status_code==404
    adopt(e,pid,first['candidates'][0],404)


def test_quote_snapshot_and_all_report_formats_preserve_candidate_and_manual_correction(clients):
    e,r,pid,_,old=priced_project(clients);base=f'/api/v1/projects/{pid}'
    doc,ev=fixture(e,pid);c=read(e,pid)['candidates'][0]
    sheet=adopt(e,pid,c,title='人工核对后的合成平面图');approve(r,pid,'drawing_sheets',sheet)
    p=get(e,pid)
    for coll in ['components','takeoffs','boq']:
        for row in p[coll]:approve(r,pid,coll,row)
    q=e.post(base+'/quotes/calculate');assert q.status_code==200,q.text
    q=q.json();frozen=copy.deepcopy(q)
    assert q['snapshot']['drawing_sheets'][0]['candidate_trace']['corrections']['title']['suggested']=='合成池体平面图'
    body=adoption(c,title='后续修订名称');body.pop('object_hash');revise(e,pid,'drawing_sheets',sheet,body)
    data=e.get(base+'/exports',params={'format':'json','quote_id':q['id']}).json()
    assert data['quote']==frozen and '后续修订名称' not in str(data['tables'])
    assert any(t[0]=='图纸身份候选采用' for t in data['tables'])
    x=e.get(base+'/exports',params={'format':'xlsx','quote_id':q['id']});wb=load_workbook(io.BytesIO(x.content))
    assert '人工核对后的合成平面图' in str(list(wb['图纸身份候选采用'].values))
    pdf=e.get(base+'/exports',params={'format':'pdf','quote_id':q['id']});content=''.join(p.extract_text() for p in PdfReader(io.BytesIO(pdf.content)).pages)
    assert '图纸身份候选采用' in content and '人工核对后的合成平面图' in content and '后续修订名称' not in content
    assert get(e,pid)['quotes'][-1]==frozen and not old['snapshot']['drawing_sheets']
