from decimal import Decimal
import copy
import pytest
from backend import store
from tests.test_dxf_routes import setup,read,body,preview,adopt,mutate,polyline
from tests.test_dxf_assembly import segment,sample
from tests.test_workflow import get,approve


def test_source_scene_is_readonly_and_links_exact_candidate_identity(clients):
    c,r,pid,did,eid,url=setup(clients,sample());before=get(c,pid)
    data=read(c,url);assert data==read(r,url) and get(c,pid)==before
    scene=data['scene'];assert scene['display_only'] and scene['eligible_count']==2 and scene['excluded_count']==0
    assert scene['projection']=='xz' and scene['width']==800 and scene['height']==400
    assert scene['bounds_m']=={'x':{'min':'0','max':'3'},'z':{'min':'0','max':'17'}}
    for original,shape in zip(data['candidates'],scene['objects']):
        assert shape['candidate_id']==original['id'] and shape['object_hash']==original['object_hash']
        assert shape['object_hash']==store.digest({k:v for k,v in original.items() if k!='object_hash'})
        assert len(original['points'])==len(shape['points'])
        for point in shape['points']:
            assert Decimal(0)<=Decimal(point['x'])<=800 and Decimal(0)<=Decimal(point['y'])<=400
    # Changing display axes must leave the engine's candidates and geometry hashes alone.
    planar=read(c,url,'?projection=xy');assert planar['candidates']==data['candidates']
    assert planar['scene']['objects'][1]['collapsed_segments']==[1]
    assert get(c,pid)==before


@pytest.mark.parametrize('projection,axes',[('xy',('x','y')),('xz',('x','z')),('yz',('y','z'))])
def test_axes_are_declared_and_the_display_uses_uniform_metric_scale(clients,projection,axes):
    c,r,pid,did,eid,url=setup(clients,segment('A',(0,0,0),(3000,4000,12000)))
    data=read(c,url,'?projection='+projection);scene=data['scene'];assert tuple(scene['bounds_m'])==axes
    a,b=scene['objects'][0]['points'];delta=[abs(Decimal(b[k])-Decimal(a[k])) for k in ('x','y')]
    dimensions={'x':Decimal(3),'y':Decimal(4),'z':Decimal(12)}
    assert abs(delta[0]/dimensions[axes[0]]-delta[1]/dimensions[axes[1]])<Decimal('.000001')
    assert Decimal(b['y'])<Decimal(a['y']) # positive CAD height is displayed upwards


@pytest.mark.parametrize('projection',['xy','xz','yz'])
def test_large_wcs_origin_with_micro_distance_does_not_collapse_to_browser_float(clients,projection):
    entities=segment('A',('999999999999.000001',)*3,('999999999999.000002',)*3)
    c,r,pid,did,eid,url=setup(clients,entities,unit=6)
    scene=read(c,url,'?projection='+projection)['scene'];shape=scene['objects'][0]
    assert not shape['collapsed_segments'] and shape['points'][0]!=shape['points'][1]
    assert abs(Decimal(shape['points'][0]['y'])-Decimal(shape['points'][1]['y']))==320
    assert all(v['min']=='999999999999.000001' and v['max']=='999999999999.000002' for v in scene['bounds_m'].values())


@pytest.mark.parametrize('unit,expected',[(4,'0.003'),(5,'0.03'),(6,'3')])
def test_view_bounds_use_declared_units_in_metres(clients,unit,expected):
    c,r,pid,did,eid,url=setup(clients,segment('A',(-3,-4,-12),(3,4,12)),unit=unit)
    scene=read(c,url)['scene'];assert scene['bounds_m']['x']['max']==expected
    assert scene['bounds_m']['x']['min']=='-'+expected


def test_flat_projection_is_visible_as_a_point_and_never_changes_true_length(clients):
    c,r,pid,did,eid,url=setup(clients,segment('A',(3000,4000,12000),(3000,4000,17000)))
    data=read(c,url,'?projection=xy');shape=data['scene']['objects'][0]
    assert shape['points']==[{'x':'400.000000','y':'200.000000'}]*2 and shape['collapsed_segments']==[1]
    row=data['candidates'][0];request=body(row,eid,calibration={'from_point':1,'to_point':2,'distance':'5','unit':'m','tolerance':'0'})
    view=preview(c,url,row,request);assert view['result']['quantity']=='10.000' and view['calibration']['passed']
    comp=adopt(c,url,row,request,view)['component'];approve(r,pid,'components',comp)
    assert c.post(f'/api/v1/projects/{pid}/takeoffs/calculate').json()[0]['quantity']=='10.000'


def test_exact_projection_overlap_keeps_both_independent_objects(clients):
    entities=segment('A',(0,0,0),(3000,4000,0))+segment('B',(0,0,1000),(3000,4000,1000))
    c,r,pid,did,eid,url=setup(clients,entities)
    data=read(c,url,'?projection=xy');a,b=data['scene']['objects']
    assert a['points']==b['points'] and a['candidate_id']!=b['candidate_id'] and len(data['candidates'])==2
    assert read(c,url,'?projection=xz')['scene']['objects'][0]['points']!=read(c,url,'?projection=xz')['scene']['objects'][1]['points']


@pytest.mark.parametrize('query,count,excluded,total',[
    ('?limit=1',1,0,3),('?offset=1&limit=1',1,0,3),('?offset=2&limit=1',0,1,3),
    ('?offset=3&limit=1',0,0,3),('?layer=VERTICAL',1,0,1),('?layer=UNKNOWN',0,0,0)])
def test_scene_only_represents_explicit_layer_and_current_batch(clients,query,count,excluded,total):
    c,r,pid,did,eid,url=setup(clients,sample()+segment('C',(0,0,0),(1,1,1))+[(67,'1')])
    data=read(c,url,query);scene=data['scene']
    assert data['total']==total and scene['eligible_count']==count and scene['excluded_count']==excluded
    assert len(scene['objects'])==count and len(data['candidates'])==count+excluded
    if count==0:assert scene['bounds_m'] is None
    for shape in scene['objects']:assert any(r['id']==shape['candidate_id'] for r in data['candidates'])


@pytest.mark.parametrize('query',['?projection=XYZ','?projection=XY','?projection=','?projection=__proto__','?limit=201'])
def test_invalid_display_options_are_rejected_without_mutation(clients,query):
    c,r,pid,did,eid,url=setup(clients);before=get(c,pid);read(c,url,query,422);assert get(c,pid)==before


@pytest.mark.parametrize('change',['bytes','job','superseded'])
def test_stale_originals_are_not_served_as_current_display_geometry(clients,change):
    c,r,pid,did,eid,url=setup(clients);data=read(c,url)
    if change=='bytes':(store.ROOT/'originals'/data['candidates'][0]['document_hash']).write_bytes(b'corrupt')
    else:
        def alter(p):
            if change=='job':next(j for j in p['jobs'] if j['document_id']==did)['state']='failed'
            else:next(d for d in p['documents'] if d['id']==did)['superseded']=True
        mutate(pid,alter)
    read(c,url,'?projection=xy',409)


def test_tenant_and_unauthenticated_access_are_blocked(clients):
    from fastapi.testclient import TestClient
    from backend.main import app
    c,r,pid,did,eid,url=setup(clients);read(clients['other'],url,'?projection=xy',404)
    with TestClient(app) as anonymous:assert anonymous.get(url).status_code==401


def test_display_coordinates_cannot_be_submitted_as_source_geometry(clients):
    c,r,pid,did,eid,url=setup(clients);data=read(c,url);row=data['candidates'][0];request=body(row,eid)
    request['scene']=copy.deepcopy(data['scene']);before=get(c,pid)
    preview(c,url,row,request,422);assert get(c,pid)==before
