"""Pure bottom-up scoped fees. No inferred tax rates, redistribution or cross-scope references."""
from decimal import Decimal
from .engine import calculate, calculate_fees, rounding_config, quantity_number, money, DomainError


def calculate_scoped(items,rules,structure):
    if not isinstance(structure,dict):raise DomainError('分级取费需要完整工程结构')
    nodes=structure.get('nodes');assignments=structure.get('assignments')
    if not isinstance(nodes,list) or not 2<=len(nodes)<=200 or not isinstance(assignments,dict) or any(not isinstance(n,dict) or not all(k in n for k in ['id','kind','name','parent_id']) or not isinstance(n['id'],str) or n['id']=='project' or not isinstance(n['name'],str) or n['parent_id'] is not None and not isinstance(n['parent_id'],str) for n in nodes):raise DomainError('工程结构或归属无效')
    lookup={n['id']:n for n in nodes}
    if len(lookup)!=len(nodes) or len({b['id'] for b in items})!=len(items) or set(assignments)!={b['id'] for b in items}:raise DomainError('工程节点重复或清单归属不完整')
    if any(n['kind'] not in ('unit','single') or n['kind']=='single' and n['parent_id'] is not None or n['kind']=='unit' and lookup.get(n['parent_id'],{}).get('kind')!='single' for n in nodes):
        raise DomainError('分级费用仅支持单项/单位两级工程结构')
    if not any(n['kind']=='single' for n in nodes) or any(n['kind']=='single' and not any(u['parent_id']==n['id'] for u in nodes) for n in nodes):raise DomainError('每个单项工程须有单位工程')
    if any(not isinstance(uid,str) or uid not in lookup or lookup[uid]['kind']!='unit' for uid in assignments.values()):raise DomainError('清单只能归属单位工程')
    groups={g['node_id']:g['fees'] for g in rules['node_fees']}
    if set(groups)-lookup.keys():raise DomainError('分级费用引用了不存在的工程节点')
    base=calculate(items,{**rules,'fees':[],'node_fees':[]})
    _,rounding=rounding_config(rules)
    categories=['labor','material','machinery','equipment'];types={'人工':'labor','材料':'material','机械':'machinery','设备':'equipment'}
    raw={uid:Decimal(0) for uid in lookup};counts={uid:0 for uid in lookup}
    resources={uid:{k:Decimal(0) for k in categories} for uid in lookup}
    for b in base['lines']:
        uid=assignments[b['id']];raw[uid]+=Decimal(b['amount']);counts[uid]+=1
        for r in b['resources_snapshot']:
            if r.get('type') in types:resources[uid][types[r['type']]]+=Decimal(r['cost'])*quantity_number(b['quantity'])
    flat=[];totals={}
    def run(uid,kind,name,subtotal,children_total,lower_fees,cats,fees):
        calculated,total=calculate_fees(fees,{**cats,'subtotal':subtotal,'children_total':children_total,'lower_fees':lower_fees,'running':children_total},rounding)
        annotated=[{**f,'scope_id':uid,'scope_kind':kind,'scope_name':name} for f in calculated];flat.extend(annotated)
        own=sum((Decimal(f['amount']) for f in calculated),Decimal(0))
        value={'id':uid,'kind':kind,'subtotal':money(subtotal),'lower_fees':money(lower_fees),'own_fees':money(own),'total':money(total),
               'resource_bases':{k:str(v) for k,v in cats.items()},'fees':annotated}
        if subtotal+lower_fees+own!=total:raise DomainError('工程节点费用与汇总不闭合')
        totals[uid]=value
        return value
    for n in nodes:
        if n['kind']=='unit':run(n['id'],'unit','单位工程：'+lookup[n['parent_id']]['name']+' / '+n['name'],raw[n['id']],raw[n['id']],Decimal(0),resources[n['id']],groups.get(n['id'],[]))
    for n in nodes:
        if n['kind']!='single':continue
        children=[u['id'] for u in nodes if u['parent_id']==n['id']]
        subtotal=sum((raw[uid] for uid in children),Decimal(0));children_total=sum((Decimal(totals[uid]['total']) for uid in children),Decimal(0))
        cats={k:sum((resources[uid][k] for uid in children),Decimal(0)) for k in categories}
        run(n['id'],'single','单项工程：'+n['name'],subtotal,children_total,children_total-subtotal,cats,groups.get(n['id'],[]))
    child_total=sum((Decimal(totals[n['id']]['total']) for n in nodes if n['kind']=='single'),Decimal(0));subtotal=Decimal(base['subtotal'])
    project=run('project','project','建设项目',subtotal,child_total,child_total-subtotal,{k:Decimal(v) for k,v in base['resource_bases'].items()},rules.get('fees',[]))
    if subtotal+sum((Decimal(f['amount']) for f in flat),Decimal(0))!=Decimal(project['total']):raise DomainError('项目总价与全部分级费用不闭合')
    return {**base,'fees':flat,'total':project['total'],'engine':'pricing-3.0','scoped_pricing':{'nodes':[totals[n['id']] for n in nodes],'project':project,
            'notice':'单位费用→单项费用→项目费用逐级计算；subtotal仅为本范围清单合价，children_total含下级费用，lower_fees为下级费用之和，running再含本级已算费用。'}}
