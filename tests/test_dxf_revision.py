import copy
import pytest
from fastapi.testclient import TestClient
from backend import store
from backend.main import app
from tests.test_dxf_trace_view import saved
from tests.test_dxf_routes import read,preview,adopt,mutate,setup,polyline
from tests.test_workflow import get,approve


def recover(c,url,comp,expected=200,**changes):
    res=c.get(url.replace('dxf-source-view','dxf-revision-candidate'),params={'revision':comp['revision'],'source_stamp':comp['source_stamp'],**changes})
    assert res.status_code==expected,res.text
    return res.json()


def resolve(c,url,parts,expected=200):
    res=c.post(url.replace('dxf-route-candidates','dxf-route-selection'),json={'segments':parts})
    assert res.status_code==expected,res.text
    return res.json().get('candidate',res.json())


def parts(data):
    return [{**s['fragment'],'reverse':s['reverse']} if s.get('fragment') else {'candidate_id':s['id'],'object_hash':s['object_hash'],'reverse':s['reverse']} for s in data['selection']]


def request(data,row,**changes):
    return {**copy.deepcopy(data['defaults']),'request_id':'synthetic-restored-cad-revision','object_hash':row['object_hash'],
            **({'segments':row['selection']} if row.get('selection') else {'fragment':row['fragment']} if row.get('fragment') else {}),**changes}


def candidate_url(pid,did):return f'/api/v1/projects/{pid}/documents/{did}/dxf-route-candidates'


@pytest.mark.parametrize('mode',['single','fragment','assembly','ranged'])
def test_recovery_is_readonly_preserves_order_and_pins_target_without_reusing_confirmation(clients,mode):
    c,r,pid,did,comp,url=saved(clients,mode);before=get(c,pid)
    with store.connect() as conn:n=conn.execute('SELECT COUNT(*) FROM audit WHERE project_id=?',(pid,)).fetchone()[0]
    data=recover(c,url,comp);assert data==recover(r,url,comp) and get(c,pid)==before
    assert data['component_hash']==store.digest(comp) and data['revision']==1 and data['document_id']==did
    defaults=data['defaults'];assert defaults['component_id']==comp['id'] and defaults['component_hash']==store.digest(comp)
    assert defaults['source_confirmed'] is False and defaults['reason']==defaults['calibration_evidence_id']==''
    assert defaults['calibration']['distance']==defaults['calibration']['tolerance']==''
    assert all(defaults[k]==comp[k] for k in ['name','coordinate_basis','measurement_basis','basis_evidence_id','multiplicity','rounding_digits'])
    assert 'preview' not in data and 'request_id' not in defaults
    row=resolve(c,candidate_url(pid,did),parts(data))
    assert {k:v for k,v in row.items() if k!='object_hash'}==comp['dxf_trace']['candidate']
    assert row['object_hash']==store.digest(comp['dxf_trace']['candidate'])
    with store.connect() as conn:assert conn.execute('SELECT COUNT(*) FROM audit WHERE project_id=?',(pid,)).fetchone()[0]==n


def test_single_reversed_whole_object_becomes_explicit_full_range_fragment(clients):
    c,r,pid,did,comp,url=saved(clients,'single');data=recover(c,url,comp);selection=parts(data);selection[0]['reverse']=True
    row=resolve(c,candidate_url(pid,did),selection)
    assert row['kind']=='FRAGMENT' and row['fragment']['from_point']==1 and row['fragment']['to_point']==3
    assert row['source_point_indices']==[3,2,1] and row['points']==list(reversed(comp['dxf_trace']['candidate']['points']))


def test_recovered_sequence_can_drop_to_single_fragment_without_new_component(clients):
    c,r,pid,did,comp,url=saved(clients);data=recover(c,url,comp);rows=parts(data)
    row=resolve(c,candidate_url(pid,did),rows[1:]);assert row['kind']=='FRAGMENT' and row['source_point_indices']==[3,2]
    req=request(data,row,calibration_evidence_id=comp['dxf_trace']['proofs']['calibration']['evidence_id'],calibration={'from_point':1,'to_point':2,'distance':'5','unit':'m','tolerance':'0'},reason='合成：明确缩小为B原P3至P2',source_confirmed=True)
    url=candidate_url(pid,did);new=adopt(c,url,row,req,preview(c,url,row,req))['component']
    assert new['id']==comp['id'] and new['revision']==2 and new['preview']['quantity']=='10.000'
    assert len(get(c,pid)['components'])==1 and len(get(c,pid)['component_history'])==1


def test_adjusted_recovered_sequence_creates_revision_and_requires_new_downstream_review(clients):
    c,r,pid,did,comp,url=saved(clients);approve(r,pid,'components',comp)
    t=c.post(f'/api/v1/projects/{pid}/takeoffs/calculate').json()[0];approve(r,pid,'takeoffs',t)
    assert c.post(f'/api/v1/projects/{pid}/boq/generate').json()[0]['quantity']=='30.000'
    comp=get(c,pid)['components'][0];data=recover(c,url,comp);selections=parts(data);selections[0]['from_point']=1
    url=candidate_url(pid,did);row=resolve(c,url,selections);assert row['single_length_m']=='28'
    req=request(data,row,calibration_evidence_id=comp['dxf_trace']['proofs']['calibration']['evidence_id'],calibration={'from_point':1,'to_point':2,'distance':'13','unit':'m','tolerance':'0'},reason='合成：A取段扩展至原P1，B反向范围保持',source_confirmed=True)
    view=preview(c,url,row,req);assert view['result']['quantity']=='56.000'
    result=adopt(c,url,row,req,view);new=result['component'];p=get(c,pid)
    assert new['id']==comp['id'] and new['revision']==2 and new['state']=='draft'
    assert len(p['components'])==1 and p['component_history'][0]==comp and len(p['dxf_route_adoptions'])==2
    assert p['takeoffs'][0]['state']=='stale' and p['boq'][0]['state']=='draft' and p['boq'][0]['quantity']=='30.000'
    assert adopt(c,url,row,req,view)['duplicate']
    assert c.post(f'/api/v1/projects/{pid}/takeoffs/calculate').status_code==409
    approve(r,pid,'components',new);t=c.post(f'/api/v1/projects/{pid}/takeoffs/calculate').json()[0]
    assert t['quantity']=='56.000';approve(r,pid,'takeoffs',t)
    assert c.post(f'/api/v1/projects/{pid}/boq/generate').json()[0]['quantity']=='56.000'


def test_old_calibration_and_confirmation_are_not_reused_implicitly(clients):
    c,r,pid,did,comp,url=saved(clients);data=recover(c,url,comp);url=candidate_url(pid,did);row=resolve(c,url,parts(data))
    req=request(data,row);before=get(c,pid);preview(c,url,row,req,422)
    req.update(calibration_evidence_id=comp['dxf_trace']['proofs']['calibration']['evidence_id'],calibration={'from_point':1,'to_point':2,'distance':'5','unit':'m','tolerance':'0'},reason='合成修订重新核对')
    view=preview(c,url,row,req);adopt(c,url,row,req,view,422);assert get(c,pid)==before


def test_concurrent_review_cannot_silently_refresh_pinned_revision_hash(clients):
    c,r,pid,did,comp,url=saved(clients);data=recover(c,url,comp);url=candidate_url(pid,did);row=resolve(c,url,parts(data))
    req=request(data,row,calibration_evidence_id=comp['dxf_trace']['proofs']['calibration']['evidence_id'],calibration={'from_point':1,'to_point':2,'distance':'5','unit':'m','tolerance':'0'},reason='合成并发校验',source_confirmed=True)
    oldview=preview(c,url,row,req);approve(r,pid,'components',comp);before=get(c,pid)
    adopt(c,url,row,req,oldview,409);assert get(c,pid)==before


@pytest.mark.parametrize('fault',['bytes','job','evidence','mapping','preview'])
def test_recovery_requires_actual_current_valid_source(clients,fault):
    c,r,pid,did,comp,url=saved(clients)
    if fault=='bytes':(store.ROOT/'originals'/comp['dxf_trace']['candidate']['document_hash']).write_bytes(b'changed')
    else:
        def change(p):
            if fault=='job':next(j for j in p['jobs'] if j['document_id']==did)['state']='failed'
            elif fault=='evidence':next(e for e in p['evidence'] if e['id']==comp['dxf_trace']['proofs']['calibration']['evidence_id'])['text']+='changed'
            elif fault=='preview':p['components'][0]['preview']['quantity']='999'
            else:
                for trace in [p['components'][0]['dxf_trace'],p['dxf_route_adoptions'][0]['trace']]:trace['candidate']['source_objects'][0]['fragment']['from_point']=1
        mutate(pid,change)
    before=get(c,pid);recover(c,url,comp,409);assert get(c,pid)==before


@pytest.mark.parametrize('changes,expected',[({'revision':2},409),({'source_stamp':'0'*64},409),({'revision':0},422),({'source_stamp':'bad'},422)])
def test_recovery_rejects_old_or_invalid_source_version(clients,changes,expected):
    c,r,pid,did,comp,url=saved(clients);recover(c,url,comp,expected,**changes)


@pytest.mark.parametrize('missing',['revision','source_stamp'])
def test_recovery_needs_explicit_version(clients,missing):
    c,r,pid,did,comp,url=saved(clients);params={'revision':comp['revision'],'source_stamp':comp['source_stamp']};params.pop(missing)
    assert c.get(url.replace('dxf-source-view','dxf-revision-candidate'),params=params).status_code==422


@pytest.mark.parametrize('fault',['empty','many','missing_reverse','partial_range','null_range','extra','stale_hash','wrong_direction','overlap'])
def test_selection_resolver_is_strict_readonly_and_never_snaps(clients,fault):
    c,r,pid,did,comp,url=saved(clients);data=recover(c,url,comp);selected=parts(data);expected=422
    if fault=='empty':selected=[]
    elif fault=='many':selected=selected*11
    elif fault=='missing_reverse':selected[0].pop('reverse')
    elif fault=='partial_range':selected[0].pop('to_point')
    elif fault=='null_range':selected[0]['from_point']=None
    elif fault=='extra':selected[0]['snap']='1'
    elif fault=='stale_hash':selected[0]['object_hash']='0'*64;expected=409
    elif fault=='wrong_direction':selected[1]['reverse']=False
    else:selected[1]=copy.deepcopy(selected[0])
    before=get(c,pid);resolve(c,candidate_url(pid,did),selected,expected);assert get(c,pid)==before


def test_recovery_and_selection_enforce_tenant_and_anonymous_boundaries(clients):
    c,r,pid,did,comp,url=saved(clients);data=recover(c,url,comp)
    recover(clients['other'],url,comp,404);resolve(clients['other'],candidate_url(pid,did),parts(data),404)
    with TestClient(app) as anonymous:
        recover(anonymous,url,comp,401);resolve(anonymous,candidate_url(pid,did),parts(data),401)
    mutate(pid,lambda p:p['components'][0].pop('dxf_trace'));recover(c,url,comp,422)


def test_single_unsupported_original_cannot_be_resolved_as_a_route(clients):
    c,r,pid,did,eid,url=setup(clients,polyline(flags=9));row=read(c,url)['candidates'][0]
    resolve(c,url,[{'candidate_id':row['id'],'object_hash':row['object_hash'],'reverse':False}],422)
