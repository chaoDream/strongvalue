"""Exact integer multi-stock selection; minimize purchased length, then bars."""
from fractions import Fraction
from .compound_takeoff import op,text
from .engine import DomainError,number
from .measurement_units import LENGTH,VERSION as UNIT_VERSION
from .planar_takeoff import linear
from .stock_cutting import dec

VERSION='railing-multi-stock-cutting-1.0'
NOTICE='仅对本组已据原件确认可按需供应、同材质同截面的2至5种定尺联合选材；所有规格沿用同一锯缝、两端切除及余料阈值。目标先最小化购入总长度，长度相同再最小化根数，不等同于最低采购价格或最少根数。采用精确长度比较，在5000节点内搜索；仅达到双目标下界或搜索穷尽时标已证明，耗尽预算仅保留可行方案。各规格按需供应不含库存或供应数量约束，缺货、不同端切/加工口径须另核。余料只列计划、不自动入库或回用；净工程量、资源消耗及报价保持原口径。'


def choices(config,stock,stock_ast,trim,unit,prefix,dimensions,refs,conversions):
    c=config
    if not isinstance(c,dict) or set(c)!={'alternatives','supply_confirmed','measurement_basis','evidence_id'}:raise DomainError('多定尺须填写备选长度、按需供应确认、供应与选择口径说明及原件')
    if c['supply_confirmed'] is not True:raise DomainError('须据原件确认各定尺同规格且可按需供应；限量库存不得用此模式')
    text(c['measurement_basis'],'多定尺供应与选择口径说明');refs[prefix+'.supply_basis']=text(c['evidence_id'],'多定尺供应口径原件',200)
    items=c['alternatives']
    if not isinstance(items,list) or not 1<=len(items)<=4:raise DomainError('须填写1至4种额外定尺，与原定尺合计2至5种')
    result=[dict(index=1,length=stock,ast=stock_ast)]
    for i,item in enumerate(items,2):
        if not isinstance(item,dict) or set(item)!={'stock_length','evidence_id'}:raise DomainError('每种备选定尺仅含原始长度和原件')
        number(item['stock_length'],True);v=linear(item['stock_length'],unit,True);length=Fraction(v)
        if length<=trim:raise DomainError('每种备选定尺须大于两端切除总长')
        if any(x['length']==length for x in result):raise DomainError('备选原材定尺不得与原定尺或其他备选重复')
        key=prefix+'.choices.'+str(i);dimensions[key]=str(item['stock_length']);refs[key]=text(item['evidence_id'],'备选原材定尺原件',200)
        ast=op('multiply',{'dimension':key},{'constant':LENGTH[unit]});result.append(dict(index=i,length=length,ast=ast))
        conversions.append(dict(parameter=key,original_value=str(item['stock_length']),source_unit=unit,factor=LENGTH[unit],canonical_value=str(v),canonical_unit='m',rule_version=UNIT_VERSION))
    return result


def pack(sizes,stocks,trim,limit=5000):
    """Stocks are integer full lengths. Returned choices index the input list."""
    capacities=[s-trim for s in stocks];order=sorted(range(len(stocks)),key=lambda j:(stocks[j],j))
    def score(plan):return sum(stocks[j] for j,_ in plan),len(plan)
    candidates=[]
    for allowed in [order]+[[j] for j in order if capacities[j]>=max(sizes)]:
        plan=[];loads=[]
        for i,size in enumerate(sizes):
            for k,(j,ps) in enumerate(plan):
                if loads[k]+size<=capacities[j]:ps.append(i);loads[k]+=size;break
            else:
                j=next(j for j in allowed if capacities[j]>=size);plan.append((j,[i]));loads.append(size)
        candidates.append(plan)
    best=min(candidates,key=score);initial=score(best);maximum=max(capacities)
    count_lower=max((sum(sizes)+maximum-1)//maximum,max((sum(s>=t for s in sizes)+(maximum//t)-1)//(maximum//t) for t in set(sizes)))
    length_lower=sum(sizes)+trim*count_lower;lower=(length_lower,count_lower)
    nodes=0;aborted=False;seen=set();bins=[];loads=[];suffix=[0]*(len(sizes)+1)
    for i in range(len(sizes)-1,-1,-1):suffix[i]=suffix[i+1]+sizes[i]
    def search(i,cost):
        nonlocal best,nodes,aborted
        if score(best)==lower or aborted:return
        free=sum(capacities[j]-loads[k] for k,(j,_) in enumerate(bins));missing=max(0,suffix[i]-free)
        new_count=(missing+maximum-1)//maximum
        if i<len(sizes) and not any(capacities[j]-loads[k]>=sizes[i] for k,(j,_) in enumerate(bins)):new_count=max(new_count,1)
        if (cost+missing+trim*new_count,len(bins)+new_count)>=score(best):return
        if nodes>=limit:aborted=True;return
        nodes+=1
        if i==len(sizes):best=[(j,ps[:]) for j,ps in bins];return
        state=(i,tuple(sorted((j,loads[k]) for k,(j,_) in enumerate(bins))))
        if state in seen:return
        seen.add(state);equivalent=set();size=sizes[i]
        for k,(j,ps) in enumerate(bins):
            if (j,loads[k]) in equivalent or loads[k]+size>capacities[j]:continue
            equivalent.add((j,loads[k]));loads[k]+=size;ps.append(i);search(i+1,cost);ps.pop();loads[k]-=size
            if aborted or score(best)==lower:return
        for j in order:
            if capacities[j]<size or cost+stocks[j]>score(best)[0]:continue
            bins.append((j,[i]));loads.append(size);search(i+1,cost+stocks[j]);loads.pop();bins.pop()
            if aborted or score(best)==lower:return
    search(0,0);final=score(best)
    status='length_optimal_bound' if final==lower else 'length_feasible_unproven' if aborted else 'length_optimal_search'
    return best,dict(objective='purchased_length_then_stock_count',status=status,length_lower_bound=length_lower,stock_count_lower_bound=count_lower,initial_purchased_length=initial[0],initial_stock_count=initial[1],purchased_length=final[0],stock_count=final[1],visited_nodes=nodes,node_limit=limit,search_exhausted=not aborted and final!=lower,unproven_length_gap=final[0]-length_lower if aborted else 0)


def proof_lengths(proof,scale):
    for key in ('length_lower_bound','initial_purchased_length','purchased_length','unproven_length_gap'):proof[key]=dec(Fraction(proof[key],scale))
    return proof
