"""Synthetic full vertical hole walls; actual missing concrete contact is excluded."""
import copy,io,random
from decimal import Decimal,localcontext,ROUND_HALF_UP
import pytest
from openpyxl import load_workbook
from pypdf import PdfReader
from backend import store
from backend.compound_takeoff import PI
from backend.engine import DomainError
from backend.foundation_void_formwork import calculate,candidates
from backend.foundation_void_formwork_reports import TITLES
from tests.test_foundation_formwork import foundation as outer_foundation,body as outer_body,contact,setup as outer_setup
from tests.test_foundation_voids import rectangle
from tests.test_foundation_recesses import interval
from tests.test_foundation_openings import hole
from tests.test_post_foundation import equal_body,fixed_body,fixed_added,equal_added,fixed_adjusted,equal_adjusted,combined
from tests.test_route_takeoff import evaluate
from tests.test_polygon_takeoff import post,mutate
from tests.test_workflow import get,approve,upload,create,priced_project


def foundation(make=equal_body,shape='rectangular',eid='test'):
 s=outer_foundation(make,shape,eid);c=s['foundation_items'][0]['through_holes'];c['deduction_rule']['threshold']='100'
 c['holes']=[{**rectangle(eid=eid,x='150'),'vertical_range':interval('0','150',eid)},
             {**hole(eid=eid,name='合成H2',x='-80',diameter='40'),'vertical_range':interval('300','600',eid)},
             {**rectangle(eid=eid,name='合成H3',x='70',length='60',width='40'),'vertical_range':interval('50','100',eid)}]
 return s


def wall(eid='test',face='x_minus',start='0',end='150',level=1,hole_index=1,name='合成C1'):
 return {**contact(eid,face,start,end,level,name),'hole_index':hole_index,'measurement_basis':'合成实际竖直孔壁接触，基础外边界及相邻矩形孔贴合扣除，不受体积阈值影响，不含孔底或水平面，非真实工程。'}


def body(s,eid='test'):
 b=outer_body(s,eid);b.update(kind='foundation_void_formwork',name='合成基础孔壁模板（非真实工程）',measurement_basis='合成22基础，H1矩形孔100×60mm，X正侧通外界不计接触，X负侧与H3相接宽40mm×高50mm扣除；其余两侧整宽。H2圆孔Φ40mm、Z300至600mm整周。净.814+.264πm²→1.643m²，体积阈值不填实物理孔。');b['contact_ranges']=[wall(eid,face=f,name='合成C'+str(i)) for i,f in enumerate(('x_minus','x_plus','y_minus','y_plus'),1)]+[wall(eid,face='circumference',start='300',end='600',hole_index=2,name='合成C5')];return b


def setup(clients,pid=None,shape='rectangular'):
 e,r,pid,s,_,_=outer_setup(clients,pid,shape)
 upload(e,pid,'合成孔壁模板软件验收，非真实工程或合同。22基础，材料79.200kg；矩形400×500×600mm。H1矩形孔心(150,0)，长100宽60mm，Z0至150；X正侧同外界相通无接触，X负侧与H3对向壁相接。H2圆孔中心(-80,0)实际直径40mm，Z300至600。H3矩形孔心(70,0)，长60宽40mm，Z50至100；与H1在X100mm面贴合，实际宽40高50mm。体积阈值100m²不扣任何孔，混凝土仍计2.640m³，但孔实际存在。H1四壁毛1.056m²，扣外界.198及相邻孔.044，净.814m²；H2整周.264πm²，合计1.643m²。C1起Z改100mm，H1X负壁无相邻孔交集，总净.726+.264π→1.555m²。圆柱及台阶按原件外尺寸和孔位，仅相交层内计量；相切不代表结构安全。TEST-VOID-FORM仅软件映射码，非官方。','foundation-void-formwork-basis.txt')
 for c in get(e,pid)['components']:approve(r,pid,'components',c)
 s=get(e,pid)['components'][-1];eid=get(e,pid)['evidence'][-1]['id'];s['object_hash']=store.digest(s);s['foundation_items'][0]['through_holes']=foundation(shape=shape,eid=eid)['foundation_items'][0]['through_holes'];res=e.put(f'/api/v1/projects/{pid}/components/'+s['id'],json=s);assert res.status_code==200,res.text;approve(r,pid,'components',res.json());s=get(e,pid)['components'][-1];b=body(s,eid)
 if 'stepped' in shape:b['contact_ranges'][-1]['level_index']=2
 return e,r,pid,s,b,eid


def check_asts(r):
 with localcontext() as ctx:
  ctx.prec=80;assert evaluate(r['formula_ast'],r['dimensions'])==Decimal(r['quantity'])
  for d in r['contact_ranges']:
   for k,a in d['formula_asts'].items():assert evaluate(a,r['dimensions'])==Decimal(d['normalized_parameters'][k] if k in ('z_start','z_end') else d[k]),k
   for h in d['deductions']:
    for k,a in h['formula_asts'].items():assert evaluate(a,r['dimensions'])==Decimal(h[k]),k
 for c in r['unit_conversions']:assert c['parameter'] in r['dimensions'] and c['parameter'] in r['source_evidence']


def test_physical_wall_exterior_and_adjacent_exclusions_formula_and_no_mutation():
 s=foundation();b=body(s);before=copy.deepcopy((s,b));r=calculate(b,s);assert (s,b)==before and r['quantity']=='1.643' and Decimal(r['deduction'])==Decimal('.242');assert [Decimal(d['single_net_area']) for d in r['contact_ranges'][:4]]==list(map(Decimal,['.007','0','.015','.015']));assert r['contact_ranges'][0]['deductions'][0]['kind']=='adjacent_void' and r['contact_ranges'][1]['deductions'][0]['kind']=='exterior_boundary';check_asts(r)
 with localcontext() as ctx:ctx.prec=80;assert Decimal(r['void_formwork_summary']['unrounded_quantity'])==Decimal('.814')+Decimal('.264')*Decimal(PI)


@pytest.mark.parametrize('shape',['rectangular','stepped_rectangular','circular','stepped_circular'])
@pytest.mark.parametrize('make',[equal_body,fixed_body,fixed_added,equal_added,fixed_adjusted,equal_adjusted,combined,lambda s:combined(s,equal=True)])
def test_four_foundation_shapes_eight_final_counts_inherited_once(shape,make):
 s=foundation(make,shape);b=body(s)
 if 'stepped' in shape:b['contact_ranges'][-1]['level_index']=2
 r=calculate(b,s);check_asts(r)
 with localcontext() as ctx:ctx.prec=80;count=Decimal(r['contact_ranges'][0]['total_count']);single=Decimal('.037')+Decimal('.012')*Decimal(PI) if shape=='rectangular' else Decimal('.046')+Decimal('.012')*Decimal(PI);assert Decimal(r['void_formwork_summary']['unrounded_quantity'])==single*count


@pytest.mark.parametrize('unit,factor',[('mm','1'),('cm','.1'),('dm','.01'),('m','.001'),('km','.000001')])
def test_z_units_and_low_calling_precision(unit,factor):
 s=foundation();b=body(s)
 for d in b['contact_ranges']:
  d['unit']=unit
  for k in d['parameters']:d['parameters'][k]=str(Decimal(d['parameters'][k])*Decimal(factor))
 with localcontext() as ctx:ctx.prec=6;r=calculate(b,s)
 assert r==calculate(b,s) and r['quantity']=='1.643';check_asts(r)


@pytest.mark.parametrize('face,x,y,expected',[('x_plus','150','0','.009'),('x_minus','-150','0','.009'),('y_plus','0','220','.015'),('y_minus','0','-220','.015')])
def test_four_exterior_boundaries_have_zero_actual_contact(face,x,y,expected):
 s=foundation();s['foundation_items'][0]['through_holes']['holes']=[{**rectangle(x=x,y=y),'vertical_range':interval('0','150')}];b=body(s);b['contact_ranges']=[wall(face=face)];r=calculate(b,s);assert r['quantity']=='0.000' and Decimal(r['contact_ranges'][0]['single_deduction_area'])==Decimal(expected);check_asts(r)


@pytest.mark.parametrize('start,end,cut',[('0','150','.002'),('0','50','0'),('50','100','.002'),('100','150','0'),('75','125','.001'),('0','75','.001')])
def test_neighbor_contact_clips_height_exactly(start,end,cut):
 s=foundation();b=body(s);b['contact_ranges']=[wall(start=start,end=end)];r=calculate(b,s);assert Decimal(r['contact_ranges'][0]['single_deduction_area'])==Decimal(cut);check_asts(r)


@pytest.mark.parametrize('side',['x_minus','x_plus','y_minus','y_plus'])
def test_shared_rectangular_walls_axes_and_partial_width(side):
 s=foundation();c=s['foundation_items'][0]['through_holes'];h=rectangle(x='0',y='0');n=rectangle(name='合成相邻',x='0',y='0',length='40',width='40');axis='x' if side.startswith('x_') else 'y';perp='y' if axis=='x' else 'x';sign=-1 if side.endswith('minus') else 1;hsize=50 if axis=='x' else 30;n['parameters'][axis]=str(sign*(hsize+20));n['parameters'][perp]='20';c['holes']=[{**h,'vertical_range':interval('0','150')},{**n,'vertical_range':interval('50','100')}];b=body(s);b['contact_ranges']=[wall(face=side)];r=calculate(b,s);expected=Decimal('.0015') if axis=='x' else Decimal('.002');assert Decimal(r['contact_ranges'][0]['single_deduction_area'])==expected;check_asts(r)


def test_reciprocal_shared_wall_removed_from_both_and_multiple_neighbors():
 s=foundation();b=body(s);b['contact_ranges']=[wall(),wall(hole_index=3,face='x_plus',start='50',end='100',name='合成对向壁')];r=calculate(b,s);assert Decimal(r['contact_ranges'][1]['single_net_area'])==0 and Decimal(r['deduction'])==Decimal('.088');check_asts(r)
 # Two disjoint neighbors may share portions of one wall without repeated subtraction.
 c=s['foundation_items'][0]['through_holes'];c['holes'][2]['parameters']['y']='-20';c['holes'][2]['parameters']['width']='20';n=copy.deepcopy(c['holes'][2]);n['name']='合成H4';n['parameters']['y']='20';c['holes'].append(n);b=body(s);b['contact_ranges']=[wall()];r=calculate(b,s);assert len(r['contact_ranges'][0]['deductions'])==2 and Decimal(r['deduction'])==Decimal('.044');check_asts(r)


def test_source_volume_threshold_does_not_fill_holes_and_tangency_no_area_cut():
 from backend.post_foundation import calculate as source_calculate
 s=foundation();b=body(s);assert source_calculate(s)['deduction']=='0' and calculate(b,s)['quantity']=='1.643'
 # A gap of one nanometre eliminates the shared wall, no automatic snapping.
 s['foundation_items'][0]['through_holes']['holes'][2]['parameters']['x']='69.999999';r=calculate(body(s),s);assert not r['contact_ranges'][0]['deductions'] and r['quantity']=='1.687'


def test_adjacent_split_equal_whole_and_exact_duplicate_rejected():
 s=foundation();b=body(s);b['contact_ranges']=[wall(end='50'),wall(start='50',end='150',name='合成后段')];r=calculate(b,s);assert r['quantity']=='0.154';check_asts(r)
 b['contact_ranges'][1]['parameters']['z_start']='49.999999999'
 with pytest.raises(DomainError,match='重叠'):calculate(b,s)


def test_legacy_circular_through_holes_without_explicit_z_supported():
 from tests.test_foundation_openings import body as through_body
 from tests.test_post_foundation import source
 s={**through_body(source(),shape='stepped_circular'),'id':'legacy-foundation','revision':1,'source_geometry':source()};b=body(s);b['contact_ranges']=[wall(hole_index=1,face='circumference',end='200')];r=calculate(b,s);assert r['quantity']!='0.000';check_asts(r)


def test_seeded_independent_oracle_of_shared_rectangle_intersections():
 rng=random.Random(9601)
 for _ in range(120):
  s=foundation();c=s['foundation_items'][0]['through_holes'];nw=rng.randint(1,60);ny=rng.randint(-100,100);zl=rng.randint(0,149);zh=rng.randint(zl+1,150);c['holes'][2]['parameters'].update(width=str(nw),y=str(ny));c['holes'][2]['vertical_range']=interval(str(zl),str(zh));b=body(s);lo=rng.randint(0,149);hi=rng.randint(lo+1,150);b['contact_ranges']=[wall(start=str(lo),end=str(hi))];b['rounding_digits']=rng.randint(0,12);r=calculate(b,s)
  with localcontext() as ctx:ctx.prec=80;w=max(Decimal(0),min(Decimal(30),Decimal(ny)+Decimal(nw)/2)-max(Decimal(-30),Decimal(ny)-Decimal(nw)/2));height=max(0,min(hi,zh)-max(lo,zl));expected=(Decimal(60*(hi-lo))-w*height)/1000000*22;assert Decimal(r['quantity'])==expected.quantize(Decimal(1).scaleb(-b['rounding_digits']),rounding=ROUND_HALF_UP)
  check_asts(r)


@pytest.mark.parametrize('fault',['empty','null-row','extra','missing','bool-index','hole','hole-bool','level','face','face-type','circle-rect-face','zero','reverse','before-hole','after-hole','outside-layer','precision','nan','bool','unit','parameters','evidence','blank-proof','basis','name','same-name','same-range','count','confirm','truthy','rounding','material','too-many','arc','strip','no-holes'])
def test_strict_inputs(fault):
 s=foundation();b=body(s);d=b['contact_ranges'][0]
 if fault=='empty':b['contact_ranges']=[]
 elif fault=='null-row':b['contact_ranges']=[None]
 elif fault=='extra':d['count']='2'
 elif fault=='missing':d.pop('hole_index')
 elif fault=='bool-index':d['material_index']=True
 elif fault in ('hole','hole-bool'):d['hole_index']=99 if fault=='hole' else True
 elif fault=='level':d['level_index']=2
 elif fault=='face':d['face']='top'
 elif fault=='face-type':d['face']=[]
 elif fault=='circle-rect-face':d['hole_index']=2
 elif fault in ('zero','reverse','before-hole','after-hole','precision','nan','bool'):d['parameters']['z_start' if fault=='before-hole' else 'z_end']={'zero':'0','reverse':'-1','before-hole':'-1','after-hole':'151','precision':'1.0000000000000000001','nan':'NaN','bool':True}[fault]
 elif fault=='outside-layer':s=foundation(shape='stepped_rectangular');b=body(s);b['contact_ranges'][-1]['level_index']=1
 elif fault=='unit':d['unit']='m²'
 elif fault=='parameters':d['parameters']['width']='3'
 elif fault=='evidence':d['evidence'].pop('z_start')
 elif fault=='blank-proof':d['evidence']['z_start']=' '
 elif fault=='basis':d['measurement_basis']=' '
 elif fault=='name':d['name']=' '
 elif fault=='same-name':b['contact_ranges'][1]['name']=d['name']
 elif fault=='same-range':b['contact_ranges'].append({**copy.deepcopy(d),'name':'重复范围'})
 elif fault=='count':b['multiplicity']='2'
 elif fault in ('confirm','truthy'):b['scope_confirmed']=False if fault=='confirm' else 1
 elif fault=='rounding':b['rounding_digits']=True
 elif fault=='material':b['material']=' '
 elif fault=='too-many':b['contact_ranges']=[copy.deepcopy(d)]*201
 elif fault in ('arc','strip'):d['arc_range' if fault=='arc' else 'lateral_range']={}
 elif fault=='no-holes':s['foundation_items'][0].pop('through_holes');b=body(s)
 with pytest.raises(DomainError):calculate(b,s)


def test_candidate_holes_current_approved_source_and_preview_no_mutation(clients):
 e,r,pid,s,b,eid=setup(clients);before=get(e,pid);res=e.get(f'/api/v1/projects/{pid}/foundation-void-formwork-candidates',params=dict(component_id=s['id']));assert res.status_code==200,res.text;item=res.json()['foundation_items'][0];assert len(item['holes'])==3 and item['holes'][1]['faces']==['circumference'];assert post(e,pid,b,'/components/preview').json()['quantity']=='1.643' and get(e,pid)==before


def test_cross_record_claims_different_units_and_forged_id(clients):
 e,r,pid,s,b,eid=setup(clients);b['contact_ranges']=[wall(eid,end='50')];c=post(e,pid,b).json();approve(r,pid,'components',c);post(e,pid,{**b,'id':c['id']},expected=409);b['contact_ranges']=[{**wall(eid,start='.05',end='.15'),'unit':'m'}];post(e,pid,b);b['contact_ranges'][0]['parameters']['z_start']='.049999999999';post(e,pid,b,expected=409)


@pytest.mark.parametrize('fault',['hole','face','z','proof','width','cut-kind','cut-area','formula','source-hole','book'])
def test_tamper_saved_recalculation(clients,fault):
 e,r,pid,s,b,eid=setup(clients);c=post(e,pid,b).json()
 if fault=='book':approve(r,pid,'components',c);post(e,pid,{},'/takeoffs/calculate')
 def forge(p):
  c=p['components'][-1];d=c['contact_ranges'][0];v=c['preview']['contact_ranges'][0]
  if fault=='hole':d['hole_index']=3
  elif fault=='face':d['face']='y_minus'
  elif fault=='z':d['parameters']['z_start']='100'
  elif fault=='proof':d['evidence']['z_start']='forged'
  elif fault=='width':v['width']='999'
  elif fault=='cut-kind':v['deductions'][0]['kind']='exterior_boundary'
  elif fault=='cut-area':v['deductions'][0]['single_area']='0'
  elif fault=='formula':v['formula_asts']['single_deduction_area']={'constant':'0'}
  elif fault=='source-hole':c['source_geometry']['foundation_items'][0]['through_holes']['holes'][0]['parameters']['width']='80'
  else:p['takeoffs'][-1]['quantity']='999'
 mutate(pid,forge);coll='takeoffs' if fault=='book' else 'components';approve(r,pid,coll,get(e,pid)[coll][-1],expected=404 if fault=='proof' else 422 if fault in ('hole','face') else 409)


@pytest.mark.parametrize('gate',['preview','save','review','calculate','book_review'])
def test_original_bytes_five_gates(clients,gate):
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


def test_revision_history_concurrency_and_source_stale(clients):
 e,r,pid,s,b,eid=setup(clients);c=post(e,pid,b).json();approve(r,pid,'components',c)
 for t in post(e,pid,{},'/takeoffs/calculate').json():approve(r,pid,'takeoffs',t)
 post(e,pid,{},'/boq/generate');p=get(e,pid);old=p['components'][-1];b['object_hash']=store.digest(old);b['contact_ranges'][0]['parameters']['z_start']='100';url=f'/api/v1/projects/{pid}/components/'+old['id'];res=e.put(url,json=b);assert res.status_code==200 and res.json()['preview']['quantity']=='1.555';p2=get(e,pid);assert p2['component_history'][-1]==old and p2['boq']==p['boq'] and all(t['state']=='stale' for t in p2['takeoffs']);assert e.put(url,json=b).status_code==409;approve(r,pid,'components',res.json())
 s['object_hash']=store.digest(s);s['foundation_items'][0]['parameters']['height']='700';res=e.put(f'/api/v1/projects/{pid}/components/'+s['id'],json=s);assert res.status_code==200;approve(r,pid,'components',res.json());assert get(e,pid)['components'][-1]['state']=='stale'


def test_roles_tenants_foreign_proof_and_source(clients):
 e,r,pid,s,b,eid=setup(clients);post(r,pid,b,expected=403);assert clients['other'].get(f'/api/v1/projects/{pid}').status_code==404;other=create(e)['id'];upload(e,other,'异项目原件');b['contact_ranges'][0]['evidence']['z_start']=get(e,other)['evidence'][-1]['id'];post(e,pid,b,expected=404)


@pytest.mark.parametrize('shape',['rectangular','stepped_rectangular','circular','stepped_circular'])
@pytest.mark.parametrize('fmt',['json','xlsx','pdf'])
def test_three_formats_and_old_quote_isolation(clients,shape,fmt):
 e,r,pid,_,q=priced_project(clients);e,r,pid,s,b,eid=setup(clients,pid,shape);c=post(e,pid,b).json();approve(r,pid,'components',c)
 for t in post(e,pid,{},'/takeoffs/calculate').json():approve(r,pid,'takeoffs',t)
 boq=post(e,pid,{},'/boq/generate').json();assert any(d['quantity']==c['preview']['quantity'] and d['unit']=='m²' for d in boq);url=f'/api/v1/projects/{pid}/exports';res=e.get(url,params=dict(format=fmt,unpriced=True));assert res.status_code==200,res.text
 if fmt=='json':assert set(TITLES)<={d[0] for d in res.json()['tables']}
 elif fmt=='xlsx':assert set(TITLES)<=set(load_workbook(io.BytesIO(res.content)).sheetnames)
 else:assert all(v in ''.join(p.extract_text() for p in PdfReader(io.BytesIO(res.content)).pages) for v in TITLES)
 assert not any(d[0] in TITLES for d in e.get(url,params=dict(format='json',quote_id=q['id'])).json()['tables'])
