import copy
import pytest
from fastapi.testclient import TestClient
from backend import store
from backend.main import app
from tests.test_dxf_routes import setup,read,body,preview,adopt,mutate,polyline
from tests.test_dxf_fragments import prepared,part,select,request
from tests.test_dxf_assembly import assembled,resolve,segment
from tests.test_dxf_fragment_assembly import ranged
from tests.test_workflow import get,approve


def saved(clients,mode='ranged'):
    if mode=='ranged':c,r,pid,did,eid,url,objects,parts,row,req=ranged(clients)
    elif mode=='assembly':c,r,pid,did,eid,url,objects,row,req=assembled(clients)
    elif mode=='fragment':
        c,r,pid,did,eid,url,source=prepared(clients);row=select(c,url,part(source));req=request(row,eid)
    else:
        c,r,pid,did,eid,url=setup(clients);row=read(c,url)['candidates'][0];req=body(row,eid)
    comp=adopt(c,url,row,req,preview(c,url,row,req))['component']
    return c,r,pid,did,comp,f'/api/v1/projects/{pid}/components/{comp["id"]}/dxf-source-view'


def query(comp,projection='xz'):
    return {'revision':comp['revision'],'source_stamp':comp['source_stamp'],'projection':projection}


def view(c,url,comp,projection='xz',expected=200,**changes):
    res=c.get(url,params={**query(comp,projection),**changes});assert res.status_code==expected,res.text
    return res.json()


@pytest.mark.parametrize('mode',['single','fragment','assembly','ranged'])
@pytest.mark.parametrize('projection',['xy','xz','yz'])
def test_readonly_source_context_exactly_maps_adopted_points_for_all_modes(clients,mode,projection):
    c,r,pid,did,comp,url=saved(clients,mode);before=get(c,pid)
    with store.connect() as conn:count=conn.execute('SELECT COUNT(*) FROM audit WHERE project_id=?',(pid,)).fetchone()[0]
    data=view(c,url,comp,projection)
    assert data==view(r,url,comp,projection) and get(c,pid)==before
    assert data['display_only'] and data['component_hash']==store.digest(comp) and data['document_id']==did
    assert data['revision']==1 and data['state']=='draft' and data['source_stamp']==comp['source_stamp']
    assert data['scene']['projection']==projection and data['scene']['eligible_count']==len(data['sources'])
    for part in data['parts']:
        raw=next(s for s in data['sources'] if s['id']==part['source_candidate_id'])
        shape=next(s for s in data['scene']['objects'] if s['candidate_id']==raw['id'])
        assert part['object_hash']==store.digest(raw)==shape['object_hash']
        assert part['points']==[shape['points'][i-1] for i in part['source_point_indices']]
        assert len(part['points'])==len(part['route_point_indices'])
        for source_index,route_index in zip(part['source_point_indices'],part['route_point_indices']):
            # At a numeric join, each source preserves its own raw spelling.
            route=comp['route_points'][route_index-1]['parameters']
            from decimal import Decimal
            assert all(Decimal(raw['points'][source_index-1][k])==Decimal(route[k]) for k in ['x','y','z'])
    with store.connect() as conn:assert conn.execute('SELECT COUNT(*) FROM audit WHERE project_id=?',(pid,)).fetchone()[0]==count


def test_fragment_context_retains_unselected_vertices_and_exact_reverse(clients):
    c,r,pid,did,comp,url=saved(clients,'fragment');data=view(c,url,comp)
    assert len(data['sources'][0]['points'])==4 and len(data['parts'][0]['points'])==3
    part=data['parts'][0];assert (part['from_point'],part['to_point'],part['reverse'])==(2,4,True)
    assert part['source_point_indices']==[4,3,2] and part['route_point_indices']==[1,2,3]
    assert part['single_length_m']=='10'


def test_legacy_reversed_whole_object_and_ranged_join_keep_distinct_original_indices(clients):
    c,r,pid,did,comp,url=saved(clients,'assembly');data=view(c,url,comp)
    assert data['parts'][1]['source_point_indices']==[2,1] and data['parts'][1]['route_point_indices']==[2,3]
    c,r,pid,did,comp,url=saved(clients);data=view(c,url,comp)
    assert data['parts'][0]['source_point_indices']==[2,3,4] and data['parts'][0]['route_point_indices']==[1,2,3]
    assert data['parts'][1]['source_point_indices']==[3,2] and data['parts'][1]['route_point_indices']==[3,4]
    assert len(data['sources'][0]['points'])==len(data['sources'][1]['points'])==4


def test_same_raw_object_is_drawn_once_for_adjacent_adopted_ranges(clients):
    c,r,pid,did,eid,url,source=prepared(clients)
    parts=[part(source,1,2,False),part(source,2,4,False)];row=resolve(c,url,parts);req=body(row,eid,segments=parts)
    comp=adopt(c,url,row,req,preview(c,url,row,req))['component']
    url=f'/api/v1/projects/{pid}/components/{comp["id"]}/dxf-source-view';data=view(c,url,comp)
    assert len(data['sources'])==len(data['scene']['objects'])==1 and len(data['parts'])==2
    assert data['parts'][0]['source_point_indices']==[1,2] and data['parts'][1]['source_point_indices']==[2,3,4]


def test_projection_collapsed_segment_is_reported_but_keeps_true_3d_length(clients):
    c,r,pid,did,comp,url=saved(clients);data=view(c,url,comp,'xy')
    assert data['parts'][0]['collapsed_segments']==[1] and data['parts'][0]['single_length_m']=='10'
    assert data['parts'][0]['points'][0]==data['parts'][0]['points'][1]
    assert not view(c,url,comp,'xz')['parts'][0]['collapsed_segments']


@pytest.mark.parametrize('changes',[
    {'projection':'XY'},{'projection':'xyz'},{'projection':''},{'revision':0},{'revision':-1},{'revision':'bad'},
    {'source_stamp':'bad'},{'source_stamp':''}])
def test_invalid_view_parameters_are_rejected_without_mutation(clients,changes):
    c,r,pid,did,comp,url=saved(clients);before=get(c,pid)
    res=c.get(url,params={**query(comp),**changes});assert res.status_code==422,res.text;assert get(c,pid)==before


@pytest.mark.parametrize('missing',['revision','source_stamp'])
def test_source_version_must_be_explicit(clients,missing):
    c,r,pid,did,comp,url=saved(clients);params=query(comp);params.pop(missing)
    assert c.get(url,params=params).status_code==422


@pytest.mark.parametrize('changes',[{'revision':2},{'source_stamp':'0'*64}])
def test_old_component_version_or_source_stamp_is_blocked(clients,changes):
    c,r,pid,did,comp,url=saved(clients);view(c,url,comp,expected=409,**changes)


@pytest.mark.parametrize('fault',['bytes','job','superseded','evidence','mapping','preview','source_stamp'])
def test_actual_source_and_saved_geometry_are_revalidated_on_every_projection(clients,fault):
    c,r,pid,did,comp,url=saved(clients);view(c,url,comp)
    if fault=='bytes':(store.ROOT/'originals'/comp['dxf_trace']['candidate']['document_hash']).write_bytes(b'changed')
    else:
        def change(p):
            if fault=='job':next(j for j in p['jobs'] if j['document_id']==did)['state']='failed'
            elif fault=='superseded':next(d for d in p['documents'] if d['id']==did)['superseded']=True
            elif fault=='evidence':next(e for e in p['evidence'] if e['id']==comp['dxf_trace']['proofs']['calibration']['evidence_id'])['text']+='changed'
            elif fault=='mapping':
                for t in [p['components'][0]['dxf_trace'],p['dxf_route_adoptions'][0]['trace']]:t['candidate']['source_objects'][1]['source_point_indices'].reverse()
            elif fault=='preview':p['components'][0]['preview']['quantity']='999'
            else:p['components'][0]['source_stamp']='0'*64
        mutate(pid,change)
    before=get(c,pid);view(c,url,comp,'xy',409);assert get(c,pid)==before


def test_reading_current_state_does_not_approve_or_change_prior_geometry(clients):
    c,r,pid,did,comp,url=saved(clients);old=view(c,url,comp)
    approve(r,pid,'components',comp);new=view(c,url,comp)
    assert new['state']=='approved' and new['component_hash']!=old['component_hash']
    assert new['scene']==old['scene'] and new['parts']==old['parts'] and new['sources']==old['sources']
    assert not get(c,pid)['takeoffs']


def test_other_tenant_anonymous_missing_and_non_cad_components_are_blocked(clients):
    c,r,pid,did,comp,url=saved(clients);view(clients['other'],url,comp,expected=404)
    with TestClient(app) as anonymous:view(anonymous,url,comp,expected=401)
    view(c,url.replace(comp['id'],'0'*32),comp,expected=404)
    mutate(pid,lambda p:p['components'][0].pop('dxf_trace'))
    view(c,url,comp,expected=422)


def test_display_response_cannot_be_used_as_cad_source_input(clients):
    c,r,pid,did,eid,url,objects,parts,row,req=ranged(clients)
    comp=adopt(c,url,row,req,preview(c,url,row,req))['component'];data=view(c,f'/api/v1/projects/{pid}/components/{comp["id"]}/dxf-source-view',comp)
    bad=copy.deepcopy(req);bad['scene']=data['scene'];preview(c,url,row,bad,422)
    bad=copy.deepcopy(req);bad['parts']=data['parts'];preview(c,url,row,bad,422)
