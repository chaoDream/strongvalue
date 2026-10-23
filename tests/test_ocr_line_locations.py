import copy
import pytest
from backend import store, main as core, ocr
from tests.test_ocr_drawing_candidates import fixture,mutate
from tests.test_workflow import get
from tests.test_evidence_regions import region_pdf


def selected(c,pid):
    e=next(e for e in get(c,pid)['evidence'] if e['method']=='local_ocr')
    return e,{'expected_line':copy.deepcopy(e['ocr']['lines'][0]),'evidence_hash':store.digest(e)}


def locate(c,pid,e,body,number=1):
    return c.post(f'/api/v1/projects/{pid}/evidence/{e["id"]}/ocr-lines/{number}/locate',json=body)


def test_authorized_line_location_is_read_only_and_source_bound(clients,monkeypatch):
    c,r,pid,d,candidate=fixture(clients,monkeypatch);e,body=selected(c,pid)
    before=get(c,pid);audit=c.get(f'/api/v1/projects/{pid}/audit').json()
    for client in (c,r):
        response=locate(client,pid,e,body);assert response.status_code==200,response.text
        row=response.json();assert row['bbox']==e['ocr']['lines'][0]['bbox']
        assert row['text']==e['ocr']['lines'][0]['text'] and row['document_hash']==d['hash']
        assert row['evidence_hash']==candidate['evidence_hash'] and row['coordinate_space']=='pdf_display_normalized_v1'
        assert row['page_geometry']==e['page_geometry'] and row['line']==1
    assert get(c,pid)==before and c.get(f'/api/v1/projects/{pid}/audit').json()==audit


@pytest.mark.parametrize('number',[0,-1,2001,'wrong','1.5',5])
def test_out_of_range_line_rejected(clients,monkeypatch,number):
    c,r,pid,d,_=fixture(clients,monkeypatch);e,body=selected(c,pid)
    assert locate(c,pid,e,body,number).status_code==422


@pytest.mark.parametrize('field,value',[('text','different'),('bbox',[.1,.2,.3,.4]),('confidence',.99)])
def test_stale_selected_line_cannot_silently_target_another_region(clients,monkeypatch,field,value):
    c,r,pid,d,_=fixture(clients,monkeypatch);e,body=selected(c,pid)
    body['expected_line'][field]=value
    assert locate(c,pid,e,body).status_code==409


@pytest.mark.parametrize('change',['hash','task','geometry','bytes','missing','superseded','bbox'])
def test_broken_source_and_geometry_rejected(clients,monkeypatch,change):
    c,r,pid,d,_=fixture(clients,monkeypatch);e,body=selected(c,pid)
    if change=='hash':body['evidence_hash']='0'*64
    elif change=='bytes':(store.ROOT/'originals'/d['hash']).write_bytes(b'changed')
    elif change=='missing':(store.ROOT/'originals'/d['hash']).unlink()
    else:
        def action(p,ev,j):
            if change=='task':j['state']='failed'
            elif change=='superseded':core.find(p,'documents',d['id'])['superseded']=True
            elif change=='geometry':ev['page_geometry']['rotation']=90
            elif change=='bbox':ev['ocr']['lines'][0]['bbox']=[0,0,2,.1]
        mutate(pid,action)
        body.pop('evidence_hash')
    assert locate(c,pid,e,body).status_code==409


def test_role_tenant_and_non_ocr_sources(clients,monkeypatch):
    c,r,pid,d,_=fixture(clients,monkeypatch);e,body=selected(c,pid)
    assert locate(clients['other'],pid,e,body).status_code==404
    local=next(e for e in get(c,pid)['evidence'] if e['method']=='local_text')
    assert locate(c,pid,local,body).status_code==422
    assert locate(c,pid,e,{**body,'bbox':[0,0,.1,.1]}).status_code==422
    assert locate(c,pid,e,{'evidence_hash':body['evidence_hash']}).status_code==422


def test_rotated_cropped_page_uses_display_coordinates_without_conversion(clients,monkeypatch):
    c,pid,d,_,raw=region_pdf(clients)
    monkeypatch.setattr(ocr,'capability',lambda:{'available':True})
    line={'text':'ROTATED TEST','bbox':[.1,.2,.3,.04],'confidence':.5}
    monkeypatch.setattr(ocr,'recognize',lambda *a:{'lines':[line],'revision':3,'width':1600,'height':2400})
    response=c.post(f'/api/v1/projects/{pid}/documents/{d["id"]}/ocr',json={'page':2,'document_hash':d['hash']})
    assert response.status_code==200,response.text
    e,body=selected(c,pid);result=locate(c,pid,e,body).json()
    assert result['page']==2 and result['bbox']==line['bbox']
    assert result['page_geometry']=={'rotation':90,'crop_box':[20.,30.,380.,280.]}
