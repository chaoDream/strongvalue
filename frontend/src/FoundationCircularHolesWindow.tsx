import {FoundationValue} from './FoundationSteps';
type Obj=Record<string,any>;
const labels:Obj={x:'圆心X坐标',y:'圆心Y坐标',diameter:'实际直径'};
export function blankCircularHole(){return {name:'',parameters:{x:'',y:'',diameter:''},evidence:{x:'',y:'',diameter:''},measurement_basis:'',evidence_id:''};}
export function CircularHolesEditor({value:c,prefix,proof,onChange}:any){
 const update=(i:number,h:Obj)=>onChange({...c,holes:c.holes.map((old:Obj,j:number)=>i===j?h:old)});
 return <div className="compound-part"><h4>窗口内圆W（2至4个）</h4><p>各圆心均以基础共同原点填写，共用外圆单位和XY正向。各W须严格在外圆内且相互严格分离；相切、相交、包含或越界均拒绝。W只排除本窗口支模，不创建实体孔H。</p>
 {c.holes.map((h:Obj,i:number)=>{const p=`${prefix} W${i+1}`;return <fieldset key={i} className="compound-part"><legend>{p}</legend><label className="field"><span>内圆名称</span><input required maxLength={200} aria-label={`${p} 名称`} value={h.name} onChange={e=>update(i,{...h,name:e.target.value})}/></label>
 <div className="form-grid">{Object.keys(labels).map(k=><label className="field" key={k}><span>{labels[k]}（{c.unit}）</span><input required aria-label={`${p} ${labels[k]}`} value={h.parameters[k]} onChange={e=>update(i,{...h,parameters:{...h.parameters,[k]:e.target.value}})}/>{proof(h.evidence[k],(v:string)=>update(i,{...h,evidence:{...h.evidence,[k]:v}}),`${p} ${labels[k]}依据`)}</label>)}</div>
 <label className="field"><span>本内圆不支模说明及原件</span><textarea required maxLength={2000} aria-label={`${p} 不支模说明`} value={h.measurement_basis} onChange={e=>update(i,{...h,measurement_basis:e.target.value})}/>{proof(h.evidence_id,(v:string)=>update(i,{...h,evidence_id:v}),`${p} 不支模依据`)}</label>
 <button type="button" className="text-btn" disabled={c.holes.length<=2} onClick={()=>onChange({...c,holes:c.holes.filter((_:Obj,j:number)=>j!==i)})}>移除{p}</button></fieldset>;})}
 <button type="button" className="btn" disabled={c.holes.length>=4} onClick={()=>onChange({...c,holes:[...c.holes,blankCircularHole()]})}>增加窗口内圆</button><p>当前{c.holes.length}个内圆；修改、添加或移除会撤销确认和旧预览。</p></div>;
}
export function CircularHolesDetails({value:d,sourceLink,Curve,Coverage}:any){
 const o=d.window_outer_circle;
 return <div className="compound-part"><b>多内圆XY窗口</b><p>外圆心X <FoundationValue value={o.x}/>m，Y <FoundationValue value={o.y}/>m；实际外径 <FoundationValue value={o.diameter}/>m。全部圆心均相对基础共同原点填写，内圆坐标不是偏移量。</p>
 <div className="form-grid three">{[['外圆面积（m²）',d.window_outer_area],['全部内圆W面积（m²）',d.window_exclusion_area],['外圆减全部W净区（m²）',d.window_area]].map(([label,value])=><div key={label}><small>{label}</small><FoundationValue value={value}/></div>)}</div><p>以上为设计几何，真实计量还须与实际截面相交。</p>
 <div className="table-wrap"><table><thead><tr>{['内圆','圆心XY（m）','直径（m）','面积（m²）','严格在外圆内：距平方＜半径差平方（m²）'].map(s=><th key={s}>{s}</th>)}</tr></thead><tbody>{d.window_circular_holes.map((h:Obj)=><tr key={h.index}><td>W{h.index} {h.name}</td><td><FoundationValue value={h.normalized_parameters.x}/>，<FoundationValue value={h.normalized_parameters.y}/></td><td><FoundationValue value={h.normalized_parameters.diameter}/></td><td><FoundationValue value={h.area}/></td><td>{h.containment.center_distance_squared} ＜ {h.containment.radius_gap_squared}</td></tr>)}</tbody></table></div>
 <p>两两严格分离：{d.window_hole_checks.map((q:Obj)=>`W${q.left_index} / W${q.right_index}：${q.center_distance_squared} ＞ ${q.radius_sum_squared}m²`).join('；')}。距平方严格大于半径和平方；内圆接触或相交拒绝。</p>
 {d.window_circular_holes.map((h:Obj)=>{const c=d.plan_range.holes[h.index-1];return <details key={h.index}><summary>W{h.index} {h.name} · 不支模说明及逐项原件</summary><p>{c.measurement_basis} {sourceLink?.(c.evidence_id)}</p>{Object.keys(labels).map(k=><p key={k}>{labels[k]}：{c.parameters[k]} {d.plan_range.unit} {sourceLink?.(c.evidence[k])}</p>)}</details>;})}
 <div className="form-grid three">{[['每基础真实毛接触（m²）',d.single_gross_area],['每基础实体孔H扣（m²）',d.single_opening_area],['每基础净接触（m²）',d.single_net_area]].map(([label,value])=><div key={label}><small>{label}</small><FoundationValue value={value}/></div>)}</div><p>全部{d.total_count}个基础继承一次。W不作为实体孔扣，不扣混凝土或生成孔壁、孔端。</p>
 <p>{d.window_intersection_rule}</p>{d.face==='step'&&<p>窗口内下层 <FoundationValue value={d.window_lower_area}/>m² − 上层覆盖 <FoundationValue value={d.window_upper_area}/>m²。</p>}
 <p>外包X <FoundationValue value={d.x_start}/>至<FoundationValue value={d.x_end}/>m，Y <FoundationValue value={d.y_start}/>至<FoundationValue value={d.y_end}/>m，仅用于定位。</p>
 <p>{d.plan_range.coordinate_basis} {sourceLink?.(d.plan_range.coordinate_evidence_id)}</p>{Object.entries(d.plan_range.evidence).map(([k,v]:any)=><span key={k}>{labels[k]} {sourceLink?.(v)} </span>)}
 <Curve value={d.polygon_gross_trace}/><Curve value={d.polygon_net_trace}/><Coverage value={d}/></div>;
}
