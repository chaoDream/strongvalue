"""Synthetic stock handover batch reviews: no movement, atomic failure and custody limits."""
import copy
import pytest
from backend import store
from tests import test_stock_partial_issue as bars, test_stock_offcut_issues as offcuts
from tests.test_stock_inventory import base, read, common
from tests.test_stock_operations import execute
from tests.test_workflow import get
from tests.test_workflows import batch_body


@pytest.fixture(params=['stock_operations','stock_offcut_issues'])
def handovers(clients,request):
    collection=request.param
    if collection=='stock_operations':
        e,r,pid,comp,eid,res,stocks=bars.setup(clients)
        make=lambda:bars.submit(e,pid,bars.submission(e,pid,bars.raw(e,pid,eid,res)))
        move=lambda row:execute(e,pid,row)
        def cancel(row):
            q=e.post(base(pid)+'/events',json={**common(e,pid),'operation':'cancel','target_id':row['id'],'object_hash':store.digest(row),'basis':'合成取消未执行单','evidence_id':eid})
            assert q.status_code==200,q.text
    else:
        e,r,pid,comp,c,eid,recovery,lots,res=offcuts.setup(clients)
        make=lambda:offcuts.submit(e,pid,offcuts.submission(e,pid,offcuts.raw(res,eid)))
        move=lambda row:offcuts.move(e,pid,row)
        cancel=lambda row:offcuts.move(e,pid,row,'cancel',eid)
    return e,r,pid,collection,make,move,cancel


def test_review_keeps_stock_engineering_old_export_and_individual_audit(handovers):
    e,r,pid,col,make,move,cancel=handovers;a,b=make(),make();url=f'/api/v1/projects/{pid}'
    before=get(e,pid);view=read(e,pid);frozen=e.get(base(pid)+'/export')
    audit=e.get(url+'/audit').json();body=batch_body(r,pid,col,[a,b])
    q=r.post(url+'/reviews/batch',json=body);assert q.status_code==200,q.text
    records=q.json()['reviews'];assert len(records)==2 and len({x['id'] for x in records})==2
    assert [x['object_hash'] for x in records]==[x['object_hash'] for x in body['targets']]
    after=get(e,pid);assert after['stock_events']==before['stock_events']
    for key in ('components','takeoffs','boq','quotes','report_approval','stock_receipts','stock_reservations','stock_offcut_reservations'):assert after[key]==before[key]
    # Balance sections contain no actual handover until a separate execution.
    afterview=read(e,pid)
    assert [(x['on_hand'],x['reserved']) for x in afterview['receipts']]==[(x['on_hand'],x['reserved']) for x in view['receipts']]
    assert afterview['processing']['lots']==view['processing']['lots']
    assert not {x['object']['id'] for x in r.get(url+'/review-batch-targets',params={'collection':col}).json()['targets']} & {a['id'],b['id']}
    added=e.get(url+'/audit').json()[:3];assert len(e.get(url+'/audit').json())==len(audit)+3
    assert [x['action'] for x in added].count('REVIEW_DECISION')==2
    assert added[0]['action']=='REVIEW_BATCH_COMPLETED'
    artifact=next(x for x in e.get(url+'/artifacts').json() if x['purpose']=='stock_inventory')
    assert e.get(url+'/artifacts/'+artifact['id']+'/file').content==frozen.content
    row=next(x for x in after[col] if x['id']==a['id']);move(row)
    assert len(get(e,pid)['stock_events'])==len(before['stock_events'])+1


@pytest.mark.parametrize('failure',['hash','cancelled','bytes','source_balance'])
def test_any_failure_rolls_back_all_decisions_and_audit(handovers,failure):
    e,r,pid,col,make,move,cancel=handovers;a,b=make(),make();url=f'/api/v1/projects/{pid}'
    body=batch_body(r,pid,col,[a,b]);path=None;saved=None
    if failure=='hash':body['targets'][1]['object_hash']='stale'
    elif failure=='cancelled':cancel(b)
    elif failure=='bytes':
        doc=get(e,pid)['documents'][-1];path=store.ROOT/'originals'/doc['hash'];saved=path.read_bytes();path.write_bytes(b'changed synthetic original')
    else:
        # A competing larger handover consumes the source remainder before submission.
        current=get(e,pid);res=current['stock_reservations'][-1] if col=='stock_operations' else current['stock_offcut_reservations'][-1]
        eid=a['input']['evidence_id']
        if col=='stock_operations':other=bars.ready(e,r,pid,bars.raw(e,pid,eid,res,3))
        else:other=offcuts.ready(e,r,pid,offcuts.raw(res,eid,2))
        move(other)
    before=copy.deepcopy(get(e,pid));audit=e.get(url+'/audit').json()
    try:
        q=r.post(url+'/reviews/batch',json=body);assert q.status_code==409,q.text
        assert get(e,pid)==before and e.get(url+'/audit').json()==audit
        if failure=='cancelled':assert b['id'] not in [x['object']['id'] for x in r.get(url+'/review-batch-targets',params={'collection':col}).json()['targets']]
    finally:
        if path:path.write_bytes(saved)


@pytest.mark.parametrize('decision',['reject','request_more_info'])
def test_nonapproval_and_repeated_request_never_move_stock(handovers,decision):
    e,r,pid,col,make,move,cancel=handovers;a,b=make(),make();url=f'/api/v1/projects/{pid}'
    body=batch_body(r,pid,col,[a,b],decision);before=get(e,pid)
    q=r.post(url+'/reviews/batch',json=body);assert q.status_code==200,q.text
    after=get(e,pid);assert after['stock_events']==before['stock_events']
    audit=e.get(url+'/audit').json()
    assert r.post(url+'/reviews/batch',json=body).status_code==409
    assert get(e,pid)==after and e.get(url+'/audit').json()==audit


def test_permissions_duplicate_selection_and_missing_basis(handovers,clients):
    e,r,pid,col,make,move,cancel=handovers;a,b=make(),make();url=f'/api/v1/projects/{pid}'
    body=batch_body(r,pid,col,[a,b]);before=get(e,pid)
    assert e.post(url+'/reviews/batch',json=body).status_code==403
    assert clients['other'].post(url+'/reviews/batch',json=body).status_code==403
    assert clients['other'].get(url+'/review-batch-targets',params={'collection':col}).status_code==404
    body['targets']*=2;assert r.post(url+'/reviews/batch',json=body).status_code==422
    body=batch_body(r,pid,col,[a,b]);body['reason']=' ';assert r.post(url+'/reviews/batch',json=body).status_code==422
    assert get(e,pid)==before


def test_approvals_never_multiply_shared_source_allowance(handovers):
    e,r,pid,col,make,move,cancel=handovers
    allowance=3 if col=='stock_operations' else 2
    rows=[make() for _ in range(allowance+1)];url=f'/api/v1/projects/{pid}'
    q=r.post(url+'/reviews/batch',json=batch_body(r,pid,col,rows));assert q.status_code==200,q.text
    approved={x['id']:x for x in get(e,pid)[col]}
    for row in rows[:allowance]:move(approved[row['id']])
    before=get(e,pid);audit=e.get(url+'/audit').json()
    if col=='stock_operations':execute(e,pid,approved[rows[-1]['id']],expected=409)
    else:offcuts.move(e,pid,approved[rows[-1]['id']],expected=409)
    assert get(e,pid)==before and e.get(url+'/audit').json()==audit
