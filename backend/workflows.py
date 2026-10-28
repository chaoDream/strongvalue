"""Atomic review batches, auditable price changes and quantity allocation checks."""
import copy
from datetime import date
from decimal import Decimal, localcontext

from fastapi import APIRouter, Depends, HTTPException
from . import main as core, store
from .engine import calculate, number, quantity_number, money
from .extensions import links

router = APIRouter(prefix='/api/v1')


def allocation_diagnostics(p):
    issues, quantities = [], []
    takeoffs = {t['id']: t for t in p['takeoffs']}
    def issue(kind, message, **details):
        issues.append({'kind': kind, 'message': message, 'blocking': True, **details})
    with localcontext() as ctx:
        ctx.prec = 80
        assigned = {tid: Decimal(0) for tid in takeoffs}
        for b in p['boq']:
            contributions = links(b)
            if sum((quantity_number(link['quantity']) for link in contributions), Decimal(0)) != quantity_number(b['quantity']):
                issue('link_total', b['name']+'清单数量与来源贡献量不闭合', boq_id=b['id'])
            if not b.get('code') or not b.get('features'):
                issue('missing_description', b['name']+'缺少有依据的清单编码或项目特征', boq_id=b['id'])
            for link in contributions:
                tid = link['takeoff_id']
                if tid not in takeoffs:
                    issue('missing_source', b['name']+'引用了已失效的算量书', boq_id=b['id'], takeoff_id=tid)
                    continue
                if b['unit'] != takeoffs[tid]['unit']:
                    issue('unit_mismatch', b['name']+'清单与来源计量单位不一致', boq_id=b['id'], takeoff_id=tid)
                assigned[tid] += quantity_number(link['quantity'])
        for tid, t in takeoffs.items():
            available = quantity_number(t['quantity'])
            delta = available-assigned[tid]
            quantities.append({'takeoff_id': tid, 'name': t['name'], 'unit': t['unit'], 'quantity': str(available),
                               'assigned': str(assigned[tid]), 'remaining': str(delta), 'state': t['state']})
            if delta > 0:
                issue('unallocated', t['name']+'仍有 '+str(delta)+' '+t['unit']+' 未编入清单', takeoff_id=tid)
            elif delta < 0:
                issue('overallocated', t['name']+'重复分配了 '+str(-delta)+' '+t['unit'], takeoff_id=tid)
    return {'passed': not issues, 'issues': issues, 'quantities': quantities,
            'notice': '核对已登记算量书的数量分配、单位与描述；不能发现未建模构件或验证官方编码。不同计量用途须分别建立有依据的算量书。'}


@router.get('/projects/{pid}/boq/diagnostics')
def boq_diagnostics(pid: str, a=Depends(core.actor)):
    with store.connect() as c:
        return allocation_diagnostics(core.project(c, pid, a))


@router.get('/projects/{pid}/review-batch-targets')
def review_batch_targets(pid: str, collection: str, a=Depends(core.actor)):
    if collection not in core.REVIEW_COLLECTIONS:
        raise HTTPException(422, '审核对象类型无效')
    with store.connect() as c:
        p = core.project(c, pid, a)
        objects = list(p[collection].values()) if collection == 'coverage' else p[collection]
        objects = [x for x in objects if x.get('state') not in ('approved', 'rejected', 'superseded', 'cancelled')]
        if collection in ('drawing_sheets','drawing_references'):
            from .drawing_register import current
            ids={r['id'] for r in current(p,collection)}
            objects=[x for x in objects if x['id'] in ids]
        if collection == 'quantity_diagnoses':
            from .quantity_diagnosis import latest
            objects = [x for x in objects if latest(p, x['reconciliation_id'], x['target_key'])['id']==x['id']]
        if collection == 'engineering_structures':
            objects = [x for x in objects if x['id'] == p['engineering_structures'][-1]['id']]
        if collection == 'pricing_bases':
            objects = [x for x in objects if x['id'] == p['pricing_bases'][-1]['id']]
        if collection == 'procurement_requests':
            from .procurement_requests import cancelled
            from .procurement_orders import summary
            objects = [x for x in objects if not cancelled(p,x['id']) and not summary(p,x['id'])]
        if collection == 'procurement_receipts':
            from .procurement_receipts import event_for
            objects = [x for x in objects if not event_for(p,x['id'])]
        if collection == 'procurement_orders':
            from .procurement_orders import latest
            objects = [x for x in objects if latest(p,x['input']['requisition_id'])['id']==x['id']]
        if collection == 'document_requests':
            objects = [x for x in objects if x['state'] == 'submitted']
        if collection in ('stock_operations', 'stock_offcut_issues'):
            # Cancellation/execution are immutable events, not changes to row.state.
            from . import stock_inventory as inv
            kinds = ('issue', 'partial_issue', 'consume', 'return', 'cancel') if collection == 'stock_operations' else ('offcut_partial_issue', 'offcut_partial_cancel')
            objects = [x for x in objects if not any(inv.event_for(p, kind, x['id']) for kind in kinds)]
        return {'targets': [{'object': x, 'object_hash': store.digest(x)} for x in objects[:200]], 'total': len(objects)}


@router.post('/projects/{pid}/reviews/batch')
def review_batch(pid: str, body: dict, a=Depends(core.actor)):
    if a['role'] != 'reviewer':
        raise HTTPException(403, '仅独立复核账号可以签署审核')
    collection = body.get('collection')
    targets = body.get('targets')
    if collection not in core.REVIEW_COLLECTIONS or not isinstance(targets, list) or not 1 <= len(targets) <= 200:
        raise HTTPException(422, '批量复核须选择同类的1至200个对象')
    if any(not isinstance(x, dict) or not isinstance(x.get('object_id'), str) or not isinstance(x.get('object_hash'), str) for x in targets):
        raise HTTPException(422, '每个对象须有编号和读取时的校验值')
    if len({x['object_id'] for x in targets}) != len(targets):
        raise HTTPException(422, '不能重复选择同一对象')
    with store.transaction() as c:
        p = core.project(c, pid, a)
        # Validate every original snapshot before any decision can invalidate siblings.
        for item in targets:
            obj = core.review_target(p, collection, item['object_id'])
            if obj is None or store.digest(obj) != item['object_hash']:
                raise HTTPException(409, '部分对象已变更，本批次未提交，请重新读取核对')
        batch_id = store.uid()
        records = []
        for item in targets:
            decision = {**item, 'collection': collection, 'decision': body.get('decision'), 'reason': body.get('reason')}
            records.append(core.apply_review(c, p, decision, a, hash_verified=True))
        store.audit(c, p, a, 'REVIEW_BATCH_COMPLETED', {'batch_id': batch_id, 'review_ids': [r['id'] for r in records]})
        store.save(c, p)
    return {'batch_id': batch_id, 'reviews': records}


def resources(p):
    rows = []
    for b in p['boq']:
        for qi, q in enumerate(b['quotas']):
            for ri, r in enumerate(q['resources']):
                rows.append({'key': f"{b['id']}:{qi}:{ri}", 'boq_id': b['id'], 'boq_name': b['name'], 'boq_code': b['code'],
                             'boq_hash': store.digest(b), 'quota_index': qi, 'resource_index': ri, 'quota_code': q['code'],
                             'resource': r, 'resource_hash': store.digest(r)})
    return rows


@router.get('/projects/{pid}/resource-prices')
def resource_prices(pid: str, a=Depends(core.actor)):
    with store.connect() as c:
        return resources(core.project(c, pid, a))


def validate_price_metadata(p, metadata):
    fields = ['source', 'license', 'period', 'region', 'tax_mode', 'valid_from', 'valid_until', 'evidence_id', 'source_type']
    if any(not isinstance(metadata.get(k), str) or not metadata[k].strip() or len(metadata[k]) > 2000 for k in fields):
        raise HTTPException(422, '调价须填写来源、许可、期次、地区、税制、有效期、原始依据和价格来源类型')
    if metadata['source_type'] not in ('官方信息价', '供应商报价', '询价', '历史审核价', '人工录入'):
        raise HTTPException(422, '请选择明确的价格来源类型')
    if metadata['source_type'] == '历史审核价':
        raise HTTPException(409, '历史审核价仅可作为参考，请提供当前适用且获授权的价格依据')
    try:
        start, end = date.fromisoformat(metadata['valid_from']), date.fromisoformat(metadata['valid_until'])
    except ValueError:
        raise HTTPException(422, '价格有效期格式无效') from None
    if metadata['region'] != p['region'] or metadata['tax_mode'] not in ('含税', '不含税') or not start <= core.effective_date(p) <= end:
        raise HTTPException(409, '调价地区、税制或有效期不适用于当前计价日期')
    core.require_evidence(p, metadata['evidence_id'])


def validate_price_change(p, change):
    if change['id'] in p['applied_price_changes']:
        raise HTTPException(409, '已应用调价不能重新审批；需要修正时请建立新调价')
    if store.digest(p['rules'][0] if p['rules'] else {'fees': []}) != change['impact']['rules_hash']:
        raise HTTPException(409, '计价规则已变化，请重新建立调价并核对影响金额')
    lookup = {row['key']: row for row in resources(p)}
    for entry in change['entries']:
        current = lookup.get(entry['key'])
        if not current or current['boq_hash'] != entry['boq_hash'] or current['resource_hash'] != entry['resource_hash']:
            raise HTTPException(409, '调价基线已变化，请重新建立调价单')
        validate_price_metadata(p, entry['after'])
        number(entry['after']['price'])
    from .resource_library import reference_errors
    proposed = {'id':'proposed','code':'','name':'调价资源','quotas':[{'code':'proposed','resources':[e['after'] for e in change['entries']]}]}
    issues = reference_errors({**p,'boq':[proposed]})
    if issues:
        raise HTTPException(409,'；'.join(issues))


def build_price_change(p, body):
    items, reason = body.get('entries'), body.get('reason')
    if not isinstance(items, list) or not 1 <= len(items) <= 500 or not isinstance(reason, str) or not reason.strip() or len(reason) > 3000:
        raise HTTPException(422, '调价须选择1至500条资源并填写调价及合同依据')
    if any(not isinstance(x, dict) or not isinstance(x.get('key'), str) for x in items) or len({x['key'] for x in items}) != len(items):
        raise HTTPException(422, '调价资源选择无效或重复')
    metadata = body.get('metadata')
    if not isinstance(metadata, dict):
        raise HTTPException(422, '调价来源信息不完整')
    validate_price_metadata(p, metadata)
    lookup = {r['key']: r for r in resources(p)}
    entries = []
    for item in items:
        row = lookup.get(item['key'])
        if not row or row['boq_hash'] != item.get('boq_hash') or row['resource_hash'] != item.get('resource_hash'):
            raise HTTPException(409, '资源已变化，请重新读取后核对调价')
        if row['resource'].get('pricing_status') == 'unpriced':
            raise HTTPException(409, '待询价资源没有原价，不能生成调价差额；请在定额资源编辑中补价并重新复核')
        after = {**row['resource'], **{k: metadata[k] for k in ['source', 'license', 'period', 'region', 'tax_mode', 'valid_from', 'valid_until', 'evidence_id', 'source_type']},
                 'price': str(number(item.get('price')))}
        after.pop('price_change_id', None)
        after.pop('library_ref', None)
        after.pop('supplier_quote_ref', None)
        entries.append({**{k: row[k] for k in ['key', 'boq_id', 'boq_name', 'quota_index', 'resource_index', 'boq_hash', 'resource_hash']},
                        'before': copy.deepcopy(row['resource']), 'after': after})
    ids = {e['boq_id'] for e in entries}
    before = [copy.deepcopy(b) for b in p['boq'] if b['id'] in ids]
    after_boq = copy.deepcopy(before)
    for entry in entries:
        b = next(x for x in after_boq if x['id'] == entry['boq_id'])
        b['quotas'][entry['quota_index']]['resources'][entry['resource_index']] = entry['after']
    rules = p['rules'][0] if p['rules'] else {'fees': []}
    previous, updated = calculate(before, {**rules, 'fees': [], 'node_fees': []}), calculate(after_boq, {**rules, 'fees': [], 'node_fees': []})
    with localcontext() as ctx:
        ctx.prec = 80
        delta = money(Decimal(updated['subtotal'])-Decimal(previous['subtotal']))
    change = {'id': store.uid(), 'name': str(body.get('name') or '资源价格变更')[:100], 'reason': reason,
              'entries': entries, 'state': 'draft', 'created_at': store.now(), 'evidence_id': metadata['evidence_id'],
              'impact': {'before_subtotal': previous['subtotal'], 'after_subtotal': updated['subtotal'], 'delta': delta,
                         'boq_count': len(ids), 'resource_count': len(entries), 'rules_hash': store.digest(rules),
                         'notice': '仅为受影响清单分部分项金额试算，不含措施、费用及税金；应用后须重新审定并计算报价。'}}
    p['price_changes'].append(change)
    core.invalidate(p, 'quotes')
    return change


@router.post('/projects/{pid}/price-changes')
def propose_price_change(pid: str, body: dict, a=Depends(core.actor)):
    core.edit(a)
    with store.transaction() as c:
        p = core.project(c, pid, a)
        change = build_price_change(p, body)
        store.audit(c, p, a, 'PRICE_CHANGE_PROPOSED', change)
        store.save(c, p)
    return change

@router.post('/projects/{pid}/price-changes/{cid}/apply')
def apply_price_change(pid: str, cid: str, a=Depends(core.actor)):
    core.edit(a)
    with store.transaction() as c:
        p = core.project(c, pid, a)
        change = core.find(p, 'price_changes', cid)
        if change['state'] != 'approved':
            raise HTTPException(409, '调价尚未审定或审批已撤回')
        if cid in p['applied_price_changes']:
            return {'duplicate': True, 'id': cid}
        validate_price_change(p, change)
        ids = {entry['boq_id'] for entry in change['entries']}
        p.setdefault('boq_history', []).extend(copy.deepcopy([b for b in p['boq'] if b['id'] in ids]))
        for entry in change['entries']:
            b = core.find(p, 'boq', entry['boq_id'])
            b['quotas'][entry['quota_index']]['resources'][entry['resource_index']] = {**copy.deepcopy(entry['after']), 'price_change_id': cid}
            b['state'] = 'draft'
        p['applied_price_changes'].append(cid)
        core.invalidate(p, 'quotes')
        store.audit(c, p, a, 'PRICE_CHANGE_APPLIED', {'id': cid, 'boq_ids': sorted(ids)})
        store.save(c, p)
    return {'id': cid, 'boq_ids': sorted(ids), 'duplicate': False}
