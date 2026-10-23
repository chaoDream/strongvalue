import React from 'react';
type Obj=Record<string,any>;
export default function DXFFragmentSources({candidate}:{candidate:Obj}){
 if(candidate.kind!=='FRAGMENT')return null;
 const f=candidate.fragment,s=candidate.source_object;
 return <details open className="dxf-assembly-sources"><summary>CAD原对象取段范围与方向</summary><p>{s.kind} / 句柄{s.handle} / 图层{s.layer} / 原单位{s.unit}</p><p>原始顶点P{f.from_point}—P{f.to_point}，{f.reverse?'反向采用（原末点 → 原起点）':'按原方向采用'}；保留全部中间顶点。</p><p className="form-note">取段编号用于当前长度校核，原始编号用于回查CAD。只读取已有完整管段，不插值、不补绘；相邻范围可以共用一个端点，不能重复计入原始管段。</p><div className="table-wrap"><table><thead><tr>{['取段点','对应CAD原始点','原始X','原始Y','原始Z'].map(x=><th key={x}>{x}</th>)}</tr></thead><tbody>{candidate.source_point_indices.map((n:number,i:number)=><tr key={i}><td>P{i+1}</td><td>原P{n}</td>{['x','y','z'].map(k=><td key={k}>{s.points[n-1][k]} {s.unit}</td>)}</tr>)}</tbody></table></div></details>
}
