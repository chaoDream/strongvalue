import io
import copy
import hashlib
import pytest
from fastapi import HTTPException
from reportlab.pdfgen import canvas
from pypdf import PdfReader, PdfWriter
from backend import store
from backend.evidence_regions import SPACE, page_region
from tests.test_workflow import create, get, upload


def region_pdf(clients):
    e=clients['estimator'];pid=create(e)['id']
    stream=io.BytesIO();pdf=canvas.Canvas(stream,pagesize=(400,300))
    pdf.drawString(40,220,'TEST DRAWING - software fixture');pdf.rect(50,50,200,100);pdf.showPage()
    pdf.drawString(40,220,'ROTATED / CROPPED TEST PAGE');pdf.rect(60,60,100,100);pdf.save()
    writer=PdfWriter();reader=PdfReader(io.BytesIO(stream.getvalue()))
    writer.add_page(reader.pages[0]);second=reader.pages[1];second.rotate(90);second.cropbox.lower_left=(20,30);second.cropbox.upper_right=(380,280);writer.add_page(second)
    output=io.BytesIO();writer.write(output);raw=output.getvalue()
    response=e.post(f'/api/v1/projects/{pid}/documents',data={'specialty':'建筑'},files={'file':('drawing-test.pdf',raw,'application/pdf')})
    assert response.status_code==200,response.text
    document=response.json()['document']
    body={'document_id':document['id'],'document_hash':document['hash'],'page':2,'bbox':[.1,.2,.3,.4],
          'coordinate_space':SPACE,'text':'仅测试：旋转页上的人工原图定位','drawing_no':'TEST-02'}
    return e,pid,document,body,raw


def test_region_preserves_display_coordinates_rotation_crop_and_no_quantity(clients):
    e,pid,doc,body,raw=region_pdf(clients);base=f'/api/v1/projects/{pid}'
    before=get(e,pid);response=e.post(base+'/evidence',json=body)
    assert response.status_code==200,response.text
    evidence=response.json();assert evidence['bbox']==body['bbox'] and evidence['coordinate_space']==SPACE
    assert evidence['page_geometry']=={'rotation':90,'crop_box':[20.0,30.0,380.0,280.0]}
    assert evidence['document_hash']==hashlib.sha256(raw).hexdigest()
    p=get(e,pid);assert p['components']==before['components'] and p['takeoffs']==before['takeoffs'] and p['boq']==before['boq']
    exported=e.get(base+'/exports?format=json').json()
    source=next(t for t in exported['tables'] if t[0]=='依据与证据')
    assert '坐标约定' in source[1] and any(SPACE in row for row in source[2])
    assert exported['unpriced']['evidence'][-1]==evidence


def test_invalid_regions_and_pdf_page_bounds_are_atomic(clients):
    e,pid,doc,body,raw=region_pdf(clients);base=f'/api/v1/projects/{pid}'
    before=get(e,pid);audit=e.get(base+'/audit').json()
    bad=[[],[0,0,0,.1],[0,0,.1,-1],[-.1,0,.2,.2],[.9,0,.2,.2],[0,.9,.2,.2],[True,0,.1,.1],['0',0,.1,.1],{'x':.1}]
    for bbox in bad:
        assert e.post(base+'/evidence',json={**body,'bbox':bbox}).status_code==422,bbox
    for page in [0,-1,True,1.5,3]:
        assert e.post(base+'/evidence',json={**body,'page':page}).status_code==422,page
    assert e.post(base+'/evidence',json={**body,'text':{}}).status_code==422
    assert get(e,pid)==before and e.get(base+'/audit').json()==audit
    # Python-level non-finite inputs are rejected before JSON serialization.
    for n in [float('nan'),float('inf')]:
        with pytest.raises(HTTPException) as exc:page_region(doc,{**body,'bbox':[n,0,.1,.1]})
        assert exc.value.status_code==422


def test_region_versions_permissions_and_original_integrity(clients):
    e,pid,doc,body,raw=region_pdf(clients);base=f'/api/v1/projects/{pid}'
    assert clients['reviewer'].post(base+'/evidence',json=body).status_code==403
    assert clients['other'].post(base+'/evidence',json=body).status_code==404
    assert e.post(base+'/evidence',json={**body,'document_hash':'old'}).status_code==409
    assert e.post(base+'/evidence',json={**body,'coordinate_space':'pixels'}).status_code==409
    assert e.post(base+'/evidence',json={k:v for k,v in body.items() if k!='coordinate_space'}).status_code==409
    path=store.ROOT/'originals'/doc['hash'];path.write_bytes(b'changed')
    assert e.post(base+'/evidence',json=body).status_code==409
    path.write_bytes(raw);path.unlink()
    assert e.post(base+'/evidence',json=body).status_code==409


def test_plain_pdf_and_non_pdf_evidence_compatibility(clients):
    e,pid,doc,body,raw=region_pdf(clients);base=f'/api/v1/projects/{pid}'
    simple={k:v for k,v in body.items() if k not in ('bbox','coordinate_space','document_hash')}
    response=e.post(base+'/evidence',json=simple);assert response.status_code==200,response.text
    assert response.json()['bbox'] is None and response.json()['page_geometry']['rotation']==90
    upload(e,pid,name='simple.txt');text_doc=get(e,pid)['documents'][-1]
    response=e.post(base+'/evidence',json={**simple,'document_id':text_doc['id'],'page':1})
    assert response.status_code==200 and response.json()['bbox'] is None
    assert e.post(base+'/evidence',json={**body,'document_id':text_doc['id'],'document_hash':text_doc['hash']}).status_code==422


def test_saved_region_is_immutable_after_document_replacement(clients):
    e,pid,doc,body,raw=region_pdf(clients);base=f'/api/v1/projects/{pid}'
    response=e.post(base+'/evidence',json=body);assert response.status_code==200
    saved=copy.deepcopy(response.json())
    stream=io.BytesIO();pdf=canvas.Canvas(stream);pdf.drawString(20,20,'NEW REVISION TEST');pdf.save()
    response=e.post(base+'/documents',data={'specialty':'建筑'},files={'file':('drawing-test.pdf',stream.getvalue(),'application/pdf')});assert response.status_code==200
    assert e.post(base+'/evidence',json=body).status_code==409
    assert next(v for v in get(e,pid)['evidence'] if v['id']==saved['id'])==saved
    assert e.get(base+'/documents/'+doc['id']+'/file').content==raw
