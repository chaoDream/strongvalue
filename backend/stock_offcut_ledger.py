"""Chronological offcut movement replay, including measured child-lot admissions."""
from . import store, stock_inventory as inv


def replay(p,base_lots):
    from . import stock_offcut_reuse as reuse, stock_offcut_returns as returns, stock_offcut_processing as processing, stock_offcut_jobs as job_module, stock_offcut_counts as counts, stock_offcut_issues as issue_module
    from .stock_replenishments import audit
    audit(p,offcut=True)
    from .stock_mixed_supply import audit as mixed_audit
    mixed_audit(p)
    lots=[dict(l) for l in base_lots if l.get('origin')!='offcut_processing'];by_key={l['lot_key']:l for l in lots}
    movements={k:dict(issued_quantity=0,returned_quantity=0,processed_quantity=0,processing_quantity=0,adjustment_gain=0,adjustment_loss=0,count_revision=0) for k in by_key}
    records={r['id']:r for name in (reuse.COLLECTION,returns.COLLECTION,processing.COLLECTION,job_module.COLLECTION,counts.COLLECTION,issue_module.COLLECTION) for r in p[name]}
    all_results={r['id']:r for name in ('stock_processing_results',processing.COLLECTION) for r in p[name]}
    released_events=set();used=set();active=set();applied=set();stopped=set();issues={};jobs={};taken={};released=set()
    receipt_ids={(r['input']['warehouse'].strip(),r['input']['lot_code'].strip()) for r in p['stock_receipts']}

    def admit(lot):
        identity=(lot['warehouse'].strip(),lot['lot_code'].strip())
        if identity in used or identity in receipt_ids:inv.fail('实物余料编号重复或与完整原材批号冲突')
        used.add(identity);active.add(lot['lot_key'])

    for e in p['stock_events']:
        kind=e['operation']
        if kind=='recover':
            for lot in lots:
                if lot['result_id']==e['target_id']:admit(lot)
        if kind=='offcut_void':
            lot=by_key.get(e['target_id']);row=all_results.get(e.get('result_id'))
            if not lot or lot['lot_key'] not in active or lot['lot_key'] in stopped or not row or lot['result_id']!=row['id'] or e.get('lot_code')!=lot['lot_code'] or e.get('record_hash')!=store.digest(row):inv.fail('余料停用事件、入库顺序或验收关联无法核验')
            m=movements[lot['lot_key']]
            if m['issued_quantity']-m['returned_quantity'] or m['processed_quantity']:inv.fail('余料停用仍有在外或已加工原段，不能伪造停用顺序')
            stopped.add(lot['lot_key'])
        if kind not in ('offcut_issue','offcut_release','offcut_return','offcut_return_cancel','offcut_process','offcut_process_cancel','offcut_start','offcut_start_cancel','offcut_count','offcut_count_cancel','offcut_partial_issue','offcut_partial_cancel','offcut_release_remaining'):continue
        row=records.get(e['target_id'])
        expected_collection=issue_module.COLLECTION if kind.startswith('offcut_partial') else counts.COLLECTION if kind.startswith('offcut_count') else job_module.COLLECTION if kind.startswith('offcut_start') else processing.COLLECTION if kind.startswith('offcut_process') else returns.COLLECTION if kind.startswith('offcut_return') else reuse.COLLECTION
        if not row or not any(r['id']==row['id'] for r in p[expected_collection]) or row['id'] in applied or e.get('record_hash')!=store.digest(row):inv.fail('余料事件、原单类型或历史关联无法核验')
        applied.add(row['id'])
        if kind in ('offcut_release','offcut_release_remaining'):
            prior=taken.get(row['id'],{})
            if row['id'] in released:inv.fail('余料预留重复释放')
            if kind=='offcut_release':
                if any(prior.values()):inv.fail('已有分批领用不能整单释放')
            else:
                selections=[dict(allocation_index=i,quantity=a['quantity']-prior.get(i,0)) for i,a in enumerate(row['allocations'],1) if a['quantity']>prior.get(i,0)]
                if not any(prior.values()) or not selections:inv.fail('未领余量释放缺少先前分批领用或尚未领余额')
                values,total=issue_module.selection_values(row,selections)
                if e.get('allocations')!=values or e.get('totals')!=total:inv.fail('余料未领余量释放分配或数量无法核验')
            released.add(row['id']);released_events.add(e['id']);continue
        if kind in ('offcut_partial_cancel','offcut_return_cancel','offcut_process_cancel','offcut_start_cancel','offcut_count_cancel'):continue
        if not inv.approved(p,expected_collection,row):inv.fail('余料已执行单据缺少匹配的独立复核')
        if kind=='offcut_count':
            lot=by_key.get(row.get('lot_key'))
            if not lot or lot['lot_key'] not in active or lot['lot_key'] in stopped:inv.fail('盘点早于余料入库或晚于停用')
            m=movements[lot['lot_key']];computed=counts.normalize(row['input'],lot,m)
            if any(row.get(k)!=v for k,v in computed.items()) or any(e.get(k)!=row[k] for k in ('lot_key','before','after','delta','length_delta','mass_delta')):inv.fail('盘点实盘、差异或冻结事件无法核验')
            m['adjustment_gain']+=max(0,row['delta']);m['adjustment_loss']+=max(0,-row['delta']);m['count_revision']+=1
        elif kind=='offcut_start':
            computed=job_module.normalize(p,row['input'])
            if any(row[k]!=v for k,v in computed.items()) or e.get('reference_id')!=row['reference_id'] or e.get('allocations')!=row['allocations'] or e.get('totals')!=row['totals']:inv.fail('实际投入加工规格、根数或来源关联无法核验')
            source=issues.get(row['reference_id'])
            if not source:inv.fail('实际投入加工缺少先前已执行领用')
            jobs[row['id']]={a['allocation_index']:dict(**a,accounted=0) for a in row['allocations']}
            for a in row['allocations']:
                parent=source.get(a['allocation_index'])
                if not parent or a['quantity']>parent['quantity']-parent['returned']-parent['consumed']-parent['processing']:inv.fail('实际投入超出对应领用分配未加工且未退回余额')
                parent['processing']+=a['quantity'];movements[a['lot_key']]['processing_quantity']+=a['quantity']
        elif kind=='offcut_process':
            computed,_=processing.normalize(p,row['input'])
            if any(row[k]!=v for k,v in computed.items()) or e.get('reference_id')!=row['reference_id'] or e.get('rows')!=row['rows'] or e.get('totals')!=row['totals']:inv.fail('余料再加工实测、去向或来源关联无法核验')
            source=issues.get(row['reference_id'])
            if not source:inv.fail('余料再加工缺少先前已执行领用')
            for a in row['rows']:
                parent=source.get(a['allocation_index'])
                if not parent:inv.fail('余料再加工分配缺少原领用关联')
                if row.get('job_id'):
                    job=jobs.get(row['job_id'],{}).get(a['allocation_index'])
                    if not job or a['quantity']>job['quantity']-job['accounted'] or a['quantity']>parent['processing']:inv.fail('结果验收超出本单已投入且尚未验收余额，或早于实际投入')
                    job['accounted']+=a['quantity'];parent['processing']-=a['quantity'];movements[a['parent_lot_key']]['processing_quantity']-=a['quantity']
                elif a['quantity']>parent['quantity']-parent['returned']-parent['consumed']-parent['processing']:inv.fail('余料再加工超出对应领用分配尚未加工且未退回余额')
                parent['consumed']+=a['quantity'];movements[a['parent_lot_key']]['processed_quantity']+=a['quantity']
                for child in processing.child_lots(row,a):
                    if child['lot_key'] in by_key:inv.fail('再加工余料重复入库')
                    admit(child);lots.append(child);by_key[child['lot_key']]=child;movements[child['lot_key']]=dict(issued_quantity=0,returned_quantity=0,processed_quantity=0,processing_quantity=0,adjustment_gain=0,adjustment_loss=0,count_revision=0)
        elif kind=='offcut_return':
            computed=returns.normalize(p,row['input'])
            if any(row[k]!=v for k,v in computed.items()) or e.get('reference_id')!=row['reference_id'] or e.get('allocations')!=row['allocations'] or e.get('totals')!=row['totals']:inv.fail('原段退回规格、根数或关联无法核验')
            source=issues.get(row['reference_id'])
            if not source:inv.fail('余料退回缺少先前已执行领用')
            for a in row['allocations']:
                parent=source.get(a['allocation_index'])
                if not parent or a['quantity']>parent['quantity']-parent['returned']-parent['consumed']-parent['processing']:inv.fail('退回超出对应领用分配尚未加工且未退回余额')
                parent['returned']+=a['quantity'];movements[a['lot_key']]['returned_quantity']+=a['quantity']
        else:
            if kind=='offcut_partial_issue':
                computed=issue_module.normalize(p,row['input']);reservation=next((r for r in p[reuse.COLLECTION] if r['id']==row['reservation_id']),None)
                if not reservation or not inv.approved(p,reuse.COLLECTION,reservation) or any(row[k]!=v for k,v in computed.items()) or e.get('reservation_id')!=reservation['id']:inv.fail('余料分批来源预留、审核或冻结分配无法核验')
                original,_=reuse.allocation_values(reservation['demand'],reservation['input']['allocations'],lots)
                if original!=reservation['allocations']:inv.fail('原余料预留冻结分配与实际规格不符')
                allocations,totals=row['allocations'],row['totals'];indexed=reuse.issue_allocations(row)
            else:
                if 'replenishment' in row or 'mixed_supply' in row:inv.fail('供应核定预留不得冒充实际余料领用批单')
                reservation=row;allocations,totals=reuse.allocation_values(row['demand'],row['input']['allocations'],lots)
                if allocations!=row['allocations'] or totals!=row['totals'] or any(taken.get(row['id'],{}).values()):inv.fail('整单余料领用重复或覆盖既有分批')
                indexed={i:a for i,a in enumerate(allocations,1)}
            if 'mixed_supply' in reservation:
                from .stock_mixed_supply import reviewed
                reviewed(p,reservation)
            if 'replenishment' in reservation and reservation['replenishment']['release_id'] not in released_events:inv.fail('余料补料领用早于上游未领释放')
            if reservation['id'] in released or e.get('allocations')!=allocations or e.get('totals')!=totals:inv.fail('余料领用晚于释放或冻结数量不符')
            before=taken.setdefault(reservation['id'],{})
            for i,a in indexed.items():
                before[i]=before.get(i,0)+a['quantity']
                if before[i]>reservation['allocations'][i-1]['quantity']:inv.fail('余料累计领用超出原预留逐项根数')
            issues[row['id']]={i:dict(**a,returned=0,consumed=0,processing=0) for i,a in indexed.items()}
            for a in allocations:
                if a['lot_key'] not in active or a['lot_key'] in stopped:inv.fail('余料领用早于实际入库或晚于停用')
                m=movements[a['lot_key']];m['issued_quantity']+=a['quantity']
                if m['issued_quantity']-m['returned_quantity']>by_key[a['lot_key']]['quantity']+m['adjustment_gain']-m['adjustment_loss']:inv.fail('余料领用超出实际在库数量')
    for lot in lots:
        key=lot['lot_key'];m=movements[key];custody=m['issued_quantity']-m['returned_quantity']-m['processed_quantity']-m['processing_quantity'];adjusted=lot['quantity']+m['adjustment_gain']-m['adjustment_loss'];on_hand=adjusted-m['issued_quantity']+m['returned_quantity'];reserved=reuse.held(p,key)
        if min(custody,on_hand,m['processing_quantity'],m['processed_quantity'])<0 or reserved>on_hand or on_hand+custody+m['processed_quantity']+m['processing_quantity']!=adjusted:inv.fail('余料在库、在外、已加工或预留台账不闭合')
        lot['voided']=key in stopped
        m.update(adjusted_quantity=adjusted,in_stock_quantity=on_hand,in_custody=custody,reserved=reserved,remaining=on_hand-reserved)
    return dict(lots=lots,movements=movements,issues=issues,jobs=jobs)
