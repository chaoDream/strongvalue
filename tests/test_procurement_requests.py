"""Synthetic internal requisitions: explicit choice, independent review and reversible closure."""
import copy
from datetime import date,timedelta
from concurrent.futures import ThreadPoolExecutor
import pytest
from backend import store,procurement_analyses as analyses
from tests import test_procurement_analyses as costs
from tests.test_workflow import get,approve,upload
from tests.test_supplier_quotes import mutate


def base(pid):return f'/api/v1/projects/{pid}/procurement-requests'


def setup(clients):
    e,r,pid,costraw=costs.setup(clients)
    parent=costs.save(e,pid,costs.submission(e,pid,costraw));approve(r,pid,'procurement_analyses',parent)
    parent=get(e,pid)['procurement_analyses'][0]
    raw=dict(name='合成内部采购申请，非对外采购',analysis_id=parent['id'],analysis_hash=store.digest(parent),quote_id=parent['result']['scenarios'][0]['quote']['id'],request_date=date.today().isoformat(),required_by=(date.today()+timedelta(days=7)).isoformat(),destination='合成收货库位A（非真实仓库）',reason='人工选择A，核对数量、供货条件及费用；仅合成内部申请，不自动选低价或下单',evidence_id=costraw['evidence_id'],selection_confirmed=True)
    return e,r,pid,parent,raw


def preview(e,pid,raw,expected=200):
    q=e.post(base(pid)+'/preview',json={'input':raw});assert q.status_code==expected,q.text;return q.json()


def submission(e,pid,raw):return dict(request_id=store.uid(),input=raw,preview_hash=preview(e,pid,raw)['preview_hash'])


def save(e,pid,b,expected=200):
    q=e.post(base(pid),json=b);assert q.status_code==expected,q.text;return q.json()


def cancel_body(row,eid):return dict(request_id=store.uid(),object_hash=store.digest(row),reason='合成有据确认未对外下单，取消内部申请不撤销外部事实',evidence_id=eid,not_ordered_confirmed=True)


def cancel(e,pid,row,eid,expected=200,body=None):
    q=e.post(base(pid)+'/'+row['id']+'/cancel',json=body or cancel_body(row,eid));assert q.status_code==expected,q.text;return q.json()


def test_preview_save_explicit_choice_review_and_history_never_order_or_change_engineering(clients):
    e,r,pid,parent,raw=setup(clients);before=get(e,pid);audit=e.get(f'/api/v1/projects/{pid}/audit').json()
    assert e.get(base(pid)+'/options').json()['items'][0]['eligible']
    q=preview(e,pid,raw);assert q['result']['scenario']['quote']['supplier']=='合成供应商A' and q['result']['scenario']['total']=='1396.15'
    assert get(e,pid)==before and e.get(f'/api/v1/projects/{pid}/audit').json()==audit
    b=dict(request_id=store.uid(),input=raw,preview_hash=q['preview_hash']);row=save(e,pid,b);assert row['number'].startswith('PR-') and save(e,pid,b)==row
    frozen=e.get(base(pid)+'/'+row['id']+'/export');assert frozen.status_code==200 and not frozen.json()['review_valid']
    approve(r,pid,'procurement_requests',row);view=e.get(base(pid)).json()['items'][0];assert view['current'] and view['review_valid']
    after=get(e,pid)
    for k in before:
        if k.startswith('stock_') or k in ('components','takeoffs','boq','quotes','rules','report_approval','supplier_quotes','supplier_quote_adoptions','procurement_analyses'):assert after[k]==before[k]
    assert not e.get(base(pid)+'/options').json()['items'][0]['eligible']
    q=e.get(base(pid)+'/'+row['id']+'/export');assert q.json()['review_valid'] and q.json()['record']['result']['quantity']=='15.75'
    artifact=next(a for a in e.get(f'/api/v1/projects/{pid}/artifacts').json() if a['hash']==frozen.headers['x-artifact-hash'])
    assert e.get(f'/api/v1/projects/{pid}/artifacts/'+artifact['id']+'/file').content==frozen.content


@pytest.mark.parametrize('fault',[
 lambda v:v.update(name=' '),lambda v:v.update(analysis_id='foreign'),lambda v:v.update(analysis_hash='0'*64),lambda v:v.update(quote_id='other-supplier'),lambda v:v.update(selection_confirmed=False),lambda v:v.update(selection_confirmed=1),lambda v:v.update(request_date='2999-01-01'),lambda v:v.update(request_date='2026-1-1'),lambda v:v.update(request_date=None),lambda v:v.update(required_by='2020-01-01'),lambda v:v.update(required_by='invalid'),lambda v:v.update(destination=''),lambda v:v.update(reason=' '),lambda v:v.update(evidence_id='foreign'),lambda v:v.update(quantity='99'),lambda v:v.update(price='1'),lambda v:v.update(supplier='fake')
])
def test_invalid_inputs_do_not_mutate_or_audit(clients,fault):
    e,r,pid,parent,raw=setup(clients);fault(raw);before=get(e,pid);audit=e.get(f'/api/v1/projects/{pid}/audit').json();q=e.post(base(pid)+'/preview',json={'input':raw})
    assert q.status_code in (404,409,422),q.text
    assert get(e,pid)==before and e.get(f'/api/v1/projects/{pid}/audit').json()==audit


@pytest.mark.parametrize('decision',['approve','reject','request_more_info'])
def test_cancel_preserves_review_and_record_and_permits_deliberate_reapplication(clients,decision):
    e,r,pid,parent,raw=setup(clients);row=save(e,pid,submission(e,pid,raw));approve(r,pid,'procurement_requests',row,decision=decision);row=get(e,pid)['procurement_requests'][0]
    reviews=copy.deepcopy(get(e,pid)['reviews']);body=cancel_body(row,raw['evidence_id']);event=cancel(e,pid,row,raw['evidence_id'],body=body)
    assert cancel(e,pid,row,raw['evidence_id'],body=body)==event
    p=get(e,pid);assert p['procurement_requests'][0]==row and p['reviews']==reviews
    view=e.get(base(pid)).json()['items'][0];assert view['phase']=='cancelled' and not view['review_valid'] and view['cancellation']==event
    approve(r,pid,'procurement_requests',row,expected=409)
    targets=r.get(f'/api/v1/projects/{pid}/review-batch-targets',params={'collection':'procurement_requests'}).json();assert targets['total']==0
    raw['quote_id']=parent['result']['scenarios'][1]['quote']['id'];later=save(e,pid,submission(e,pid,raw));assert later['result']['scenario']['total']=='1311.94' and later['id']!=row['id']


@pytest.mark.parametrize('stage',['preview','save','review'])
@pytest.mark.parametrize('fault',['analysis_review','quote_review','quote_bytes','scope_bytes','target','analysis_re_review'])
def test_changed_source_is_blocked_without_partial_decisions(clients,stage,fault):
    e,r,pid,parent,raw=setup(clients);b=submission(e,pid,raw);row=save(e,pid,b) if stage=='review' else None;path=saved=None
    if fault=='analysis_review':approve(r,pid,'procurement_analyses',parent,decision='reject')
    elif fault=='quote_review':approve(r,pid,'supplier_quotes',get(e,pid)['supplier_quotes'][0],decision='reject')
    elif fault=='analysis_re_review':approve(r,pid,'procurement_analyses',parent)
    elif fault=='target':mutate(pid,lambda p:p['boq'][0].update(features='合成当前清单变更'))
    else:
        name='supplier.txt' if fault=='quote_bytes' else 'synthetic-cost-scope.txt';doc=next(x for x in get(e,pid)['documents'] if x['name']==name);path=store.ROOT/'originals'/doc['hash'];saved=path.read_bytes();path.write_bytes(b'changed synthetic request source')
    before=get(e,pid);audit=e.get(f'/api/v1/projects/{pid}/audit').json()
    try:
        if stage=='preview' and fault=='analysis_re_review':assert preview(e,pid,raw)['preview_hash']!=b['preview_hash']
        elif stage=='preview':preview(e,pid,raw,409)
        elif stage=='save':save(e,pid,b,409)
        else:approve(r,pid,'procurement_requests',row,expected=409)
        assert get(e,pid)==before and e.get(f'/api/v1/projects/{pid}/audit').json()==audit
        if row:
            exported=e.get(base(pid)+'/'+row['id']+'/export');assert exported.status_code==200 and not exported.json()['current'] and exported.json()['record']==row
    finally:
        if path:path.write_bytes(saved)


def test_concurrent_same_analysis_requests_atomic_uniqueness_and_exact_retries(clients):
    e,r,pid,parent,raw=setup(clients);a=submission(e,pid,raw);b={**copy.deepcopy(a),'request_id':store.uid()}
    with ThreadPoolExecutor(max_workers=2) as pool:codes=list(pool.map(lambda x:e.post(base(pid),json=x).status_code,[a,b]))
    assert sorted(codes)==[200,409] and len(get(e,pid)['procurement_requests'])==1
    winner=a if codes[0]==200 else b;assert save(e,pid,winner)['id']==get(e,pid)['procurement_requests'][0]['id']
    changed=copy.deepcopy(winner);changed['input']['destination']='changed';save(e,pid,changed,409)


def test_roles_project_scope_and_author_cannot_review_after_role_change(clients):
    e,r,pid,parent,raw=setup(clients);b=submission(e,pid,raw)
    assert r.post(base(pid),json=b).status_code==403
    assert clients['other'].get(base(pid)).status_code==404 and clients['other'].get(base(pid)+'/options').status_code==404
    row=save(e,pid,b)
    assert clients['other'].get(base(pid)+'/'+row['id']+'/export').status_code==404
    cancel(r,pid,row,raw['evidence_id'],403)
    with store.transaction() as db:db.execute("UPDATE users SET role='reviewer' WHERE username='estimator'")
    approve(e,pid,'procurement_requests',row,expected=403)


@pytest.mark.parametrize('fault',[
 lambda b:b.update(not_ordered_confirmed=False),lambda b:b.update(not_ordered_confirmed=1),lambda b:b.update(reason=''),lambda b:b.update(evidence_id='foreign'),lambda b:b.update(object_hash='0'*64),lambda b:b.update(extra=1)
])
def test_invalid_cancel_never_releases_request_lock(clients,fault):
    e,r,pid,parent,raw=setup(clients);row=save(e,pid,submission(e,pid,raw));b=cancel_body(row,raw['evidence_id']);fault(b);before=get(e,pid)
    q=e.post(base(pid)+'/'+row['id']+'/cancel',json=b);assert q.status_code in (404,409,422)
    assert get(e,pid)==before and not e.get(base(pid)+'/options').json()['items'][0]['eligible']


def test_stale_request_can_cancel_with_new_proof_and_lost_cancel_bytes_do_not_reopen(clients):
    e,r,pid,parent,raw=setup(clients);row=save(e,pid,submission(e,pid,raw));approve(r,pid,'procurement_requests',row);row=get(e,pid)['procurement_requests'][0]
    doc=upload(e,pid,'合成新取消原件：本内部申请未向外下单，仅取消内部，不撤销订单或收货','request-cancel-only.txt','合同计价');eid=next(e['id'] for e in get(e,pid)['evidence'] if e['document_id']==doc['document']['id'])
    approve(r,pid,'procurement_analyses',get(e,pid)['procurement_analyses'][0],decision='reject')
    assert not e.get(base(pid)).json()['items'][0]['current'];event=cancel(e,pid,row,eid)
    d=next(d for d in get(e,pid)['documents'] if d['id']==doc['document']['id']);path=store.ROOT/'originals'/d['hash'];old=path.read_bytes();path.write_bytes(b'changed cancellation proof')
    try:
        view=e.get(base(pid)).json()['items'][0];assert view['phase']=='cancelled' and view['cancellation_issue'] and view['cancellation']==event
        assert get(e,pid)['procurement_requests'][0]==row
    finally:path.write_bytes(old)


def test_overdue_warning_and_today_quote_expiry_gate(clients,monkeypatch):
    e,r,pid,parent,raw=setup(clients);raw['required_by']=raw['request_date'];row=save(e,pid,submission(e,pid,raw))
    from backend import procurement_requests as requisitions
    monkeypatch.setattr(requisitions,'today',lambda:date.today()+timedelta(days=1));assert e.get(base(pid)).json()['items'][0]['overdue']
    monkeypatch.setattr(requisitions,'today',lambda:date.today()+timedelta(days=400));approve(r,pid,'procurement_requests',row,expected=409)
    assert not e.get(base(pid)).json()['items'][0]['current']
    assert not e.get(base(pid)+'/options').json()['items'][0]['eligible']


def test_record_or_cancel_tamper_blocks_new_reads_but_old_file_survives(clients):
    e,r,pid,parent,raw=setup(clients);row=save(e,pid,submission(e,pid,raw));f=e.get(base(pid)+'/'+row['id']+'/export');artifact=next(a for a in e.get(f'/api/v1/projects/{pid}/artifacts').json() if a['hash']==f.headers['x-artifact-hash'])
    event=cancel(e,pid,row,raw['evidence_id']);mutate(pid,lambda p:p['procurement_request_events'][0].update(reason='changed'))
    assert e.get(base(pid)).status_code==409 and e.get(base(pid)+'/'+row['id']+'/export').status_code==409
    assert e.get(f'/api/v1/projects/{pid}/artifacts/'+artifact['id']+'/file').content==f.content


def test_old_project_normalizes_empty_lists_and_operation_ids_cannot_cross_use(clients):
    e,r,pid,parent,raw=setup(clients);mutate(pid,lambda p:(p.pop('procurement_requests',None),p.pop('procurement_request_events',None)))
    assert e.get(base(pid)).json()['items']==[]
    b=submission(e,pid,raw);row=save(e,pid,b);c=cancel_body(row,raw['evidence_id']);c['request_id']=b['request_id'];cancel(e,pid,row,raw['evidence_id'],409,c)


def test_need_date_after_quote_period_is_a_plan_not_an_order_and_unreviewed_analysis_blocked(clients):
    e,r,pid,parent,raw=setup(clients);raw['required_by']=(date.today()+timedelta(days=730)).isoformat();assert preview(e,pid,raw)['result']['scenario']['total']=='1396.15'
    other=costs.save(e,pid,costs.submission(e,pid,parent['input']));raw.update(analysis_id=other['id'],analysis_hash=store.digest(other));preview(e,pid,raw,409)


def test_draft_cancel_is_closed_and_new_request_uses_new_human_choice(clients):
    e,r,pid,parent,raw=setup(clients);row=save(e,pid,submission(e,pid,raw));cancel(e,pid,row,raw['evidence_id'])
    assert e.get(base(pid)).json()['items'][0]['phase']=='cancelled'
    approve(r,pid,'procurement_requests',row,decision='request_more_info',expected=409)
    raw['quote_id']=parent['result']['scenarios'][1]['quote']['id'];new=save(e,pid,submission(e,pid,raw));assert new['number']!=row['number'] and new['result']['scenario']['quote']['supplier']=='合成供应商B'


def test_batch_review_failure_of_closed_sibling_rolls_back_first_application(clients):
    e,r,pid,parent,raw=setup(clients);first=save(e,pid,submission(e,pid,raw))
    second_parent=costs.save(e,pid,costs.submission(e,pid,parent['input']));approve(r,pid,'procurement_analyses',second_parent);second_parent=get(e,pid)['procurement_analyses'][-1]
    later={**raw,'analysis_id':second_parent['id'],'analysis_hash':store.digest(second_parent)};second=save(e,pid,submission(e,pid,later));cancel(e,pid,second,raw['evidence_id'])
    before=get(e,pid);url=f'/api/v1/projects/{pid}';audit=e.get(url+'/audit').json()
    body=dict(collection='procurement_requests',decision='approve',reason='合成逐申请核查',targets=[dict(object_id=x['id'],object_hash=store.digest(x)) for x in (first,second)])
    q=r.post(url+'/reviews/batch',json=body);assert q.status_code==409,q.text
    assert get(e,pid)==before and e.get(url+'/audit').json()==audit
