"""Independent analytic special cases, float oracle and near-tangent asymptotics."""
import math, random, copy
from decimal import Decimal, localcontext
import pytest
from backend.compound_takeoff import PI
from backend.curved_intersections import calculate as clip,atan2
from backend.foundation_horizontal_formwork import calculate
from tests.test_foundation_step_formwork import source,body,check,setup
from tests.test_foundation_openings import hole
from tests.test_foundation_voids import rectangle
from tests.test_foundation_recesses import interval
from tests.test_route_takeoff import evaluate
from tests.test_workflow import get,approve,priced_project
from tests.test_polygon_takeoff import post,mutate


def direct(hp,up):
    with localcontext() as ctx:
        ctx.prec=80;hp={k:Decimal(str(v)) for k,v in hp.items()};up={k:Decimal(str(v)) for k,v in up.items()};ha={k:{'dimension':'hole.'+k} for k in hp};ua={k:{'dimension':'upper.'+k} for k in up};dims={**{'hole.'+k:str(v) for k,v in hp.items()},**{'upper.'+k:str(v) for k,v in up.items()}}
        area=lambda p: Decimal(PI)/4*p['diameter']**2 if 'diameter' in p else p['length']*p['width']
        r=clip(hp,ha,up,ua,area(hp),area(up));assert evaluate(r[1],dims)==r[0]
        for t in r[3]['terms']:
            assert evaluate(t['formula_ast'],dims)==Decimal(t['area'])
            for k,a in t['formula_asts'].items():assert evaluate(a,dims)==Decimal(t[k])
        return r


@pytest.mark.parametrize('y,x,expected',[(0,1,0),(0,-1,'pi'),(1,0,'half'),(1,1,'quarter'),(1,-1,'three-quarters'),('1e-100',1,'tiny'),(1,'1e-100','almost-half')])
def test_decimal_angle_known_quadrants_and_low_context(y,x,expected):
    with localcontext() as ctx:
        ctx.prec=200;a=atan2(Decimal(y),Decimal(x));p=Decimal(PI);oracle={'pi':p,'half':p/2,'quarter':p/4,'three-quarters':3*p/4,'tiny':Decimal('1e-100'),'almost-half':p/2-Decimal('1e-100')}.get(expected,Decimal(0));assert abs(a-oracle)<Decimal('1e-48')
    assert float(a)==pytest.approx(math.atan2(float(y),float(x)),abs=1e-15)


@pytest.mark.parametrize('kind',['circle-circle','circle-rect','rect-circle'])
@pytest.mark.parametrize('sign',[-1,1])
@pytest.mark.parametrize('axis',['x','y'])
def test_exact_half_quarter_and_equal_circle_lens(kind,sign,axis):
    if kind=='circle-circle':
        hp={'x':sign if axis=='x' else 0,'y':sign if axis=='y' else 0,'diameter':2};up={'diameter':2};r=direct(hp,up)
        with localcontext() as ctx:ctx.prec=80;expected=Decimal(2)*Decimal(PI)/3-Decimal(3).sqrt()/2;assert abs(r[0]-expected)<Decimal('1e-48')
    elif kind=='circle-rect':
        hp={'x':sign if axis=='x' else 0,'y':sign if axis=='y' else 0,'diameter':'.2'};up={'length':2,'width':2};r=direct(hp,up)
        with localcontext() as ctx:ctx.prec=80;assert r[0]==Decimal(PI)*Decimal('.005')
        hp['x']=sign;hp['y']=sign;r=direct(hp,up)
        with localcontext() as ctx:ctx.prec=80;assert r[0]==Decimal(PI)*Decimal('.0025')
    else:
        hp={'x':sign if axis=='x' else 0,'y':sign if axis=='y' else 0,'length':2,'width':2};up={'diameter':2};r=direct(hp,up)
        with localcontext() as ctx:ctx.prec=80;assert r[0]==Decimal(PI)/2
        hp.update(x=sign,y=sign);r=direct(hp,up)
        with localcontext() as ctx:ctx.prec=80;assert r[0]==Decimal(PI)/4


def test_160_seeded_circle_lenses_independent_binary_math_oracle():
    rng=random.Random(10101)
    for _ in range(160):
        r=rng.uniform(.1,1);R=rng.uniform(.1,1);d=rng.uniform(abs(R-r)+.001,R+r-.001);a=(d*d+r*r-R*R)/(2*d);b=d-a;h=math.sqrt(max(0,r*r-a*a));expected=r*r*math.atan2(h,a)+R*R*math.atan2(h,b)-d*h
        result=direct({'x':str(d),'y':0,'diameter':str(2*r)},{'diameter':str(2*R)})[0];assert float(result)==pytest.approx(expected,rel=2e-11,abs=2e-14)


def test_45_seeded_circle_rectangles_independent_vertical_simpson_oracle():
    # Independent numerical integration in y with x split at circle/rectangle crossings.
    rng=random.Random(10102)
    for _ in range(45):
        R=1.;cx=rng.uniform(.5,1.2);cy=rng.uniform(-.4,.4);lx=rng.uniform(.3,.8);ly=rng.uniform(.3,.8);x1,x2=cx-lx/2,cx+lx/2;y1,y2=cy-ly/2,cy+ly/2
        if x1*x1+min(abs(y1),abs(y2))**2>=1:continue
        if all(x*x+y*y<1 for x in [x1,x2] for y in [y1,y2]):continue
        bounds=sorted(set([max(-1.,x1),min(1.,x2)]+[x for y in [y1,y2] for x in [-math.sqrt(1-y*y),math.sqrt(1-y*y)] if x1<x<x2]))
        def f(x):
            h=math.sqrt(max(0,1-x*x));return max(0,min(y2,h)-max(y1,-h))
        expected=0.
        for left,right in zip(bounds,bounds[1:]):
            n=4096;step=math.pi/n;mid=(left+right)/2;half=(right-left)/2
            def g(u):return f(mid+half*math.sin(u))*half*math.cos(u)
            expected+=step/3*(g(-math.pi/2)+g(math.pi/2)+sum((4 if j%2 else 2)*g(-math.pi/2+step*j) for j in range(1,n)))
        result=direct({'x':str(cx),'y':str(cy),'length':str(lx),'width':str(ly)},{'diameter':2})[0];assert float(result)==pytest.approx(expected,abs=2e-13)
        # Swapping disk and rectangle with opposite center displacement preserves overlap.
        swapped=direct({'x':str(-cx),'y':str(-cy),'diameter':2},{'length':str(lx),'width':str(ly)})[0];assert result==swapped


@pytest.mark.parametrize('epsilon',['1e-8','1e-12','1e-17'])
def test_near_external_tangency_positive_lens_without_float_cancellation(epsilon):
    with localcontext() as ctx:
        ctx.prec=80;e=Decimal(epsilon);d=2-e;result=direct({'x':str(d),'y':0,'diameter':2},{'diameter':2})[0];leading=Decimal(4)/3*e*e.sqrt();assert result>0 and abs(result/leading-1)<e


@pytest.mark.parametrize('ratio',['1e-9','1e-18','1e-30'])
def test_tiny_circle_on_large_circle_boundary_stays_below_half(ratio):
    with localcontext() as ctx:
        ctx.prec=80;r=Decimal(ratio);area=direct({'x':1,'y':0,'diameter':str(2*r)},{'diameter':2})[0];half=Decimal(PI)*r*r/2;assert 0<area<half


@pytest.mark.parametrize('shape,kind',[('stepped_rectangular','circle'),('stepped_circular','circle'),('stepped_circular','rect')])
@pytest.mark.parametrize('sign',[-1,1])
def test_source_partial_curves_holes_units_and_formula_replay(shape,kind,sign):
    h=hole(x=str(sign*190)) if kind=='circle' else rectangle(x=str(sign*200))
    s=source(shape,holes=[{**h,'vertical_range':interval('0','200')}]);b=body(s);before=copy.deepcopy((s,b));r=calculate(b,s);check(r);assert (s,b)==before and r['rule_version']=='foundation-horizontal-formwork-1.2';opening=r['contact_ranges'][0]['apertures'][0];assert 0<Decimal(opening['covered_area'])<Decimal(opening['full_area'])
    with localcontext() as ctx:ctx.prec=6;low=calculate(b,s)
    assert r==low
    for t in opening['curved_intersection']['terms']:assert t['formula_ast']['working_digits']==200


@pytest.mark.parametrize('fault',['term','angle','algorithm','precision','pi','covered'])
def test_saved_curved_process_tamper_gates(clients,fault):
    e,r,pid,s,b,eid=setup(clients,unsupported=True);c=post(e,pid,b).json()
    def forge(p):
        h=p['components'][-1]['preview']['contact_ranges'][0]['apertures'][0];m=h['curved_intersection']
        if fault=='term':m['terms'][0]['area']='0'
        elif fault=='angle':m['terms'][0]['formula_ast']={'constant':'0'}
        elif fault=='algorithm':m['method']='forged'
        elif fault=='precision':m['working_digits']=20
        elif fault=='pi':m['pi_constant']='3'
        else:h['covered_area']='0'
    mutate(pid,forge);approve(r,pid,'components',get(e,pid)['components'][-1],expected=409)

@pytest.mark.parametrize('case',['circle-rect','circle-circle','rect-circle'])
@pytest.mark.parametrize('fmt',['json','xlsx','pdf'])
def test_partial_curves_approved_books_and_three_formats(clients,case,fmt):
    import io
    from openpyxl import load_workbook
    from pypdf import PdfReader
    from backend.foundation_horizontal_formwork_reports import CURVED_TITLE
    e,r,pid,s,b,eid=setup(clients,shape='stepped_rectangular' if case=='circle-rect' else 'stepped_circular',unsupported=case!='rect-circle');before=get(e,pid);preview=post(e,pid,b,'/components/preview').json();assert get(e,pid)==before;c=post(e,pid,b).json();assert {**c['preview'],'source_stamp':c['source_stamp']}==preview;approve(r,pid,'components',c)
    for t in post(e,pid,{},'/takeoffs/calculate').json():approve(r,pid,'takeoffs',t)
    boq=post(e,pid,{},'/boq/generate').json();assert any(x['quantity']==preview['quantity'] and x['unit']=='m²' for x in boq)
    res=e.get(f'/api/v1/projects/{pid}/exports',params=dict(format=fmt,unpriced=True));assert res.status_code==200,res.text
    if fmt=='json':assert CURVED_TITLE in [t[0] for t in res.json()['tables']]
    elif fmt=='xlsx':assert CURVED_TITLE in load_workbook(io.BytesIO(res.content)).sheetnames
    else:assert CURVED_TITLE in ''.join(p.extract_text() for p in PdfReader(io.BytesIO(res.content)).pages)


def test_curved_new_export_table_excluded_from_old_quote(clients):
    from backend.foundation_horizontal_formwork_reports import CURVED_TITLE
    e,r,pid,_,q=priced_project(clients);e,r,pid,s,b,eid=setup(clients,pid,unsupported=True);c=post(e,pid,b).json();approve(r,pid,'components',c)
    for t in post(e,pid,{},'/takeoffs/calculate').json():approve(r,pid,'takeoffs',t)
    post(e,pid,{},'/boq/generate');res=e.get(f'/api/v1/projects/{pid}/exports',params=dict(format='json',quote_id=q['id']));assert res.status_code==200,res.text;assert CURVED_TITLE not in [t[0] for t in res.json()['tables']]
