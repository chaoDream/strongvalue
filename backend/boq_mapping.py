"""Source-backed code mapping. No built-in official codes or inferred quantities."""
import copy
import re
from datetime import date
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from . import main as core, store
from .engine import DomainError, precise, quantity_number
from .compound_takeoff import STRUCTURED_KINDS, structured_validator, component_quantity
from .extensions import links, validate_links
from .mapping_types import TYPES, selected_profile, component_profile

router = APIRouter(prefix='/api/v1')


@router.get('/boq-mapping-types')
def mapping_types(a=Depends(core.actor)):
    return TYPES


def validate_mapping(p, rule):
    for key in ['name', 'code', 'item_name', 'features', 'standard', 'edition', 'license', 'evidence_id']:
        if not isinstance(rule.get(key), str) or not rule[key].strip() or len(rule[key]) > 2000:
            raise HTTPException(422, '映射规则需名称、真实编码、项目名称/特征、标准版本、许可和原始依据')
    if not re.fullmatch(r'[A-Za-z0-9_.-]{2,60}', rule['code']):
        raise HTTPException(422, '编码须为原始依据中的2至60位字母、数字或常用编码分隔符')
    if not selected_profile(rule):
        raise HTTPException(422, '映射须明确受控构件类型、适用计量口径及对应单位')
    if rule.get('discipline') not in p['scope'] or rule.get('region') != p['region']:
        raise HTTPException(409, '映射地区或专业不在项目范围内')
    keywords = rule.get('keywords')
    if not isinstance(keywords, list) or not 1 <= len(keywords) <= 10 or any(not isinstance(k,str) or not k.strip() or len(k)>80 for k in keywords):
        raise HTTPException(422, '须填写1至10个明确的构件名称关键词')
    try:
        start, end = date.fromisoformat(rule['valid_from']), date.fromisoformat(rule['valid_until'])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(422, '映射有效期格式无效') from None
    if not start <= core.effective_date(p) <= end:
        raise HTTPException(409, '映射规则不在当前计价日期适用期间')
    if rule['standard'] != p.get('boq_standard') or rule['edition'] != p.get('boq_edition'):
        raise HTTPException(409, '请先设置项目适用清单标准及版本，且须与映射一致')
    evidence = core.require_evidence(p, rule['evidence_id'])
    from .task_execution import original_bytes
    doc = core.find(p, 'documents', evidence['document_id'])
    original_bytes(doc)
    if evidence.get('document_hash') != doc['hash']:
        raise HTTPException(409, '编码依据与原件校验值不一致')
    if not re.search(r'(?<![A-Za-z0-9_.-])'+re.escape(rule['code'])+r'(?![A-Za-z0-9_.-])', evidence['text']):
        raise HTTPException(409, '原始依据未包含该完整编码，不能以模型或规则名称替代编码来源')
    stamp = store.digest({'evidence': evidence, 'document_hash': doc['hash']})
    if 'schema' in rule and (rule['schema'] != 'boq-mapping-v2' or rule.get('source_stamp') != stamp):
        raise HTTPException(409, '编码来源或映射格式已变化，请修订规则并重新复核')
    return stamp


@precise
def source_context(p, b):
    """Resolve every contribution against current approved and recomputed inputs."""
    validate_links(p, b)
    result = []
    contributions = {}
    for link in links(b):
        quantity = quantity_number(link['quantity'])
        contributions[link['takeoff_id']] = contributions.get(link['takeoff_id'], Decimal(0)) + quantity
    for takeoff_id, contribution in contributions.items():
        t = core.find(p, 'takeoffs', takeoff_id)
        if t.get('source_kind') in ('imported_boq', 'change_order') or not t.get('component_id'):
            raise HTTPException(409, '外部导入或签证数量无当前设计构件口径，须人工核定编码')
        comp = core.find(p, 'components', t['component_id'])
        if comp['state'] != 'approved' or t.get('component_hash') != store.digest(comp):
            raise HTTPException(409, '来源构件未审定或版本已变化，请重新算量复核')
        quantity = structured_validator(comp['kind'])(p, comp, saved=True)[0] if comp['kind'] in STRUCTURED_KINDS else component_quantity(comp)
        if any(t.get(k) != v for k, v in quantity.items()) or t['unit'] != b['unit']:
            raise HTTPException(409, '计算书与当前构件或清单单位不一致，请重新算量复核')
        from .task_execution import original_bytes
        for eid in set(comp['evidence'].values()):
            evidence = core.require_evidence(p, eid)
            doc = core.find(p, 'documents', evidence['document_id'])
            original_bytes(doc)
            if evidence.get('document_hash') != doc['hash']:
                raise HTTPException(409, '构件依据与原件校验值不一致')
        profile = component_profile(comp, quantity)
        if not profile:
            raise HTTPException(409, '当前构件计量口径不受编码映射支持')
        result.append({'takeoff_id': t['id'], 'takeoff_name': t['name'], 'takeoff_hash': store.digest(t),
                       'component_id': comp['id'], 'component_name': comp['name'], 'component_revision': comp['revision'],
                       'component_hash': store.digest(comp), 'kind': comp['kind'], 'kind_label': TYPES[comp['kind']]['label'],
                       'measurement_profile': profile['id'], 'profile_label': profile['label'], 'unit': t['unit'], 'quantity': str(contribution)})
    return result


def matches(p, b, rule, sources=None):
    try:
        sources = source_context(p, b) if sources is None else sources
    except (HTTPException, DomainError):
        return False
    profile = selected_profile(rule)
    return bool(profile and sources) and b['unit'] == rule['unit'] and all(
        s['kind'] == rule['kind'] and s['measurement_profile'] == profile['id'] and
        all(k.casefold() in s['component_name'].casefold() for k in rule['keywords']) for s in sources)


def mapping_issues(p):
    issues = []
    for b in p['boq']:
        ref = b.get('mapping_ref')
        if not ref:
            continue
        rule = next((r for r in p.get('boq_mappings', []) if r['id'] == ref['id']), None)
        try:
            if not rule or rule['state'] != 'approved' or store.digest(rule) != ref['hash'] or rule['code'] != b['code']:
                raise HTTPException(409, '映射版本或审批已失效')
            validate_mapping(p, rule)
            if not matches(p, b, rule):
                raise HTTPException(409, '构件类型或适用条件已变化')
        except HTTPException as exc:
            issues.append(b['name']+'清单编码映射需重新核对：'+str(exc.detail))
    return issues


def invalidate_dependents(p, ids):
    affected = [b for b in p['boq'] if b.get('mapping_ref', {}).get('id') in ids]
    for b in affected:
        b['state'] = 'stale'
    if affected:
        core.invalidate(p, 'quotes')


@router.post('/projects/{pid}/boq-mappings')
def create_mapping(pid: str, body: dict, a=Depends(core.actor)):
    core.edit(a)
    with store.transaction() as c:
        p = core.project(c, pid, a)
        fields = ['name','code','item_name','features','standard','edition','license','evidence_id','kind','unit','keywords','discipline','region','valid_from','valid_until']
        rule = {k: copy.deepcopy(body[k]) for k in fields if k in body}
        if 'measurement_profile' in body: rule['measurement_profile'] = copy.deepcopy(body['measurement_profile'])
        stamp = validate_mapping(p, rule)
        previous = core.find(p, 'boq_mappings', body['previous_id']) if body.get('previous_id') else None
        if previous and any(r['family'] == previous['family'] and r['version'] > previous['version'] for r in p['boq_mappings']):
            raise HTTPException(409, '映射规则已有新版本，请从最新版本修订')
        rule.update(id=store.uid(), family=previous['family'] if previous else store.uid(), version=previous['version']+1 if previous else 1,
                    state='draft', created_at=store.now(), schema='boq-mapping-v2', source_stamp=stamp)
        p['boq_mappings'].append(rule)
        store.audit(c, p, a, 'BOQ_MAPPING_CREATED', rule)
        store.save(c, p)
    return rule


@router.get('/projects/{pid}/boq/mapping-preview')
def preview(pid: str, a=Depends(core.actor)):
    with store.connect() as c:
        p = core.project(c, pid, a)
    valid = []
    for rule in p['boq_mappings']:
        if rule['state'] != 'approved':
            continue
        try:
            validate_mapping(p, rule)
            valid.append(rule)
        except HTTPException:
            continue
    rows = []
    for b in p['boq']:
        try:
            sources = source_context(p, b)
            issue = ''
        except (HTTPException, DomainError) as exc:
            sources = []; issue = str(getattr(exc, 'detail', exc))
        candidates = [r for r in valid if matches(p, b, r, sources)]
        rows.append({'boq': b, 'boq_hash': store.digest(b), 'candidates': [{'rule': r, 'rule_hash': store.digest(r),
                     'reason': '原始编码证据、地区/专业、标准版本、有效期、构件类型、计量口径、单位及每项来源名称关键词匹配'} for r in candidates],
                     'sources': sources, 'source_issue': issue,
                     'state': 'unique' if len(candidates)==1 else 'ambiguous' if candidates else 'unmatched'})
    return {'rows': rows, 'notice': '逐一核对全部已审计算书的当前构件、计量口径及贡献量。材料体积与外包占位、模板与防水、底板与盖板分别匹配；混合口径合并项不推荐。多候选须人工选择，无候选不生成编码；采用不改数量，清单仍须复核。不替代官方编码库或专业语义审查。'}


@router.post('/projects/{pid}/boq/apply-mappings')
def apply_mappings(pid: str, body: dict, a=Depends(core.actor)):
    core.edit(a)
    selections = body.get('selections')
    if not isinstance(selections, list) or not 1 <= len(selections) <= 200 or any(not isinstance(x,dict) or not isinstance(x.get('boq_id'),str) for x in selections):
        raise HTTPException(422, '须明确选择1至200项清单及映射')
    if len({x['boq_id'] for x in selections}) != len(selections):
        raise HTTPException(422, '不能对同一清单重复采用映射')
    with store.transaction() as c:
        p = core.project(c, pid, a)
        for selection in selections:
            b = core.find(p, 'boq', selection['boq_id'])
            rule = core.find(p, 'boq_mappings', selection.get('rule_id'))
            if store.digest(b) != selection.get('boq_hash') or rule['state'] != 'approved' or store.digest(rule) != selection.get('rule_hash'):
                raise HTTPException(409, '清单或映射已变化，本批次未应用')
            validate_mapping(p, rule)
            if not matches(p, b, rule):
                raise HTTPException(409, '该映射不适用于所选清单的构件或单位')
        for selection in selections:
            b = core.find(p, 'boq', selection['boq_id']);rule = core.find(p, 'boq_mappings', selection['rule_id'])
            p.setdefault('boq_history', []).append(copy.deepcopy(b))
            b.update(code=rule['code'], name=rule['item_name'], features=rule['features'], code_evidence_id=rule['evidence_id'],
                     code_standard=rule['standard'], code_edition=rule['edition'], discipline=rule['discipline'],
                     mapping_ref={'id':rule['id'],'hash':store.digest(rule)}, state='draft')
        core.invalidate(p, 'quotes')
        store.audit(c, p, a, 'BOQ_MAPPINGS_APPLIED', selections)
        store.save(c, p)
    return {'count': len(selections)}
