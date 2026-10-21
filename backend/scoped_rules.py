"""Bindings and original evidence for scoped fees; the existing rule review approves all scopes."""
from fastapi import HTTPException
from . import main as core,store
from .engineering_structure import latest,validate as validate_structure
from .engine import validate_rules
from .task_execution import original_bytes


def validate(p,rule,verify_bytes=True):
    validate_rules(rule)
    if not rule.get('node_fees'):return
    s=latest(p)
    if not s or s['state']!='approved':raise HTTPException(409,'分级取费须先复核工程结构')
    validate_structure(p,s,verify_bytes)
    if rule.get('structure_hash')!=store.digest(s):raise HTTPException(409,'分级取费绑定的工程结构已变化，请修订规则包并重新复核')
    nodes={n['id'] for n in s['nodes']};verified=set()
    for group in rule['node_fees']:
        if group['node_id'] not in nodes:raise HTTPException(409,'分级取费节点不在当前工程结构内')
        for fee in group['fees']:
            ev=core.require_evidence(p,fee['evidence_id']);doc=core.find(p,'documents',ev['document_id'])
            if ev['document_hash']!=doc['hash'] or not (store.ROOT/'originals'/doc['hash']).is_file():raise HTTPException(409,'分级费用依据原件失效或缺失')
            if verify_bytes and doc['hash'] not in verified:
                original_bytes(doc);verified.add(doc['hash'])


def issues(p):
    if not p['rules'] or not p['rules'][0].get('node_fees'):return []
    from .engine import DomainError
    try:validate(p,p['rules'][0],False)
    except (HTTPException,DomainError) as e:return ['分级取费无效：'+str(e.detail if isinstance(e,HTTPException) else e)]
    return []
