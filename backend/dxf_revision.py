"""Read-only recovery of a validated CAD selection; never reuse calibration approval."""
import copy
from . import store


def candidate(comp):
    trace=comp['dxf_trace'];row=trace['candidate']
    entries=list(zip(row['source_objects'],row['source_ranges'])) if row['kind']=='ASSEMBLY' else [(row,None)]
    selected=[{**copy.deepcopy(src),'object_hash':store.digest(src),'reverse':scope['reverse'] if scope else src.get('fragment',{}).get('reverse',False)} for src,scope in entries]
    defaults={k:comp[k] for k in ('name','coordinate_basis','measurement_basis','basis_evidence_id','multiplicity','rounding_digits')}
    defaults.update(component_id=comp['id'],component_hash=store.digest(comp),calibration_evidence_id='',
                    calibration={'from_point':1,'to_point':2,'distance':'','unit':trace['calibration_input']['unit'],'tolerance':''},
                    reason='',source_confirmed=False)
    return {'document_id':row['document_id'],'component_id':comp['id'],'component_hash':store.digest(comp),'revision':comp['revision'],
            'source_stamp':comp['source_stamp'],'name':comp['name'],'state':comp['state'],'selection':selected,'defaults':defaults,
            'notice':'已校验原采用来源并恢复候选序列，尚未修订构件。原说明仅供核对；请调整范围后重新检查连接、填写独立长度及容差依据、修订原因，并明确确认。旧预览和审核不沿用。'}
