// Never echo arbitrary server messages: they may contain storage URLs or secrets.
const publicMessages=new Set([
 'CMS object storage is not configured.',
 'Unsupported asset kind.',
 'Filename is required and must be at most 255 characters.',
 'Display name is required and must be at most 255 characters.',
 'checksumSha256 must be a 64-character hexadecimal digest.',
 'Image assets require alt text.',
 'An unexpected server error occurred.',
 'Page revision must be approved before entering a release.',
]);
export async function seedRequestError(response,method,endpoint) {
 const error=new Error('console_request_rejected_'+response.status);
 const path=endpoint.split('?')[0];
 error.diagnostic={method,endpoint:'/api/v1/cms'+path,httpStatus:response.status};
 const requestId=response.headers.get('x-request-id');
 if(requestId&&/^[a-zA-Z0-9_-]{1,128}$/.test(requestId))error.diagnostic.requestId=requestId;
 try{
  const payload=await response.json();
  const code=payload.error?.code;
  if(typeof code==='string'&&/^[a-z_0-9]{1,64}$/.test(code))error.diagnostic.serverCode=code;
  if(publicMessages.has(payload.error?.message))error.diagnostic.reason=payload.error.message;
 }catch{/* Status and endpoint remain available for non-JSON failures. */}
 return error;
}
