import hashlib
import io
import json
import zipfile
import subprocess
import sys
import pytest
from backend import store, delivery
from scripts.verify_delivery import verify
from tests.test_workflow import priced_project, get, approve, upload, create


def archive(response):
    assert response.status_code == 200, response.text if response.status_code != 200 else ''
    return zipfile.ZipFile(io.BytesIO(response.content))


def test_bundle_selected_snapshot_originals_reports_audit_and_offline_verify(clients, tmp_path):
    e, r, pid, _, q = priced_project(clients)
    approve(r, pid, 'quotes', q)
    base = f'/api/v1/projects/{pid}'
    check = e.get(base+'/delivery-check?quote_id='+q['id']).json()
    assert check['valid'] and check['approved'] and check['unique_original_count'] == 1
    response = e.get(base+'/exports?format=zip&quote_id='+q['id'])
    z = archive(response)
    assert response.headers['content-type'] == 'application/zip'
    manifest = json.loads(z.read('manifest.json'))
    assert '试点范围审核成果' in manifest['status'] and manifest['quote_hash'] == q['hash']
    assert {'reports/result.pdf', 'reports/result.xlsx', 'reports/result.json', 'quote.json', 'audit.json', 'verify_delivery.py'} <= set(z.namelist())
    assert json.loads(z.read('quote.json')) == q
    document = get(e, pid)['documents'][0]
    assert hashlib.sha256(z.read('originals/'+document['hash'])).hexdigest() == document['hash']
    assert len([n for n in z.namelist() if n.startswith('originals/')]) == 1
    assert b'%PDF' == z.read('reports/result.pdf')[:4]
    assert json.loads(z.read('reports/result.json'))['quote'] == q
    path = tmp_path/'bundle.zip'; path.write_bytes(response.content)
    assert verify(path)['valid']
    standalone = tmp_path/'verify_delivery.py'; standalone.write_bytes(z.read('verify_delivery.py'))
    verified = subprocess.run([sys.executable, str(standalone), str(path)], capture_output=True, text=True)
    assert verified.returncode == 0 and json.loads(verified.stdout)['valid']
    item = e.get(base+'/artifacts').json()[0]
    assert item['format'] == 'zip' and item['hash'] == response.headers['x-artifact-hash']
    assert e.get(base+'/artifacts/'+item['id']+'/verify').json()['valid']
    assert e.get(base+'/artifacts/'+item['id']+'/file').content == response.content
    assert e.get(base+'/audit').json()[0]['action'] == 'EXPORT_COMPLETED'


def test_bundle_old_version_survives_source_replacement_with_draft_status(clients, tmp_path):
    e, r, pid, _, q = priced_project(clients); base = f'/api/v1/projects/{pid}'
    approve(r, pid, 'quotes', q)
    first = e.get(base+'/exports?format=zip&quote_id='+q['id']); archive(first)
    item = e.get(base+'/artifacts').json()[0]
    upload(e, pid, '全新版本的合成图纸文字。', name='fixture.txt')
    upload(e, pid, '只属于新版本的附件。', name='new-only.txt')
    current = e.get(base+'/exports?format=zip&quote_id='+q['id']); z = archive(current)
    m = json.loads(z.read('manifest.json'))
    assert '未完成成果草稿' in m['status']
    assert [d['name'] for d in m['documents']] == ['fixture.txt']
    assert b'new-only' not in z.read('manifest.json')
    assert json.loads(z.read('quote.json')) == q
    assert e.get(base+'/artifacts/'+item['id']+'/file').content == first.content
    path = tmp_path/'stale.zip'; path.write_bytes(current.content)
    assert verify(path)['valid']


def test_bundle_missing_or_corrupt_original_blocks_without_artifact(clients):
    e, _, pid, _, q = priced_project(clients); base = f'/api/v1/projects/{pid}'
    d = get(e, pid)['documents'][0]; path = store.ROOT/'originals'/d['hash']
    old = path.read_bytes(); path.write_bytes(b'corrupted')
    check = e.get(base+'/delivery-check').json()
    assert not check['valid'] and any('原件' in i for i in check['issues'])
    assert e.get(base+'/exports?format=zip').status_code == 409
    assert e.get(base+'/artifacts').json() == []
    path.write_bytes(old); path.unlink()
    assert not e.get(base+'/delivery-check').json()['valid']
    assert e.get(base+'/exports?format=zip').status_code == 409


def test_bundle_snapshot_and_audit_tampering_and_size_limit(clients, monkeypatch):
    e, _, pid, _, q = priced_project(clients); base = f'/api/v1/projects/{pid}'
    monkeypatch.setattr(delivery, 'MAX_ORIGINAL_BYTES', 1)
    assert e.get(base+'/exports?format=zip').status_code == 409
    monkeypatch.setattr(delivery, 'MAX_ORIGINAL_BYTES', 100*1024*1024)
    with store.transaction() as c:
        row = c.execute('SELECT data FROM projects WHERE id=?', (pid,)).fetchone()
        p = json.loads(row['data']); p['quotes'][0]['total'] = '0.01'; store.save(c, p)
    assert any('报价快照' in i for i in e.get(base+'/delivery-check').json()['issues'])
    with store.transaction() as c:
        p['quotes'][0] = q; store.save(c, p)
        c.execute('DROP TRIGGER audit_no_update')
        c.execute("UPDATE audit SET hash='broken' WHERE project_id=? AND seq=(SELECT MIN(seq) FROM audit WHERE project_id=?)", (pid,pid))
    assert any('审计链' in i for i in e.get(base+'/delivery-check').json()['issues'])
    assert e.get(base+'/exports?format=zip').status_code == 409


def test_artifact_corruption_and_tenant_scoping(clients):
    e, _, pid, _, q = priced_project(clients); base = f'/api/v1/projects/{pid}'
    response = e.get(base+'/exports?format=json'); assert response.status_code == 200
    item = e.get(base+'/artifacts').json()[0]; endpoint = base+'/artifacts/'+item['id']
    other = clients['other']
    for route in ['/delivery-check', '/exports?format=zip', '/artifacts/'+item['id']+'/verify', '/artifacts/'+item['id']+'/file']:
        assert other.get(base+route).status_code == 404
    foreign = create(e)['id']
    assert e.get(f'/api/v1/projects/{foreign}/artifacts/{item["id"]}/verify').status_code == 404
    path = store.ROOT/'exports'/item['hash']; path.write_bytes(b'damaged')
    assert not e.get(endpoint+'/verify').json()['valid']
    assert e.get(endpoint+'/file').status_code == 409
    path.unlink()
    assert e.get(endpoint+'/verify').json()['actual_hash'] is None
    assert e.get(endpoint+'/file').status_code == 409


def test_unpriced_bundle_and_custom_template(clients, tmp_path):
    e = clients['estimator']; pid = create(e)['id']; base = f'/api/v1/projects/{pid}'
    upload(e, pid, name='../../untrusted.txt')
    template = clients['admin'].post('/api/v1/organization/report-templates', json={
        'name':'测试交付模板', 'organization':'测试编制单位', 'cover_note':'仅合成测试', 'footer':'测试', 'accent':'244B78'}).json()
    response = e.get(base+'/exports?format=zip&template_id='+template['id']); z = archive(response)
    manifest = json.loads(z.read('manifest.json'))
    assert manifest['quote_id'] is None and '未完成成果草稿' in manifest['status']
    assert 'unpriced-snapshot.json' in z.namelist() and 'quote.json' not in z.namelist()
    assert all('..' not in name for name in z.namelist())
    assert json.loads(z.read('reports/result.json'))['report_template']['id'] == template['id']
    path = tmp_path/'unpriced.zip'; path.write_bytes(response.content); assert verify(path)['valid']
    assert e.get(base+'/delivery-check?quote_id=missing').status_code == 404


def test_offline_verifier_rejects_tampering_extra_and_unsafe_members(clients, tmp_path):
    e, _, pid, _, q = priced_project(clients)
    z = archive(e.get(f'/api/v1/projects/{pid}/exports?format=zip'))
    original = {n:z.read(n) for n in z.namelist()}
    for kind in ['tamper', 'extra', 'traversal', 'duplicate', 'missing', 'bad_json']:
        files = dict(original)
        if kind == 'tamper': files['reports/result.pdf'] = b'changed'
        if kind == 'extra': files['unexpected.txt'] = b'extra'
        if kind == 'traversal': files['../escape'] = b'x'
        if kind == 'missing': del files['audit.json']
        if kind == 'bad_json': files['manifest.json'] = b'[]'
        path = tmp_path/(kind+'.zip')
        with zipfile.ZipFile(path,'w') as out:
            for name, data in files.items(): out.writestr(name,data)
            if kind == 'duplicate':
                with pytest.warns(UserWarning, match='Duplicate name'):
                    out.writestr('quote.json',files['quote.json'])
        assert not verify(path)['valid'],kind


def test_original_changed_during_bundle_generation_rolls_back(clients, monkeypatch):
    e, _, pid, _, q = priced_project(clients); base = f'/api/v1/projects/{pid}'
    doc = get(e, pid)['documents'][0]
    original_export = delivery.export_report
    def changed_source(*args, **kwargs):
        result = original_export(*args, **kwargs)
        (store.ROOT/'originals'/doc['hash']).write_bytes(b'changed during report render')
        return result
    monkeypatch.setattr(delivery, 'export_report', changed_source)
    before = e.get(base+'/audit').json()
    response = e.get(base+'/exports?format=zip')
    assert response.status_code == 409 and '打包时原件发生变化' in response.text
    assert e.get(base+'/artifacts').json() == []
    assert e.get(base+'/audit').json() == before
