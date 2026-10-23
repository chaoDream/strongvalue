import copy
import io
import pytest
from reportlab.pdfgen import canvas
from backend import main as core, ocr, store
from tests.test_workflow import create, get


def pdf():
    b=io.BytesIO();c=canvas.Canvas(b,pagesize=(600,400));c.drawString(30,300,'SYNTHETIC OCR TEST');c.save();return b.getvalue()


def setup(clients,monkeypatch):
    c=clients['estimator'];pid=create(c)['id'];base=f'/api/v1/projects/{pid}'
    doc=c.post(base+'/documents',data={'specialty':'建筑'},files={'file':('scan.pdf',pdf(),'application/pdf')}).json()['document']
    monkeypatch.setattr(ocr,'capability',lambda:{'available':True,'engine':ocr.VERSION,'local_only':True,'notice':'test only'})
    return c,pid,doc,base+'/documents/'+doc['id']+'/ocr'


def output():
    return {'lines':[{'text':'图号：J-01','bbox':[.1,.2,.3,.04],'confidence':.8}], 'revision':3,'width':2400,'height':1600}


def submit(c,url,doc,**extra):
    return c.post(url,json={'page':1,'document_hash':doc['hash'],**extra})


def test_unreviewed_evidence_source_and_idempotency(clients,monkeypatch):
    c,pid,d,url=setup(clients,monkeypatch);calls=[]
    monkeypatch.setattr(ocr,'recognize',lambda raw,page:(calls.append(page) or output()))
    before=get(c,pid);j=submit(c,url,d);assert j.status_code==200,j.text
    after=get(c,pid);ev=after['evidence'][-1]
    assert ev['method']=='local_ocr' and ev['state']=='draft' and ev['document_hash']==d['hash']
    assert ev['ocr']['lines']==output()['lines'] and ev['page_geometry']['rotation']==0
    assert after['components']==before['components'] and after['facts']==before['facts'] and after['quotes']==before['quotes']
    assert submit(c,url,d).json()['state']=='completed' and calls==[1]
    assert len(get(c,pid)['evidence'])==len(after['evidence'])
    # Machine candidates remain distinct from source PDF text and need explicit original-page confirmation.
    candidate=c.get(f'/api/v1/projects/{pid}/drawing-candidates').json()['candidates'][0]
    assert candidate['source_method']=='local_ocr' and candidate['ocr'] and not after['drawing_sheets']


@pytest.mark.parametrize('page',[0,-1,2,501,True,'1',1.5])
def test_page_validation_before_running(clients,monkeypatch,page):
    c,pid,d,url=setup(clients,monkeypatch)
    monkeypatch.setattr(ocr,'recognize',lambda *a:pytest.fail('must not run'))
    assert submit(c,url,d,page=page).status_code==422


def test_hash_permission_and_capability(clients,monkeypatch):
    c,pid,d,url=setup(clients,monkeypatch)
    assert submit(c,url,d,document_hash='0'*64).status_code==409
    assert submit(clients['reviewer'],url,d).status_code==403
    assert submit(clients['other'],url,d).status_code==404
    monkeypatch.setattr(ocr,'capability',lambda:{'available':False,'notice':'not installed'})
    assert submit(c,url,d).status_code==503
    assert not [j for j in get(c,pid)['jobs'] if j['kind']=='local_ocr']


def test_failure_retry_preserves_local_parse_and_is_audited(clients,monkeypatch):
    c,pid,d,url=setup(clients,monkeypatch);before=get(c,pid)
    def fail(*a):raise ValueError('识别超时')
    monkeypatch.setattr(ocr,'recognize',fail)
    job=submit(c,url,d).json();p=get(c,pid)
    j=next(j for j in p['jobs'] if j['id']==job['id'])
    assert j['state']=='failed' and p['documents']==before['documents'] and p['evidence']==before['evidence']
    assert any(x['action']=='OCR_FAILED' for x in c.get(f'/api/v1/projects/{pid}/audit').json())
    monkeypatch.setattr(ocr,'recognize',lambda *a:output())
    assert c.post(f'/api/v1/projects/{pid}/jobs/{j["id"]}/retry').status_code==200
    j=next(row for row in get(c,pid)['jobs'] if row['id']==job['id'])
    assert j['state']=='completed' and j['attempts']==2


def test_replacement_during_execution_discards_result(clients,monkeypatch):
    c,pid,d,url=setup(clients,monkeypatch)
    def recognize(*a):
        with store.transaction() as conn:
            import json
            p=json.loads(conn.execute('SELECT data FROM projects WHERE id=?',(pid,)).fetchone()['data'])
            core.find(p,'documents',d['id'])['superseded']=True;store.save(conn,p)
        return output()
    monkeypatch.setattr(ocr,'recognize',recognize)
    submit(c,url,d);p=get(c,pid)
    assert not any(e['method']=='local_ocr' for e in p['evidence'])
    assert p['jobs'][-1]['state']=='failed'


def test_no_text_and_broken_original(clients,monkeypatch):
    c,pid,d,url=setup(clients,monkeypatch)
    monkeypatch.setattr(ocr,'recognize',lambda *a:{**output(),'lines':[]})
    submit(c,url,d);p=get(c,pid)
    assert p['jobs'][-1]['state']=='completed' and '不能据此判定' in p['jobs'][-1]['message']
    assert not any(e['method']=='local_ocr' for e in p['evidence'])
    (store.ROOT/'originals'/d['hash']).write_bytes(b'tampered')
    assert submit(c,url,d).status_code==409


@pytest.mark.parametrize('field,value', [('bbox',[0,0,-1,.1]),('bbox',[.9,0,.2,.1]),('bbox',[float('nan'),0,.1,.1]),('confidence',2),('confidence',True),('text','x'*2001)])
def test_invalid_recognizer_output_rejected(field,value):
    result=copy.deepcopy(output());result['lines'][0][field]=value
    with pytest.raises(ValueError):ocr.validate_lines(result)


def test_interrupted_ocr_does_not_mark_document_parse_failed(clients,monkeypatch):
    c,pid,d,url=setup(clients,monkeypatch)
    monkeypatch.setattr(ocr,'ocr_job',lambda *a:None)
    submit(c,url,d)
    from backend.recovery import recover_interrupted_jobs
    recover_interrupted_jobs();p=get(c,pid)
    assert p['jobs'][-1]['state']=='failed'
    assert p['documents'][0]['parse_state']=='needs_review'
