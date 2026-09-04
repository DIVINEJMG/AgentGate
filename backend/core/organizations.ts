import { db } from '@appdeploy/sdk';

export type OrganizationRole = 'owner' | 'admin' | 'security_manager' | 'operator' | 'approver' | 'viewer';
export interface OrganizationRecord { name:string; createdBy:string; createdAt:string; }
export interface MembershipRecord { organizationId:string; userId:string; role:OrganizationRole; createdAt:string; }
const ROLE_PERMISSIONS: Record<OrganizationRole,string[]> = {
 owner:['organizations.read','organizations.manage','memberships.manage','agents.read','agents.manage','policies.read','policies.manage','approvals.review','audit.read','integrations.manage'],
 admin:['organizations.read','organizations.manage','memberships.manage','agents.read','agents.manage','policies.read','policies.manage','approvals.review','audit.read','integrations.manage'],
 security_manager:['organizations.read','agents.read','agents.manage','policies.read','policies.manage','approvals.review','audit.read','integrations.manage'],
 operator:['organizations.read','agents.read','agents.manage','policies.read','audit.read'],
 approver:['organizations.read','agents.read','policies.read','approvals.review','audit.read'],
 viewer:['organizations.read','agents.read','policies.read','audit.read']
};
const membershipsTable=(userId:string)=>`memberships:${userId}`;
export async function createOrganization(userId:string,rawName:unknown){const name=typeof rawName==='string'?rawName.trim():'';if(name.length<2||name.length>80)throw new Error('Organization name must be between 2 and 80 characters.');const createdAt=new Date().toISOString();const [organizationId]=await db.add('organizations',[{name,createdBy:userId,createdAt}]);if(!organizationId)throw new Error('Failed to create organization.');const [membershipId]=await db.add(membershipsTable(userId),[{organizationId,userId,role:'owner',createdAt}]);if(!membershipId){await db.delete('organizations',[organizationId]);throw new Error('Failed to create organization membership.');}return{id:organizationId,name,role:'owner' as const,permissions:ROLE_PERMISSIONS.owner,createdAt};}
export async function listOrganizationsForUser(userId:string){const {items:memberships}=await db.list<MembershipRecord>(membershipsTable(userId),{limit:50});if(!memberships.length)return[];const organizations=await db.get<OrganizationRecord>('organizations',memberships.map(m=>m.organizationId));return memberships.flatMap((m,i)=>{const o=organizations[i];return o?[{id:m.organizationId,name:o.name,role:m.role,permissions:ROLE_PERMISSIONS[m.role],createdAt:o.createdAt}]:[];});}
export function serializeOrganizationsV1(organizations:Awaited<ReturnType<typeof listOrganizationsForUser>>){return{organizations,count:organizations.length};}
export function serializeOrganizationsV2(organizations:Awaited<ReturnType<typeof listOrganizationsForUser>>){return{items:organizations.map(({id,name,createdAt,role,permissions})=>({organization:{id,name,createdAt},membership:{role,permissions}})),total:organizations.length};}
