import React from 'react';
import {FoundationValue} from './FoundationSteps';
type Obj=Record<string,any>;
function F({label,children}:any){return <label className="field"><span>{label}</span>{children}</label>}
export function FoundationLateralEditor({value,prefix,face,update,proof}:any){
 const change=(v:Obj)=>update({...v,scope_confirmed:false});
 return <fieldset><legend>矩形侧面横向范围</legend><label className="check-line"><input type="checkbox" aria-label={`${prefix} 启用横向条带`} checked={!!value} onChange={e=>update(e.target.checked?{parameters:{lateral_start:'',lateral_end:''},unit:'mm',evidence:{lateral_start:'',lateral_end:''},coordinate_basis:'',coordinate_evidence_id:'',scope_confirmed:false}:undefined)}/>按原件选取横向条带；未启用时为本层整宽</label>
 {value&&<><p className="form-note">当前侧面横向沿局部{face.startsWith('x_')?'Y':'X'}轴，基础中心为0，正向随局部坐标轴，不随观察面翻转。须在本层负半宽至正半宽内，起坐标小于止坐标；侧开口仅扣横向与标高的实际交集。同来源基础项所有条带须使用相同基准说明及同一原件。切换整宽/条带清空本项接触说明与依据。</p>
 <F label="横向坐标单位"><select aria-label={`${prefix} 横向单位`} value={value.unit} onChange={e=>change({...value,unit:e.target.value})}>{['mm','cm','dm','m','km'].map(u=><option key={u}>{u}</option>)}</select></F>
 <div className="form-grid">{['lateral_start','lateral_end'].map((k,j)=><F key={k} label={j?'横向止坐标':'横向起坐标'}><input required inputMode="decimal" aria-label={`${prefix} 横向${j?'止':'起'}坐标`} value={value.parameters[k]} onChange={e=>change({...value,parameters:{...value.parameters,[k]:e.target.value}})}/>{proof(value.evidence[k],(v:string)=>change({...value,evidence:{...value.evidence,[k]:v}}),`${prefix} 横向${j?'止':'起'}坐标依据`)}</F>)}</div>
 <F label="基础局部XY与图纸的共同坐标基准"><textarea required maxLength={2000} aria-label={`${prefix} 横向基准说明`} value={value.coordinate_basis} onChange={e=>change({...value,coordinate_basis:e.target.value})}/>{proof(value.coordinate_evidence_id,(v:string)=>change({...value,coordinate_evidence_id:v}),`${prefix} 横向基准依据`)}</F>
 <label className="check-line"><input type="checkbox" aria-label={`${prefix} 横向基准确认`} checked={value.scope_confirmed} onChange={e=>update({...value,scope_confirmed:e.target.checked})}/>已核定基础中心、局部XY正向、全部基础相同条带及实际侧开口；同来源基础项各条带基准一致。</label></>}
 </fieldset>;
}
export function FoundationLateralDetails({r,sourceLink}:any){
 const rows=r.contact_ranges.filter((d:Obj)=>d.lateral_range);if(!rows.length)return null;
 const states:Obj={laterally_disjoint:'横向分离',lateral_tangent:'横向边界相接',vertically_disjoint:'标高分离',vertical_tangent:'标高端面相接'};
 return <><h4>矩形条带横向坐标及共同基准</h4><div className="table-wrap"><table style={{tableLayout:'fixed',width:'100%'}}><thead><tr>{['接触范围 / 横向轴','起坐标 / 止坐标（m）','本层整宽 / 选带宽（m）','坐标原件与共同基准'].map(t=><th key={t}>{t}</th>)}</tr></thead><tbody>{rows.map((d:Obj)=><tr key={d.index}><td>C{d.index} {d.name}<br/>{d.face_label} · 沿{d.lateral_axis}</td><td>{d.lateral_start} / {d.lateral_end}</td><td><div>整宽 <FoundationValue value={d.full_face_width}/></div><div>条带 <FoundationValue value={d.width}/></div></td><td>起坐标 {sourceLink?.(d.lateral_range.evidence.lateral_start)}<br/>止坐标 {sourceLink?.(d.lateral_range.evidence.lateral_end)}<details><summary>共同坐标基准与原件</summary><p>{d.lateral_range.coordinate_basis}</p>{sourceLink?.(d.lateral_range.coordinate_evidence_id)}</details></td></tr>)}</tbody></table></div>
 <p className="form-note">横向沿基础局部轴，面向不同侧面不翻转正负方向。侧开口按条带和标高的实际交集扣除；仅最终总面积修约。</p>
 {!!r.lateral_checks?.length&&<details><summary>条带横向与标高分离校验</summary>{r.lateral_checks.map((c:Obj,i:number)=><p key={i}>C{c.left_index} {c.left} / C{c.right_index} {c.right}：{states[c.state]}；横向间隔{c.lateral_gap}m，标高间隔{c.z_gap}m。负间隔表示该方向有正范围交叠。</p>)}</details>}</>;
}
