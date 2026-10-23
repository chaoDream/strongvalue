import React from 'react';
import {FoundationValue} from './FoundationSteps';
type Obj=Record<string,any>;
function F({label,children}:any){return <label className="field"><span>{label}</span>{children}</label>}
export function FoundationArcEditor({value,prefix,update,proof}:any){
 const change=(c:Obj)=>update({...c,scope_confirmed:false});
 return <fieldset><legend>圆柱侧面角度范围</legend><label className="check-line"><input type="checkbox" aria-label={`${prefix} 启用局部圆弧`} checked={!!value} onChange={e=>update(e.target.checked?{parameters:{angle_start:'',angle_end:''},unit:'°',evidence:{angle_start:'',angle_end:''},coordinate_basis:'',coordinate_evidence_id:'',scope_confirmed:false}:undefined)}/>按原件选取局部圆弧；未启用时为本层整周</label>
  {value&&<><p className="form-note">单位为度（°）。共同圆心沿用基础局部XY，0°为+X；从+Z向原点俯视，从+X向+Y逆时针为正。须0≤起角&lt;止角≤360°，跨0°分两项，不自动求余或取小弧。同一来源基础项全部圆弧（含跨层、跨记录）须使用完全相同的角度基准说明及同一原件。切换整周/圆弧清空本项接触说明与依据。</p>
   <div className="form-grid">{['angle_start','angle_end'].map((k,j)=><F key={k} label={j?'止角（°）':'起角（°）'}><input required inputMode="decimal" aria-label={`${prefix} ${j?'止角':'起角'}`} value={value.parameters[k]} onChange={e=>change({...value,parameters:{...value.parameters,[k]:e.target.value}})}/>{proof(value.evidence[k],(v:string)=>change({...value,evidence:{...value.evidence,[k]:v}}),`${prefix} ${j?'止角':'起角'}依据`)}</F>)}</div>
   <F label="局部XY与图纸的共同角度基准"><textarea required maxLength={2000} aria-label={`${prefix} 角度基准说明`} value={value.coordinate_basis} onChange={e=>change({...value,coordinate_basis:e.target.value})}/>{proof(value.coordinate_evidence_id,(v:string)=>change({...value,coordinate_evidence_id:v}),`${prefix} 角度基准依据`)}</F>
   <label className="check-line"><input type="checkbox" aria-label={`${prefix} 圆弧基准确认`} checked={value.scope_confirmed} onChange={e=>update({...value,scope_confirmed:e.target.checked})}/>已核定共同圆心、+X零角、+Z俯视逆时针及实际接触起止角度；同来源基础项各圆弧基准一致。</label>
  </>}
 </fieldset>;
}
export function FoundationArcDetails({r,sourceLink}:any){
 const rows=(r.contact_ranges||[]).filter((d:Obj)=>d.arc_range);if(!rows.length)return null;
 const states:Obj={angular_disjoint:'角度分离',angular_tangent:'角度边界相接',vertically_disjoint:'标高分离',vertical_tangent:'标高端面相接'};
 return <><h4>局部圆弧角度及共同基准</h4><div className="table-wrap"><table style={{tableLayout:'fixed',width:'100%'}}><thead><tr>{['接触范围','起角 / 止角 / 弧角（°）','本层整周长 / 选弧展开宽（m）','角度原件与共同基准'].map(t=><th key={t}>{t}</th>)}</tr></thead><tbody>{rows.map((d:Obj)=><tr key={d.index}><td>C{d.index} {d.name}<br/>M{d.material_index} L{d.level_index}</td><td>{d.angle_start} / {d.angle_end} / {d.angle_span}</td><td><div>整周 <FoundationValue value={d.full_circumference}/></div><div>选弧 <FoundationValue value={d.width}/></div></td><td><div>起角 {sourceLink?.(d.arc_range.evidence.angle_start)}</div><div>止角 {sourceLink?.(d.arc_range.evidence.angle_end)}</div><details><summary>共同基准与原件</summary><p>{d.arc_range.coordinate_basis}</p>{sourceLink?.(d.arc_range.coordinate_evidence_id)}</details></td></tr>)}</tbody></table></div>
  <p className="form-note">选弧展开宽=π×实际直径×弧角/360。0°与360°同一周期边界；角度和标高同时正交叠才重复，整周范围也参与校验。仅最终面积修约。</p>
  {!!r.arc_checks?.length&&<details><summary>圆弧角度与标高分离校验</summary>{r.arc_checks.map((c:Obj,i:number)=><p key={i}>C{c.left_index} {c.left} / C{c.right_index} {c.right}：{states[c.state]}；周期角间隔{c.angle_gap}°（对比平移{c.periodic_shift}°），标高间隔{c.z_gap}m。负间隔表示该方向有正范围交叠。</p>)}</details>}
 </>;
}
