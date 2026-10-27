"""Chronological offcut movement replay, including measured child-lot admissions."""
from . import store, stock_inventory as inv


def replay(p,base_lots):
    from . import stock_offcut_reuse as reuse, stock_offcut_returns as returns, stock_offcut_processing as processing, stock_offcut_jobs as job_module
    lots=[dict(l) for l in base_lots if l.get('origin')!='offcut_processing'];by_key={l['lot_key']:l for l in lots}
    movements={k:dict(issued_quantity=0,returned_quantity=0,processed_quantity=0,processing_quantity=0) for k in by_key}
    records={r['id']:r for name in (reuse.COLLECTION,returns.COLLECTION,processing.COLLECTION,job_module.COLLECTION) for r in p[name]}
    all_results={r['id']:r for name in ('stock_processing_results',processing.COLLECTION) for r in p[name]}
    used=set();active=set();applied=set();stopped=set();issues={};jobs={}
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
        if kind not in ('offcut_issue','offcut_release','offcut_return','offcut_return_cancel','offcut_process','offcut_process_cancel','offcut_start','offcut_start_cancel'):continue
        row=records.get(e['target_id'])
        expected_collection=job_module.COLLECTION if kind.startswith('offcut_start') else processing.COLLECTION if kind.startswith('offcut_process') else returns.COLLECTION if kind.startswith('offcut_return') else reuse.COLLECTION
        if not row or not any(r['id']==row['id'] for r in p[expected_collection]) or row['id'] in applied or e.get('record_hash')!=store.digest(row):inv.fail('余料事件、原单类型或历史关联无法核验')
        applied.add(row['id'])
        if kind in ('offcut_release','offcut_return_cancel','offcut_process_cancel','offcut_start_cancel'):continue
        if not inv.approved(p,expected_collection,row):inv.fail('余料已执行单据缺少匹配的独立复核')
        if kind=='offcut_start':
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
                    admit(child);lots.append(child);by_key[child['lot_key']]=child;movements[child['lot_key']]=dict(issued_quantity=0,returned_quantity=0,processed_quantity=0,processing_quantity=0)
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
            allocations,totals=reuse.allocation_values(row['demand'],row['input']['allocations'],lots)
            if allocations!=row['allocations'] or totals!=row['totals'] or e.get('allocations')!=row['allocations'] or e.get('totals')!=row['totals']:inv.fail('余料领用规格、数量或总长无法核验')
            issues[row['id']]={i:dict(**a,returned=0,consumed=0,processing=0) for i,a in enumerate(allocations,1)}
            for a in allocations:
                if a['lot_key'] not in active or a['lot_key'] in stopped:inv.fail('余料领用早于实际入库或晚于停用')
                m=movements[a['lot_key']];m['issued_quantity']+=a['quantity']
                if m['issued_quantity']-m['returned_quantity']>by_key[a['lot_key']]['quantity']:inv.fail('余料领用超出实际在库数量')
    for lot in lots:
        key=lot['lot_key'];m=movements[key];custody=m['issued_quantity']-m['returned_quantity']-m['processed_quantity']-m['processing_quantity'];on_hand=lot['quantity']-m['issued_quantity']+m['returned_quantity'];reserved=reuse.held(p,key)
        if min(custody,on_hand,m['processing_quantity'],m['processed_quantity'])<0 or reserved>on_hand or on_hand+custody+m['processed_quantity']+m['processing_quantity']!=lot['quantity']:inv.fail('余料在库、在外、已加工或预留台账不闭合')
        lot['voided']=key in stopped
        m.update(in_stock_quantity=on_hand,in_custody=custody,reserved=reserved,remaining=on_hand-reserved)
    return dict(lots=lots,movements=movements,issues=issues,jobs=jobs)
