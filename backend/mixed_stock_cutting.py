"""Bounded deterministic mixed-length cutting, with explicit proof status."""
from decimal import Decimal
from fractions import Fraction
from math import lcm
from .compound_takeoff import op,text
from .engine import DomainError,number,quantity_number,precise
from .measurement_units import LENGTH,VERSION as UNIT_VERSION
from .planar_takeoff import linear
from .stock_cutting import PARAMETERS,dec

VERSION='railing-mixed-length-cutting-1.0'
SEARCH_LIMIT=5000
NOTICE='仅在本材料表明确选择并有据确认同材质、同截面可混排的定长件及立柱之间排料，单位长度质量须一致；不因名称相似自动认定兼容。每组2至8项、全部重复后最多200件，全表混排最多400件，单一原材定尺，每件一次锯缝含最后一件，两端切除另计。先按长件优先生成可行方案，再在5000个搜索节点内寻找更少根数；达到长度下界或搜索穷尽才标已证明最少，预算耗尽则明确未证明最优，不把可行方案冒充最优。按原件阈值区分报废及待留用余料；不自动入库、回用库存或改变净工程量和报价。组内项不得同时启用单项定尺或比例备料，不同组不能重复占用同一材料项。多原材规格、供应价格、不同截面、接头及施工定位仍须另核。'
LEDGER=('net_length','kerf_length','trim_length','scrap_length','retained_length','loss_length','purchased_length')


def pack(sizes,capacity,limit=SEARCH_LIMIT):
    """First-fit upper bound, then symmetry-pruned exact search until budget."""
    best=[];loads=[]
    for i,size in enumerate(sizes):
        for j,used in enumerate(loads):
            if used+size<=capacity:loads[j]+=size;best[j].append(i);break
        else:loads.append(size);best.append([i])
    volume_lower=(sum(sizes)+capacity-1)//capacity
    cardinality_lower=max((sum(s>=t for s in sizes)+(capacity//t)-1)//(capacity//t) for t in set(sizes))
    lower=max(volume_lower,cardinality_lower);initial=len(best);nodes=0;aborted=False;seen=set();bins=[];used=[]
    def search(i):
        nonlocal best,nodes,aborted
        if len(best)==lower or aborted:return
        if nodes>=limit:aborted=True;return
        nodes+=1
        if i==len(sizes):best=[v[:] for v in bins];return
        state=(i,tuple(sorted(used)))
        if state in seen:return
        seen.add(state);equivalent=set();size=sizes[i]
        for j,load in enumerate(used):
            if load in equivalent or load+size>capacity:continue
            equivalent.add(load);used[j]+=size;bins[j].append(i);search(i+1);bins[j].pop();used[j]-=size
            if aborted or len(best)==lower:return
        if len(bins)+1<len(best):
            bins.append([i]);used.append(size);search(i+1);used.pop();bins.pop()
    if initial>lower:search(0)
    status='optimal_bound' if len(best)==lower else 'feasible_unproven' if aborted else 'optimal_search'
    return best,dict(status=status,lower_bound=lower,volume_lower_bound=volume_lower,cardinality_lower_bound=cardinality_lower,initial_stock_count=initial,stock_count=len(best),visited_nodes=nodes,node_limit=limit,search_exhausted=not aborted and len(best)>lower,unproven_gap=len(best)-lower if status=='feasible_unproven' else 0)


@precise
def calculate(configs,rows,groups):
    if not isinstance(configs,list) or not 1<=len(configs)<=10:raise DomainError('混合下料须明确1至10组，关闭时移除配置，不得传空列表')
    by_name={r['name']:r for r in rows};occupied=set();names=set();out=[];dimensions={};refs={};conversions=[];piece_total=0
    for gi,c in enumerate(configs,1):
        prefix='material.mixed_cutting.'+str(gi)
        fields=set(PARAMETERS)|{'name','members','length_unit','material_basis','compatibility_confirmed','cut_basis_confirmed','measurement_basis','evidence'}
        if isinstance(c,dict) and 'stock_choices' in c:fields.add('stock_choices')
        if not isinstance(c,dict) or set(c)!=fields:raise DomainError('混合下料须完整填写组名、材料项、材质截面确认、下料参数、说明和原件')
        proofs=set(PARAMETERS)|{'material_basis','measurement_basis'}
        if not isinstance(c['evidence'],dict) or set(c['evidence'])!=proofs:raise DomainError('混排参数、材质截面及范围说明须分别关联原件')
        name=text(c['name'],'混排组名',200)
        if name in names:raise DomainError('混排组名不得重复')
        names.add(name);text(c['material_basis'],'同材质同截面兼容说明');text(c['measurement_basis'],'混排下料范围说明')
        if c['compatibility_confirmed'] is not True or c['cut_basis_confirmed'] is not True:raise DomainError('须据原件确认同材质同截面可混排及完整下料、每件锯缝与切除未重复')
        members=c['members']
        if not isinstance(members,list) or not 2<=len(members)<=8 or any(not isinstance(m,str) for m in members):raise DomainError('每组混排须明确2至8个当前材料项名称')
        if len(set(members))!=len(members) or occupied.intersection(members):raise DomainError('混排材料项不能在组内或不同组中重复占用')
        if any(m not in by_name for m in members):raise DomainError('混排引用的材料项已改名或不属于当前材料表，请重新核定')
        occupied.update(members);selected=[r for r in rows if r['name'] in members]
        if any('single_member_length' not in r or r['mode']=='continuous' or 'cutting_plan' in r or 'stock_allowance' in r for r in selected):raise DomainError('混排只接受定长件及立柱；请先关闭组内项的单项定尺或比例备料')
        if len({Fraction(r['normalized_unit_weight']) for r in selected})!=1:raise DomainError('混排各项单位长度质量必须一致，不能将不同截面合并排料')
        normalized={};asts={}
        for k in PARAMETERS:
            number(c[k],k in ('stock_length','reusable_min_length'));v=linear(c[k],c['length_unit'],k in ('stock_length','reusable_min_length'));normalized[k]=Fraction(v)
            key=prefix+'.'+k;dimensions[key]=str(c[k]);asts[k]=op('multiply',{'dimension':key},{'constant':LENGTH[c['length_unit']]})
            conversions.append(dict(parameter=key,original_value=str(c[k]),source_unit=c['length_unit'],factor=LENGTH[c['length_unit']],canonical_value=str(v),canonical_unit='m',rule_version=UNIT_VERSION))
        for k in proofs:refs[prefix+'.'+k]=text(c['evidence'][k],'混排逐参数原件',200)
        stock=normalized['stock_length'];kerf=normalized['kerf_per_piece'];trim=normalized['trim_start']+normalized['trim_end'];usable=stock-trim
        if usable<=0:raise DomainError('混排原材须大于两端切除总长')
        multi='stock_choices' in c;options=[dict(index=1,length=stock,ast=asts['stock_length'])]
        if multi:
            from . import multi_stock_cutting as multi_cut
            options=multi_cut.choices(c['stock_choices'],stock,asts['stock_length'],trim,c['length_unit'],prefix,dimensions,refs,conversions)
        pieces=[];demands=[]
        for r in selected:
            n=Fraction(r['normalized_quantity'])*Fraction(groups);cut=Fraction(r['single_member_length'])
            if n.denominator!=1 or not 1<=n<=200:raise DomainError('混排各项全部重复后件数须为1至200整数')
            if cut+kerf>max(o['length']-trim for o in options):raise DomainError(r['name']+' 单件下料加锯缝超出混排原材可用长')
            demands.append(dict(item_index=r['index'],name=r['name'],piece_count=int(n),cut_length=dec(cut),count_formula_ast=op('multiply',r['formula_ast']['args'][1],{'dimension':'multiplicity'})))
            pieces.extend(dict(item_index=r['index'],name=r['name'],piece_index=j,cut=cut,pitch=cut+kerf,ast=r['formula_ast']['args'][0]) for j in range(1,int(n)+1))
        piece_total+=len(pieces)
        if len(pieces)>200 or piece_total>400:raise DomainError('每组混排最多200件，全表最多400件，请按实际范围拆分')
        pieces.sort(key=lambda p:(-p['pitch'],p['item_index'],p['piece_index']))
        if multi and any(o['length']-trim<min(p['pitch'] for p in pieces) for o in options):raise DomainError('每种候选定尺至少须能下料一件所选材料')
        scale=lcm(trim.denominator,*[o['length'].denominator for o in options],*[p['pitch'].denominator for p in pieces]);sizes=[int(p['pitch']*scale) for p in pieces]
        if multi:
            packed,proof=multi_cut.pack(sizes,[int(o['length']*scale) for o in options],int(trim*scale),limit=SEARCH_LIMIT);proof=multi_cut.proof_lengths(proof,scale)
        else:
            packed,proof=pack(sizes,int(usable*scale),limit=SEARCH_LIMIT);packed=[(0,indices) for indices in packed]
        values={k:Fraction(0) for k in LEDGER};formulas={k:[] for k in LEDGER};bars=[]
        for bi,(choice,indices) in enumerate(packed,1):
            stock=options[choice]['length'];stock_ast=options[choice]['ast'];usable=stock-trim
            ps=[pieces[j] for j in indices];net=sum((p['cut'] for p in ps),Fraction(0));kerf_total=len(ps)*kerf;offcut=usable-net-kerf_total
            disposition='none' if offcut==0 else 'retain' if offcut>=normalized['reusable_min_length'] else 'scrap'
            v=dict(net_length=net,kerf_length=kerf_total,trim_length=trim,scrap_length=offcut if disposition=='scrap' else Fraction(0),retained_length=offcut if disposition=='retain' else Fraction(0),purchased_length=stock)
            v['loss_length']=v['kerf_length']+v['trim_length']+v['scrap_length']
            net_ast=op('add',*[p['ast'] for p in ps]);kerf_ast=op('multiply',asts['kerf_per_piece'],{'constant':str(len(ps))});trim_ast=op('add',asts['trim_start'],asts['trim_end']);off_ast=op('subtract',stock_ast,op('add',net_ast,kerf_ast,trim_ast))
            fa=dict(net_length=net_ast,kerf_length=kerf_ast,trim_length=trim_ast,scrap_length=off_ast if disposition=='scrap' else {'constant':'0'},retained_length=off_ast if disposition=='retain' else {'constant':'0'},purchased_length=stock_ast);fa['loss_length']=op('add',kerf_ast,trim_ast,fa['scrap_length'])
            position=normalized['trim_start'];segments=[]
            for p in ps:
                segments.append(dict(item_index=p['item_index'],name=p['name'],piece_index=p['piece_index'],from_s=dec(position),to_s=dec(position+p['cut']),kerf_to_s=dec(position+p['pitch']),cut_length=dec(p['cut'])))
                position+=p['pitch']
            for k in LEDGER:values[k]+=v[k];formulas[k].append(fa[k])
            bars.append(dict(index=bi,piece_count=len(ps),pieces=segments,offcut_length=dec(offcut),offcut_disposition=disposition,values={k:dec(x) for k,x in v.items()},formula_asts=fa))
            if multi:bars[-1]['stock_choice_index']=options[choice]['index']
        if values['purchased_length']!=values['net_length']+values['loss_length']+values['retained_length']:raise DomainError('混排长度台账未闭合')
        weight=Fraction(selected[0]['normalized_unit_weight']);weight_ast=selected[0]['formula_ast']['args'][2];total_asts={k:op('add',*v) for k,v in formulas.items()}
        total_asts.update({k.replace('_length','_mass'):op('multiply',v,weight_ast) for k,v in list(total_asts.items())})
        total={**values,**{k.replace('_length','_mass'):v*weight for k,v in values.items()}}
        out.append(dict(**c,index=gi,normalized_parameters={k:dec(v) for k,v in normalized.items()},demands=demands,required_piece_count=len(pieces),stock_count=len(bars),normalized_unit_weight=dec(weight),proof=proof,bars=bars,total={k:dec(v) for k,v in total.items()},total_formula_asts=total_asts,balance_difference='0',rule_version=VERSION,notice=NOTICE))
        if multi:
            out[-1].update(stock_selection=dict(objective='purchased_length_then_stock_count',options=[dict(index=o['index'],stock_length=dec(o['length']),stock_count=sum(b['stock_choice_index']==o['index'] for b in bars)) for o in options]),rule_version=multi_cut.VERSION,notice=multi_cut.NOTICE)
    multi_table=any('stock_selection' in g for g in out)
    return dict(groups=out,covered_item_count=len(occupied),uncovered_item_count=len(rows)-len(occupied),stock_count=sum(g['stock_count'] for g in out),total={k:str(quantity_number(sum((Decimal(g['total'][k]) for g in out),Decimal(0)))) for k in out[0]['total']},total_formula_asts={k:op('add',*[g['total_formula_asts'][k] for g in out]) for k in out[0]['total']},dimensions=dimensions,source_evidence=refs,unit_conversions=conversions,rule_version=multi_cut.VERSION if multi_table else VERSION,notice=multi_cut.NOTICE if multi_table else NOTICE)
