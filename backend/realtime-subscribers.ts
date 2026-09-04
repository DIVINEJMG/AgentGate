import { db, ws, json, error } from '@appdeploy/sdk';
const TABLE='entity_subscriptions';
async function list(){const {items}=await db.list(TABLE,{limit:1000});return items as Array<{id:string;entity_type:string;entity_id:string;connection_id:string}>;}
export async function addSubscription(entityType:string,entityId:string,connectionId:string){await db.add(TABLE,[{entity_type:entityType,entity_id:entityId,connection_id:connectionId,created_at:Date.now()}]);}
export async function removeSubscriptions(entityType:string,entityId:string,connectionId:string){const items=await list();const ids=items.filter(i=>i.entity_type===entityType&&i.entity_id===entityId&&i.connection_id===connectionId).map(i=>i.id);if(ids.length)await db.delete(TABLE,ids);}
export const realtimeSubscriptionRoutes={
 'POST /api/subscriptions':[async({body}:{body:unknown})=>{const {entity_type,entity_id,connection_id}=(body||{}) as Record<string,string>;if(!entity_type||!entity_id||!connection_id)return error('entity_type, entity_id, connection_id are required');await addSubscription(entity_type,entity_id,connection_id);return json({ok:true});}],
 'POST /api/subscriptions/remove':[async({body}:{body:unknown})=>{const {entity_type,entity_id,connection_id}=(body||{}) as Record<string,string>;if(!entity_type||!entity_id||!connection_id)return error('entity_type, entity_id, connection_id are required');await removeSubscriptions(entity_type,entity_id,connection_id);return json({ok:true});}],
};
