"""Mark interrupted jobs retryable after process restart; no silent loss."""
import json
from . import store


def recover_interrupted_jobs():
    with store.transaction() as c:
        for row in c.execute('SELECT data FROM projects').fetchall():
            p = json.loads(row['data'])
            changed = False
            for job in p.get('jobs', []):
                if job['state'] in ('queued', 'running'):
                    job.update(state='failed', message='服务上次停止时任务尚未完成，请重试；已保存原件和中间结果。')
                    for d in p['documents']:
                        if d['id'] == job['document_id'] and job.get('kind') != 'model_analysis':
                            d['parse_state'] = 'failed'
                    changed = True
            if changed:
                store.audit(c, p, {'username': 'system', 'role': 'system'}, 'JOBS_RECOVERED', {'reason': 'process_restart'})
                store.save(c, p)
