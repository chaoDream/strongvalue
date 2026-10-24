"""Synthetic exposed ledges, exact clipping and upgraded curved-lens coverage."""
import copy, io, random
from decimal import Decimal, localcontext, ROUND_HALF_UP
import pytest
from openpyxl import load_workbook
from pypdf import PdfReader
from backend import store
from backend.engine import DomainError
from backend.compound_takeoff import PI
from backend.foundation_horizontal_formwork import calculate
from backend.foundation_horizontal_formwork_reports import STEP_TITLES, tables
from backend.post_foundation import calculate as foundation_quantity
from backend.railing_material import snapshot
from tests.test_foundation_horizontal_formwork import contact as end_contact, body as end_body, setup as end_setup, check as end_check
from tests.test_foundation_formwork import foundation
from tests.test_foundation_voids import rectangle
from tests.test_foundation_openings import hole
from tests.test_foundation_recesses import interval
from tests.test_post_foundation import equal_body, fixed_body, fixed_added, equal_added, fixed_adjusted, equal_adjusted, combined
from tests.test_route_takeoff import evaluate
from tests.test_polygon_takeoff import post, mutate
from tests.test_workflow import get, approve, priced_project, upload


def contact(eid='test', level=1, name='合成平台'):
    return {**end_contact(eid, 'step', name), 'level_index':level,
            'measurement_basis':'合成整个平台实际接触，扣下层截面内孔减上层覆盖交集，孔底另核，非真实工程。'}


def body(s,eid='test'):
    return {**end_body(s,eid),'contact_ranges':[contact(eid)],'measurement_basis':'合成台阶整个平台实际支模，扣除上层完整名义截面及外露平台内真实孔，不重复顶底或孔底；仅软件验证。'}


def source(shape='stepped_rectangular', make=equal_body, holes=None):
    s=foundation(make,shape)
    s['foundation_items'][0]['through_holes']['holes']=holes if holes is not None else [{**rectangle(x='200'),'vertical_range':interval('0','200')}]
    return s


def check(r):
    end_check(r)
    with localcontext() as ctx:
        ctx.prec=80
        for d in r['contact_ranges']:
            for h in d['apertures']:
                for k,a in h.get('formula_asts',{}).items(): assert evaluate(a,r['dimensions'])==Decimal(h[k])


@pytest.mark.parametrize('shape',['stepped_rectangular','stepped_circular'])
@pytest.mark.parametrize('make',[equal_body,fixed_body,fixed_added,equal_added,fixed_adjusted,equal_adjusted,combined,lambda s:combined(s,equal=True)])
def test_shapes_counts_and_exact_covered_hole(shape,make):
    # Rectangular lower 600x500; upper400x300; H 100x60 at200 clips half.
    hs=[{**rectangle(x='200' if shape=='stepped_rectangular' else '250',length='40',width='60'),'vertical_range':interval('0','200')}]
    s=source(shape,make,hs);b=body(s);before=copy.deepcopy((s,b));r=calculate(b,s);check(r);assert (s,b)==before
    with localcontext() as ctx:
        ctx.prec=80;d=r['contact_ranges'][0];expected=Decimal('.18')-Decimal('.0012') if shape=='stepped_rectangular' else Decimal('.05')*Decimal(PI)-Decimal('.0024')
        assert Decimal(d['single_net_area'])==expected
        assert Decimal(r['horizontal_formwork_summary']['unrounded_quantity'])==expected*Decimal(d['total_count'])
    assert r['rule_version']=='foundation-horizontal-formwork-1.1' and d['z']=='0.2' and d['upper_level_index']==2


@pytest.mark.parametrize('x,y,l,w,cut',[('0','0','100','60','0'),('200','0','100','60','.003'),('-200','0','100','60','.003'),('0','150','100','60','.003'),('0','-150','100','60','.003'),('200','150','100','60','.0045'),('-200','-150','100','60','.0045'),('250','0','100','60','.006'),('0','200','100','60','.006'),('0','0','600','500','.18'),('0','0','400','300','0')])
def test_rectangular_min_max_clipping_sides_corners_containment_and_full_cover(x,y,l,w,cut):
    s=source(holes=[{**rectangle(x=x,y=y,length=l,width=w),'vertical_range':interval('100' if l=='600' and w=='500' else '0','200')}]);r=calculate(body(s),s);assert Decimal(r['contact_ranges'][0]['single_opening_area'])==Decimal(cut);check(r)


@pytest.mark.parametrize('lo,hi,cut',[('0','199.999999999','0'),('0','200','.003'),('0','200.000000001','invalid'),('200','400','invalid'),('200','200.000000001','invalid'),('100','200','.003')])
def test_hole_at_platform_boundary_outside_upper_not_snapped(lo,hi,cut):
    s=source(holes=[{**rectangle(x='200'),'vertical_range':interval(lo,hi)}]);b=body(s)
    if cut=='invalid':
        with pytest.raises(DomainError):calculate(b,s)
    else:
        r=calculate(b,s);assert Decimal(r['contact_ranges'][0]['single_opening_area'])==Decimal(cut);check(r)


@pytest.mark.parametrize('lo,hi,active',[('0','200',True),('0','600',True),('200','600',False),('200.000000001','600',False),('0','199.999999999',False)])
def test_covered_holes_active_from_below_and_do_not_count_hole_bottom(lo,hi,active):
    s=source(holes=[{**rectangle(x='0'),'vertical_range':interval(lo,hi)}]);r=calculate(body(s),s);d=r['contact_ranges'][0];assert bool(d['apertures'])==active and Decimal(d['single_opening_area'])==0 and r['quantity']=='3.960';check(r)


@pytest.mark.parametrize('shape,kind,x,y,a,b,branch,cut',[
    ('stepped_circular','circle','0','0','40',None,'覆盖内','0'),
    ('stepped_circular','circle','180','0','40',None,'覆盖内','0'),
    ('stepped_circular','circle','220','0','40',None,'外切','pi'),
    ('stepped_circular','circle','250','0','40',None,'分离','pi'),
    ('stepped_circular','circle','0','0','500',None,'包含上层','ring'),
    ('stepped_circular','rect','0','0','240','320','覆盖内','0'),
    ('stepped_circular','rect','240','0','80','60','外切','.0048'),
    ('stepped_circular','rect','0','0','400','400','包含上层','rect-minus-circle'),
    ('stepped_rectangular','circle','180','0','40',None,'覆盖内','0'),
    ('stepped_rectangular','circle','220','0','40',None,'外切','pi'),
    ('stepped_rectangular','circle','0','0','500',None,'包含上层','circle-minus-rect'),
])
def test_exact_circle_rect_containment_disjoint_and_tangent(shape,kind,x,y,a,b,branch,cut):
    h=hole(x=x,y=y,diameter=a) if kind=='circle' else rectangle(x=x,y=y,length=a,width=b)
    s=source(shape,holes=[{**h,'vertical_range':interval('0','200')}]);r=calculate(body(s),s);d=r['contact_ranges'][0];check(r);assert branch in d['apertures'][0]['intersection_rule']
    with localcontext() as ctx:
        ctx.prec=80;p=Decimal(PI);expected={'pi':p*Decimal('.0004'),'ring':p*Decimal('.0225'),'rect-minus-circle':Decimal('.16')-p*Decimal('.04'),'circle-minus-rect':p*Decimal('.0625')-Decimal('.12')}.get(cut,Decimal(cut) if cut.replace('.','').isdigit() else None);assert Decimal(d['single_opening_area'])==expected


@pytest.mark.parametrize('shape,kind,x,y,d,l,w',[
    ('stepped_circular','circle','190','0','40',None,None),
    ('stepped_circular','circle','220.000000001','0','80',None,None),
    ('stepped_circular','rect','200','0',None,'100','60'),
    ('stepped_rectangular','circle','190','0','40',None,None),
    ('stepped_rectangular','circle','190','140','40',None,None),
])
def test_partial_curved_boundary_supported_and_unselected_end_faces_unchanged(shape,kind,x,y,d,l,w):
    h=hole(x=x,y=y,diameter=d) if kind=='circle' else rectangle(x=x,y=y,length=l,width=w);s=source(shape,holes=[{**h,'vertical_range':interval('0','200')}]);b=body(s)
    r=calculate(b,s);check(r);assert r['rule_version']=='foundation-horizontal-formwork-1.2' and r['contact_ranges'][0]['apertures'][0]['curved_intersection']
    assert calculate(end_body(s),s)['rule_version']=='foundation-horizontal-formwork-1.0'


@pytest.mark.parametrize('shape',['stepped_rectangular','stepped_circular'])
@pytest.mark.parametrize('unit,factor',[('mm','1'),('cm','.1'),('dm','.01'),('m','.001'),('km','.000001')])
def test_units_hole_units_independent_and_low_context(shape,unit,factor):
    hs=[{**rectangle(x='250',length='40',width='60'),'vertical_range':interval('0','200')}];s=source(shape,holes=hs);expected=calculate(body(s),s);item=s['foundation_items'][0];item['length_unit']=unit
    for tier in item['levels']:
        for k in tier['parameters']:tier['parameters'][k]=str(Decimal(tier['parameters'][k])*Decimal(factor))
    with localcontext() as ctx:ctx.prec=6;r=calculate(body(s),s)
    assert r['quantity']==expected['quantity'] and Decimal(r['deduction'])==Decimal(expected['deduction']);check(r)


def test_seeded_200_rectangles_independent_intersection_and_final_rounding_oracle():
    rng=random.Random(10001)
    for _ in range(200):
        l=rng.randint(1,300);w=rng.randint(1,250);x=rng.randint(-300+l//2+1,299-l//2);y=rng.randint(-250+w//2+1,249-w//2)
        s=source(holes=[{**rectangle(x=str(x),y=str(y),length=str(l),width=str(w)),'vertical_range':interval('0','200')}]);b=body(s);b['rounding_digits']=rng.randint(0,12);r=calculate(b,s)
        with localcontext() as ctx:
            ctx.prec=80;D=Decimal;overlap=max(D(0),min(D(x)+D(l)/2,D(200))-max(D(x)-D(l)/2,D(-200)))*max(D(0),min(D(y)+D(w)/2,D(150))-max(D(y)-D(w)/2,D(-150)));cut=(D(l*w)-overlap)/1000000;net=(D('.18')-cut)*22;assert D(r['quantity'])==net.quantize(D(1).scaleb(-b['rounding_digits']),rounding=ROUND_HALF_UP)
        check(r)


def test_three_levels_independent_claims_equal_footprint_zero_and_mixed_end_results():
    s=source();tiers=s['foundation_items'][0]['levels'];third=copy.deepcopy(tiers[1]);third['parameters'].update(length='200',width='100',height='100');tiers.append(third);b=body(s);b['contact_ranges'].append(contact(level=2,name='合成平台2'));r=calculate(b,s);assert [d['z'] for d in r['contact_ranges']]==['0.2','0.6'] and r['quantity']=='6.094';check(r)
    b['contact_ranges']+=end_body(s)['contact_ranges'];r2=calculate(b,s);assert r2['contact_ranges'][2:]==[{**d,'index':d['index']+2} for d in calculate(end_body(s),s)['contact_ranges']];check(r2)
    s['foundation_items'][0]['levels'][1]['parameters'].update(length='600',width='500');b=body(s);r=calculate(b,s);assert r['quantity']=='0.000';check(r)


@pytest.mark.parametrize('value',[True,False,0,-1,2,99,'1',1.0,None,[],{}])
def test_level_index_strict_existing_lower_level(value):
    s=source();b=body(s);b['contact_ranges'][0]['level_index']=value
    with pytest.raises(DomainError):calculate(b,s)


@pytest.mark.parametrize('fault',['missing-level','level-on-top','level-on-bottom','duplicate','manual-area','manual-upper','manual-z'])
def test_strict_step_schema_and_duplicate(fault):
    s=source();b=body(s);d=b['contact_ranges'][0]
    if fault=='missing-level':d.pop('level_index')
    elif fault in ('level-on-top','level-on-bottom'):d['face']=fault[9:]
    elif fault=='duplicate':b['contact_ranges'].append({**d,'name':'重复'})
    else:d[fault]='1'
    with pytest.raises(DomainError):calculate(b,s)


def setup(clients,pid=None,shape='stepped_rectangular',unsupported=False):
    e,r,pid,s,b,eid=end_setup(clients,pid,shape)
    upload(e,pid,'合成台阶平台软件验证，非真实工程工法。22个同心台阶基础，下600×500×200，上400×300×400mm。H1矩形100×60mm孔心(200,0)，Z0至200mm。平台毛.18，孔全.006，上层覆盖交集.003，平台实际扣.003，净.177×22=3.894m²。孔底另核，支模与修约原件及TEST-STEP仅合成验证。','foundation-step-basis.txt')
    for c in get(e,pid)['components']:approve(r,pid,'components',c)
    s=next(c for c in get(e,pid)['components'] if c['id']==s['id']);eid=get(e,pid)['evidence'][-1]['id'];s['object_hash']=store.digest(s);h=hole(eid=eid,x='190') if unsupported else rectangle(eid=eid,x='200');s['foundation_items'][0]['through_holes']['holes']=[{**h,'vertical_range':interval('0','200',eid)}];res=e.put(f'/api/v1/projects/{pid}/components/'+s['id'],json=s);assert res.status_code==200,res.text;approve(r,pid,'components',res.json());s=next(c for c in get(e,pid)['components'] if c['id']==s['id']);return e,r,pid,s,body(s,eid),eid


def test_candidate_and_preview_readonly_unsupported_does_not_hide_end_faces(clients):
    e,r,pid,s,b,eid=setup(clients,unsupported=True);before=get(e,pid);res=e.get(f'/api/v1/projects/{pid}/foundation-horizontal-formwork-candidates',params=dict(component_id=s['id']));assert res.status_code==200,res.text;faces=[f for f in res.json()['foundation_items'][0]['faces'] if f['face'] in ('top','bottom','step')];assert [f['face'] for f in faces]==['top','bottom','step'];assert faces[-1]['available'] is True and faces[-1]['apertures'][0]['curved_intersection'];post(e,pid,b,'/components/preview');post(e,pid,end_body(s,eid),'/components/preview');assert get(e,pid)==before


def test_duplicate_actual_id_history_distinct_level_and_source_stale(clients):
    e,r,pid,s,b,eid=setup(clients);before=get(e,pid);assert post(e,pid,b,'/components/preview').json()['quantity']=='3.894';assert get(e,pid)==before;c=post(e,pid,b).json();approve(r,pid,'components',c);post(e,pid,{**b,'id':c['id']},expected=409);post(e,pid,end_body(s,eid))
    for c0 in get(e,pid)['components']:approve(r,pid,'components',c0)
    for t in post(e,pid,{},'/takeoffs/calculate').json():approve(r,pid,'takeoffs',t)
    post(e,pid,{},'/boq/generate');before=get(e,pid);old=next(c0 for c0 in before['components'] if c0['id']==c['id']);b.update(object_hash=store.digest(old),rounding_digits=6);res=e.put(f'/api/v1/projects/{pid}/components/'+old['id'],json=b);assert res.status_code==200,res.text;after=get(e,pid);assert after['component_history'][-1]==old and after['boq']==before['boq'] and e.put(f'/api/v1/projects/{pid}/components/'+old['id'],json=b).status_code==409
    approve(r,pid,'components',res.json());s['object_hash']=store.digest(s);s['foundation_items'][0]['levels'][1]['parameters']['length']='300';res=e.put(f'/api/v1/projects/{pid}/components/'+s['id'],json=s);assert res.status_code==200,res.text;approve(r,pid,'components',res.json());assert all(c0['state']=='stale' for c0 in get(e,pid)['components'] if c0['kind']=='foundation_horizontal_formwork')


@pytest.mark.parametrize('fault',['upper','covered','clip','rule','ast','source'])
def test_saved_step_geometry_tamper_rejected(clients,fault):
    e,r,pid,s,b,eid=setup(clients);c=post(e,pid,b).json()
    def forge(p):
        c=p['components'][-1];d=c['preview']['contact_ranges'][0];h=d['apertures'][0]
        if fault=='upper':d['single_upper_area']='0'
        elif fault=='covered':h['covered_area']='0'
        elif fault=='clip':h['single_area']='0'
        elif fault=='rule':h['intersection_rule']='forged'
        elif fault=='ast':h['formula_ast']={'constant':'0'}
        else:c['source_geometry']['foundation_items'][0]['levels'][1]['parameters']['length']='300'
    mutate(pid,forge);approve(r,pid,'components',get(e,pid)['components'][-1],expected=409)


@pytest.mark.parametrize('fmt',['json','xlsx','pdf'])
def test_three_formats_step_tables_and_old_quote_isolation(clients,fmt):
    e,r,pid,_,q=priced_project(clients);e,r,pid,s,b,eid=setup(clients,pid);c=post(e,pid,b).json();approve(r,pid,'components',c)
    for t in post(e,pid,{},'/takeoffs/calculate').json():approve(r,pid,'takeoffs',t)
    post(e,pid,{},'/boq/generate');url=f'/api/v1/projects/{pid}/exports';res=e.get(url,params=dict(format=fmt,unpriced=True));assert res.status_code==200,res.text
    if fmt=='json':assert set(STEP_TITLES)<={t[0] for t in res.json()['tables']}
    elif fmt=='xlsx':assert set(STEP_TITLES)<=set(load_workbook(io.BytesIO(res.content)).sheetnames)
    else:assert all(title in ''.join(p.extract_text() for p in PdfReader(io.BytesIO(res.content)).pages) for title in STEP_TITLES)
    assert not any(t[0] in STEP_TITLES for t in e.get(url,params=dict(format='json',quote_id=q['id'])).json()['tables'])
