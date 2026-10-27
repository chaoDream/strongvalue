import {FoundationValue} from './FoundationSteps';
import {blankCircularHole,CircularHolesEditor,CircularHolesDetails} from './FoundationCircularHolesWindow';
type Obj=Record<string,any>;
const labels:Obj={x:'外圆心X坐标',y:'外圆心Y坐标',diameter:'实际外径'};
export function blankCircularBlock(){return {...blankCircularHole(),holes:[],parent:null};}
export function CircularIslandsEditor({value:c,prefix,proof,onChange}:any){
 const holeCount=c.parts.reduce((n:number,p:Obj)=>n+p.holes.length,0);
 function update(i:number,p:Obj){
  const old=c.parts[i],removed=p.holes.length<old.holes.length?old.holes.findIndex((h:Obj)=>!p.holes.includes(h)):-1;
  onChange({...c,parts:c.parts.map((v:Obj,j:number)=>{
   const n=i===j?p:v,r=n.parent;
   if(removed<0||r===null||r.block_index!==i+1)return n;
   return {...n,parent:r.hole_index===removed+1?{block_index:'',hole_index:''}:r.hole_index>removed+1?{...r,hole_index:r.hole_index-1}:r};
  })});
 }
 function remove(i:number){onChange({...c,parts:c.parts.filter((_:Obj,j:number)=>j!==i).map((p:Obj)=>p.parent===null?p:{...p,parent:p.parent.block_index===i+1?{block_index:'',hole_index:''}:p.parent.block_index>i+1?{...p.parent,block_index:p.parent.block_index-1}:p.parent})});}
 return <div className="compound-part"><h4>圆形根块与岛区（2至4块）</h4><p>至少一个岛区外圆严格在最近父块的指定内圆W内，其余外圆须严格分离。每块0至4个W，全部最多8个；W只排除所属块，岛区作为独立支模块计回。共同单位和基础中心XY，不改变Z或重复数量。删除被引用块或W后须重新选择父关系。</p>
 {c.parts.map((p:Obj,i:number)=>{const label=`${prefix} B${i+1}`;return <fieldset className="compound-part" key={i}><legend>{label}</legend><label className="field"><span>净块名称</span><input required maxLength={200} aria-label={`${label} 名称`} value={p.name} onChange={e=>update(i,{...p,name:e.target.value})}/></label>
 <label className="field"><span>最近父块内圆</span><select required aria-label={`${label} 父块内圆`} value={p.parent===null?'root':p.parent.block_index&&p.parent.hole_index?`${p.parent.block_index}:${p.parent.hole_index}`:''} onChange={e=>update(i,{...p,parent:e.target.value==='root'?null:e.target.value?{block_index:Number(e.target.value.split(':')[0]),hole_index:Number(e.target.value.split(':')[1])}:{block_index:'',hole_index:''}})}><option value="">明确选择父块内圆或根块</option><option value="root">根块（不在其他块内圆中）</option>{c.parts.flatMap((v:Obj,j:number)=>i===j?[]:v.holes.map((h:Obj,k:number)=><option key={`${j+1}:${k+1}`} value={`${j+1}:${k+1}`}>B{j+1} {v.name} · W{k+1} {h.name}</option>))}</select></label>
 <div className="form-grid">{Object.entries(labels).map(([k,labelText])=><label className="field" key={k}><span>{String(labelText)}（{c.unit}）</span><input required aria-label={`${label} ${labelText}`} value={p.parameters[k]} onChange={e=>update(i,{...p,parameters:{...p.parameters,[k]:e.target.value}})}/>{proof(p.evidence[k],(v:string)=>update(i,{...p,evidence:{...p.evidence,[k]:v}}),`${label} ${labelText}依据`)}</label>)}</div>
 <label className="field"><span>本块实际支模及父关系说明</span><textarea required maxLength={2000} aria-label={`${label} 支模关系说明`} value={p.measurement_basis} onChange={e=>update(i,{...p,measurement_basis:e.target.value})}/>{proof(p.evidence_id,(v:string)=>update(i,{...p,evidence_id:v}),`${label} 支模关系依据`)}</label>
 <CircularHolesEditor value={{...p,unit:c.unit}} prefix={label} proof={proof} min={0} max={Math.min(4,p.holes.length+8-holeCount)} onChange={(v:Obj)=>{const {unit,...next}=v;update(i,next);}}/>
 <button type="button" className="text-btn" disabled={c.parts.length<=2} onClick={()=>remove(i)}>移除{label}净块</button></fieldset>;})}
 <button type="button" className="btn" disabled={c.parts.length>=4} onClick={()=>onChange({...c,parts:[...c.parts,blankCircularBlock()]})}>增加圆形净块</button><p>当前{c.parts.length}块、{holeCount}个W；全部修改撤销确认及旧预览。</p></div>;
}
export function CircularIslandsDetails({value:d,sourceLink,Curve,Coverage}:any){
 return <div className="compound-part"><b>圆形嵌套岛区XY窗口</b><p>{d.window_parts.length}块按本块外圆减所属W分别计量；岛区显式计回，其他孔内空区不填实。平面层级不代表标高、基础层号或数量。全部{d.total_count}个基础在各块累计后只继承一次。</p>
 <div className="table-wrap"><table><thead><tr>{['净块／平面层级','最近父块内圆','窗口净区（m²）','每基础真实净接触（m²）'].map(v=><th key={v}>{v}</th>)}</tr></thead><tbody>{d.window_parts.map((p:Obj)=><tr key={p.index}><td>B{p.index} {p.name} · {p.nesting_depth}级</td><td>{p.parent?`B${p.parent.block_index} W${p.parent.hole_index}`:'根块'}</td><td><FoundationValue value={p.window_area}/></td><td><FoundationValue value={p.single_net_area}/></td></tr>)}</tbody></table></div>
 <details><summary>圆形块严格拓扑与最近父关系</summary>{d.window_part_checks.map((q:Obj,i:number)=><p key={i}>B{q.left_index} {q.relation==='inside_window_hole'?`W${q.parent_hole_index} 内含 B${q.right_index}`:`对 B${q.right_index} 外圆分离`}：圆心距平方{q.center_distance_squared} {q.operator} {q.relation==='inside_window_hole'?'半径差平方':'半径和平方'}{q.limit_squared}m²。{q.relation==='inside_window_hole'&&'包含见证可能含祖先；最近父关系以块表为准。'}</p>)}</details>
 {d.window_parts.map((p:Obj)=><details key={p.index}><summary>B{p.index} {p.name} · 查看本块圆形净区与原件</summary><p>{p.measurement_basis} {sourceLink?.(p.evidence_id)}</p><CircularHolesDetails value={{...d,...p,nested_circular_window:false,polygon_zero_contact:p.polygon_zero_contact}} sourceLink={sourceLink} Curve={Curve} Coverage={Coverage} title="本块圆形净区"/></details>)}
 <p>{d.plan_range.coordinate_basis} {sourceLink?.(d.plan_range.coordinate_evidence_id)}</p><Curve value={d.polygon_gross_trace}/><Curve value={d.polygon_net_trace}/><Coverage value={d}/></div>;
}
