"""Synthetic explicit no-post ranges and relocations; no structural approval."""
import copy,io,random
from decimal import Decimal,localcontext
from fractions import Fraction
import pytest
from openpyxl import load_workbook
from pypdf import PdfReader
from backend import store
from backend.engine import DomainError
from backend.railing_material import calculate
from backend.post_adjustments import compare
from backend.railing_takeoff import snapshot
from tests.test_railing_posts import body as fixed_body,setup as fixed_setup
from tests.test_fixed_posts import stations
from tests.test_additional_posts import line_source
from tests.test_route_railing import payload as railing_payload
from tests.test_route_takeoff import point,evaluate
from tests.test_railing_takeoff import interval
from tests.test_polygon_takeoff import post,mutate
from tests.test_workflow import get,approve,upload,priced_project,create


def avoid(a='1800',b='2200',unit='mm',eid='test',label='合成禁布'):
 return dict(label=label,**{'from':str(a),'to':str(b)},unit=unit,measurement_basis='合成原件明确含两端的禁布闭区间',evidence={k:eid for k in ('from','to','measurement_basis')})


def move(node=1,s='2500',unit='mm',eid='test'):
 return dict(node_index=node,s=str(s),s_unit=unit,measurement_basis='按原件原内部节点j显式移柱，新S从原段起点计',evidence={k:eid for k in ('node_index','s','measurement_basis')})


def body(s,eid='test'):
 b=fixed_body(s,eid);m=b['material_items'][0];m.update(mode='spaced_adjusted_posts',name='合成均分禁布移柱')
 for i,r in enumerate(m['post_ranges']):r.update(avoidance_ranges=[avoid(eid=eid) if i==0 else avoid('3200','3400',eid=eid)],post_moves=[move(eid=eid) if i==0 else move(2,'3500',eid=eid)])
 b['measurement_basis']='原均分11根，AB内部j1从S13/7移2.5m，避让[1.8,2.2]；BC内部j2从S10/3移3.5m避让[3.2,3.4]；根数规格不变，总79.2kg，非真实工程';return b


def setup(clients,pid=None):
 e,r,pid,s,_,_=fixed_setup(clients,pid)
 upload(e,pid,'合成均分禁布移柱验收，非真实工程：AB13m最大间距2m均分7跨，BC5m最大间距2m均分3跨，共享B只归AB，原11根。AB禁布含边界S1800→2200mm，原内部j1由S13/7m移到2500mm；BC禁布含边界S3200→3400mm，原内部j2由S10/3m移到3500mm。移后不得越界、重合或交叉换序，不移端柱、不重新均分，移后全部柱不在禁布范围。所有11根下料1200mm、3000g/m，来源重复2，总79.2kg，修约3位。修订AB移到2600mm后根数及质量仍79.2kg。原跨数和最大/实际均分跨间距描述移前，移后柱距和专业构造另核。','equal-post-adjustments-basis.txt')
 for c in get(e,pid)['components']:approve(r,pid,'components',c)
 s=get(e,pid)['components'][-1];eid=get(e,pid)['evidence'][-1]['id'];return e,r,pid,s,body(s,eid),eid


def test_exact_moves_keep_count_mass_originals_and_asts():
 s=geometry();b=body(s);before=copy.deepcopy((b,s));v=calculate(b,s);assert (b,s)==before
 assert v['quantity']=='79.200' and v['rule_version']=='railing-material-mass-1.8'
 g=v['material_items'][0]['post_layout'];base=calculate(fixed_body(s),s)['material_items'][0]['post_layout'];assert (g['post_count'],g['moved_post_count'],g['avoidance_range_count'])==(11,2,2)
 expected1=stations(base,'S1 整段');expected1[1]=Decimal('2.5');expected2=stations(base,'S2 整段');expected2[1]=Decimal('3.5');assert stations(g,'S1 整段')==expected1 and stations(g,'S2 整段')==expected2
 assert [p['original_s'] for p in g['post_stations']]==[p['s'] for p in base['post_stations']]
 for r in g['post_ranges']:assert [(a['original_post_count'],a['final_post_count']) for a in r['avoidance_ranges']]==[(1,0)]
 with localcontext() as c:
  c.prec=80;assert evaluate(v['formula_ast'],v['dimensions'])==Decimal('79.2')
  for p in g['post_stations']:assert evaluate(p['station_formula_ast'],v['dimensions'])==Decimal(p['s']);assert evaluate(p['original_station_formula_ast'],v['dimensions'])==Decimal(p['original_s'])
  for r in g['post_ranges']:
   for a in r['avoidance_ranges']:
    for k in ('from','to'):assert evaluate(a[k+'_formula_ast'],v['dimensions'])==Decimal(a['normalized_'+k])


# Importing a fixture builder under a different name avoids collecting its tests.
from tests.test_railing_posts import geometry


@pytest.mark.parametrize('basis',['spatial','horizontal'])
@pytest.mark.parametrize('unit,factor',[('m',Decimal(1)),('mm',Decimal(1000)),('cm',Decimal(100)),('dm',Decimal(10)),('km',Decimal('.001'))])
def test_units_and_basis(basis,unit,factor):
 s=geometry(basis=basis);b=body(s)
 for r in b['material_items'][0]['post_ranges']:
  r['avoidance_ranges']=[avoid(str(Decimal('1.8')*factor),str(Decimal('2.2')*factor),unit)]
  # Both horizontal ranges have a post at S2 for from_start; keep ownership.
  r['post_moves']=[move(1,str(Decimal('2.5')*factor),unit)]
 assert calculate(b,s)['quantity']==('79.200' if basis=='spatial' else '50.400')


@pytest.mark.parametrize('fault',['no_controls','missing_list','bad_list','extra_avoid','bad_bounds','zero_width','outside','endpoint_avoid','closed_boundary','extra_move','bad_node','bool_node','end_node','missing_node_proof','blank_target_proof','duplicate_node','same_position','target_endpoint','target_gap','cross','collision','unmoved_forbidden','extra_addition'])
def test_strict_controls_and_geometry(fault):
 s=geometry();b=body(s);r=b['material_items'][0]['post_ranges'][0];a=r['avoidance_ranges'][0];m=r['post_moves'][0]
 if fault=='no_controls':
  for p in b['material_items'][0]['post_ranges']:p.update(avoidance_ranges=[],post_moves=[])
 elif fault=='missing_list':r.pop('post_moves')
 elif fault=='bad_list':r['post_moves']={}
 elif fault=='extra_avoid':a['count']='1'
 elif fault=='bad_bounds':a['from']='2300'
 elif fault=='zero_width':a['to']='1800'
 elif fault=='outside':a['to']='14000'
 elif fault=='endpoint_avoid':a.update(**{'from':'0','to':'100'})
 elif fault=='closed_boundary':m['s']='2200'
 elif fault=='extra_move':m['direction']='left'
 elif fault=='bad_node':m['node_index']='1'
 elif fault=='bool_node':m['node_index']=True
 elif fault=='end_node':m['node_index']=7
 elif fault=='missing_node_proof':m['evidence'].pop('node_index')
 elif fault=='blank_target_proof':m['evidence']['s']=''
 elif fault=='duplicate_node':r['post_moves'].append(move(1,'2600'))
 elif fault=='same_position':r['spacing']='1300';m['s']='1300'
 elif fault=='target_endpoint':m['s']='13000'
 elif fault=='target_gap':m['s']='14000'
 elif fault=='cross':m['s']='4500'
 elif fault=='collision':m['s']='4000'
 elif fault=='unmoved_forbidden':r['post_moves']=[]
 else:r['additional_posts']=[]
 with pytest.raises(DomainError):calculate(b,s)


def test_empty_safe_avoidance_and_move_only_are_explicitly_supported():
 s=geometry();b=body(s)
 for r in b['material_items'][0]['post_ranges']:r.update(avoidance_ranges=[],post_moves=[])
 b['material_items'][0]['post_ranges'][0]['avoidance_ranges']=[avoid('500','1000')];g=calculate(b,s)['material_items'][0]['post_layout'];assert g['moved_post_count']==0 and g['avoidance_range_count']==1
 b['material_items'][0]['post_ranges'][0].update(avoidance_ranges=[],post_moves=[move()]);assert calculate(b,s)['material_items'][0]['post_layout']['moved_post_count']==1


def test_partial_s_and_two_simultaneous_moves_keep_order():
 s=geometry();s['edge_decisions'][0].update(mode='ranges',ranges=[interval(2,10)]);b=body(s);r=b['material_items'][0]['post_ranges'][0];r.update(avoidance_ranges=[avoid('3800','4200')],post_moves=[move(1,'6500'),move(2,'7500')]);g=calculate(b,s)['material_items'][0]['post_layout'];assert stations(g,'S1 区间1')==[2,Decimal('6.5'),Decimal('7.5'),8,10]
 r['post_moves'][0]['s']='1500'
 with pytest.raises(DomainError,match='严格在'):calculate(b,s)


def test_exact_comparison_irrational_equal_and_boundary_no_tolerance():
 s=line_source(1);src=s['source_geometry'];src['route_points']=[point('A',0,0,0),point('B',1,1,0)];s['source_component_hash']=store.digest(snapshot(src));s['edge_decisions']=railing_payload(src)['edge_decisions'];b=body(s);r=b['material_items'][0]['post_ranges'][0];r.update(spacing='1',spacing_unit='m',avoidance_ranges=[avoid('.70710678118654752','.70710678118654753','m')],post_moves=[move(1,'.5','m')])
 with localcontext() as c:c.prec=7;g=calculate(b,s)['material_items'][0]['post_layout']
 assert g['post_ranges'][0]['avoidance_ranges'][0]['original_post_count']==1 and g['post_stations'][1]['s']=='0.5'
 r['post_moves'][0]['s']='1.41421356237309505'
 with pytest.raises(DomainError,match='严格在'):calculate(b,s)
 assert compare((Fraction(0),Fraction(1,2)),(Fraction('.70710678118654752'),Fraction(0)),Fraction(2))>0


def test_seeded_independent_rational_station_move_oracle():
 rng=random.Random(7501)
 for _ in range(35):
  L=rng.randint(8,50);s=line_source(L);b=body(s);r=b['material_items'][0]['post_ranges'][0];r.update(spacing='2',spacing_unit='m');n=(L+1)//2;j=rng.randint(1,n-1);old=Fraction(L*j,n);floor=old.numerator//old.denominator;target=Decimal(floor)+Decimal('1.25');r.update(avoidance_ranges=[avoid(floor,floor+1,'m')],post_moves=[move(j,str(target),'m')]);g=calculate(b,s)['material_items'][0]['post_layout'];expected=[Fraction(L*k,n) if k!=j else Fraction(target) for k in range(n+1)]
  with localcontext() as c:
   c.prec=80
   for a,e in zip(stations(g,'S1 整段'),expected):assert abs(a-Decimal(e.numerator)/Decimal(e.denominator))<Decimal('1e-75')


@pytest.mark.parametrize('fmt',['json','xlsx','pdf'])
def test_workflow_exports_and_old_quote(clients,fmt):
 e,r,pid,_,quote=priced_project(clients);e,r,pid,s,b,eid=setup(clients,pid);before=get(e,pid);post(e,pid,b,'/components/preview');assert get(e,pid)==before;c=post(e,pid,b).json();approve(r,pid,'components',c)
 for t in post(e,pid,{},'/takeoffs/calculate').json():approve(r,pid,'takeoffs',t)
 assert any(t['quantity']=='79.200' for t in post(e,pid,{},'/boq/generate').json());url=f'/api/v1/projects/{pid}/exports';res=e.get(url,params=dict(format=fmt,unpriced=True));assert res.status_code==200,res.text;titles=['立柱禁布范围核查','立柱移前移后端距','立柱逐根端距','均分移柱规则与范围']
 if fmt=='json':assert set(titles)<={v[0] for v in res.json()['tables']}
 elif fmt=='xlsx':assert set(titles)<=set(load_workbook(io.BytesIO(res.content)).sheetnames)
 else:assert all(v in ''.join(p.extract_text() for p in PdfReader(io.BytesIO(res.content)).pages) for v in titles)
 old=e.get(url,params=dict(format='json',quote_id=quote['id'])).json();assert old['quote']==quote and not any(t[0] in titles for t in old['tables'])


@pytest.mark.parametrize('fault',['move','original','station','count','avoidance','formula','source','book'])
def test_saved_records_rebuilt(clients,fault):
 e,r,pid,s,b,eid=setup(clients);c=post(e,pid,b).json()
 if fault=='book':approve(r,pid,'components',c);post(e,pid,{},'/takeoffs/calculate')
 def forge(p):
  c=p['components'][-1];g=c['preview']['material_items'][0]['post_layout']
  if fault=='move':c['material_items'][0]['post_ranges'][0]['post_moves'][0]['s']='2600'
  elif fault=='original':g['post_stations'][1]['original_s']='1.9'
  elif fault=='station':g['post_stations'][1]['s']='2.6'
  elif fault=='count':g['moved_post_count']=99
  elif fault=='avoidance':g['post_ranges'][0]['avoidance_ranges'][0]['original_post_count']=0
  elif fault=='formula':g['post_stations'][1]['station_formula_ast']={'constant':'2.5'}
  elif fault=='source':c['source_geometry']['edge_decisions'][0]['mode']='exclude'
  else:p['takeoffs'][-1]['material_items'][0]['post_layout']['moved_post_count']=99
 mutate(pid,forge);coll='takeoffs' if fault=='book' else 'components';approve(r,pid,coll,get(e,pid)[coll][-1],expected=409)


def test_revision_same_mass_history_concurrency_and_source(clients):
 e,r,pid,s,b,eid=setup(clients);c=post(e,pid,b).json();approve(r,pid,'components',c)
 for t in post(e,pid,{},'/takeoffs/calculate').json():approve(r,pid,'takeoffs',t)
 post(e,pid,{},'/boq/generate');p=get(e,pid);old=p['components'][-1];old_boq=copy.deepcopy(p['boq']);new=copy.deepcopy(b);new['material_items'][0]['post_ranges'][0]['post_moves'][0]['s']='2600';new['object_hash']=store.digest(old);url=f'/api/v1/projects/{pid}/components/'+c['id'];res=e.put(url,json=new);assert res.status_code==200,res.text
 p=get(e,pid);assert p['component_history'][-1]==old and all(t['state']=='stale' for t in p['takeoffs']) and p['boq']==old_boq;assert res.json()['preview']['quantity']=='79.200' and e.put(url,json=new).status_code==409;approve(r,pid,'components',res.json())
 for t in post(e,pid,{},'/takeoffs/calculate').json():approve(r,pid,'takeoffs',t)
 assert Decimal(get(e,pid)['takeoffs'][-1]['material_items'][0]['post_layout']['post_stations'][1]['s'])==Decimal('2.6');assert any(v['quantity']=='79.200' for v in post(e,pid,{},'/boq/generate').json())
 s['object_hash']=store.digest(s);s['edge_decisions'][0].update(mode='ranges',ranges=[interval(0,10,eid)]);res=e.put(f'/api/v1/projects/{pid}/components/'+s['id'],json=s);assert res.status_code==200,res.text;approve(r,pid,'components',res.json());assert get(e,pid)['components'][-1]['state']=='stale';post(e,pid,b,'/components/preview',409)


@pytest.mark.parametrize('gate',['preview','save','review','calculate','book_review'])
def test_original_bytes_gates(clients,gate):
 e,r,pid,s,b,eid=setup(clients)
 if gate in ('review','calculate','book_review'):c=post(e,pid,b).json()
 if gate in ('calculate','book_review'):approve(r,pid,'components',c)
 if gate=='book_review':t=post(e,pid,{},'/takeoffs/calculate').json()[-1]
 doc=get(e,pid)['documents'][-1];(store.ROOT/'originals'/doc['hash']).write_bytes(b'corrupt')
 if gate=='preview':post(e,pid,b,'/components/preview',409)
 elif gate=='save':post(e,pid,b,expected=409)
 elif gate=='review':approve(r,pid,'components',c,expected=409)
 elif gate=='calculate':post(e,pid,{},'/takeoffs/calculate',409)
 else:approve(r,pid,'takeoffs',t,expected=409)


def test_roles_tenant_and_foreign_target_evidence(clients):
 e,r,pid,s,b,eid=setup(clients);post(r,pid,b,expected=403);assert clients['other'].get(f'/api/v1/projects/{pid}').status_code==404
 other=create(e)['id'];upload(e,other,'其他工程原件');b['material_items'][0]['post_ranges'][0]['post_moves'][0]['evidence']['s']=get(e,other)['evidence'][-1]['id'];post(e,pid,b,expected=404)


def test_control_count_boundaries():
 s=line_source(202);b=body(s);r=b['material_items'][0]['post_ranges'][0];r.update(avoidance_ranges=[],post_moves=[move(i,str(2*i+Decimal('.25')),'m') for i in range(1,101)])
 assert calculate(b,s)['material_items'][0]['post_layout']['moved_post_count']==100
 r['post_moves'].append(move(1,'2.3','m'))
 with pytest.raises(DomainError,match='100'):calculate(b,s)
 r.update(post_moves=[],avoidance_ranges=[avoid(str(Decimal(i)/100),str(Decimal(i)/100+Decimal('.001')),'m',label='范围'+str(i)) for i in range(1,21)])
 assert calculate(b,s)['material_items'][0]['post_layout']['avoidance_range_count']==20
 r['avoidance_ranges'].append(avoid('.4','.41','m',label='超额'))
 with pytest.raises(DomainError,match='20'):calculate(b,s)


@pytest.mark.parametrize('duplicate',['name','bounds'])
def test_duplicate_avoidance_and_overlaps(duplicate):
 s=geometry();b=body(s);r=b['material_items'][0]['post_ranges'][0];r.update(post_moves=[],avoidance_ranges=[avoid('.4','.6','m',label='A'),avoid('.5','.7','m',label='B')]);assert calculate(b,s)['material_items'][0]['post_layout']['avoidance_range_count']==3
 if duplicate=='name':r['avoidance_ranges'][1]['label']='A'
 else:r['avoidance_ranges'][1].update(**{'from':'400','to':'600','unit':'mm'})
 with pytest.raises(DomainError,match='重复'):calculate(b,s)


@pytest.mark.parametrize('starts',[True,False])
def test_closed_polygon_hole_and_endpoint_ownership(starts):
 from tests.test_railing_takeoff import geometry as pg,payload as pp
 src=pg();s=pp(src);s.update(id='polygon',revision=1,source_geometry=snapshot(src))
 for d in s['edge_decisions']:d.update(mode='full',ranges=[])
 b=body(s)
 for r in b['material_items'][0]['post_ranges']:r.update(start_post=starts,end_post=not starts,avoidance_ranges=[],post_moves=[])
 b['material_items'][0]['post_ranges'][0].update(avoidance_ranges=[avoid()],post_moves=[move()]);g=calculate(b,s)['material_items'][0]['post_layout'];assert g['post_count']==15 and g['moved_post_count']==1
 b['material_items'][0]['post_ranges'][0]['end_post' if starts else 'start_post']=True
 with pytest.raises(DomainError,match='共享端点'):calculate(b,s)


def test_mixed_adjusted_and_added_schedules_have_separate_dimensions():
 from tests.test_equal_additional_posts import body as added_body
 s=geometry();b=body(s);b['material_items']+=added_body(s)['material_items'];v=calculate(b,s);assert v['quantity']=='172.800'
 with localcontext() as c:
  c.prec=80;assert evaluate(v['formula_ast'],v['dimensions'])==Decimal('172.8')


def test_exact_surd_comparison_against_rational_oracle():
 rng=random.Random(7402)
 for _ in range(100):
  root=Fraction(rng.randint(1,20),rng.randint(1,7));a=(Fraction(rng.randint(-10,10),3),Fraction(rng.randint(-2,2)));b=(Fraction(rng.randint(-10,10),7),Fraction(rng.randint(-2,2)));delta=a[0]+a[1]*root-b[0]-b[1]*root
  assert compare(a,b,root*root)==(delta>0)-(delta<0)


def test_equal_node_identity_uses_actual_bays_not_max_spacing():
 s=line_source(12);b=body(s);r=b['material_items'][0]['post_ranges'][0];r.update(spacing='5',spacing_unit='m',avoidance_ranges=[avoid('3.9','4.1','m')],post_moves=[move(1,'4','m')])
 with pytest.raises(DomainError,match='原位置相同'):calculate(b,s)
 r['post_moves']=[move(1,'8','m')]
 with pytest.raises(DomainError,match='重合或交叉'):calculate(b,s)
 r['post_moves']=[move(1,'4.25','m')];g=calculate(b,s)['material_items'][0]['post_layout'];assert stations(g,'S1 整段')==[0,Decimal('4.25'),8,12] and g['post_stations'][1]['original_s']=='4'


@pytest.mark.parametrize('where',['parameter','evidence'])
def test_fixed_anchor_fields_are_rejected_in_equal_controls(where):
 s=geometry();b=body(s);r=b['material_items'][0]['post_ranges'][0]
 (r if where=='parameter' else r['evidence'])['direction']='from_start' if where=='parameter' else 'test'
 with pytest.raises(DomainError):calculate(b,s)


def test_equal_and_fixed_relocations_coexist_with_original_versions():
 from tests.test_post_adjustments import body as fixed_adjusted
 s=geometry();b=body(s);b['material_items']+=fixed_adjusted(s)['material_items'];v=calculate(b,s);assert v['quantity']=='158.400'
 assert [i['post_layout']['rule_version'] for i in v['material_items']]==['railing-equal-adjusted-posts-1.0','railing-fixed-adjusted-posts-1.0']
 with localcontext() as c:
  c.prec=80;assert evaluate(v['formula_ast'],v['dimensions'])==Decimal('158.4')
