export type Box=[number,number,number,number];
export type Region={page:number;bbox:Box;label?:string;key?:string};
export const regionSpace='pdf_display_normalized_v1';
export function validBox(v:unknown):v is Box{return Array.isArray(v)&&v.length===4&&v.every(n=>typeof n==='number'&&Number.isFinite(n))&&v[0]>=0&&v[1]>=0&&v[2]>0&&v[3]>0&&v[0]+v[2]<=1&&v[1]+v[3]<=1}
export function boxBetween(a:[number,number],b:[number,number]):Box{return [Math.min(a[0],b[0]),Math.min(a[1],b[1]),Math.abs(a[0]-b[0]),Math.abs(a[1]-b[1])]}
