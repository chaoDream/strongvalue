"""Deterministic comparison against the pinned, stored previous CAD component."""
from decimal import Decimal
from fastapi import HTTPException
from . import store
from .engine import precise
from .route_takeoff import calculate

VERSION = 'dxf-revision-diff-1.0'
NOTICE = '按固定原构件的历史坐标与本次候选比较，差额不是新增计算书或清单；旧来源和审核不因此恢复有效。不同文件或解析任务不按句柄、近似坐标自动配对。'
FIELDS = ('name', 'coordinate_basis', 'measurement_basis', 'basis_evidence_id', 'multiplicity', 'rounding_digits')


def source_segments(row, result):
    entries = list(zip(row['source_objects'], row['source_ranges'], strict=True)) if row['kind'] == 'ASSEMBLY' else [(row, None)]
    from .dxf_assembly import rebuild
    raws = list({src.get('source_object', src)['id']: src.get('source_object', src) for src, _ in entries}.values())
    if rebuild(raws, row) != row:
        raise ValueError('inconsistent stored source mapping')
    segments = []
    for src, scope in entries:
        raw = src.get('source_object', src)
        indices = src['source_point_indices'] if src.get('fragment') else list(range(1, len(src['points']) + 1))
        if not src.get('fragment') and scope and scope['reverse']:
            indices.reverse()
        for start, end in zip(indices, indices[1:]):
            if abs(start-end) != 1:
                raise ValueError('nonconsecutive source vertices')
            identity = {k: raw[k] for k in ('document_id', 'document_hash', 'job_id', 'job_hash', 'id')}
            identity['segment_index'] = min(start, end)
            segments.append({'key': store.digest(identity), 'source': identity, 'handle': raw['handle'], 'layer': raw['layer'],
                             'from_point': start, 'to_point': end, 'position': len(segments)+1})
    if len({s['key'] for s in segments}) != len(segments):
        raise ValueError('duplicate source segments')
    for segment, measured in zip(segments, result['route_segments'], strict=True):
        segment['length_m'] = measured['length']
    return segments


@precise
def compare(old, row, body, result):
    previous = old['dxf_trace']['candidate']
    data = {'version': VERSION, 'previous_component_hash': store.digest(old),
            'before': {'component_id': old['id'], 'revision': old['revision'], 'state': old['state'], 'name': old['name'],
                       'document_id': previous['document_id'], 'document_hash': previous['document_hash']},
            'after': {'component_id': old['id'], 'revision': old['revision']+1, 'name': body['name'],
                      'document_id': row['document_id'], 'document_hash': row['document_hash']},
            'scope_changes': [{'field': k, 'before': old[k], 'after': body[k]} for k in FIELDS if old[k] != body[k]],
            'metrics': [], 'segments': [], 'status': 'complete', 'notice': NOTICE}
    try:
        from .dxf_geometry import route_body
        expected = route_body(previous, old['dxf_trace']['evidence_id'], **old['dxf_trace']['scope'])
        if any(old[k] != v for k, v in expected.items() if k != 'name'):
            raise ValueError('inconsistent historical geometry')
        before = calculate(old)
        a = source_segments(previous, before)
        b = source_segments(row, result)
    except (KeyError, TypeError, ValueError, HTTPException):
        # Explicit replacement remains possible even when historical geometry is damaged.
        data.update(status='unavailable', notice=NOTICE+' 原版历史坐标或映射不完整，无法可靠生成差额；须人工核对原版。')
        return data
    values = [('single_length', before['route_summary']['single_length'], result['route_summary']['single_length']),
              ('multiplicity', before['route_summary']['multiplicity'], result['route_summary']['multiplicity']),
              ('rounding_digits', before['route_summary']['rounding_digits'], result['route_summary']['rounding_digits']),
              ('unrounded_quantity', before['gross'], result['gross']), ('quantity', before['quantity'], result['quantity'])]
    data['metrics'] = [{'field': k, 'before': str(v), 'after': str(w), 'delta': str(Decimal(w)-Decimal(v))} for k, v, w in values]
    left = {s['key']: s for s in a}; right = {s['key']: s for s in b}
    for key in dict.fromkeys([s['key'] for s in a+b]):
        old_segment, new_segment = left.get(key), right.get(key)
        status = ('added' if not old_segment else 'removed' if not new_segment else
                  'reversed' if old_segment['from_point'] != new_segment['from_point'] else 'retained')
        data['segments'].append({'key': key, 'status': status, 'before': old_segment, 'after': new_segment})
    return data


def validate(p, comp, record):
    trace = comp['dxf_trace']; diff = trace.get('revision_comparison')
    if diff is None and not record.get('revision_comparison_hash'):
        return  # Existing releases retain their original trace and request identities.
    try:
        if store.digest(diff) != record.get('revision_comparison_hash'):
            raise ValueError()
        previous_hash = record['previous_component_hash']
        old = next((c for c in p['components']+p.get('component_history', []) if store.digest(c) == previous_hash), None)
        if not old or old['id'] != comp['id'] or old['revision']+1 != comp['revision']:
            raise ValueError()
        if diff != compare(old, trace['candidate'], comp, calculate(comp)):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise HTTPException(409, 'CAD修订差异或原版本快照已变化，请重新核对采用') from None


def report_tables(adoptions):
    revisions = [(x, x['trace']['revision_comparison']) for x in adoptions if x['trace'].get('revision_comparison')]
    if not revisions:
        return []
    labels = {'single_length': '单组长度（m）', 'multiplicity': '整组重复数量', 'rounding_digits': '总量修约位数',
              'unrounded_quantity': '未修约总量（m）', 'quantity': '修约后工程量（m）', 'name': '管线名称',
              'coordinate_basis': '共同WCS及高程基准', 'measurement_basis': '设计中心线计量口径', 'basis_evidence_id': '用途与计量依据'}
    states = {'added': '新增', 'removed': '移除', 'reversed': '反向采用', 'retained': '保留'}
    review_states = {'draft': '待复核', 'approved': '已审定', 'stale': '已失效', 'rejected': '已拒绝', 'needs_info': '待补件'}
    def title(x, d):
        return x.get('component_name', x['component_id'])+' / V'+str(d['before']['revision'])+'→V'+str(d['after']['revision'])
    def desc(s):
        return '未采用' if not s else '第'+str(s['position'])+'段 / 原P'+str(s['from_point'])+'→P'+str(s['to_point'])+' / '+s['length_m']+'m'
    quantities, scopes, segments = [], [], []
    for x, d in revisions:
        name = title(x, d)
        original_state = review_states.get(d['before']['state'], d['before']['state'])
        for m in d['metrics']:
            quantities.append([name, labels[m['field']], m['before'], m['after'], m['delta'], original_state+' / '+d['previous_component_hash']])
        if not d['metrics']:
            quantities.append([name, '无法可靠比较', '原历史数据不完整', '本次计算见管线明细', '待人工核对', d['previous_component_hash']])
        scopes.append([name, '比较边界', d['notice'], '原版状态及哈希：'+original_state+' / '+d['previous_component_hash']])
        scopes.append([name, '原文件', d['before']['document_id']+' / '+d['before']['document_hash'], d['after']['document_id']+' / '+d['after']['document_hash']])
        scopes.extend([[name, labels[v['field']], str(v['before']), str(v['after'])] for v in d['scope_changes']])
        for v in d['segments']:
            s = v['after'] or v['before']
            segments.append([name, states[v['status']], s['handle']+' / '+s['layer'], desc(v['before']), desc(v['after']),
                             s['source']['document_id']+' / 原对象 '+s['source']['id']+' / 原段 '+str(s['source']['segment_index'])+' / 段身份 '+v['key']])
    result = [('DXF修订数量对照', ['管线 / 修订', '比较项', '修订前', '修订后', '增减差额', '原版状态 / 校验值'], quantities),
              ('DXF修订口径变化', ['管线 / 修订', '比较项', '修订前 / 比较边界', '修订后 / 原版校验值'], scopes)]
    if segments:
        result.append(('DXF修订原始管段', ['管线 / 修订', '变化', '原对象 / 图层', '修订前', '修订后', '原文件ID / 原对象 / 管段身份'], segments))
    return result
