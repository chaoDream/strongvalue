import React, {useState} from 'react';
import DXFAssemblySources from './DXFAssemblySources';
import DXFTraceView from './DXFTraceView';
import DXFRevisionComparison from './DXFRevisionComparison';
import DXFFragmentSources from './DXFFragmentSources';
import {Plus, X, Trash2, ArrowUp, ArrowDown} from 'lucide-react';

type Obj = Record<string, any>;
const axes = ['x','y','z'];
const states:Obj = {draft:'待复核',approved:'已审定',stale:'已失效',rejected:'已拒绝',needs_info:'待补件'};
function F({label,children}:any){return <label className="field"><span>{label}</span>{children}</label>}

export function RouteDiagram({result}:{result:Obj}) {
  const [projection,setProjection]=useState('xz');
  const [a,b]=projection.split('');
  const points=result.route_points.map((p:Obj)=>[Number(p.normalized_parameters[a]),Number(p.normalized_parameters[b])]);
  const minX=Math.min(...points.map((p:number[])=>p[0])), maxX=Math.max(...points.map((p:number[])=>p[0]));
  const minY=Math.min(...points.map((p:number[])=>p[1])), maxY=Math.max(...points.map((p:number[])=>p[1]));
  const scale=Math.min(540/(maxX-minX||1),210/(maxY-minY||1));
  const plot=points.map((p:number[])=>[50+(p[0]-minX)*scale,255-(p[1]-minY)*scale]);
  return <><F label="查看管线投影"><select aria-label="管线投影" value={projection} onChange={e=>setProjection(e.target.value)}><option value="xy">XY平面</option><option value="xz">XZ高程</option><option value="yz">YZ高程</option></select></F>
    <svg viewBox="0 0 640 300" role="img" aria-label={`管线${projection.toUpperCase()}投影，有序设计转折点`} style={{width:'100%',maxHeight:300,background:'#f7f9fb',borderRadius:8}}>
      <text x="20" y="24" fontSize="13" fill="#536174">{a.toUpperCase()} / {b.toUpperCase()} · 设计坐标投影</text>
      <polyline points={plot.map((p:number[])=>p.join(',')).join(' ')} fill="none" stroke="#167d68" strokeWidth="3"/>
      {plot.map((p:number[],i:number)=><g key={i}><circle cx={p[0]} cy={p[1]} r="4" fill="#167d68"/><text x={p[0]+7} y={p[1]-8} fontSize="12" fill="#174d42">P{i+1}</text></g>)}
    </svg><p className="form-note">投影仅供定位，编号按输入顺序连接；重叠投影不表示管线相连。工程量以服务器三维计算为准。</p></>;
}

export function RouteDetails({item,sourceLink,projectId,request}:{item:Obj;sourceLink?:(id:string)=>React.ReactNode;projectId?:string;request?:any}) {
  const r=item.preview||item, s=r.route_summary;
  if(!s)return null;
  return <><DXFRevisionComparison value={item.dxf_trace?.revision_comparison}/>{item.dxf_trace&&item.id&&projectId&&request&&<DXFTraceView item={item} projectId={projectId} request={request}/>} {item.dxf_trace&&<DXFFragmentSources candidate={item.dxf_trace.candidate}/>} {item.dxf_trace&&<DXFAssemblySources candidate={item.dxf_trace.candidate}/>} {item.dxf_trace&&<details open><summary>DXF原对象与单位校核</summary><p>{item.dxf_trace.candidate.kind} / 句柄 {item.dxf_trace.candidate.handle} / 图层 {item.dxf_trace.candidate.layer} / 声明单位 {item.dxf_trace.candidate.unit}</p><p>P{item.dxf_trace.calibration.from_point}—P{item.dxf_trace.calibration.to_point}：已知 {item.dxf_trace.calibration.known_distance_m}m / CAD {item.dxf_trace.calibration.cad_distance_m}m；容差 {item.dxf_trace.calibration.tolerance_m}m，偏差 {item.dxf_trace.calibration.delta_m}m</p><p>{item.dxf_trace.reason} · {item.dxf_trace.author}</p>{sourceLink&&<p>独立校核依据：{sourceLink(item.dxf_trace.proofs.calibration.evidence_id)}</p>}</details>}<div className="notice neutral"><div><b>设计管线长度 {r.quantity} m</b><p>{s.point_count}个转折点 / {s.segment_count}段；重复数量{s.multiplicity}。仅总量四舍五入至{s.rounding_digits}位小数。</p></div></div>
    <p>{r.coordinate_basis}</p><RouteDiagram result={r}/>
    <details open><summary>逐段三维长度</summary><div className="table-wrap"><table><thead><tr>{['管段','起点 → 终点','ΔXYZ（m）','三维段长（m）','水平投影长（m）'].map(x=><th key={x}>{x}</th>)}</tr></thead><tbody>
      {r.route_segments.map((v:Obj)=><tr key={v.index}><td>S{v.index}</td><td>P{v.from_index} {v.from_name} → P{v.to_index} {v.to_name}</td><td>{axes.map(k=><small className="block" key={k}>Δ{k.toUpperCase()}：{v.delta[k]}</small>)}</td><td style={{maxWidth:220,overflowWrap:'anywhere'}}>{v.length}</td><td style={{maxWidth:200,overflowWrap:'anywhere'}}>{v.horizontal_length}</td></tr>)}
    </tbody></table></div></details>
    <details><summary>设计坐标与逐点原始依据</summary><div className="table-wrap"><table><thead><tr>{['转折点','原始坐标','标准坐标（m）','原始依据'].map(x=><th key={x}>{x}</th>)}</tr></thead><tbody>
      {r.route_points.map((p:Obj)=><tr key={p.index}><td>P{p.index} {p.name}</td><td>{axes.map(k=><small className="block" key={k}>{k.toUpperCase()}：{p.parameters[k]} {p.unit}</small>)}</td><td>{axes.map(k=><small className="block" key={k}>{k.toUpperCase()}：{p.normalized_parameters[k]}</small>)}</td><td>{axes.map(k=><small className="block" key={k}>{k.toUpperCase()}：{sourceLink?sourceLink(p.evidence[k]):p.evidence[k]}</small>)}</td></tr>)}
    </tbody></table></div></details>
    <details><summary>长度汇总、修约与公式</summary><p className="wrap-cell">单组未修约：{s.single_length} m</p><p className="wrap-cell">乘重复数量后：{s.unrounded_quantity} m；总量修约差额：{s.rounding_adjustment} m</p><p>{r.formula}</p><p>{r.rounding_rule}</p><p>{r.rule_version}</p></details>
    {sourceLink&&<div className="tool-strip">{['coordinate','basis','multiplicity','rounding'].map(k=><span key={k}>{({coordinate:'共同坐标',basis:'计量口径',multiplicity:'重复数量',rounding:'修约依据'} as Obj)[k]}：{sourceLink(r.source_evidence[k])}</span>)}</div>}
    <p className="form-note">{r.notice}</p></>;
}

type Props={p:Obj;user:Obj;request:(path:string,method?:string,data?:any)=>Promise<any>;refresh:()=>Promise<any>;onReview:(collection:string,item:Obj)=>void;sourceLink:(id:string)=>React.ReactNode};
export default function RouteTakeoffPanel({p,user,request,refresh,onReview,sourceLink}:Props){
  const [draft,setDraft]=useState<Obj|null>(null),[preview,setPreview]=useState<Obj|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const editable=user.role!=='reviewer',base='/projects/'+p.id;
  const point=():Obj=>({name:'',parameters:{x:'',y:'',z:''},unit:'m',evidence:{x:'',y:'',z:''}});
  function change(d:Obj){setDraft(d);setPreview(null);setError('');}
  const field=(k:string,v:any)=>change({...draft,[k]:v});
  const editPoint=(i:number,k:string,v:any)=>field('route_points',draft!.route_points.map((x:Obj,j:number)=>i===j?{...x,[k]:v}:x));
  function move(i:number,offset:number){const ps=[...draft!.route_points];[ps[i],ps[i+offset]]=[ps[i+offset],ps[i]];field('route_points',ps);}
  const evidence=(value:string,update:(v:string)=>void,label:string)=><select required aria-label={label} value={value||''} onChange={e=>update(e.target.value)}><option value="">选择当前原始依据</option>{p.evidence.filter((e:Obj)=>!p.documents.find((d:Obj)=>d.id===e.document_id)?.superseded).map((e:Obj)=><option key={e.id} value={e.id}>{p.documents.find((d:Obj)=>d.id===e.document_id)?.name} · {e.text.slice(0,40)}</option>)}</select>;
  async function run(save:boolean){if(!draft)return;setBusy(true);setError('');try{const body={...draft,kind:'design_route',parameters:{},quantity_type:'length'};if(save){await request(base+'/components'+(draft.id?'/'+draft.id:''),draft.id?'PUT':'POST',body);await refresh();setDraft(null);setPreview(null);}else setPreview(await request(base+'/components/preview','POST',body));}catch(e:any){setError(e.message);}finally{setBusy(false);}}
  async function edit(item:Obj){setBusy(true);setError('');try{const t=await request(base+'/review-target?collection=components&object_id='+encodeURIComponent(item.id));change({...structuredClone(t.object),object_hash:t.object_hash});}catch(e:any){setError(e.message);}finally{setBusy(false);}}
  return <section className="card space-bottom planar-panel"><div className="section-head"><div><h2>设计坐标管线长度</h2><p>按已确认的平面坐标与高程，逐段计算三维中心线长度。</p></div><button className="btn" disabled={!editable||busy||!p.evidence.length} onClick={()=>change({name:'',route_points:[point(),point()],coordinate_basis:'',coordinate_evidence_id:'',measurement_basis:'',basis_evidence_id:'',multiplicity:'1',multiplicity_evidence_id:'',rounding_digits:'',rounding_evidence_id:''})}><Plus size={15}/>建立设计管线</button></div>
    <p className="form-note">每条管线为有序开放折线，2至101个点。须逐点确认XYZ及高程基准；闭环、支路、重复路径须拆分核定。不自动推算管件、弯头或附加长度。</p>
    {p.components.filter((c:Obj)=>c.kind==='design_route').map((c:Obj)=><details key={c.id} className="compound-record" open><summary><b>{c.name}</b> · V{c.revision} · {states[c.state]} · {c.preview.quantity} m</summary><RouteDetails item={c} sourceLink={sourceLink} projectId={p.id} request={request}/><div className="actions"><button className="text-btn" disabled={!editable||busy} onClick={()=>edit(c)}>修订设计管线</button><button className="text-btn" onClick={()=>onReview('components',c)}>管线明细与独立复核</button></div></details>)}
    {error&&!draft&&<p role="alert" className="error-inline">{error}</p>}
    {draft&&<div className="overlay"><section className="modal planar-modal" role="dialog" aria-modal="true" aria-label="编制设计管线"><header><h2>编制设计管线</h2><button className="icon-btn" aria-label="关闭设计管线" disabled={busy} onClick={()=>setDraft(null)}><X size={18}/></button></header><form onSubmit={e=>{e.preventDefault();run(true);}}><div className="modal-body"><fieldset disabled={busy} style={{border:0,padding:0,margin:0}}>
      {draft.dxf_trace&&<div className="notice neutral"><div><p>此管线坐标绑定DXF原对象。更换CAD来源请使用“DXF设计坐标读取与单位校核”选择原管线修订；人工改坐标须明确转为人工维护，原采用记录留档。</p><label className="check-option"><input type="checkbox" checked={!!draft.detach_dxf} onChange={e=>field('detach_dxf',e.target.checked)}/>转为人工坐标维护</label>{draft.detach_dxf&&<F label="转为人工维护的原因"><textarea required maxLength={2000} value={draft.detach_reason||''} onChange={e=>field('detach_reason',e.target.value)}/></F>}</div></div>}<div className="form-grid"><F label="管线名称"><input required maxLength={200} value={draft.name} onChange={e=>field('name',e.target.value)}/></F><F label="整组重复数量"><input required type="number" min="1" max="1000000" step="1" value={draft.multiplicity} onChange={e=>field('multiplicity',e.target.value)}/></F><F label="重复数量依据">{evidence(draft.multiplicity_evidence_id,v=>field('multiplicity_evidence_id',v),'管线重复数量依据')}</F><F label="总量四舍五入小数位"><select required aria-label="管线修约位数" value={draft.rounding_digits} onChange={e=>field('rounding_digits',e.target.value===''?'':Number(e.target.value))}><option value="">按依据选择</option>{Array.from({length:13},(_,i)=><option key={i} value={i}>{i}位小数</option>)}</select></F><F label="修约原始依据">{evidence(draft.rounding_evidence_id,v=>field('rounding_evidence_id',v),'管线修约原始依据')}</F></div>
      <F label="共同原点、XYZ方向及高程基准"><textarea required maxLength={2000} value={draft.coordinate_basis} onChange={e=>field('coordinate_basis',e.target.value)}/></F><F label="共同坐标原始依据">{evidence(draft.coordinate_evidence_id,v=>field('coordinate_evidence_id',v),'管线共同坐标原始依据')}</F>
      <F label="中心线范围、管段连接及计量口径"><textarea required maxLength={2000} value={draft.measurement_basis} onChange={e=>field('measurement_basis',e.target.value)}/></F><F label="中心线计量原始依据">{evidence(draft.basis_evidence_id,v=>field('basis_evidence_id',v),'管线计量原始依据')}</F>
      {draft.route_points.map((pt:Obj,i:number)=><fieldset className="compound-part" key={i}><legend>P{i+1} · 设计转折点</legend><div className="form-grid"><F label="转折点名称"><input required aria-label={`P${i+1} 名称`} maxLength={200} value={pt.name} onChange={e=>editPoint(i,'name',e.target.value)}/></F><F label="本点XYZ原始单位"><select aria-label={`P${i+1} 原始单位`} value={pt.unit} onChange={e=>editPoint(i,'unit',e.target.value)}>{['m','mm','cm','dm','km'].map(u=><option key={u}>{u}</option>)}</select></F></div>
        <div className="form-grid">{axes.map(k=><F key={k} label={k==='z'?'Z设计高程':k.toUpperCase()+'设计坐标'}><input required aria-label={`P${i+1} ${k.toUpperCase()}`} type="number" min="-1000000000000" max="1000000000000" step="any" value={pt.parameters[k]} onChange={e=>editPoint(i,'parameters',{...pt.parameters,[k]:e.target.value})}/>{evidence(pt.evidence[k],v=>editPoint(i,'evidence',{...pt.evidence,[k]:v}),`P${i+1} ${k.toUpperCase()}证据`)}</F>)}</div>
        <div className="actions"><button className="text-btn" type="button" aria-label={`上移P${i+1}`} disabled={i===0} onClick={()=>move(i,-1)}><ArrowUp size={14}/>上移</button><button className="text-btn" type="button" aria-label={`下移P${i+1}`} disabled={i===draft.route_points.length-1} onClick={()=>move(i,1)}><ArrowDown size={14}/>下移</button><button className="text-btn" type="button" disabled={draft.route_points.length<=2} onClick={()=>field('route_points',draft.route_points.filter((_:any,j:number)=>j!==i))}><Trash2 size={14}/>移除此点</button></div>
      </fieldset>)}<button className="btn" type="button" disabled={draft.route_points.length>=101} onClick={()=>field('route_points',[...draft.route_points,point()])}><Plus size={15}/>增加转折点</button>
    </fieldset>{preview&&<div className="space-top"><h3>三维管线长度预览</h3><RouteDetails item={preview} sourceLink={sourceLink}/></div>}{error&&<p role="alert" className="error-inline">{error}</p>}</div><footer><button className="btn" type="button" disabled={busy} onClick={()=>run(false)}>预览三维长度</button><button className="btn primary" disabled={busy}>{busy?'处理中…':'保存并送审'}</button></footer></form></section></div>}
  </section>;
}
