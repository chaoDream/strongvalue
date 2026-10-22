"""Source-bound project facts; normalization never chooses between conflicting sources."""
import re
import unicodedata
from decimal import Decimal, localcontext
from fastapi import HTTPException
from .engine import DomainError
from . import store

VERSION = 'project-facts-1.0'
# Unit aliases are explicit. Working days, months and percentage fractions are not inferred.
CATALOG = {
    'project_name': ('项目名称', '', ['项目名称', '工程名称']),
    'client': ('建设单位', '', ['建设单位', '招标人', '项目业主']),
    'designer': ('设计单位', '', ['设计单位']),
    'contractor': ('施工单位', '', ['施工单位', '承包人']),
    'supervisor': ('监理单位', '', ['监理单位']),
    'location': ('建设地点', '', ['建设地点', '工程地点', '项目地点']),
    'lot': ('标段', '', ['标段名称', '标段编号', '标段']),
    'scope': ('工程范围', '', ['工程范围', '招标范围', '施工范围']),
    'bid_deadline': ('投标截止时间', '', ['投标截止时间', '投标截止日期']),
    'quality': ('质量要求', '', ['质量要求', '质量标准']),
    'pricing_requirement': ('计价要求', '', ['计价要求', '报价要求']),
    'contract_clause': ('合同条款', '', ['合同条款']),
    'capacity': ('处理规模', 'm³/日', []),
    'duration': ('施工工期', '日历天', []),
    'construction_amount': ('施工标金额', '元', []),
    'supervision_rate': ('监理报价费率', '%', []),
    'contract_amount': ('合同金额', '元', []),
}
UNITS = {
    'capacity': {'m³/日':'1', 'm3/日':'1', 'm³/d':'1', 'm3/d':'1', 'm³/天':'1', '立方米/日':'1',
                 '万m³/日':'10000', '万m3/日':'10000', '万m³/d':'10000', '万m3/d':'10000', '万立方米/日':'10000'},
    'duration': {'日历天':'1', '天':'1', '日':'1'},
    'construction_amount': {'元':'1', '万元':'10000', '亿元':'100000000'},
    'contract_amount': {'元':'1', '万元':'10000', '亿元':'100000000'},
    'supervision_rate': {'%':'1', '％':'1'},
}


def catalog():
    return {k: {'label': v[0], 'unit': v[1], 'units': list(UNITS.get(k, {'':''}))} for k,v in CATALOG.items()}


def normalize(key, value, unit='', *, known=False):
    if not isinstance(key,str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,79}',key):
        raise DomainError('事实字段标识无效')
    if known and key not in CATALOG:
        raise DomainError('模型事实字段不在受控目录中')
    if not isinstance(value,str) or not value.strip() or len(value)>2000 or not isinstance(unit,str) or len(unit)>30:
        raise DomainError('事实值或单位无效（事实值最多2000字）')
    raw_value, raw_unit = value, unit
    value = re.sub(r'\s+', ' ', unicodedata.normalize('NFKC',value)).strip()
    unit = unit.strip()
    if key in UNITS:
        if unit not in UNITS[key]: raise DomainError('事实单位不支持；请明确使用目录中的原始单位')
        # No exponents, nonfinite numbers, malformed thousands or implicit fractions.
        if not re.fullmatch(r'(?:\d+|\d{1,3}(?:[,，]\d{3})+)(?:\.\d{1,12})?',value):
            raise DomainError('数值事实须为明确的非负十进制数')
        with localcontext() as ctx:
            ctx.prec = 50
            number = Decimal(value.replace(',','').replace('，','')) * Decimal(UNITS[key][unit])
        if number>Decimal('1000000000000000') or key in ('capacity','duration') and number<=0 or key=='supervision_rate' and number>100:
            raise DomainError('事实数值超出受控范围')
        value, unit = format(number,'f'), CATALOG[key][1]
    elif key in CATALOG and unit:
        raise DomainError('文本事实不接受数值单位')
    return {'key': key, 'label': CATALOG[key][0] if key in CATALOG else key, 'value': value, 'unit': unit,
            'original_value': raw_value, 'original_unit': raw_unit, 'normalization_version': VERSION}


def equivalent(a,b):
    if a['key']!=b['key']: return False
    try:
        x,y=(normalize(f['key'],f['value'],f.get('unit','')) for f in (a,b))
        if a['key'] in UNITS: return Decimal(x['value'])==Decimal(y['value']) and x['unit']==y['unit']
        return (x['value'],x['unit'])==(y['value'],y['unit'])
    except DomainError:
        # Legacy unsupported values remain distinct instead of guessing a conversion.
        return (a['value'],a.get('unit',''))==(b['value'],b.get('unit',''))


def conflicts(p,record):
    return [f for f in p['facts'] if f['key']==record['key'] and f['state'] not in ('rejected','superseded') and not equivalent(f,record)]


def append(p,record):
    if conflicts(p,record):
        record['state']='conflict'
        for f in p['facts']:
            if f['key']==record['key'] and f['state'] not in ('rejected','superseded'): f['state']='conflict'
    p['facts'].append(record)


def conflict_groups(p):
    """Read-only FactConflict projection; equivalent candidates retain separate evidence."""
    by_key={}
    for f in p['facts']:
        if f['state'] not in ('rejected','superseded'): by_key.setdefault(f['key'],[]).append(f)
    out=[]
    for key,candidates in by_key.items():
        groups=[]
        for f in candidates:
            group=next((g for g in groups if equivalent(g['_first'],f)),None)
            if group is None:
                group={'_first':f,'value':f['value'],'unit':f.get('unit',''),'candidate_ids':[]}
                groups.append(group)
            group['candidate_ids'].append(f['id'])
        if len(groups)>1:
            out.append({'id':'fact-conflict:'+store.digest({'project_id':p['id'],'key':key})[:24],
                        'key':key,'label':candidates[0]['label'],'state':'unresolved',
                        'candidate_ids':[f['id'] for f in candidates],
                        'value_groups':[{k:v for k,v in g.items() if k!='_first'} for g in groups]})
    return out


def bind(record,evidence,document,method,raw_text):
    if evidence.get('document_id')!=document['id'] or evidence.get('document_hash')!=document['hash']:
        raise HTTPException(409,'事实证据与原件版本不一致')
    if not isinstance(raw_text,str) or not raw_text.strip() or len(raw_text)>2000 or raw_text not in evidence['text']:
        raise DomainError('事实原文须为所选来源中的连续原文，最多2000字')
    return {**record, 'id':store.uid(), 'state':'draft', 'evidence_id':evidence['id'],
            'document_id':document['id'], 'document_hash':document['hash'], 'evidence_hash':store.digest(evidence),
            'raw_text':raw_text, 'method':method,
            'confidence_category': {'explicit_text':'明确文字候选', 'manual':'人工记录待核', 'model':'模型候选待核'}[method]}


def validate(p,fact):
    from . import main as core
    from .task_execution import original_bytes
    e=core.require_evidence(p,fact['evidence_id'])
    d=core.find(p,'documents',e['document_id'])
    original_bytes(d)
    if e.get('document_hash')!=d['hash']: raise HTTPException(409,'事实证据与原件版本不一致')
    if fact.get('normalization_version'):
        try: rebuilt=normalize(fact['key'],fact.get('original_value'),fact.get('original_unit'))
        except DomainError: raise HTTPException(409,'事实原始值或单位记录不完整，请重新记录') from None
        if fact['normalization_version']!=VERSION or any(fact.get(k)!=rebuilt[k] for k in ('key','value','unit')):
            raise HTTPException(409,'事实单位换算记录已变化，请重新记录')
        if fact.get('document_id')!=d['id'] or fact.get('document_hash')!=d['hash'] or fact.get('evidence_hash')!=store.digest(e) or not fact.get('raw_text') or fact['raw_text'] not in e['text']:
            raise HTTPException(409,'事实来源已变化，请重新记录并复核')
