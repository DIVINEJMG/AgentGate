export async function validateModel(bytes:ArrayBuffer,checksum?:string) {
 if(bytes.byteLength<20||bytes.byteLength>50*1024*1024)throw Error('model_size_limit');
 if(checksum){const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),byte=>byte.toString(16).padStart(2,'0')).join('');if(digest!==checksum)throw Error('model_checksum_mismatch');}
 const view=new DataView(bytes);
 if(view.getUint32(0,true)!==0x46546c67||view.getUint32(4,true)!==2||view.getUint32(8,true)!==bytes.byteLength||view.getUint32(16,true)!==0x4e4f534a)throw Error('invalid_glb');
 const length=view.getUint32(12,true);if(length%4||length>bytes.byteLength-20)throw Error('invalid_glb');
 const json=JSON.parse(new TextDecoder().decode(new Uint8Array(bytes,20,length)));
 if(json.asset?.version!=='2.0'||[...json.buffers||[],...json.images||[]].some((item:{uri?:string})=>item.uri)||json.extensionsRequired?.length)throw Error('external_model_dependency');
 if((json.meshes||[]).length>128||(json.accessors||[]).some((item:{count:number})=>item.count>200000)||(json.images||[]).length>16)throw Error('model_resource_limit');
 let offset=20+length;while(offset<bytes.byteLength){if(offset+8>bytes.byteLength)throw Error('invalid_glb');const chunk=view.getUint32(offset,true);if(chunk%4||offset+8+chunk>bytes.byteLength)throw Error('invalid_glb');offset+=8+chunk;}
 return json;
}
