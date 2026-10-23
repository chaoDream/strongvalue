type Obj=Record<string,any>;
const fields:Obj={name:'管线名称',single_length:'单组长度（m）',multiplicity:'整组重复数量',rounding_digits:'总量修约位数',unrounded_quantity:'未修约总量（m）',quantity:'修约后工程量（m）',coordinate_basis:'共同WCS及高程基准',measurement_basis:'设计中心线计量口径',basis_evidence_id:'用途与计量依据'};
const statuses:Obj={added:'新增',removed:'移除',reversed:'反向采用',retained:'保留'};
function segment(s:Obj|null){return s?`第${s.position}段 · 原P${s.from_point}→P${s.to_point} · ${s.length_m}m`:'未采用'}
export default function DXFRevisionComparison({value}:{value?:Obj}){
 if(!value)return null;
 const counts=value.segments.reduce((r:Obj,s:Obj)=>({...r,[s.status]:(r[s.status]||0)+1}),{});
 return <section className="space-top" aria-label="CAD修订前后对照"><h3>CAD修订前后对照 · V{value.before.revision} → V{value.after.revision}</h3>
  <p>{value.before.name} → {value.after.name}；原版状态：{({draft:'待复核',approved:'已审定',stale:'已失效',rejected:'已拒绝',needs_info:'待补件'} as Obj)[value.before.state]||value.before.state}</p>
  <p className="form-note">{value.notice}</p>
  {value.status==='complete'&&<><div className="table-wrap"><table><thead><tr><th>比较项</th><th>修订前</th><th>修订后</th><th>增减差额</th></tr></thead><tbody>{value.metrics.map((m:Obj)=><tr key={m.field}><td>{fields[m.field]}</td><td className="wrap-cell">{m.before}</td><td className="wrap-cell">{m.after}</td><td className="wrap-cell">{m.delta}</td></tr>)}</tbody></table></div>
   <p>原始管段：新增 {counts.added||0} 段，移除 {counts.removed||0} 段，反向采用 {counts.reversed||0} 段，保留 {counts.retained||0} 段。</p>
   <details open><summary>逐段范围、方向与顺序变化</summary><div className="table-wrap"><table><thead><tr><th>变化</th><th>原对象 / 图层</th><th>修订前</th><th>修订后</th><th>原文件校验值</th></tr></thead><tbody>{value.segments.map((v:Obj)=>{const s=v.after||v.before;return <tr key={v.key}><td>{statuses[v.status]}</td><td>{s.handle} / {s.layer}</td><td className="wrap-cell">{segment(v.before)}</td><td className="wrap-cell">{segment(v.after)}</td><td title={s.source.document_hash}>{s.source.document_hash.slice(0,12)}…</td></tr>})}</tbody></table></div></details></>}
  <details open><summary>说明与原始依据变化</summary>{value.scope_changes.length?<div className="table-wrap"><table><thead><tr><th>比较项</th><th>修订前</th><th>修订后</th></tr></thead><tbody>{value.scope_changes.map((v:Obj)=><tr key={v.field}><td>{fields[v.field]}</td><td className="wrap-cell">{String(v.before)}</td><td className="wrap-cell">{String(v.after)}</td></tr>)}</tbody></table></div>:<p>名称、坐标说明、计量口径、用途依据、重复数量与修约位数均未改变。</p>}</details>
  <details><summary>固定原版本与来源</summary><p className="wrap-cell">原版本校验值：{value.previous_component_hash}</p><p className="wrap-cell">原文件：{value.before.document_id} / {value.before.document_hash}</p><p className="wrap-cell">本次文件：{value.after.document_id} / {value.after.document_hash}</p></details>
 </section>;
}
