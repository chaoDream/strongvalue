import copy
import json
from decimal import Decimal
import pytest
from fastapi import HTTPException
from backend import store
from backend.quote_comparison import compare_quotes
from tests.test_workflow import priced_project,get


def line(**changes):
    return {'id':'line1','name':'合成清单','unit':'m³','quantity':'15','unit_price':'105.01','amount':'1575.15',
            'code':'TEST','features':'数学测试','node':'测试单位工程','quotas':[{'code':'Q','coefficient':'1','resources':[{'name':'合成资源','price':'100.005','consumption':'1.05','source':'测试来源'}]}],**changes}


def quote(lines=None,fees=None,**changes):
    lines=lines if lines is not None else [line()];fees=fees or []
    subtotal=sum((Decimal(v['amount']) for v in lines),Decimal(0));total=subtotal+sum((Decimal(v['amount']) for v in fees),Decimal(0))
    q={'id':'q1','version':1,'scope':['建筑'],'state':'draft','lines':lines,'fees':fees,'subtotal':str(subtotal),'total':str(total),
       'snapshot':{'rules':[],'pricing_date':'2026-10-21'},**changes}
    q['input_hash']=store.digest(q['snapshot']);q['hash']=store.digest(q)
    return q


def compare(a,b):
    return compare_quotes({'id':'synthetic','quotes':[a,b],'stale_quotes':[a['id']]},a['id'],b['id'])


def test_ordered_quantity_price_rounding_and_fee_reconciliation():
    a=quote(fees=[{'name':'测试费','amount':'47.25','base_amount':'1575.15','rate':'.03'}])
    changed=line(quantity='20',unit_price='126.01',amount='2520.20');changed['quotas'][0]['resources'][0]['price']='120.01'
    b=quote([changed],[{'name':'测试费','amount':'75.61','base_amount':'2520.20','rate':'.03'}],id='q2',version=2)
    frozen=copy.deepcopy((a,b));r=compare(a,b)
    assert r['closed'] and r['attribution']['quantity']=='525.05' and r['attribution']['unit_price']=='420.00'
    assert r['subtotal_delta']=='945.05' and r['fee_delta']=='28.36' and r['total_delta']=='973.41'
    assert r['lines'][0]['resource_changes'][0]['fields']==['price']
    assert r['left_stale'] and (a,b)==frozen
    assert store.digest({k:v for k,v in r.items() if k!='hash'})==r['hash']


def test_add_remove_basis_change_and_metadata_only_changes():
    a=quote([line(),line(id='removed',amount='10')])
    b=quote([line(unit='m²',amount='1800'),line(id='added',amount='5')],id='q2',version=2)
    r=compare(a,b)
    assert r['attribution']['basis_change']=='224.85' and r['attribution']['removed']=='-10.00' and r['attribution']['added']=='5.00'
    assert not next(x for x in r['lines'] if x['id']=='line1')['comparable']
    changed_basis=quote([line(features='不同计量特征',amount='1600')],id='q2')
    assert compare(quote(),changed_basis)['attribution']['basis_change']=='24.85'
    renamed=quote([line(name='改名后的合成清单')],id='q3',version=3)
    r=compare(quote(),renamed)
    assert r['lines'][0]['fields']==['name'] and r['total_delta']=='0.00'


def test_duplicate_named_fees_are_not_merged_and_order_is_explicit():
    fees=[{'name':'同名费','amount':'1','rate':'.01','base_amount':'100'},{'name':'同名费','amount':'2','rate':'.02','base_amount':'100'}]
    a=quote(fees=fees);b=quote(fees=[{**fees[0],'amount':'3'},fees[1]],id='q2')
    r=compare(a,b)
    assert len(r['fee_changes'])==1 and r['fee_changes'][0]['position']==1 and r['fee_delta']=='2.00'
    b=quote(fees=list(reversed(fees)),id='q2')
    r=compare(a,b);assert len(r['fee_changes'])==2 and r['fee_delta']=='0.00'


def test_rounding_residual_and_large_amounts_preserve_precision():
    a=quote([line(quantity='1.333',unit_price='1.11',amount='1.48')])
    b=quote([line(quantity='1.337',unit_price='1.12',amount='1.50')],id='q2')
    r=compare(a,b);assert r['attribution']['rounding']=='0.01' and r['total_delta']=='0.02'
    # Precision greater than the process default Decimal context.
    a=quote([line(quantity='1',unit_price='123456789012345678901234567890.01',amount='123456789012345678901234567890.01')])
    b=copy.deepcopy(a);b.update(id='q2',total='123456789012345678901234567890.02',subtotal='123456789012345678901234567890.02')
    b['lines'][0].update(unit_price=b['total'],amount=b['total']);b.pop('hash');b['hash']=store.digest(b)
    # Construct totals exactly without the test helper's default Decimal precision.
    a['total']=a['subtotal']=a['lines'][0]['amount'];a.pop('hash');a['hash']=store.digest(a)
    r=compare(a,b);assert r['total_delta']=='0.01' and r['attribution']['unit_price']=='0.01'


def test_snapshot_integrity_and_internal_totals_fail_closed():
    a=quote();b=quote(id='q2');b['total']='0.01'
    with pytest.raises(HTTPException) as exc:compare(a,b)
    assert exc.value.status_code==409
    b.pop('hash');b['hash']=store.digest(b)
    with pytest.raises(HTTPException) as exc:compare(a,b)
    assert '不闭合' in exc.value.detail
    b=quote(id='q2');b['snapshot']['rules'].append({'changed':'invalid'})
    with pytest.raises(HTTPException):compare(a,b)
    for value in ['NaN','Infinity','invalid']:
        invalid=quote(id='q2');invalid['lines'][0]['unit_price']=value;invalid.pop('hash');invalid['hash']=store.digest(invalid)
        with pytest.raises(HTTPException) as exc:compare(a,invalid)
        assert exc.value.status_code==409
    r=compare(a,a);assert r['lines']==[] and r['closed'] and r['total_delta']=='0.00'


def test_comparison_export_tenant_hashes_history_and_no_approval_changes(clients):
    e,r,pid,_,q=priced_project(clients);base=f'/api/v1/projects/{pid}'
    q2=e.post(base+'/quotes/calculate').json()
    report=e.get(base+f'/quotes/compare?left={q["id"]}&right={q2["id"]}').json();assert report['closed']
    before=get(e,pid)
    body={'left':q['id'],'right':q2['id'],'left_hash':q['hash'],'right_hash':q2['hash'],'report_hash':report['hash']}
    assert e.post(base+'/quotes/comparison-export',json={**body,'right_hash':'old'}).status_code==409
    assert e.post(base+'/quotes/comparison-export',json={**body,'report_hash':'old'}).status_code==409
    response=r.post(base+'/quotes/comparison-export',json=body);assert response.status_code==200,response.text
    assert response.json()==report and get(e,pid)==before
    artifact=e.get(base+'/artifacts').json()[0]
    assert artifact['purpose']=='quote_comparison' and artifact['approved'] is False
    assert e.get(base+'/artifacts/'+artifact['id']+'/file').content==response.content
    assert e.get(base+'/artifacts/'+artifact['id']+'/verify').json()['valid']
    assert clients['other'].get(base+f'/quotes/compare?left={q["id"]}&right={q2["id"]}').status_code==404
    assert clients['other'].post(base+'/quotes/comparison-export',json=body).status_code==404
    assert e.get(base+f'/quotes/compare?left=missing&right={q2["id"]}').status_code==404
    # Subsequent snapshot validity changes do not rewrite the retained report.
    with store.transaction() as c:
        p=json.loads(c.execute('SELECT data FROM projects WHERE id=?',(pid,)).fetchone()['data']);p['stale_quotes'].append(q['id']);store.save(c,p)
    assert e.get(base+'/artifacts/'+artifact['id']+'/file').content==response.content
