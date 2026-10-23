"""Evidence-backed per-stock kerfs and exact capacity search with a node budget."""
from fractions import Fraction
from .compound_takeoff import op,text
from .engine import DomainError,number
from .measurement_units import LENGTH,VERSION as UNIT_VERSION
from .planar_takeoff import linear

VERSION='railing-stock-kerf-1.0'
NOTICE='S1沿用本组每件锯缝，备选S2起逐规格据原件核定，每件含最后一件均计一次，不重复计入端切或净下料。端切及余料阈值按本组或逐规格加工原件，容量按所选定尺实际锯缝逐件检查。先最小购入总长，同长度再最少根数；5000节点内搜索，仅双下界达到或穷尽搜索才证明最优。下界使用正供应候选最小锯缝和最小端切作安全松弛，不代表可直接施工的拼接规格。供应可按需或有限上限核定；不自动占用库存、回用余料、采购或改变净量及报价。不同截面、采购价格和跨表统筹另核。'


def apply(config,options,unit,prefix,dimensions,refs,conversions):
    if not isinstance(config,dict) or set(config)!={'alternatives','scope_confirmed','measurement_basis','evidence_id'}:raise DomainError('逐规格锯缝须完整填写备选锯缝、范围确认、说明及原件')
    if config['scope_confirmed'] is not True:raise DomainError('须据原件确认逐规格每件锯缝含最后一件，且未重复计入端切或净下料')
    text(config['measurement_basis'],'逐规格锯缝范围说明');refs[prefix+'.kerfs_basis']=text(config['evidence_id'],'逐规格锯缝范围原件',200)
    rows=config['alternatives']
    if not isinstance(rows,list) or len(rows)!=len(options)-1:raise DomainError('须按S2至全部备选顺序填写锯缝，不得缺项或多项')
    for i,(row,o) in enumerate(zip(rows,options[1:]),2):
        if not isinstance(row,dict) or set(row)!={'kerf_per_piece','evidence_id'}:raise DomainError('每种备选锯缝仅含每件锯缝及原件')
        number(row['kerf_per_piece']);v=linear(row['kerf_per_piece'],unit);key=prefix+'.kerfs.'+str(i)
        dimensions[key]=str(row['kerf_per_piece']);refs[key]=text(row['evidence_id'],'逐规格每件锯缝原件',200);o['kerf_per_piece']=Fraction(v);o['kerf_ast']=op('multiply',{'dimension':key},{'constant':LENGTH[unit]})
        conversions.append(dict(parameter=key,original_value=str(row['kerf_per_piece']),source_unit=unit,factor=LENGTH[unit],canonical_value=str(v),canonical_unit='m',rule_version=UNIT_VERSION))


def pack(cuts,stocks,trims,kerfs,limits=None,limit=5000):
    """Inputs are integer lengths; each piece consumes cut + its chosen stock kerf."""
    caps=[s-t for s,t in zip(stocks,trims)];counts=limits if limits is not None else [len(cuts)]*len(stocks)
    order=sorted((j for j,n in enumerate(counts) if n),key=lambda j:(stocks[j],j))
    failure='按核定供应上限及逐规格锯缝无法完成下料，已由容量约束或穷尽搜索证明；请重新核定'
    if not order or any(not any(c+kerfs[j]<=caps[j] for j in order) for c in cuts):raise DomainError(failure)
    pitches=[c+min(kerfs[j] for j in order) for c in cuts];maximum=max(caps[j] for j in order);minimum_trim=min(trims[j] for j in order)
    count_lower=max((sum(pitches)+maximum-1)//maximum,max((sum(s>=t for s in pitches)+(maximum//t)-1)//(maximum//t) for t in set(pitches)))
    lower=(sum(pitches)+minimum_trim*count_lower,count_lower)
    def score(plan):return sum(stocks[j] for j,_ in plan),len(plan)
    def greedy(preference):
        plan=[];loads=[];used=[0]*len(stocks)
        for i,cut in enumerate(cuts):
            for k,(j,ps) in enumerate(plan):
                size=cut+kerfs[j]
                if loads[k]+size<=caps[j]:loads[k]+=size;ps.append(i);break
            else:
                j=next((j for j in preference if used[j]<counts[j] and cut+kerfs[j]<=caps[j]),None)
                if j is None:return None
                used[j]+=1;plan.append((j,[i]));loads.append(cut+kerfs[j])
        return plan
    seeds=[greedy(p) for p in [order,list(reversed(order))]+[[j]+[k for k in order if k!=j] for j in order]]
    valid=[p for p in seeds if p is not None];best=min(valid,key=score) if valid else None;initial=score(best) if best is not None else (None,None)
    suffix=[0]*(len(cuts)+1)
    for i in range(len(cuts)-1,-1,-1):suffix[i]=suffix[i+1]+pitches[i]
    bins=[];loads=[];used=[0]*len(stocks);seen=set();nodes=0;aborted=False
    def search(i,cost):
        nonlocal best,nodes,aborted
        if aborted or best is not None and score(best)==lower:return
        frees=[caps[j]-loads[k] for k,(j,_) in enumerate(bins)];remaining=[counts[j]-used[j] for j in range(len(stocks))]
        if suffix[i]>sum(frees)+sum(remaining[j]*caps[j] for j in order):return
        for t in set(pitches[i:]):
            if sum(s>=t for s in pitches[i:])>sum(f//t for f in frees)+sum(remaining[j]*(caps[j]//t) for j in order):return
        missing=max(0,suffix[i]-sum(frees));new_count=(missing+maximum-1)//maximum
        if i<len(cuts) and not any(caps[j]-loads[k]>=cuts[i]+kerfs[j] for k,(j,_) in enumerate(bins)):new_count=max(1,new_count)
        if best is not None and (cost+missing+minimum_trim*new_count,len(bins)+new_count)>=score(best):return
        if nodes>=limit:aborted=True;return
        nodes+=1
        if i==len(cuts):best=[(j,ps[:]) for j,ps in bins];return
        state=(i,tuple(sorted((j,loads[k]) for k,(j,_) in enumerate(bins))))
        if state in seen:return
        seen.add(state);equivalent=set()
        for k,(j,ps) in enumerate(bins):
            size=cuts[i]+kerfs[j]
            if (j,loads[k]) in equivalent or loads[k]+size>caps[j]:continue
            equivalent.add((j,loads[k]));loads[k]+=size;ps.append(i);search(i+1,cost);ps.pop();loads[k]-=size
            if aborted:return
        for j in order:
            size=cuts[i]+kerfs[j]
            if used[j]>=counts[j] or size>caps[j] or best is not None and cost+stocks[j]>score(best)[0]:continue
            used[j]+=1;bins.append((j,[i]));loads.append(size);search(i+1,cost+stocks[j]);loads.pop();bins.pop();used[j]-=1
            if aborted:return
    search(0,0)
    if best is None:
        if aborted:raise DomainError('逐规格锯缝搜索预算已耗尽，尚未找到可行方案，不能据此判断供应不足')
        raise DomainError(failure)
    final=score(best);status='length_optimal_bound' if final==lower else 'length_feasible_unproven' if aborted else 'length_optimal_search'
    proof=dict(objective='purchased_length_then_stock_count',kerf_model='per_stock_per_piece',lower_bound_basis='minimum_available_kerf_and_trim',status=status,length_lower_bound=lower[0],stock_count_lower_bound=count_lower,initial_purchased_length=initial[0],initial_stock_count=initial[1],purchased_length=final[0],stock_count=final[1],visited_nodes=nodes,node_limit=limit,search_exhausted=not aborted and final!=lower,unproven_length_gap=final[0]-lower[0] if aborted else 0)
    if limits is not None:proof['supply_model']='finite_limits'
    return best,proof
