"""Source-backed code mapping. No built-in official codes or inferred quantities."""
import copy
import re
from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from . import main as core, store
from .engine import GEOMETRIES
from .extensions import links

router = APIRouter(prefix='/api/v1')


def validate_mapping(p, rule):
    for key in ['name', 'code', 'item_name', 'features', 'standard', 'edition', 'license', 'evidence_id']:
        if not isinstance(rule.get(key), str) or not rule[key].strip() or len(rule[key]) > 2000:
            raise HTTPException(422, '映射规则需名称、真实编码、项目名称/特征、标准版本、许可和原始依据')
    if not re.fullmatch(r'[A-Za-z0-9_.-]{2,60}', rule['code']):
        raise HTTPException(422, '编码须为原始依据中的2至60位字母、数字或常用编码分隔符')
    if not isinstance(rule.get('kind'),str) or rule['kind'] not in GEOMETRIES or rule.get('unit') != GEOMETRIES[rule['kind']]['unit']:
        raise HTTPException(422, '映射单位须与受控构件几何类型一致')
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
    if not re.search(r'(?<![A-Za-z0-9_.-])'+re.escape(rule['code'])+r'(?![A-Za-z0-9_.-])', evidence['text']):
        raise HTTPException(409, '原始依据未包含该完整编码，不能以模型或规则名称替代编码来源')


def matches(p, b, rule):
    if b['unit'] != rule['unit']:
        return False
    for link in links(b):
        t = next((x for x in p['takeoffs'] if x['id'] == link['takeoff_id']), None)
        if not t or t['state'] != 'approved':
            return False
        comp = next((x for x in p['components'] if x['id'] == t.get('component_id')), None)
        if not comp or comp['state'] != 'approved' or comp['kind'] != rule['kind'] or not all(k.casefold() in comp['name'].casefold() for k in rule['keywords']):
            return False
    return bool(links(b))


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
        validate_mapping(p, body)
        previous = core.find(p, 'boq_mappings', body['previous_id']) if body.get('previous_id') else None
        if previous and any(r['family'] == previous['family'] and r['version'] > previous['version'] for r in p['boq_mappings']):
            raise HTTPException(409, '映射规则已有新版本，请从最新版本修订')
        fields = ['name','code','item_name','features','standard','edition','license','evidence_id','kind','unit','keywords','discipline','region','valid_from','valid_until']
        rule = {k: copy.deepcopy(body[k]) for k in fields}
        rule.update(id=store.uid(), family=previous['family'] if previous else store.uid(), version=previous['version']+1 if previous else 1,
                    state='draft', created_at=store.now())
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
        candidates = [r for r in valid if matches(p, b, r)]
        rows.append({'boq': b, 'boq_hash': store.digest(b), 'candidates': [{'rule': r, 'rule_hash': store.digest(r),
                     'reason': '原始编码证据、地区/专业、标准版本、有效期、几何类型、单位和全部名称关键词匹配'} for r in candidates],
                     'state': 'unique' if len(candidates)==1 else 'ambiguous' if candidates else 'unmatched'})
    return {'rows': rows, 'notice': '多候选须人工明确选择，无候选不生成编码；采用后清单仍须复核。不会替代官方编码库或专业语义漏项检查。'}


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
