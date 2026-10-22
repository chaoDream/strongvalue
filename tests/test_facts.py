import copy
import io
import pytest
from openpyxl import load_workbook
from pypdf import PdfReader
from backend import facts, store, gateway, main as core
from backend.engine import DomainError
from backend.parsers import fact_candidates
from tests.test_workflow import create, get, upload, approve, priced_project


@pytest.mark.parametrize('key,value,unit,expected',[
    ('capacity','0.05','万m³/日','500.00'), ('capacity','500.0','m3/d','500.0'),
    ('construction_amount','298.24858','万元','2982485.80000'),
    ('contract_amount','0.029824858','亿元','2982485.800000000'),
    ('construction_amount','2，982，485.80','元','2982485.80'),
    ('supervision_rate','１.４９','％','1.49'), ('duration','45','天','45'),
    ('project_name','  合成　项目  一期  ','','合成 项目 一期'),
])
def test_normalization_preserves_original_and_units(key,value,unit,expected):
    f=facts.normalize(key,value,unit)
    assert f['value']==expected and f['original_value']==value and f['original_unit']==unit
    assert f['unit']==facts.CATALOG[key][1]


@pytest.mark.parametrize('key,value,unit',[
    ('duration','45','工作日'), ('duration','1.5','月'), ('capacity','500',''),
    ('capacity','0','m³/日'), ('duration','0','天'), ('construction_amount','-1','元'),
    ('construction_amount','1e3','元'), ('construction_amount','NaN','元'),
    ('construction_amount','123,4','元'), ('construction_amount','1000000000000001','元'),
    ('supervision_rate','101','%'), ('supervision_rate','.0149','比例'),
    ('client','某单位','元'), ('project_name',{},''), ('bad key','x',''),
])
def test_ambiguous_or_invalid_normalization_rejected(key,value,unit):
    with pytest.raises(DomainError):facts.normalize(key,value,unit)


def test_all_explicit_text_fields_and_separate_professional_values():
    text='\n'.join(f'{names[0]}：合成{label}' for label,unit,names in facts.CATALOG.values() if names)
    text+='\n规模0.05万m³/日；施工工期45日历天；施工中标价298.24858万元；监理中标报价1.49%；合同金额3万元'
    fs=fact_candidates(text); assert len(fs)==len(facts.CATALOG)
    bykey={f['key']:f for f in fs}
    assert bykey['construction_amount']['value']=='2982485.80000'
    assert bykey['contract_amount']['value']=='30000' and bykey['supervision_rate']['value']=='1.49'
    assert bykey['client']['value']!=bykey['contractor']['value']
    assert all(f['raw_text'] in text for f in fs)


@pytest.mark.parametrize('text',[
    '文中提到建设单位但没有字段内容。', '项目名称：',
    '项目名称：合成一期 建设地点：另一个字段',
    '规模-500m³/日', '规模1e3m³/日', '监理工期45日历天',
    '监理中标报价-1.49%', '监理中标价2982485.80元', '规模−500m³/日',
])
def test_extraction_does_not_infer_ambiguous_or_wrong_professional_fields(text):
    assert fact_candidates(text)==[]


def manual(c,pid,**changes):
    p=get(c,pid);body={'key':'capacity','label':'处理规模','value':'0.05','unit':'万m³/日','evidence_id':p['evidence'][0]['id']}
    body.update(changes)
    return c.post(f'/api/v1/projects/{pid}/facts',json=body)


def test_equivalent_candidates_keep_sources_and_real_conflict_requires_decision(clients):
    e,r=clients['estimator'],clients['reviewer'];pid=create(e)['id']
    upload(e,pid,'规模500m³/日',name='a.txt');upload(e,pid,'规模0.05万m³/日',name='b.txt')
    p=get(e,pid);assert len(p['facts'])==2 and all(f['state']=='draft' for f in p['facts'])
    assert p['fact_conflicts']==[]
    approve(r,pid,'facts',p['facts'][0]);approve(r,pid,'facts',p['facts'][1])
    upload(e,pid,'规模600m³/日',name='c.txt');p=get(e,pid)
    assert all(f['state']=='conflict' for f in p['facts'])
    group=p['fact_conflicts'][0]
    assert group['key']=='capacity' and group['state']=='unresolved'
    assert len(group['value_groups'])==2 and len(group['value_groups'][0]['candidate_ids'])==2
    assert len(group['candidate_ids'])==3
    approve(r,pid,'facts',p['facts'][0],expected=409)
    approve(r,pid,'facts',p['facts'][-1],'reject')
    for f in p['facts'][:2]:approve(r,pid,'facts',f)
    assert [f['state'] for f in get(e,pid)['facts']]==['approved','approved','rejected']
    assert get(e,pid)['fact_conflicts']==[]


def test_manual_bound_source_validation_permissions_and_custom_compatibility(clients):
    e=clients['estimator'];pid=create(e)['id'];upload(e,pid,'明确记录规模0.05万m³/日')
    response=manual(e,pid,raw_text='规模0.05万m³/日');assert response.status_code==200,response.text
    f=response.json();assert f['value']=='500.00' and f['confidence_category']=='人工记录待核'
    assert f['document_hash'] and f['evidence_hash'] and f['raw_text']=='规模0.05万m³/日'
    assert manual(e,pid,raw_text='不存在的摘录').status_code==422
    assert manual(e,pid,unit='月').status_code==422
    assert manual(clients['reviewer'],pid).status_code==403
    assert clients['other'].post(f'/api/v1/projects/{pid}/facts',json={'key':'capacity','label':'规模','value':'500','unit':'m³/日','evidence_id':f['evidence_id']}).status_code==404
    assert manual(e,pid,key='legacy_custom',label='自定义字段',value='人工判断',unit='').status_code==200
    assert clients['reviewer'].get('/api/v1/fact-types').json()['construction_amount']['units']==['元','万元','亿元']


@pytest.mark.parametrize('mutation',['bytes','missing','evidence','conversion','incomplete'])
def test_approval_rechecks_actual_source_and_normalization(clients,mutation):
    e,r=clients['estimator'],clients['reviewer'];pid=create(e)['id'];upload(e,pid,'规模500m³/日')
    p=get(e,pid);f=p['facts'][0];doc=p['documents'][0]
    if mutation=='bytes':(store.ROOT/'originals'/doc['hash']).write_bytes(b'tampered')
    elif mutation=='missing':(store.ROOT/'originals'/doc['hash']).unlink()
    else:
        with store.transaction() as c:
            p=core.project(c,pid,{'tenant':'local','username':'estimator','role':'estimator'})
            if mutation=='evidence':p['evidence'][0]['text']+='new evidence'
            elif mutation=='incomplete':p['facts'][0].pop('original_value')
            else:p['facts'][0]['original_unit']='万m³/日'
            store.save(c,p)
    approve(r,pid,'facts',f,expected=409)


def test_source_replacement_supersedes_old_candidates(clients):
    e=clients['estimator'];pid=create(e)['id']
    upload(e,pid,'项目名称：合成一期',name='tender.txt');upload(e,pid,'项目名称：合成二期',name='tender.txt')
    assert [f['state'] for f in get(e,pid)['facts']]==['superseded','draft']


def test_model_candidates_use_same_units_conflicts_and_reject_invalid_keys(clients,monkeypatch):
    e=clients['estimator'];pid=create(e)['id'];upload(e,pid,'规模500m³/日，备用规模0.05万m³/日。')
    did=get(e,pid)['documents'][0]['id']
    monkeypatch.setenv('QIANG_AI_GATEWAY_URL','http://localhost:9999/extract');monkeypatch.setenv('QIANG_AI_MODEL','test-model')
    candidate={'kind':'fact','key':'capacity','label':'规模','value':'0.05','unit':'万m³/日','page':1,'quote':'备用规模0.05万m³/日','bbox':None}
    monkeypatch.setattr(gateway,'request_gateway',lambda *a:{'model':'test-model','warnings':[],'candidates':[
        candidate,{**candidate,'key':'auto_approved'},{**candidate,'unit':'月'}]})
    response=e.post(f'/api/v1/projects/{pid}/documents/{did}/analyze',json={'consent':True,'destination':'localhost:9999'})
    assert response.status_code==200
    p=get(e,pid);model=[f for f in p['facts'] if f['method']=='model']
    assert len(model)==1 and model[0]['value']=='500.00' and model[0]['state']=='draft'
    assert len(p['analyses'][0]['rejected_candidates'])==2


def test_facts_report_uses_immutable_snapshot_in_all_formats(clients):
    e,r,pid,_,old=priced_project(clients);base=f'/api/v1/projects/{pid}'
    # Existing source text is an explicit manual basis; new fact invalidates dependent approvals.
    f=manual(e,pid,key='project_name',label='项目名称',value='合成快照名称',unit='').json()
    p=get(e,pid)
    for coll in ['facts','components','takeoffs','boq','rules']:
        for row in p[coll]:approve(r,pid,coll,row)
    q=e.post(base+'/quotes/calculate').json();assert q['snapshot']['facts'][0]['value']=='合成快照名称'
    before=copy.deepcopy(q)
    manual(e,pid,key='project_name',label='项目名称',value='更新后的名称',unit='')
    data=e.get(base+'/exports',params={'format':'json','quote_id':q['id']}).json()
    section=next(s for s in data['tables'] if s[0]=='项目事实与冲突候选')
    assert '合成快照名称' in str(section) and '更新后的名称' not in str(section)
    x=e.get(base+'/exports',params={'format':'xlsx','quote_id':q['id']})
    wb=load_workbook(io.BytesIO(x.content));assert '合成快照名称' in str(list(wb['项目事实与冲突候选'].values))
    pdf=e.get(base+'/exports',params={'format':'pdf','quote_id':q['id']})
    text=''.join(p.extract_text() for p in PdfReader(io.BytesIO(pdf.content)).pages)
    assert '合成快照名称' in text and '项目事实与冲突候选' in text and '更新后的名称' not in text
    assert get(e,pid)['quotes'][-1]==before and not old['snapshot']['facts']


def test_approved_fact_source_change_blocks_new_quote(clients):
    e,r,pid,_,_=priced_project(clients)
    f=manual(e,pid,key='project_name',label='项目名称',value='合成项目',unit='').json()
    approve(r,pid,'facts',f)
    p=get(e,pid);doc=p['documents'][0]
    (store.ROOT/'originals'/doc['hash']).write_bytes(b'changed')
    assert any('原件' in x for x in get(e,pid)['blockers'])
    assert e.post(f'/api/v1/projects/{pid}/quotes/calculate').status_code==409


def test_oversize_fact_does_not_fail_other_candidates_or_document_job(clients):
    e=clients['estimator'];pid=create(e)['id']
    upload(e,pid,'项目名称：'+'长'*2000+'\n建设单位：合成建设单位')
    p=get(e,pid)
    assert p['jobs'][0]['state']=='completed' and [f['key'] for f in p['facts']]==['client']


def test_empty_label_does_not_consume_next_line_field():
    fs=fact_candidates('项目名称：\n建设单位：合成建设单位')
    assert [(f['key'],f['value']) for f in fs]==[('client','合成建设单位')]


@pytest.mark.parametrize('key,left,lu,right,ru,equal',[
    ('capacity','500','m³/日','0.05','万m³/日',True),
    ('construction_amount','2982485.80','元','298.24858','万元',True),
    ('construction_amount','100','万元','100','元',False),
    ('custom','1','m','1','cm',False),
    ('project_name','一期','','二期','',False),
])
def test_legacy_candidates_compare_by_explicit_value_and_unit(key,left,lu,right,ru,equal):
    assert facts.equivalent({'key':key,'value':left,'unit':lu},{'key':key,'value':right,'unit':ru}) is equal
