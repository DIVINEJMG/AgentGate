import {useEffect,useRef,useState} from 'react';
import {GLTFLoader} from 'three/examples/jsm/loaders/GLTFLoader.js';
import * as THREE from 'three';
import {validateModel} from './content/modelValidation';
export default function PublicModel({url,poster,alt,checksum}:{url:string;poster:string;alt:string;checksum?:string}) {
 const mount=useRef<HTMLDivElement>(null);const [ready,setReady]=useState(false);
 useEffect(()=>{
   const element=mount.current;if(!element)return;
   setReady(false);
   let disposed=false;let renderer:THREE.WebGLRenderer|undefined;let model:THREE.Group|undefined;
   const controller=new AbortController();
   const disposeModel=()=>model?.traverse(object=>{if(object instanceof THREE.Mesh){object.geometry.dispose();for(const material of Array.isArray(object.material)?object.material:[object.material]){for(const value of Object.values(material))if(value instanceof THREE.Texture)value.dispose();material.dispose();}}});
   const observer=new IntersectionObserver(entries=>{if(!entries[0].isIntersecting)return;observer.disconnect();void(async()=>{
     try{
       const response=await fetch(url,{signal:controller.signal,redirect:'error'});if(!response.ok||Number(response.headers.get('content-length')||0)>50*1024*1024)throw Error('model_unavailable');
       const reader=response.body?.getReader();if(!reader)throw Error('model_unavailable');const chunks:Uint8Array[]=[];let length=0;try{for(;;){const part=await reader.read();if(part.done)break;length+=part.value.length;if(length>50*1024*1024)throw Error('model_size_limit');chunks.push(part.value);}}finally{await reader.cancel();}const joined=new Uint8Array(length);let offset=0;for(const chunk of chunks){joined.set(chunk,offset);offset+=chunk.length;}const bytes=joined.buffer;if(disposed||length<20)throw Error('invalid_glb');
       // Producer verifies the container. Recheck dependency-free glTF before the loader may fetch anything.
       await validateModel(bytes,checksum);
       const gltf=await new GLTFLoader().parseAsync(bytes,'');model=gltf.scene;if(disposed){disposeModel();return;}
       let vertices=0;model.traverse(object=>{if(object instanceof THREE.Mesh){vertices+=object.geometry.getAttribute('position')?.count||0;for(const material of Array.isArray(object.material)?object.material:[object.material])for(const value of Object.values(material))if(value instanceof THREE.Texture&&value.image&&(value.image.width>4096||value.image.height>4096))throw Error('model_texture_limit');}});if(vertices>500000)throw Error('model_resource_limit');
       renderer=new THREE.WebGLRenderer({alpha:true,antialias:true});renderer.setPixelRatio(Math.min(devicePixelRatio,1.5));renderer.setSize(element.clientWidth||600,360);element.appendChild(renderer.domElement);
       const scene=new THREE.Scene();const camera=new THREE.PerspectiveCamera(35,(element.clientWidth||600)/360,.1,100);const bounds=new THREE.Box3().setFromObject(model);const center=bounds.getCenter(new THREE.Vector3());const size=bounds.getSize(new THREE.Vector3());model.position.sub(center);scene.add(model);camera.position.set(0,0,Math.max(size.x,size.y,size.z)*2.5||3);scene.add(new THREE.HemisphereLight(0xffffff,0x26382f,3));renderer.render(scene,camera);setReady(true);
     }catch{disposeModel();renderer?.dispose();renderer?.domElement.remove();/* Keep the verified poster visible on load, decoding or WebGL failure. */}
   })();});observer.observe(element);
   return()=>{disposed=true;controller.abort();observer.disconnect();disposeModel();renderer?.dispose();renderer?.domElement.remove();};
 },[url,checksum]);
 return <div ref={mount} aria-label={alt} style={{minHeight:360}}>{!ready&&poster&&<img src={poster} alt={alt} loading='lazy'/>}</div>;
}
