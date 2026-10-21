"""Derived, project-scoped unresolved pricing work; no fake prices or extra mutable task state."""
from . import store


def tasks(p):
    result = []
    for b in p['boq']:
        base = {'boq_id': b['id'], 'boq_name': b['name'], 'boq_hash': store.digest(b)}
        if not b.get('quotas'):
            result.append({**base, 'key': b['id']+':quota', 'kind': 'quota_match', 'state': 'pending',
                           'message': '待匹配有授权依据的定额', 'resource': None})
        for qi, quota in enumerate(b.get('quotas', [])):
            for ri, r in enumerate(quota['resources']):
                if r.get('pricing_status') != 'unpriced':
                    continue
                result.append({**base, 'key': f"{b['id']}:{qi}:{ri}", 'kind': 'price_inquiry', 'state': 'pending',
                               'message': '待取得当前适用且获授权的资源价格，补价后须重新复核',
                               'quota_code': quota['code'], 'quota_index': qi, 'resource_index': ri,
                               'resource_hash': store.digest(r), 'resource': r})
    return result
