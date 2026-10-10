import {createHash} from 'node:crypto';
import {seedRequestError} from './seed-request-error.mjs';

export function createConsoleSeedRequest({origin,token,importId,fetchImpl=fetch}) {
 return async(method,endpoint,body)=>{
  if(method!=='GET'&&/publish|approve|schedule|restore|withdraw|ready/.test(endpoint))throw Error('publication_forbidden');
  const serialized=body===undefined?undefined:JSON.stringify(body);
  const headers={Authorization:'Bearer '+token,'Content-Type':'application/json'};
  if(['POST','PUT','PATCH','DELETE'].includes(method)){
   if(!importId)throw Error('import_identity_required');
   // The receipt's import identity survives restarts. Different payloads must
   // never replay an earlier write, even at the same endpoint.
   headers['Idempotency-Key']='seed-'+createHash('sha256')
    .update(JSON.stringify([importId,method,endpoint,serialized??null])).digest('hex');
  }
  const response=await fetchImpl(origin.replace(/\/$/,'')+'/api/v1/cms'+endpoint,{
   method,headers,body:serialized,signal:AbortSignal.timeout(30000),redirect:'error',
  });
  if(!response.ok)throw await seedRequestError(response,method,endpoint);
  return response.json();
 };
}
