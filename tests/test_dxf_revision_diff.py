import copy
import io
from decimal import Decimal, localcontext
import pytest
from openpyxl import load_workbook
from pypdf import PdfReader
from backend import store
from backend.dxf_revision_diff import compare
from tests.test_dxf_revision import recover, resolve, parts, request, candidate_url
from tests.test_dxf_trace_view import saved
from tests.test_dxf_routes import preview, adopt, mutate, setup, polyline, read, body
from tests.test_dxf_fragment_assembly import ranged
from tests.test_workflow import get, approve, priced_project, upload


def revision(clients, change='expand'):
    c, r, pid, did, old, url = saved(clients)
    data = recover(c, url, old); selection = parts(data)
    if change == 'expand': selection[0]['from_point'] = 1
    elif change == 'shrink': selection = selection[1:]
    elif change == 'reverse':
        selection.reverse()
        for s in selection: s['reverse'] = not s['reverse']
    url = candidate_url(pid, did); row = resolve(c, url, selection)
    req = request(data, row, calibration_evidence_id=old['dxf_trace']['proofs']['calibration']['evidence_id'],
                  calibration={'from_point': 1, 'to_point': 2, 'distance': '13' if change == 'expand' else '5', 'unit': 'm', 'tolerance': '0'},
                  reason='合成：重新核对修订原始管段与计量口径', source_confirmed=True)
    return c, r, pid, old, url, row, req


@pytest.mark.parametrize('change,quantity,delta,counts', [
    ('expand', '56.000', '26.000', {'added': 1, 'retained': 3}),
    ('shrink', '10.000', '-20.000', {'removed': 2, 'retained': 1}),
    ('reverse', '30.000', '0.000', {'reversed': 3}),
    ('same', '30.000', '0.000', {'retained': 3}),
])
def test_comparison_uses_original_segment_identities_and_is_readonly(clients, change, quantity, delta, counts):
    c, r, pid, old, url, row, req = revision(clients, change); before = get(c, pid)
    view = preview(c, url, row, req); diff = view['revision_comparison']
    assert get(c, pid) == before
    assert diff['status'] == 'complete' and diff['previous_component_hash'] == store.digest(old)
    assert diff['before']['revision'] == 1 and diff['after']['revision'] == 2
    metrics = {m['field']: m for m in diff['metrics']}
    assert metrics['quantity'] == {'field': 'quantity', 'before': '30.000', 'after': quantity, 'delta': delta}
    assert {s: sum(v['status'] == s for v in diff['segments']) for s in counts} == counts
    assert len(diff['segments']) == sum(counts.values())
    if change == 'expand':
        added = next(v['after'] for v in diff['segments'] if v['status'] == 'added')
        assert added['handle'] == 'A' and (added['from_point'], added['to_point'], added['length_m']) == (1, 2, '13')
    if change == 'reverse':
        assert all(v['before']['from_point'] == v['after']['to_point'] for v in diff['segments'])
        assert [v['after']['position'] for v in diff['segments']] == [3, 2, 1]
    saved_result = adopt(c, url, row, req, view)
    new = saved_result['component']; record = saved_result['adoption']
    assert new['dxf_trace']['revision_comparison'] == diff == record['trace']['revision_comparison']
    assert record['revision_comparison_hash'] == store.digest(diff)
    assert get(c, pid)['component_history'] == [old]
    assert adopt(c, url, row, req, view)['duplicate']
    approve(r, pid, 'components', new)
    assert c.post(f'/api/v1/projects/{pid}/takeoffs/calculate').json()[0]['quantity'] == quantity


@pytest.mark.parametrize('field,value', [('multiplicity', '3'), ('rounding_digits', 0), ('name', '合成新名称'),
    ('coordinate_basis', '合成：明确修订共同原点与高程说明'), ('measurement_basis', '合成：重新确认计量边界')])
def test_non_geometry_changes_are_explicit_even_with_same_segment_coverage(clients, field, value):
    c, r, pid, old, url, row, req = revision(clients, 'same'); req[field] = value
    view = preview(c, url, row, req); diff = view['revision_comparison']
    assert diff['scope_changes'] == [{'field': field, 'before': old[field], 'after': value}]
    assert all(s['status'] == 'retained' for s in diff['segments'])
    if field == 'multiplicity': assert next(m for m in diff['metrics'] if m['field'] == 'quantity')['delta'] == '15.000'
    new = adopt(c, url, row, req, view)['component']; approve(r, pid, 'components', new)


def test_decimal_delta_preserves_more_than_javascript_number_precision(clients):
    c, r, pid, old, url, row, req = revision(clients)
    fake_old = copy.deepcopy(old); before = copy.deepcopy(fake_old['preview']); after = copy.deepcopy(before)
    before['quantity'] = '999999999999999.123456789012'; after['quantity'] = '999999999999999.123456789013'
    # Exercise the actual pure comparator with precise quantities from the server calculator.
    from unittest.mock import patch
    with localcontext() as ctx, patch('backend.dxf_revision_diff.calculate', return_value=before):
        ctx.prec = 12
        diff = compare(fake_old, fake_old['dxf_trace']['candidate'], fake_old, after)
    assert Decimal(next(m for m in diff['metrics'] if m['field'] == 'quantity')['delta']) == Decimal('0.000000000001')


@pytest.mark.parametrize('fault', ['metric', 'segment', 'scope', 'previous_hash', 'history', 'remove_comparison', 'record_hash', 'consistent_forgery'])
def test_saved_comparison_and_pinned_previous_snapshot_are_revalidated(clients, fault):
    c, r, pid, old, url, row, req = revision(clients)
    new = adopt(c, url, row, req, preview(c, url, row, req))['component']
    def change(p):
        comp = p['components'][0]; record = p['dxf_route_adoptions'][-1]; diff = comp['dxf_trace']['revision_comparison']
        if fault == 'history': p['component_history'][0]['state'] = 'stale'
        elif fault == 'record_hash': record['revision_comparison_hash'] = '0'*64
        elif fault == 'remove_comparison':
            comp['dxf_trace'].pop('revision_comparison'); record['trace'].pop('revision_comparison')
        else:
            if fault in ('metric', 'consistent_forgery'): diff['metrics'][-1]['delta'] = '999'
            elif fault == 'segment': diff['segments'][0]['status'] = 'removed'
            elif fault == 'scope': diff['scope_changes'].append({'field': 'name', 'before': 'fake', 'after': 'fake'})
            else: diff['previous_component_hash'] = '0'*64
            record['trace'] = copy.deepcopy(comp['dxf_trace'])
            if fault == 'consistent_forgery': record['revision_comparison_hash'] = store.digest(diff)
    mutate(pid, change)
    current = get(c, pid)['components'][0]; approve(r, pid, 'components', current, expected=409)
    res = c.get(f'/api/v1/projects/{pid}/components/{new["id"]}/dxf-source-view', params={'revision': 2, 'source_stamp': new['source_stamp']})
    assert res.status_code == 409


def test_explicit_replacement_of_invalid_old_source_does_not_match_reused_handles(clients):
    c, r, pid, old, url, row, req = revision(clients, 'same')
    old_did = old['dxf_trace']['candidate']['document_id']
    c, r, pid, did, eid, new_url = setup(clients, polyline(), pid=pid)
    upload(c, pid, '合成独立新原件依据：已确认WCS及高程，P1至P2为13m，零容差，中心线重复2、总量三位。', 'replacement-basis.txt')
    eid = get(c, pid)['evidence'][-1]['id']
    mutate(pid, lambda p: next(j for j in p['jobs'] if j['document_id'] == old_did).update(state='failed'))
    old = get(c, pid)['components'][0]; raw = read(c, new_url)['candidates'][0]
    req = body(raw, eid, request_id='synthetic-explicit-replacement-01', component_id=old['id'], component_hash=store.digest(old))
    view = preview(c, new_url, raw, req); diff = view['revision_comparison']
    assert diff['before']['document_id'] != diff['after']['document_id']
    assert sum(v['status'] == 'removed' for v in diff['segments']) == 3
    assert sum(v['status'] == 'added' for v in diff['segments']) == 2
    assert not any(v['status'] in ('retained', 'reversed') for v in diff['segments'])
    new = adopt(c, new_url, raw, req, view)['component']; approve(r, pid, 'components', new)


@pytest.mark.parametrize('fault', ['missing_indices', 'out_of_range', 'selection', 'coordinates'])
def test_broken_historical_mapping_is_marked_unavailable_and_can_be_explicitly_replaced(clients, fault):
    c, r, pid, old, url, row, req = revision(clients, 'same')
    def damage(p):
        comp = p['components'][0]; first = comp['dxf_trace']['candidate']['source_objects'][0]
        if fault == 'missing_indices': first['source_point_indices'] = [99]
        elif fault == 'out_of_range': first['source_point_indices'] = [99, 100, 101]
        elif fault == 'selection': comp['dxf_trace']['candidate']['selection'][0]['from_point'] = 1
        else: comp['route_points'][0]['parameters']['x'] = '3001'
    mutate(pid, damage)
    old = get(c, pid)['components'][0]; req['component_hash'] = store.digest(old)
    view = preview(c, url, row, req); diff = view['revision_comparison']
    assert diff['status'] == 'unavailable' and diff['metrics'] == diff['segments'] == []
    new = adopt(c, url, row, req, view)['component']; approve(r, pid, 'components', new)


def test_comparison_is_server_owned_and_preview_hash_binds_exact_diff(clients):
    c, r, pid, old, url, row, req = revision(clients)
    before = get(c, pid); view = preview(c, url, row, req)
    assert preview(c, url, row, {**req, 'revision_comparison': view['revision_comparison']}, expected=422)
    forged = copy.deepcopy(view); forged.pop('preview_hash'); forged['revision_comparison']['metrics'][-1]['delta'] = '0'
    adopt(c, url, row, req, {'preview_hash': store.digest(forged)}, expected=409)
    assert get(c, pid) == before


def test_second_revision_uses_current_fixed_snapshot_and_keeps_first_comparison(clients):
    c, r, pid, old, url, row, req = revision(clients)
    new = adopt(c, url, row, req, preview(c, url, row, req))['component']; approve(r, pid, 'components', new)
    new = get(c, pid)['components'][0]
    req.update(request_id='synthetic-third-cad-revision-001', component_hash=store.digest(new), multiplicity='3')
    view = preview(c, url, row, req); diff = view['revision_comparison']
    assert diff['before']['revision'] == 2 and diff['before']['state'] == 'approved'
    assert next(m for m in diff['metrics'] if m['field'] == 'quantity')['delta'] == '28.000'
    third = adopt(c, url, row, req, view)['component']; approve(r, pid, 'components', third)
    p = get(c, pid)
    assert p['component_history'][1] == new and p['component_history'][0] == old
    assert p['dxf_route_adoptions'][1]['trace'] == new['dxf_trace']


@pytest.mark.parametrize('fmt', ['json', 'xlsx', 'pdf'])
def test_comparison_exports_all_formats_and_does_not_leak_into_old_quote(clients, fmt):
    c, r, pid, _, quote = priced_project(clients)
    c, r, pid, did, eid, url, objects, selected, row, req = ranged(clients, pid=pid)
    first = adopt(c, url, row, req, preview(c, url, row, req))['component']
    assert 'revision_comparison' not in first['dxf_trace']
    selected[0]['from_point'] = 1; row = resolve(c, url, selected)
    req.update(request_id='synthetic-diff-report-revision-01', object_hash=row['object_hash'], segments=row['selection'], component_id=first['id'], component_hash=store.digest(first),
               calibration={'from_point': 1, 'to_point': 2, 'distance': '13', 'unit': 'm', 'tolerance': '0'})
    adopt(c, url, row, req, preview(c, url, row, req))
    path = f'/api/v1/projects/{pid}/exports'; res = c.get(path, params={'format': fmt, 'unpriced': True})
    assert res.status_code == 200, res.text
    titles = ['DXF修订数量对照', 'DXF修订口径变化', 'DXF修订原始管段']
    if fmt == 'json':
        tables = res.json()['tables']; assert all(any(t[0] == title for t in tables) for title in titles)
        assert '26.000' in str(tables) and '第1段 / 原P1→P2 / 13m' in str(tables)
    elif fmt == 'xlsx': assert all(t in load_workbook(io.BytesIO(res.content)).sheetnames for t in titles)
    else:
        text = ''.join(p.extract_text() for p in PdfReader(io.BytesIO(res.content)).pages)
        assert all(t in text for t in titles) and '26.000' in text
    historic = c.get(path, params={'format': 'json', 'quote_id': quote['id']}).json()
    assert historic['quote'] == quote and not any(t[0] in titles for t in historic['tables'])
