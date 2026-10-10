import {createServer} from 'node:http';
import {readFile,stat} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {publicResponse} from '../server-dist/public-site.mjs';

const root=path.resolve(import.meta.dirname,'..');
const assets=path.join(root,'dist');
const template=await readFile(path.join(root,'server-dist/template.html'),'utf8');
const types={'.js':'text/javascript','.css':'text/css','.json':'application/json','.png':'image/png','.webp':'image/webp','.svg':'image/svg+xml','.ico':'image/x-icon','.woff2':'font/woff2','.glb':'model/gltf-binary'};
export function createPublicPreview() {
return createServer(async(request,response)=>{
 try {
  const pathname=decodeURIComponent(new URL(request.url,'http://localhost').pathname);
  const file=path.resolve(assets,'.'+pathname);
  if(file.startsWith(assets+path.sep)&&await stat(file).then(item=>item.isFile()).catch(()=>false)){
   response.setHeader('Content-Type',types[path.extname(file)]||'application/octet-stream');
   response.end(await readFile(file));return;
  }
  const result=await publicResponse({url:request.url,env:process.env,template});
  response.statusCode=result.status;
  for(const [name,value]of Object.entries(result.headers))response.setHeader(name,value);
  response.end(result.body);
 }catch{response.statusCode=503;response.setHeader('Cache-Control','no-store');response.end('Public rendering unavailable.');}
});
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 const port=Number(process.env.PORT||4173);
 createPublicPreview().listen(port,'127.0.0.1',()=>console.log(`Public frontend preview: http://127.0.0.1:${port}`));
}
