import React from 'react';
type Obj=Record<string,any>;
const xyz=(p:Obj)=>['x','y','z'].map(k=>k.toUpperCase()+': '+p[k]).join(' / ');
export default function DXFAssemblySources({candidate}:{candidate:Obj}){
 if(!candidate.source_objects)return null;
 return <details open className="dxf-assembly-sources"><summary>CAD原对象顺序、方向与连接点</summary><p className="form-note">按下列明确序列组成开放折线。连接点须XYZ精确相同，仅保留一个计算顶点；原始两侧数值分别留存。不自动吸附、补段或推断支路连接。</p><div className="table-wrap"><table><thead><tr>{['顺序 / 原对象','采用方向','组装点范围','原始起点 → 终点'].map(s=><th key={s}>{s}</th>)}</tr></thead><tbody>{candidate.source_objects.map((s:Obj,i:number)=>{const r=candidate.source_ranges[i];return <tr key={s.id}><td>{i+1}. {s.kind} / {s.handle}<small className="block">图层 {s.layer} / {s.unit}</small></td><td>{r.reverse?'反向：原终点 → 原起点':'原方向：原起点 → 原终点'}</td><td>P{r.start_point}—P{r.end_point}</td><td className="wrap-cell">{xyz(s.points[0])}<small className="block">→ {xyz(s.points[s.points.length-1])}</small></td></tr>})}</tbody></table></div><details><summary>精确连接点的原始数值</summary>{candidate.joins.map((j:Obj)=><p className="wrap-cell" key={j.point_index}>P{j.point_index}：{j.previous_handle}终点（采用方向） {xyz(j.incoming_end)}；{j.next_handle}起点（采用方向） {xyz(j.outgoing_start)}，单位{j.unit}</p>)}</details></details>
}
