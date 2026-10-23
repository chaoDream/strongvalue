"""Finite supply constraints, evidence-backed limits and bounded exact search."""
from .compound_takeoff import text
from .engine import DomainError,number

VERSION='railing-limited-stock-cutting-1.0'
NOTICE='仅在本混排组核定的各定尺可用根数上限内选材，每种0至200根；先最小购入总长度，同长度再最少根数。供应根数为本次方案的有据上限，不代表实际库存、预留锁定、采购入库或跨组共享额度。同规格沿用统一加工口径，在5000节点内精确搜索；已有可行解但未搜完时不称最优，尚无可行解且预算耗尽时不判供应不足，仅搜索穷尽或容量证明确认无法满足。剩余可供根数=核定上限−本方案选用，非入库余料；余料台账独立，净工程量及报价不变。不同截面、有限库存的实际占用/回用、供应价格和跨表统筹另核。'


def validate(config,options,prefix,dimensions,refs):
    if not isinstance(config,dict) or set(config)!={'quantities','scope_confirmed','measurement_basis','evidence_id'}:raise DomainError('有限供应须填写全部候选可用根数、范围确认、供应说明和原件')
    if config['scope_confirmed'] is not True:raise DomainError('须确认本次各定尺可用根数上限及供应范围，不表示库存预留或入库')
    text(config['measurement_basis'],'有限供应范围说明');refs[prefix+'.limits_basis']=text(config['evidence_id'],'有限供应范围原件',200)
    rows=config['quantities']
    if not isinstance(rows,list) or len(rows)!=len(options):raise DomainError('有限供应须按S1至全部候选顺序逐项填写根数，不得缺项')
    limits=[]
    for i,row in enumerate(rows,1):
        if not isinstance(row,dict) or set(row)!={'quantity','evidence_id'}:raise DomainError('每种供应上限仅含可用根数和原件')
        n=number(row['quantity'])
        if n!=n.to_integral_value() or n>200:raise DomainError('每种定尺供应上限须为0至200整数，0表示本次不可用')
        key=prefix+'.supply_limits.'+str(i);dimensions[key]=str(row['quantity']);refs[key]=text(row['evidence_id'],'可用根数原件',200);limits.append(int(n))
    return limits


def pack(sizes,stocks,trim,limits,limit=5000):
    trims=trim if isinstance(trim,list) else [trim]*len(stocks);caps=[s-t for s,t in zip(stocks,trims)];order=sorted((j for j,n in enumerate(limits) if n),key=lambda j:(stocks[j],j))
    failure='按核定供应上限无法完成下料，已由容量约束或穷尽搜索证明；请补充供应或重新核定'
    if not order or max(caps[j] for j in order)<max(sizes):raise DomainError(failure)
    maximum=max(caps[j] for j in order);minimum_trim=min(trims[j] for j in order)
    count_lower=max((sum(sizes)+maximum-1)//maximum,max((sum(s>=t for s in sizes)+(maximum//t)-1)//(maximum//t) for t in set(sizes)))
    lower=(sum(sizes)+minimum_trim*count_lower,count_lower)
    def score(plan):return sum(stocks[j] for j,_ in plan),len(plan)
    def greedy(preference):
        plan=[];loads=[];used=[0]*len(stocks)
        for i,size in enumerate(sizes):
            for k,(j,ps) in enumerate(plan):
                if loads[k]+size<=caps[j]:loads[k]+=size;ps.append(i);break
            else:
                choice=next((j for j in preference if used[j]<limits[j] and caps[j]>=size),None)
                if choice is None:return None
                used[choice]+=1;plan.append((choice,[i]));loads.append(size)
        return plan
    seeds=[greedy(p) for p in [order,list(reversed(order))]+[[j]+[k for k in order if k!=j] for j in order]]
    valid=[p for p in seeds if p is not None];best=min(valid,key=score) if valid else None;initial=score(best) if best is not None else (None,None)
    nodes=0;aborted=False;seen=set();bins=[];loads=[];used=[0]*len(stocks);suffix=[0]*(len(sizes)+1)
    for i in range(len(sizes)-1,-1,-1):suffix[i]=suffix[i+1]+sizes[i]
    def search(i,cost):
        nonlocal best,nodes,aborted
        if aborted or (best is not None and score(best)==lower):return
        frees=[caps[j]-loads[k] for k,(j,_) in enumerate(bins)];remaining=[limits[j]-used[j] for j in range(len(stocks))]
        if suffix[i]>sum(frees)+sum(remaining[j]*caps[j] for j in order):return
        # Length-threshold capacity bound, including free space in opened bars.
        if i<len(sizes):
            t=sizes[i]
            if sum(s>=t for s in sizes[i:])>sum(f//t for f in frees)+sum(remaining[j]*(caps[j]//t) for j in order):return
        missing=max(0,suffix[i]-sum(frees));new_count=(missing+maximum-1)//maximum
        if i<len(sizes) and not any(f>=sizes[i] for f in frees):new_count=max(1,new_count)
        if best is not None and (cost+missing+minimum_trim*new_count,len(bins)+new_count)>=score(best):return
        if nodes>=limit:aborted=True;return
        nodes+=1
        if i==len(sizes):best=[(j,ps[:]) for j,ps in bins];return
        state=(i,tuple(sorted((j,loads[k]) for k,(j,_) in enumerate(bins))))
        if state in seen:return
        seen.add(state);same=set();size=sizes[i]
        for k,(j,ps) in enumerate(bins):
            if (j,loads[k]) in same or loads[k]+size>caps[j]:continue
            same.add((j,loads[k]));loads[k]+=size;ps.append(i);search(i+1,cost);ps.pop();loads[k]-=size
            if aborted or (best is not None and score(best)==lower):return
        for j in order:
            if used[j]>=limits[j] or caps[j]<size or (best is not None and cost+stocks[j]>score(best)[0]):continue
            used[j]+=1;bins.append((j,[i]));loads.append(size);search(i+1,cost+stocks[j]);loads.pop();bins.pop();used[j]-=1
            if aborted or (best is not None and score(best)==lower):return
    search(0,0)
    if best is None:raise DomainError('搜索预算耗尽，尚未找到可行方案，不能据此判断供应不足；请拆分范围或调整候选后重新核定' if aborted else failure)
    final=score(best);status='length_optimal_bound' if final==lower else 'length_feasible_unproven' if aborted else 'length_optimal_search'
    return best,dict(objective='purchased_length_then_stock_count',supply_model='finite_limits',status=status,length_lower_bound=lower[0],stock_count_lower_bound=count_lower,initial_purchased_length=initial[0],initial_stock_count=initial[1],purchased_length=final[0],stock_count=final[1],visited_nodes=nodes,node_limit=limit,search_exhausted=not aborted and final!=lower,unproven_length_gap=final[0]-lower[0] if aborted else 0)
