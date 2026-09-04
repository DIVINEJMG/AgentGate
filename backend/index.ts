import { error, json, requireAuth, router } from '@appdeploy/sdk';
import { createOrganization, listOrganizationsForUser, serializeOrganizationsV1, serializeOrganizationsV2 } from './core/organizations';
import { readCanonicalSystemStatus, serializeV1, serializeV2 } from './core/system';
import { realtimeSubscriptionRoutes } from './realtime-subscribers';
const messageOf=(value:unknown)=>value instanceof Error?value.message:'Unexpected error.';
export const handler=router({
 'GET /api/_healthcheck':[async()=>json({message:'Success'})],
 'GET /api/v1/system/status':[async()=>json(serializeV1(readCanonicalSystemStatus()))],
 'GET /api/v2/system/status':[async()=>json(serializeV2(readCanonicalSystemStatus()))],
 'GET /api/v1/me':[requireAuth(),async(ctx)=>json({user:{id:ctx.user!.userId,email:ctx.user!.email??null,name:ctx.user!.name??null}})],
 'GET /api/v2/me':[requireAuth(),async(ctx)=>json({identity:{subject:ctx.user!.userId,email:ctx.user!.email??null,displayName:ctx.user!.name??null,type:'human'}})],
 'GET /api/v1/organizations':[requireAuth(),async(ctx)=>json(serializeOrganizationsV1(await listOrganizationsForUser(ctx.user!.userId)))],
 'GET /api/v2/organizations':[requireAuth(),async(ctx)=>json(serializeOrganizationsV2(await listOrganizationsForUser(ctx.user!.userId)))],
 'POST /api/v1/organizations':[requireAuth(),async(ctx)=>{try{const body=ctx.body as {name?:unknown};return json({organization:await createOrganization(ctx.user!.userId,body?.name)},201);}catch(caught){return error(messageOf(caught),400);}}],
 'POST /api/v2/organizations':[requireAuth(),async(ctx)=>{try{const body=ctx.body as {name?:unknown};const organization=await createOrganization(ctx.user!.userId,body?.name);return json({data:{organization:{id:organization.id,name:organization.name,createdAt:organization.createdAt},membership:{role:organization.role,permissions:organization.permissions}}},201);}catch(caught){return error(messageOf(caught),400);}}],
 ...realtimeSubscriptionRoutes,
});
