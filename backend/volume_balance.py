"""Reviewed component volume ledger. Geometry containment is a separate human decision."""
from decimal import Decimal, ROUND_HALF_UP
from fastapi import HTTPException
from . import store
from .compound_takeoff import component_quantity, structured_validator, STRUCTURED_KINDS, op, text
from .engine import DomainError, number, precise, quantity_number

EXCAVATION_KINDS = {'trench','station_trench','spatial_regions','planar_regions','polygon_region','compound'}
OCCUPATION_KINDS = {'chamber_plate','circular_chamber','route_bedding','pipe_envelope','slab','walls','masonry','ring','spatial_regions','planar_regions','polygon_region','compound'}
VERSION = 'reviewed-volume-balance-1.0'
NOTICE = '按当前已审构件体积及核定比例建立回填平衡，不推断实体是否位于开挖内或相互重叠。须确认同一计量范围及扣减不重复。采用来源已审工程量（含来源既定修约），不还原未修约量，不自动推算管道外包体积、压实/松方系数、分层做法或运输。'


def snapshot(source):
    return {k:v for k,v in source.items() if k != 'state'}


def reference(source):
    r = component_quantity(source)
    return {'component_id':source['id'],'component_hash':store.digest(snapshot(source)),'name':source['name'],
            'revision':source['revision'],'kind':source['kind'],'quantity':r['quantity'],'unit':r['unit'],'rule_version':r['rule_version']}


def check_sources(p, refs):
    from .main import require_evidence, find
    from .task_execution import original_bytes
    sources,checked = {},set()
    for key,eid in refs.items():
        ev = require_evidence(p,eid);doc = find(p,'documents',ev['document_id'])
        if ev['document_hash'] != doc['hash']:raise HTTPException(409,'回填关联依据与原件校验值不一致')
        if doc['hash'] not in checked:original_bytes(doc);checked.add(doc['hash'])
        sources[key] = {'evidence':ev,'document_hash':doc['hash']}
    return sources


def source_component(p, cid, excavation=False):
    from .main import find
    s = find(p,'components',cid)
    allowed = EXCAVATION_KINDS if excavation else OCCUPATION_KINDS
    if s['kind'] not in allowed or s['state'] != 'approved':
        raise HTTPException(409,'须选择当前已审定且适用于'+('开挖' if excavation else '实体占位')+'的体积构件')
    if s['kind'] in STRUCTURED_KINDS:
        structured_validator(s['kind'])(p,s,saved=True)
    if s['kind']=='circular_chamber' and s.get('purpose')!='outer_envelope':
        raise HTTPException(409,'井筒材料体积不能代替回填占位，请另建并审定井筒外包体积')
    r = component_quantity(s)
    if r['unit'] != 'm³' or s.get('preview') != r:
        raise HTTPException(409,'关联构件须为可复算的立方米体积，不能关联面积、长度或被改写的预览')
    check_sources(p,s['evidence'])
    return s


def selected_sources(p, excavation_id, ids):
    if not isinstance(ids,list) or len(ids)>50 or any(not isinstance(v,str) or not v for v in ids):
        raise DomainError('扣减来源须为最多50个构件ID')
    if len(set(ids)) != len(ids) or excavation_id in ids:
        raise DomainError('扣减来源不能重复，也不能扣除开挖构件自身')
    return {'excavation':snapshot(source_component(p,excavation_id,True)),
            'occupations':[snapshot(source_component(p,cid)) for cid in ids]}


def candidates(p, body):
    sources = selected_sources(p,body.get('excavation_component_id'),body.get('occupation_component_ids'))
    return {'excavation':reference(sources['excavation']),'occupations':[reference(s) for s in sources['occupations']],'notice':NOTICE}


@precise
def calculate(comp, sources=None):
    sources = sources if sources is not None else comp['source_geometry']
    if comp.get('parameters') != {} or comp.get('quantity_type') != 'volume' or comp.get('boundaries_confirmed') is not True:
        raise DomainError('关联回填须为空顶层参数、体积类型，并明确确认范围一致及扣减不重复')
    text(comp.get('name'),'回填名称',200);text(comp.get('measurement_basis'),'范围、扣除口径与回填依据')
    digits = comp.get('rounding_digits')
    if type(digits) is not int or not 0<=digits<=12:raise DomainError('回填总量修约位数须明确为0至12的整数')
    excavation = sources['excavation'];er = reference(excavation)
    if comp.get('excavation_component_id') != er['component_id'] or comp.get('excavation_component_hash') != er['component_hash']:
        raise HTTPException(409,'开挖来源已修订，请重新读取并核定回填平衡')
    occupations = comp.get('occupations')
    if not isinstance(occupations,list) or len(occupations)>50 or len(occupations)!=len(sources['occupations']):
        raise DomainError('扣减明细须完整对应所选来源，最多50项')
    ids=[]
    for row in occupations:
        if not isinstance(row,dict) or set(row)!={'component_id','component_hash','factor','basis','evidence_id'} or not isinstance(row['component_id'],str):
            raise DomainError('每项扣减须包含构件ID与版本哈希、核定比例、说明和原始依据')
        ids.append(row['component_id'])
    if len(set(ids))!=len(ids) or er['component_id'] in ids:raise DomainError('扣减来源不能重复或为开挖自身')
    if set(ids)!={s['id'] for s in sources['occupations']}:raise DomainError('扣减来源与已读取快照不一致')
    if er['unit']!='m³' or excavation['kind'] not in EXCAVATION_KINDS:raise DomainError('开挖来源必须为适用的立方米体积构件')
    refs = {'balance_basis':text(comp.get('basis_evidence_id'),'回填总依据',200),'balance_rounding':text(comp.get('rounding_evidence_id'),'回填修约依据',200)}
    refs.update({'excavation.'+k:v for k,v in excavation['evidence'].items()})
    gross = quantity_number(er['quantity']);dimensions={'excavation.quantity':er['quantity']}
    deducted = Decimal(0);rows=[];nodes=[];by_id={s['id']:s for s in sources['occupations']}
    for i,d in enumerate(occupations):
        s = by_id[d['component_id']];r = reference(s)
        if r['unit']!='m³' or s['kind'] not in OCCUPATION_KINDS:raise DomainError('占位来源须为适用的立方米实体，不能递归引用回填')
        if s['kind']=='circular_chamber' and s.get('purpose')!='outer_envelope':raise DomainError('回填须引用井筒外包占位，不能扣除井筒材料体积')
        if d['component_hash']!=r['component_hash']:raise HTTPException(409,'扣减来源已修订，请重新读取并核定分配比例')
        factor = number(d['factor'],True)
        if factor>1:raise DomainError('核定扣除比例须大于0且不超过1；不扣除的构件请移除')
        text(d['basis'],'扣减范围及比例依据')
        key='occupied.'+str(i+1)+'.'
        refs[key+'basis']=text(d['evidence_id'],'扣减比例原始依据',200)
        refs.update({key+k:v for k,v in s['evidence'].items()})
        dimensions[key+'quantity']=r['quantity'];dimensions[key+'factor']=str(d['factor'])
        value=quantity_number(quantity_number(r['quantity'])*factor);deducted+=value
        ast=op('multiply',{'dimension':key+'quantity'},{'dimension':key+'factor'});nodes.append(ast)
        rows.append({**r,'factor':str(d['factor']),'basis':d['basis'],'evidence_id':d['evidence_id'],'deduction':str(value),'formula_ast':ast,'index':i+1})
    quantity_number(deducted)
    if deducted>gross:raise DomainError('核定扣减体积超过开挖量，请核对来源范围、重叠及比例，不允许自动归零')
    net=gross-deducted;quantity=quantity_number(net.quantize(Decimal(1).scaleb(-digits),rounding=ROUND_HALF_UP))
    return {'quantity':str(quantity),'gross':str(gross),'deduction':str(deducted),'unit':'m³','parameters':{},
            'formula':'已审开挖 '+str(gross)+' − 核定扣减 '+str(deducted)+'；回填总量修约'+str(digits)+'位',
            'formula_ast':{'op':'round','args':[op('subtract',{'dimension':'excavation.quantity'},op('add',*nodes) if nodes else {'constant':'0'})],'digits':digits,'mode':'HALF_UP'},
            'dimensions':dimensions,'source_evidence':refs,'source_geometry':sources,'excavation_reference':er,'balance_deductions':rows,
            'balance_summary':{'occupation_count':len(rows),'unrounded_quantity':str(net),'rounding_digits':digits,'rounding_adjustment':str(quantity-net),'boundaries_confirmed':True},
            'rounding_rule':'采用来源已审工程量（含其既定修约）；扣除量不逐项修约，仅净回填总量HALF_UP至'+str(digits)+'位小数；Decimal 80位',
            'rule_version':VERSION,'notice':NOTICE}


def validate(p, comp, saved=False):
    rows=comp.get('occupations')
    if not isinstance(rows,list) or any(not isinstance(d,dict) for d in rows):raise DomainError('扣减明细须为列表')
    sources=selected_sources(p,comp.get('excavation_component_id'),[d.get('component_id') for d in rows])
    result=calculate(comp,sources);proofs=check_sources(p,result['source_evidence'])
    stamp=store.digest({'source_geometry':sources,'sources':proofs})
    if saved and (comp.get('source_geometry')!=sources or comp.get('source_stamp')!=stamp or comp.get('evidence')!=result['source_evidence'] or comp.get('preview')!=result):
        raise HTTPException(409,'回填来源、分配比例或计算记录已变化，请修订后重新复核')
    return result,stamp
