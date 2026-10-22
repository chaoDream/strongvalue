"""Exact stored-file previews and physical PDF pagination, using synthetic data."""
import copy
import hashlib
import io
import json
import pytest
from pypdf import PdfReader
from fastapi import HTTPException
from backend import store, reports
from tests.test_workflow import create, priced_project, get, approve, upload
from tests.test_report_templates import template_body
from tests.test_project_access import restrict


def generate(e, pid, **body):
    response = e.post('/api/v1/projects/'+pid+'/report-previews', json=body)
    assert response.status_code == 200, response.text
    return response.json()


def change_artifact(pid, aid, update):
    # Deliberate low-level corruption in an isolated test database.
    with store.transaction() as c:
        artifact = json.loads(c.execute('SELECT data FROM artifacts WHERE id=? AND project_id=?', (aid, pid)).fetchone()['data'])
        update(artifact)
        c.execute('DROP TRIGGER artifact_no_update')
        c.execute('UPDATE artifacts SET data=? WHERE id=?', (store.canonical(artifact), aid))


def test_preview_returns_own_artifact_and_exact_download_bytes(clients):
    e, r, pid, _, q = priced_project(clients); base='/api/v1/projects/'+pid
    before = copy.deepcopy(get(e,pid)['quotes'])
    first = generate(e,pid,quote_id=q['id']); aid=first['artifact']['id']
    assert first['file_status']=='verified' and first['current_status']=='unapproved'
    assert first['artifact']['quote_id']==q['id'] and not first['artifact']['approved']
    response=e.get(first['file_url']);assert response.status_code==200,response.text
    assert response.headers['content-disposition'].startswith('inline')
    assert hashlib.sha256(response.content).hexdigest()==first['artifact']['hash']==response.headers['x-artifact-hash']
    download=e.get(base+'/artifacts/'+aid+'/file');assert download.content==response.content
    assert download.headers['content-disposition'].startswith('attachment')
    second=generate(r,pid,quote_id=q['id']);assert second['artifact']['id']!=aid
    listed=e.get(base+'/artifacts').json();assert listed[0]['id']==second['artifact']['id']
    assert e.get(base+'/artifacts/'+aid+'/preview').json()['artifact']['id']==aid
    assert get(e,pid)['quotes']==before and len(e.get(base+'/artifacts').json())==2
    events=e.get(base+'/audit').json();assert sum(v['action']=='EXPORT_COMPLETED' for v in events)==2


def test_physical_pages_bookmarks_and_section_ranges_match(clients):
    e, _, pid, _, q=priced_project(clients);result=generate(e,pid,quote_id=q['id'])
    reader=PdfReader(io.BytesIO(e.get(result['file_url']).content));sections=result['sections']
    assert result['manifest_available'] and result['page_count']==len(reader.pages)
    assert sections[0]['page']==1 and sections[-1]['end_page']==len(reader.pages)
    assert len(sections)==len(reader.outline)
    for i,(section,dest) in enumerate(zip(sections,reader.outline)):
        assert reader.get_destination_page_number(dest)+1==section['page'] and dest.title==section['title']
        assert section['title'] in reader.pages[section['page']-1].extract_text()
        assert section['end_page']==(sections[i+1]['page']-1 if i+1<len(sections) else len(reader.pages))


def test_multi_page_table_ranges_cover_all_continuation_pages(clients):
    e, _, pid, _, q=priced_project(clients);base='/api/v1/projects/'+pid
    b=get(e,pid)['boq'][0]
    response=e.post(base+'/boq/'+b['id']+'/split',json={'parts':[{'name':'合成分页条目'+str(i),'quantity':'.5'} for i in range(30)]})
    assert response.status_code==200,response.text
    result=generate(e,pid,unpriced=True);reader=PdfReader(io.BytesIO(e.get(result['file_url']).content))
    section=next(s for s in result['sections'] if s['title']=='分部分项清单')
    assert section['end_page']>section['page']
    text=''.join(p.extract_text() for p in reader.pages[section['page']-1:section['end_page']])
    assert '合成分页条目0' in text and '合成分页条目29' in text and result['page_count']==len(reader.pages)


def test_template_version_and_historical_approved_file_remain_fixed(clients):
    e,r,pid,_,q=priced_project(clients);base='/api/v1/projects/'+pid;admin=clients['admin']
    approve(r,pid,'quotes',q)
    template=admin.post('/api/v1/organization/report-templates',json=template_body()).json()
    result=generate(e,pid,quote_id=q['id'],template_id=template['id']);aid=result['artifact']['id']
    assert result['artifact']['approved'] and result['current_status']=='approved'
    original=e.get(result['file_url']).content
    admin.post('/api/v1/organization/report-templates',json=template_body(previous_id=template['id'],cover_note='新版说明'))
    upload(e,pid,'合成的新图纸版本',name='fixture.txt')
    old=e.get(base+'/artifacts/'+aid+'/preview').json()
    assert old['artifact']==result['artifact'] and old['artifact']['approved'] and old['current_status']=='stale'
    assert e.get(old['file_url']).content==original and old['artifact']['template']==template
    fresh=generate(e,pid,quote_id=q['id']);assert not fresh['artifact']['approved'] and fresh['current_status']=='stale'
    assert '未完成成果草稿' in PdfReader(io.BytesIO(e.get(fresh['file_url']).content)).pages[0].extract_text()


def test_explicit_unpriced_does_not_fall_back_to_latest_quote(clients):
    e,_,pid,_,q=priced_project(clients);result=generate(e,pid,unpriced=True)
    assert result['artifact']['quote_id'] is None and result['current_status']=='unpriced'
    text=PdfReader(io.BytesIO(e.get(result['file_url']).content)).pages[0].extract_text()
    assert '无价草稿' in text and '未计价' in text


@pytest.mark.parametrize('body',[{}, {'unpriced':False}, {'quote_id':'','unpriced':False}, {'quote_id':'id','unpriced':True},
    {'quote_id':[]}, {'unpriced':'true'}, {'unpriced':1}, {'unpriced':True,'template_id':{}}, {'unpriced':True,'format':'json'}, {'unpriced':True,'tenant':'other'}])
def test_ambiguous_invalid_selection_never_creates_artifact(clients,body):
    e=clients['estimator'];pid=create(e)['id'];base='/api/v1/projects/'+pid
    assert e.post(base+'/report-previews',json=body).status_code==422
    assert e.get(base+'/artifacts').json()==[]


def test_preview_access_checks_tenant_project_membership_and_template(clients):
    e,r,pid,_,q=priced_project(clients);base='/api/v1/projects/'+pid;result=generate(e,pid,quote_id=q['id']);aid=result['artifact']['id']
    assert clients['other'].post(base+'/report-previews',json={'quote_id':q['id']}).status_code==404
    assert clients['other'].get(base+'/artifacts/'+aid+'/preview').status_code==404
    other_pid=create(e)['id']
    assert e.get('/api/v1/projects/'+other_pid+'/artifacts/'+aid+'/preview').status_code==404
    assert e.post('/api/v1/projects/'+other_pid+'/report-previews',json={'quote_id':q['id']}).status_code==404
    template=clients['other'].post('/api/v1/organization/report-templates',json=template_body()).json()
    assert e.post(base+'/report-previews',json={'quote_id':q['id'],'template_id':template['id']}).status_code==404
    restrict(clients['admin'],pid,[])
    assert e.get(base+'/artifacts/'+aid+'/preview').status_code==404
    assert r.post(base+'/report-previews',json={'quote_id':q['id']}).status_code==404
    assert clients['admin'].get(base+'/artifacts/'+aid+'/preview').status_code==200


@pytest.mark.parametrize('kind',['missing','corrupt'])
def test_missing_or_corrupt_pdf_never_opens_as_valid_preview(clients,kind):
    e,_,pid,_,q=priced_project(clients);result=generate(e,pid,quote_id=q['id']);artifact=result['artifact']
    path=store.ROOT/'exports'/artifact['hash']
    if kind=='missing':path.unlink()
    else:path.write_bytes(b'corrupted synthetic PDF')
    base='/api/v1/projects/'+pid+'/artifacts/'+artifact['id']
    assert e.get(base+'/preview').status_code==409 and e.get(base+'/file?inline=true').status_code==409


@pytest.mark.parametrize('fmt',['json','xlsx','zip'])
def test_non_pdf_artifacts_are_not_sent_to_pdf_reader(clients,fmt):
    e,_,pid,_,q=priced_project(clients);base='/api/v1/projects/'+pid
    response=e.get(base+'/exports?format='+fmt);assert response.status_code==200,response.text
    aid=response.headers['x-artifact-id']
    assert e.get(base+'/artifacts/'+aid+'/preview').status_code==422


@pytest.mark.parametrize('kind',['count','gap','wrong_title','outline_range','schema'])
def test_manifest_must_match_actual_pdf_outline_and_complete_page_ranges(clients,kind):
    e,_,pid,_,q=priced_project(clients);result=generate(e,pid,quote_id=q['id']);aid=result['artifact']['id']
    def corrupt(a):
        m=a['pdf_manifest']
        if kind=='count':m['page_count']+=1
        elif kind=='gap':m['sections'][0]['end_page']+=1
        elif kind=='wrong_title':m['sections'][0]['title']='错误章节'
        elif kind=='outline_range':m['sections'][0]['end_page']+=1;m['sections'][1]['page']+=1
        elif kind=='schema':m['schema']='unknown'
    change_artifact(pid,aid,corrupt)
    assert e.get('/api/v1/projects/'+pid+'/artifacts/'+aid+'/preview').status_code==409


def test_legacy_pdf_without_manifest_keeps_manual_paging(clients):
    e,_,pid,_,q=priced_project(clients);result=generate(e,pid,quote_id=q['id']);aid=result['artifact']['id']
    change_artifact(pid,aid,lambda a:a.pop('pdf_manifest'))
    response=e.get('/api/v1/projects/'+pid+'/artifacts/'+aid+'/preview');assert response.status_code==200,response.text
    assert response.json()['page_count']==result['page_count'] and response.json()['sections']==[] and not response.json()['manifest_available']


@pytest.mark.parametrize('field',['snapshot','total'])
def test_tampered_quote_blocks_new_generation_without_overwriting_old_pdf(clients,field):
    e,_,pid,_,q=priced_project(clients);base='/api/v1/projects/'+pid;old=generate(e,pid,quote_id=q['id']);raw=e.get(old['file_url']).content
    with store.transaction() as c:
        p=json.loads(c.execute('SELECT data FROM projects WHERE id=?',(pid,)).fetchone()['data'])
        if field=='snapshot':p['quotes'][0]['snapshot']['boq'][0]['quantity']='999'
        else:p['quotes'][0]['total']='0.01'
        store.save(c,p)
    assert e.post(base+'/report-previews',json={'quote_id':q['id']}).status_code==409
    assert e.get(base+'/exports?format=pdf&quote_id='+q['id']).status_code==409
    assert len(e.get(base+'/artifacts').json())==1
    assert e.get(base+'/artifacts/'+old['artifact']['id']+'/preview').json()['current_status']=='unverified'
    assert e.get(old['file_url']).content==raw


def test_rendering_failure_rolls_back_artifact_and_export_audit(clients,monkeypatch):
    e=clients['estimator'];pid=create(e)['id'];base='/api/v1/projects/'+pid
    def fail():raise HTTPException(422,'合成字体不可用')
    monkeypatch.setattr(reports,'chinese_font',fail)
    assert e.post(base+'/report-previews',json={'unpriced':True}).status_code==422
    assert e.get(base+'/artifacts').json()==[] and not any(v['action']=='EXPORT_COMPLETED' for v in e.get(base+'/audit').json())
