import React from 'react';
type Obj=Record<string,any>;
const rectLabels:Obj={length:'基础长',width:'基础宽',height:'基础高'};
const roundLabels:Obj={diameter:'实际直径',height:'基础高'};
export function FoundationValue({value}:any){const s=String(value),dot=s.indexOf('.');return dot>=0&&s.length-dot>14?<details><summary>{s.slice(0,dot+10)}…</summary><div style={{whiteSpace:'normal',overflowWrap:'anywhere'}}>未修约完整值：{s}</div></details>:<>{s}</>;}
export const isRoundFoundation=(d:Obj)=>['circular','stepped_circular'].includes(d.shape);
export const foundationLabels=(d:Obj)=>isRoundFoundation(d)?roundLabels:rectLabels;
export function foundationDimensions(d:Obj,parameters:Obj){return isRoundFoundation(d)?`Φ${parameters.diameter} × ${parameters.height}`:Object.keys(rectLabels).map(k=>parameters[k]).join(' × ');}
const level=(round:boolean)=>({parameters:round?{diameter:'',height:''}:{length:'',width:'',height:''},evidence:round?{diameter:'',height:''}:{length:'',width:'',height:''}});
function newShape(d:Obj,shape:string){
 const round=['circular','stepped_circular'].includes(shape),stepped=shape.startsWith('stepped_'),empty=level(round);
 return {material_index:d.material_index,...(shape!=='rectangular'?{shape}:{}),...(stepped?{levels:[level(round),level(round)],stacking_confirmed:false}:{parameters:empty.parameters}),length_unit:d.length_unit,measurement_basis:'',evidence:stepped?{measurement_basis:''}:{...empty.evidence,measurement_basis:''}};
}
export function FoundationShapeEditor({value:d,prefix,update,replace,proof}:any){
 const stepped=!!d.levels,round=isRoundFoundation(d),labels=foundationLabels(d);
 return <>
  <label className="field"><span>基础形式</span><select aria-label={`${prefix} 基础形式`} value={d.shape||'rectangular'} onChange={e=>replace(newShape(d,e.target.value))}><option value="rectangular">独立矩形单体</option><option value="stepped_rectangular">同心矩形台阶基础</option><option value="circular">实心圆柱基础</option><option value="stepped_circular">同心圆形台阶基础</option></select></label>
  <p className="form-note">切换形式会清空本项尺寸及原件；切换形式或增删层还会清空本项及总体范围说明与依据，须重新核定。</p>
  {round&&<p>填写原件中的实际直径，不能填写半径或公称尺寸。外轮廓按竖直圆柱π/4×直径²×净高计算，孔洞仅按另列贯通孔扣减；圆周率及未修约计算保留在明细中。</p>}
  {stepped?<>
   <p>从最底层向上填写，每层高度为该层净层高。各层同心同轴、垂直紧接，上层{round?'直径':'长宽'}不得超出下层。Z从本基础底面0起累计，仅供核对层次，不表示工程绝对标高；层数不增加基础个数。</p>
   {d.levels.map((v:Obj,i:number)=><fieldset key={i}><legend>第{i+1}层（自下而上，{d.length_unit}）</legend>
    {Object.entries(labels).map(([k,label])=><label className="field" key={k}><span>{k==='height'?'本层净层高':String(label)}</span><input required type="number" min="0" step="any" aria-label={`${prefix} L${i+1} ${k==='height'?'层高':label}`} value={v.parameters[k]} onChange={e=>update('levels',d.levels.map((x:Obj,j:number)=>j===i?{...x,parameters:{...x.parameters,[k]:e.target.value}}:x))}/>{proof(v.evidence[k],(id:string)=>update('levels',d.levels.map((x:Obj,j:number)=>j===i?{...x,evidence:{...x.evidence,[k]:id}}:x)),`${prefix} L${i+1} ${k==='height'?'层高':label}依据`)}</label>)}
    <button type="button" className="text-btn" disabled={d.levels.length<=2} onClick={()=>replace({...d,levels:d.levels.filter((_:any,j:number)=>j!==i),measurement_basis:'',evidence:{measurement_basis:''},stacking_confirmed:false})}>移除第{i+1}层</button>
   </fieldset>)}
   <button type="button" className="btn" disabled={d.levels.length>=10} onClick={()=>replace({...d,levels:[...d.levels,level(round)],measurement_basis:'',evidence:{measurement_basis:''},stacking_confirmed:false})}>增加上层台阶</button>
   <label className="check-line"><input type="checkbox" aria-label={`${prefix} 台阶叠置确认`} checked={d.stacking_confirmed} onChange={e=>update('stacking_confirmed',e.target.checked)}/>已据原件确认各层自下而上同心同轴紧接、不外伸，层高非累计高度</label>
  </>:Object.entries(labels).map(([k,label])=><label className="field" key={k}><span>{prefix} {String(label)}</span><input required type="number" min="0" step="any" aria-label={`${prefix} ${label}`} value={d.parameters[k]} onChange={e=>update('parameters',{...d.parameters,[k]:e.target.value})}/>{proof(d.evidence[k],(id:string)=>update('evidence',{...d.evidence,[k]:id}),`${prefix} ${label}依据`)}</label>)}
 </>;
}
export function FoundationLevelDetails({r,link}:any){
 const rows=r.foundation_items.filter((d:Obj)=>d.foundation_levels);
 return <>
  {r.foundation_pi_constant&&r.foundation_items.some(isRoundFoundation)&&<details><summary>{r.foundation_opening_summary?'圆形轮廓与圆周率':'圆形基础实心口径与圆周率'}</summary><p className="form-note">圆形外轮廓按圆柱计算，孔洞仅按另列贯通孔扣减，Φ为实际直径；π={r.foundation_pi_constant}。逐层和分项不修约，仅总体积修约。带省略号数值为显示截取，展开可核对完整未修约值。</p></details>}
  {!!rows.length&&<p>台阶Z为从本基础底面0起的相对层次；各层同心同轴紧接，层高非累计高度，层数不乘基础个数。</p>}
  {rows.map((d:Obj)=><div key={d.material_index}><b>M{d.material_index} {d.material_name} · {d.foundation_levels.length}层 / 每基础{isRoundFoundation(d)?' · 圆形':''}</b><div className="table-wrap"><table style={{tableLayout:'fixed',width:'100%'}}><thead><tr>{['自下而上',isRoundFoundation(d)?'实际直径Φ×层高（m）':'长×宽×层高（m）','相对底→顶Z（m）',r.foundation_opening_summary?'单基础本层 / 全部净体积（m³）':'单基础本层 / 全部体积（m³）'].map(t=><th key={t}>{t}</th>)}</tr></thead><tbody>{d.foundation_levels.map((v:Obj)=><tr key={v.index}><td>L{v.index}</td><td>{foundationDimensions(d,v.normalized_parameters)}</td><td>{v.from_z} → {v.to_z}</td><td>{(isRoundFoundation(d)||d.through_holes)?<><FoundationValue value={v.net_level_volume??v.single_level_volume}/><span>全部：</span><FoundationValue value={v.total_volume}/></>:<>{v.single_level_volume} / {v.total_volume}</>}</td></tr>)}</tbody></table></div><details><summary>M{d.material_index} 台阶原始尺寸与逐层原件</summary>{d.foundation_levels.map((v:Obj)=><div key={v.index}><b>第{v.index}层（{d.length_unit}）</b>{Object.entries(foundationLabels(d)).map(([k,label])=><p key={k}>{k==='height'?'净层高':String(label)}：{v.parameters[k]} {link&&link(v.evidence[k])}</p>)}</div>)}</details></div>)}
 </>;
}
