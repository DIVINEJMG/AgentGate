import test from 'node:test';
import assert from 'node:assert/strict';
import {seedRequestError} from '../scripts/seed-request-error.mjs';
test('import errors identify the request and allowlisted reason',async()=>{
 const response=new Response(JSON.stringify({error:{code:'http_error',message:'Image assets require alt text.'}}),{status:400,headers:{'x-request-id':'test-123'}});
 const error=await seedRequestError(response,'POST','/assets/upload-intents?private=value');
 assert.equal(error.message,'console_request_rejected_400');
 assert.deepEqual(error.diagnostic,{method:'POST',endpoint:'/api/v1/cms/assets/upload-intents',httpStatus:400,requestId:'test-123',serverCode:'http_error',reason:'Image assets require alt text.'});
});
test('import errors suppress arbitrary server content and invalid correlation values',async()=>{
 const response=new Response(JSON.stringify({error:{code:'https://private.example',message:'secret token signed URL',details:'private'}}),{status:500,headers:{'x-request-id':'private?token=secret'}});
 const error=await seedRequestError(response,'GET','/assets?private=value');
 assert.deepEqual(error.diagnostic,{method:'GET',endpoint:'/api/v1/cms/assets',httpStatus:500});
});
test('non-JSON failures retain status and request identity',async()=>{
 const error=await seedRequestError(new Response('private server HTML',{status:502}),'GET','/assets');
 assert.equal(error.diagnostic.httpStatus,502);
 assert.equal(error.diagnostic.endpoint,'/api/v1/cms/assets');
});
