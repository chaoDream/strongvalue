import React, {useState} from 'react';
import {Plus, X} from 'lucide-react';
import FacePartitionEditor from './FacePartitionEditor';
type Obj=Record<string,any>;
const states:Obj={draft:'待复核',approved:'已审定',stale:'已失效',rejected:'已拒绝',needs_info:'待补件'};
const normals:Obj={'x-':'X负向','x+':'X正向','y-':'Y负向','y+':'Y正向','z-':'Z负向','z+':'Z正向'};
function F({label,children}:any){return <label className="field"><span>{label}</span>{children}</label>}
function FaceDiagram({faces,decisions}:{faces:Obj[];decisions:Obj[]}) {
  const [plane,setPlane]=useState('');
  if(!faces.length)return null;
  const keys=Array.from(new Set(faces.map(f=>f.normal+' / '+f.plane))), selected=keys.includes(plane)?plane:keys[0];
  const current=faces.filter(f=>f.normal+' / '+f.plane===selected);
  const u0=Math.min(...current.map(f=>Number(f.u_from))),v0=Math.min(...current.map(f=>Number(f.v_from)));
  const u1=Math.max(...current.map(f=>Number(f.u_to))),v1=Math.max(...current.map(f=>Number(f.v_to)));
  const safe=u1>u0&&v1>v0&&current.every(f=>Number(f.u_to)>Number(f.u_from)&&Number(f.v_to)>Number(f.v_from));
  const scale=safe?Math.min(640/(u1-u0),220/(v1-v0)):1;
  return <><F label="边界面所在平面（坐标单位m）"><select aria-label="边界面所在平面" value={selected} onChange={e=>setPlane(e.target.value)}>{keys.map(k=><option key={k}>{k}</option>)}</select></F>
    {safe?<div className="planar-diagram"><svg viewBox="0 0 720 280" role="img" aria-label="所选平面的边界分面，非测量图">
      {current.map(f=>{const d=decisions.find(d=>d.face_id===f.face_id),x=40+(Number(f.u_from)-u0)*scale,y=245-(Number(f.v_to)-v0)*scale;
        return <g key={f.face_id}><rect x={x} y={y} width={(Number(f.u_to)-Number(f.u_from))*scale} height={(Number(f.v_to)-Number(f.v_from))*scale} fill={d?.included===true?'#85aa94':d?.included===false?'#d7dfda':'#e4c889'} stroke="#456a54" strokeWidth="1"/><text x={x+4} y={y+16} fontSize="12" fill="#263b2e">F{f.index}</text></g>})}
      <text x="40" y="270" fontSize="12" fill="#536b5b">{current[0].u_axis.toUpperCase()}向右，{current[0].v_axis.toUpperCase()}向上 · 示意图不可测量</text>
    </svg><p className="form-note">绿色计入，灰色排除，黄色待核定。F编号对应逐面决定；极小分面可能无法按比例清晰显示。</p></div>:<p className="form-note">当前坐标尺度不适合绘制，请按精确坐标和面积核对。</p>}</>;
}
function FaceTable({faces}:{faces:Obj[]}) {
  return <div className="table-wrap"><table><thead><tr>{['分面 / 决定','朝向 / 位置（m）','面内坐标范围（m）','单组面积（m²）','计量依据'].map(x=><th key={x}>{x}</th>)}</tr></thead><tbody>{faces.map(f=><tr key={f.face_id}>
    <td>F{f.index} · {f.included?'计入':'排除'}{f.parent_index&&<small className="block">原F{f.parent_index} · 子{f.sub_index}</small>}</td><td>{normals[f.normal]} / {f.plane}</td><td>{f.u_axis}: {f.u_from} → {f.u_to}<small className="block">{f.v_axis}: {f.v_from} → {f.v_to}</small></td><td>{f.area}</td><td>{f.basis}</td>
  </tr>)}</tbody></table></div>;
}
export function SurfaceDetails({item,sourceLink}:{item:Obj;sourceLink?:(id:string)=>React.ReactNode}) {
  const r=item.preview||item,s=r.surface_summary;
  if(!s)return null;
  return <><div className="notice neutral"><div><b>{s.purpose_label} · 工程量 {r.quantity} m²</b>
    <p>来源：{s.source_name} · V{s.source_revision}；单组边界 {s.total_area} m²，计入 {s.included_area} m²，排除 {s.excluded_area} m²；重复数量 {s.multiplicity}。</p>
    <p>{s.face_count}个分面均已核定，计入{s.included_count}个。</p></div></div>
    {r.surface_subdivisions?.length>0&&<details><summary>分区坐标与原始依据（{r.surface_subdivisions.length}条）</summary>{r.surface_subdivisions.map((c:Obj)=><p key={c.parameter}>原F{c.parent_index} · {c.coordinate_axis.toUpperCase()}：{c.original_value} {c.source_unit} × {c.factor} → {c.canonical_value} m {sourceLink&&sourceLink(c.evidence_id)}</p>)}</details>}<p>{r.coordinate_basis}</p><FaceDiagram faces={r.surface_faces} decisions={r.surface_faces}/><FaceTable faces={r.surface_faces}/>
    {sourceLink&&<details><summary>逐面依据与原始几何来源</summary>{r.surface_faces.map((f:Obj)=><p key={f.face_id}>F{f.index} · {f.included?'计入':'排除'}：{sourceLink(f.evidence_id)}</p>)}<p>总体计量：{sourceLink(r.source_evidence.surface_basis)}</p><p>共同坐标：{sourceLink(r.source_evidence.coordinate)}</p>
      <div className="table-wrap"><table><thead><tr><th>来源长方体</th><th>原始参数</th><th>逐参数原图依据</th></tr></thead><tbody>{r.source_geometry.regions.map((b:Obj,i:number)=><tr key={i}><td>{b.name} · {b.operation==='add'?'加项':'扣减'}</td><td>{Object.entries(b.parameters).map(([k,v])=><small className="block" key={k}>{k}: {String(v)} {b.unit}</small>)}</td><td>{Object.entries(b.evidence).map(([k,v])=><small className="block" key={k}>{k}: {sourceLink(String(v))}</small>)}</td></tr>)}</tbody></table></div>
    </details>}<p className="form-note">{r.notice} {r.rounding_rule}</p></>;
}
type Props={p:Obj;user:Obj;request:(path:string,method?:string,data?:any)=>Promise<any>;refresh:()=>Promise<any>;onReview:(collection:string,item:Obj)=>void;sourceLink:(id:string)=>React.ReactNode};
export default function SurfaceTakeoffPanel({p,user,request,refresh,onReview}:Props){
  const [draft,setDraft]=useState<Obj|null>(null),[candidate,setCandidate]=useState<Obj|null>(null),[preview,setPreview]=useState<Obj|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const [partitionDirty,setPartitionDirty]=useState(false);
  const [filter,setFilter]=useState('all'),[batch,setBatch]=useState<Obj>({included:'',basis:'',evidence_id:''});
  const base='/projects/'+p.id,editable=user.role!=='reviewer';
  const sources=p.components.filter((c:Obj)=>c.kind==='spatial_regions'&&c.state==='approved');
  const ev=(value:string,update:(v:string)=>void,label:string,required=true)=><select required={required} aria-label={label} value={value||''} onChange={e=>update(e.target.value)}><option value="">选择当前原始依据</option>{p.evidence.filter((e:Obj)=>!p.documents.find((d:Obj)=>d.id===e.document_id)?.superseded).map((e:Obj)=><option value={e.id} key={e.id}>{p.documents.find((d:Obj)=>d.id===e.document_id)?.name} · {e.text.slice(0,40)}</option>)}</select>;
  function change(d:Obj){setDraft(d);setPreview(null);setError('');}
  const field=(k:string,v:any)=>change({...draft,[k]:v});
  async function load(cid:string,previous?:Obj){
    setBusy(true);setError('');setPreview(null);setCandidate(null);
    try{let c=await request(base+'/surface-face-candidates?component_id='+encodeURIComponent(cid));
      const same=previous&&previous.source_component_hash===c.source_component_hash;
      if(same&&previous.face_subdivisions?.length)c=await request(base+'/surface-face-candidates/partition','POST',previous);
      setCandidate(c);setPartitionDirty(false);
      if(same)setDraft(previous);
      else change({...draft,...(previous||{}),source_component_id:cid,source_component_hash:c.source_component_hash,face_subdivisions:[],face_decisions:c.faces.map((f:Obj)=>({face_id:f.face_id,included:null,basis:'',evidence_id:''}))});
    }catch(e:any){setError(e.message);}finally{setBusy(false);}
  }
  async function edit(item:Obj){setBusy(true);setError('');try{const t=await request(base+'/review-target?collection=components&object_id='+encodeURIComponent(item.id));const d={...structuredClone(t.object),object_hash:t.object_hash};setDraft(d);await load(d.source_component_id,d);}catch(e:any){setError(e.message);}finally{setBusy(false);}}
  async function applyPartitions(){if(!draft)return;setBusy(true);setError('');setPreview(null);try{
    const c=await request(base+'/surface-face-candidates/partition','POST',draft);
    const old=new Map(draft.face_decisions.map((d:Obj)=>[d.face_id,d]));
    change({...draft,face_decisions:c.faces.map((f:Obj)=>old.get(f.face_id)||{face_id:f.face_id,included:null,basis:'',evidence_id:''})});
    setCandidate(c);setPartitionDirty(false);
  }catch(e:any){setError(e.message);}finally{setBusy(false);}}
  function decide(id:string,k:string,value:any){field('face_decisions',draft!.face_decisions.map((d:Obj)=>d.face_id===id?{...d,[k]:value}:d));}
  const shown=candidate?.faces.filter((f:Obj)=>filter==='all'||f.normal===filter)||[];
  function applyBatch(){if(!draft||!batch.included||!batch.basis.trim()||!batch.evidence_id){setError('批量填写须明确计入/排除、说明及原始依据');return;}
    const ids=new Set(shown.map((f:Obj)=>f.face_id));field('face_decisions',draft.face_decisions.map((d:Obj)=>ids.has(d.face_id)?{...d,included:batch.included==='include',basis:batch.basis,evidence_id:batch.evidence_id}:d));}
  async function run(save:boolean){if(!draft)return;setBusy(true);setError('');try{const body={...draft,kind:'surface_faces',parameters:{},quantity_type:'area'};
    if(save){await request(base+'/components'+(draft.id?'/'+draft.id:''),draft.id?'PUT':'POST',body);await refresh();setDraft(null);setCandidate(null);setPreview(null);}
    else setPreview(await request(base+'/components/preview','POST',body));
  }catch(e:any){setError(e.message);}finally{setBusy(false);}}
  return <section className="card space-bottom planar-panel"><div className="section-head"><div><h2>模板与防水逐面计量</h2><p>从已审空间净实体提取边界，逐面明确计入或排除及原始依据。</p></div><button className="btn" disabled={!editable||busy||!sources.length} onClick={()=>{setCandidate(null);setPreview(null);setPartitionDirty(false);setFilter('all');setBatch({included:'',basis:'',evidence_id:''});change({name:'',purpose:'formwork',source_component_id:'',measurement_basis:'',basis_evidence_id:'',face_subdivisions:[],face_decisions:[]});}}><Plus size={15}/>建立表面算量</button></div>
    <p className="form-note">先审定上方空间构件。外表面及内腔面都只是候选，须逐面核定实际做法和计量口径。读取新来源会清空旧分面决定，不能按总表面积直接推定模板或防水量。</p>
    {p.components.filter((c:Obj)=>c.kind==='surface_faces').map((c:Obj)=><details key={c.id} className="compound-record" open><summary><b>{c.name}</b> · V{c.revision} · {states[c.state]} · {c.preview.quantity} m²</summary><SurfaceDetails item={c}/><div className="actions"><button className="text-btn" disabled={!editable||busy} onClick={()=>edit(c)}>修订逐面计量</button><button className="text-btn" onClick={()=>onReview('components',c)}>逐面明细与独立复核</button></div></details>)}
    {error&&!draft&&<p className="error-inline" role="alert">{error}</p>}
    {draft&&<div className="overlay"><section className="modal planar-modal" role="dialog" aria-modal="true" aria-label="编制逐面计量"><header><h2>编制模板与防水逐面计量</h2><button className="icon-btn" disabled={busy} aria-label="关闭逐面计量" onClick={()=>setDraft(null)}><X size={18}/></button></header>
      <form onSubmit={e=>{e.preventDefault();run(true);}}><div className="modal-body">
        <div className="form-grid"><F label="表面算量名称"><input required maxLength={200} value={draft.name} onChange={e=>field('name',e.target.value)}/></F><F label="计量用途"><select value={draft.purpose} onChange={e=>field('purpose',e.target.value)}><option value="formwork">模板接触面</option><option value="waterproof">防水展开面</option></select></F></div>
        <F label="来源已审空间构件"><select required aria-label="来源已审空间构件" value={draft.source_component_id} onChange={e=>{setCandidate(null);field('source_component_id',e.target.value);}}><option value="">选择来源</option>{sources.map((c:Obj)=><option key={c.id} value={c.id}>{c.name} · V{c.revision}</option>)}</select></F>
        <button className="btn" type="button" disabled={busy||!draft.source_component_id} onClick={()=>load(draft.source_component_id)}>读取当前边界面并重新核定</button>
        <F label="总体计量与做法依据"><textarea required maxLength={2000} value={draft.measurement_basis} onChange={e=>field('measurement_basis',e.target.value)}/></F><F label="总体计量原始依据">{ev(draft.basis_evidence_id,v=>field('basis_evidence_id',v),'表面总体计量原始依据')}</F>
        {candidate&&<><p className="form-note">来源 V{candidate.source_revision}；单组边界 {candidate.total_area} m²，共{candidate.faces.length}面；重复数量继承来源 {candidate.multiplicity}。全部面均需计入或排除，未核定 {draft.face_decisions.filter((d:Obj)=>d.included===null).length} 面。</p>
          <FacePartitionEditor faces={candidate.base_faces||candidate.faces} groups={draft.face_subdivisions||[]} busy={busy} dirty={partitionDirty} onApply={applyPartitions} onChange={groups=>{field('face_subdivisions',groups);setPartitionDirty(true);}} evidence={(value,update,label)=>ev(value,update,label,false)}/><FaceDiagram faces={candidate.faces} decisions={draft.face_decisions}/>
          <F label="按面朝向筛选"><select aria-label="按面朝向筛选" value={filter} onChange={e=>setFilter(e.target.value)}><option value="all">全部边界面</option>{Object.entries(normals).map(([k,v])=><option key={k} value={k}>{String(v)}</option>)}</select></F>
          <details><summary>对当前筛选的{shown.length}面批量填写决定</summary><div className="form-grid"><F label="批量计量决定"><select aria-label="批量计量决定" value={batch.included} onChange={e=>setBatch({...batch,included:e.target.value})}><option value="">明确选择决定</option><option value="include">计入</option><option value="exclude">排除</option></select></F><F label="批量计量原始依据">{ev(batch.evidence_id,v=>setBatch({...batch,evidence_id:v}),'批量计量原始依据',false)}</F></div><F label="批量计入或排除说明"><textarea value={batch.basis} maxLength={2000} onChange={e=>setBatch({...batch,basis:e.target.value})}/></F><button className="btn" type="button" disabled={busy} onClick={applyBatch}>填写当前筛选面</button></details>
          {shown.map((f:Obj)=>{const d=draft.face_decisions.find((d:Obj)=>d.face_id===f.face_id);return <fieldset className="compound-part" key={f.face_id}><legend>F{f.index}{f.parent_index?`（原F${f.parent_index} 子${f.sub_index}）`:''} · {normals[f.normal]} · {f.area} m²</legend><p>{f.normal[0]}={f.plane} m；{f.u_axis}: {f.u_from} → {f.u_to} m，{f.v_axis}: {f.v_from} → {f.v_to} m。</p><div className="form-grid"><F label={`F${f.index} 计量决定`}><select required value={d.included===null?'':d.included?'include':'exclude'} onChange={e=>decide(f.face_id,'included',e.target.value===''?null:e.target.value==='include')}><option value="">待核定</option><option value="include">计入</option><option value="exclude">排除</option></select></F><F label={`F${f.index} 原始依据`}>{ev(d.evidence_id,v=>decide(f.face_id,'evidence_id',v),`F${f.index} 原始依据`)}</F></div><F label={`F${f.index} 计量说明`}><textarea required maxLength={2000} value={d.basis} onChange={e=>decide(f.face_id,'basis',e.target.value)}/></F></fieldset>})}
        </>}{preview&&<div className="space-top"><h3>逐面计量计算预览</h3><SurfaceDetails item={preview}/></div>}{error&&<p className="error-inline" role="alert">{error}</p>}
      </div><footer><button className="btn" type="button" disabled={busy||!candidate||partitionDirty} onClick={()=>run(false)}>预览逐面计量</button><button className="btn primary" disabled={busy||!candidate||partitionDirty}>{busy?'处理中…':'保存并送审'}</button></footer></form>
    </section></div>}
  </section>;
}
