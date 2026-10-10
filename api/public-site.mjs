import {readFile} from 'node:fs/promises';
import {publicResponse} from '../frontend/server-dist/public-site.mjs';
let template;
export default async function handler(request,response) {
 try{
  template ||= await readFile(new URL('../frontend/server-dist/template.html',import.meta.url),'utf8');
  const result=await publicResponse({url:request.url,env:process.env,template});
  response.statusCode=result.status;for(const [name,value]of Object.entries(result.headers))response.setHeader(name,value);response.end(result.body);
 }catch{console.error('Public frontend request failed',{category:'public_rendering_error'});response.statusCode=503;response.setHeader('Cache-Control','no-store');response.end('Public content is temporarily unavailable.');}
}
