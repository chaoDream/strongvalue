import React, {useState} from 'react';
import {Plus, X, Trash2} from 'lucide-react';
import {PlanarDiagram} from './PlanarTakeoffPanel';

type Obj = Record<string, any>;
const fields: Obj = {x:'起点X坐标', y:'起点Y坐标', z:'起点Z标高', length:'沿X长度', width:'沿Y宽度', height:'沿Z高度'};
const states: Obj = {draft:'待复核', approved:'已审定', stale:'已失效', rejected:'已拒绝', needs_info:'待补件'};
const units = ['m','mm','cm','dm','km'];
function F({label,children}:any) {return <label className="field"><span>{label}</span>{children}</label>}

export function SpatialDetails({item,sourceLink}:{item:Obj;sourceLink?:(id:string)=>React.ReactNode}) {
  const [layerIndex,setLayerIndex] = useState(0);
  const r = item.preview || item, s = r.spatial_summary;
  if (!s) return null;
  const index = Math.min(layerIndex, r.spatial_layers.length-1), layer = r.spatial_layers[index];
  const projection = {planar_regions:r.spatial_regions.filter((b:Obj)=>layer.region_indices.includes(b.index))};
  return <>
    <p>{r.coordinate_basis}</p>
    <div className="notice neutral"><div>
      <b>单组净体积 {s.net_volume} m³ · 工程量 {r.quantity} m³</b>
      <p>加项累加 {s.naive_add_volume} → 并集 {s.add_union_volume}，重叠扣重 {s.add_overlap_volume} m³；扣减累加 {s.naive_deduct_volume} → 并集 {s.deduct_union_volume}，重复扣减 {s.deduct_overlap_volume} m³。</p>
      <p>整组重复数量 {s.multiplicity}；按共同Z标高分为 {r.spatial_layers.length} 层。</p>
    </div></div>
    <F label="查看Z分层截面（起点标高至终点标高，单位m）"><select aria-label="空间分层截面" value={index} onChange={e=>setLayerIndex(Number(e.target.value))}>
      {r.spatial_layers.map((z:Obj,i:number)=><option key={i} value={i}>{z.z_from} → {z.z_to} m · 本层净体积 {z.net_volume} m³</option>)}
    </select></F>
    <PlanarDiagram result={projection} prefix="B"/>
    <p className="form-note">当前Z层内的XY净截面，B编号对应长方体；加项截面 {layer.add_area} − 扣减截面 {layer.deduct_area} = {layer.net_area} m²，层高 {layer.height} m。</p>
    <details><summary>长方体原始坐标与逐参数依据（{r.spatial_regions.length}项）</summary>
      <div className="table-wrap"><table><thead><tr>{['长方体','加扣','原始XYZ / 长宽高','标准值（m）','体积','计量依据'].map(x=><th key={x}>{x}</th>)}</tr></thead><tbody>
        {r.spatial_regions.map((b:Obj)=><tr key={b.index}>
          <td>B{b.index} · {b.name}</td><td>{b.operation==='add'?'加项':'扣减'}</td>
          <td>{Object.entries(b.parameters).map(([k,v])=><small className="block" key={k}>{fields[k]}：{String(v)} {b.unit}</small>)}</td>
          <td>{Object.entries(b.normalized_parameters).map(([k,v])=><small className="block" key={k}>{fields[k]}：{String(v)}</small>)}</td>
          <td>{b.cuboid_volume} m³</td><td>{b.measurement_basis}{sourceLink&&Object.entries(b.evidence).map(([k,id])=><small className="block" key={k}>{fields[k]||'计量说明'}：{sourceLink(String(id))}</small>)}</td>
        </tr>)}
      </tbody></table></div>
    </details>
    <details><summary>分层计算与条带过程</summary>
      {r.spatial_layers.map((z:Obj,i:number)=><details key={i}><summary>Z {z.z_from} → {z.z_to} m · 净体积 {z.net_volume} m³</summary>
        <p>净截面 {z.add_area} − {z.deduct_area} = {z.net_area} m²，乘层高 {z.height} m。</p>
        <div className="table-wrap"><table><thead><tr>{['X区间（m）','加项Y并集（m）','扣减Y并集（m）','条带净面积（m²）'].map(x=><th key={x}>{x}</th>)}</tr></thead><tbody>
          {z.strips.map((v:Obj,j:number)=><tr key={j}><td>{v.x_from} → {v.x_to}</td><td>{v.add_intervals.map((a:string[])=>a.join(' → ')).join('；')}</td><td>{v.deduct_intervals.map((a:string[])=>a.join(' → ')).join('；')||'无'}</td><td>{v.net_area}</td></tr>)}
        </tbody></table></div>
      </details>)}
    </details>
    <details><summary>两两相交定位（{r.spatial_intersections.length}项）</summary><p className="form-note">两两相交体积不可相加代替并集扣重。</p>
      {r.spatial_intersections.map((v:Obj,i:number)=><p key={i}>{v.operation==='add'?'加项':'扣减'}：{v.left} / {v.right}，相交 {v.volume} m³</p>)}
    </details>
    {sourceLink&&<div className="tool-strip">{['coordinate','basis','multiplicity'].map(k=><span key={k}>{({coordinate:'XYZ坐标口径',basis:'计量口径',multiplicity:'重复数量'} as Obj)[k]}：{sourceLink(r.source_evidence[k])}</span>)}</div>}
    <p className="form-note">{r.notice} {r.rounding_rule}</p>
  </>;
}

type Props = {p:Obj;user:Obj;request:(path:string,method?:string,data?:any)=>Promise<any>;refresh:()=>Promise<any>;onReview:(collection:string,item:Obj)=>void;sourceLink:(id:string)=>React.ReactNode};
export default function SpatialTakeoffPanel({p,user,request,refresh,onReview}:Props) {
  const [draft,setDraft]=useState<Obj|null>(null), [preview,setPreview]=useState<Obj|null>(null), [error,setError]=useState(''), [busy,setBusy]=useState(false);
  const base='/projects/'+p.id, editable=user.role!=='reviewer';
  const row=():Obj=>({name:'',operation:'add',parameters:Object.fromEntries(Object.keys(fields).map(k=>[k,''])),unit:'m',evidence:{},measurement_basis:''});
  function change(d:Obj) {setDraft(d);setPreview(null);setError('');}
  const field=(k:string,v:any)=>change({...draft,[k]:v});
  const region=(i:number,k:string,v:any)=>field('regions',draft!.regions.map((x:Obj,j:number)=>i===j?{...x,[k]:v}:x));
  const evidence=(value:string,update:(v:string)=>void,label:string)=><select required aria-label={label} value={value||''} onChange={e=>update(e.target.value)}>
    <option value="">选择当前原始依据</option>{p.evidence.filter((e:Obj)=>!p.documents.find((d:Obj)=>d.id===e.document_id)?.superseded).map((e:Obj)=><option key={e.id} value={e.id}>{p.documents.find((d:Obj)=>d.id===e.document_id)?.name} · {e.text.slice(0,40)}</option>)}
  </select>;
  async function run(save:boolean) {
    if (!draft) return;
    setBusy(true);setError('');
    try {
      const body={...draft,kind:'spatial_regions',parameters:{},quantity_type:'volume'};
      if(save) {await request(base+'/components'+(draft.id?'/'+draft.id:''),draft.id?'PUT':'POST',body);await refresh();setDraft(null);setPreview(null);}
      else setPreview(await request(base+'/components/preview','POST',body));
    } catch(e:any) {setError(e.message);} finally {setBusy(false);}
  }
  async function edit(item:Obj) {
    setBusy(true);setError('');
    try {const t=await request(base+'/review-target?collection=components&object_id='+encodeURIComponent(item.id));setDraft({...structuredClone(t.object),object_hash:t.object_hash});setPreview(null);}
    catch(e:any) {setError(e.message);} finally {setBusy(false);}
  }
  return <section className="card space-bottom planar-panel">
    <div className="section-head"><div><h2>空间长方体并集与扣减</h2><p>按共同XYZ设计坐标和标高分层，处理不同高度的重叠及重复扣减。</p></div>
      <button className="btn" disabled={!editable||busy||!p.evidence.length} onClick={()=>change({name:'',regions:[row()],multiplicity:'1',multiplicity_evidence_id:'',coordinate_basis:'',coordinate_evidence_id:'',measurement_basis:'',basis_evidence_id:''})}><Plus size={15}/>建立空间构件</button>
    </div>
    <p className="form-note">须确认共同原点、XYZ方向及标高基准；长方体与坐标轴平行，Z为底面起点、高度沿正Z方向。支持负坐标，扣减须完全位于加项空间并集内，最多20项。</p>
    {p.components.filter((x:Obj)=>x.kind==='spatial_regions').map((x:Obj)=><details key={x.id} className="compound-record" open>
      <summary><b>{x.name}</b> · V{x.revision} · {states[x.state]} · {x.preview.quantity} m³</summary>
      <SpatialDetails item={x}/><div className="actions"><button className="text-btn" disabled={!editable||busy} onClick={()=>edit(x)}>修订空间构件</button><button className="text-btn" onClick={()=>onReview('components',x)}>空间明细与独立复核</button></div>
    </details>)}
    {error&&!draft&&<p className="error-inline" role="alert">{error}</p>}
    {draft&&<div className="overlay"><section className="modal planar-modal" role="dialog" aria-modal="true" aria-label="编制空间长方体">
      <header><h2>编制空间长方体</h2><button className="icon-btn" disabled={busy} aria-label="关闭空间构件" onClick={()=>setDraft(null)}><X size={18}/></button></header>
      <form onSubmit={e=>{e.preventDefault();run(true);}}><div className="modal-body">
        <div className="form-grid"><F label="构件名称"><input required maxLength={200} value={draft.name} onChange={e=>field('name',e.target.value)}/></F>
          <F label="整组重复数量"><input required type="number" min="1" max="1000000" step="1" value={draft.multiplicity} onChange={e=>field('multiplicity',e.target.value)}/></F>
          <F label="重复数量依据">{evidence(draft.multiplicity_evidence_id,v=>field('multiplicity_evidence_id',v),'空间重复数量依据')}</F>
        </div>
        <F label="共同原点、XYZ方向及标高基准"><textarea required maxLength={2000} value={draft.coordinate_basis} onChange={e=>field('coordinate_basis',e.target.value)}/></F>
        <F label="XYZ坐标与标高原始依据">{evidence(draft.coordinate_evidence_id,v=>field('coordinate_evidence_id',v),'XYZ坐标与标高原始依据')}</F>
        <F label="总体计量与扣减依据"><textarea required maxLength={2000} value={draft.measurement_basis} onChange={e=>field('measurement_basis',e.target.value)}/></F>
        <F label="总体计量原始依据">{evidence(draft.basis_evidence_id,v=>field('basis_evidence_id',v),'空间总体计量原始依据')}</F>
        {draft.regions.map((b:Obj,i:number)=><fieldset key={i} className="compound-part"><legend>B{i+1} · 轴向长方体</legend>
          <div className="form-grid"><F label="长方体名称"><input required maxLength={200} value={b.name} onChange={e=>region(i,'name',e.target.value)}/></F>
            <F label="加项 / 扣减"><select value={b.operation} onChange={e=>region(i,'operation',e.target.value)}><option value="add">加项并集</option><option value="subtract">扣减并集</option></select></F>
            <F label="XYZ与长宽高的原始单位"><select required aria-label={`B${i+1} 原始单位`} value={b.unit} onChange={e=>region(i,'unit',e.target.value)}>{units.map(u=><option key={u}>{u}</option>)}</select></F>
          </div>
          <div className="form-grid">{Object.keys(fields).map(k=><F key={k} label={fields[k]}>
            <input required type="number" min={['x','y','z'].includes(k)?'-1000000000000':'0.000000000000000001'} max="1000000000000" step="any" value={b.parameters[k]} onChange={e=>region(i,'parameters',{...b.parameters,[k]:e.target.value})}/>
            {evidence(b.evidence[k],v=>region(i,'evidence',{...b.evidence,[k]:v}),`B${i+1} ${fields[k]}证据`)}
          </F>)}</div>
          <F label="本长方体计量说明"><textarea required maxLength={2000} value={b.measurement_basis} onChange={e=>region(i,'measurement_basis',e.target.value)}/></F>
          <F label="本长方体计量原始依据">{evidence(b.evidence.measurement_basis,v=>region(i,'evidence',{...b.evidence,measurement_basis:v}),`B${i+1} 计量原始依据`)}</F>
          <button className="text-btn" type="button" disabled={busy||draft.regions.length===1} onClick={()=>field('regions',draft.regions.filter((_:any,j:number)=>j!==i))}><Trash2 size={14}/>移除此长方体</button>
        </fieldset>)}
        <button className="btn" type="button" disabled={busy||draft.regions.length>=20} onClick={()=>field('regions',[...draft.regions,row()])}><Plus size={15}/>增加长方体</button>
        {preview&&<div className="space-top"><h3>空间并集与扣减预览</h3><SpatialDetails item={preview}/></div>}{error&&<p className="error-inline" role="alert">{error}</p>}
      </div><footer><button className="btn" type="button" disabled={busy} onClick={()=>run(false)}>预览空间并集</button><button className="btn primary" disabled={busy}>{busy?'处理中…':'保存并送审'}</button></footer></form>
    </section></div>}
  </section>;
}
