import { publicRoutes, safeDestination, type ContentBundle, type PublicRoute } from './contract';
export type HeaderDestination={label:string;route:string;detail:string;sectionId?:string;selectedRecordId?:string;path?:string;openInNewTab?:boolean};
export function headerNavigation(bundle:ContentBundle,key='header') {
  const source=bundle.release?.resources.find(item=>item.resourceType==='navigation'&&item.data.key===key&&item.data.locale===bundle.page?.locale);
  if(!source)return null;
  const items=source.data.items as {id:string;parentId:string|null;label:string;position:number;visible:boolean;openInNewTab?:boolean;destinationKind:string;destination:string;metadata?:{description?:string;sectionId?:string;selectedRecordId?:string}}[];
  if(!Array.isArray(items))throw new Error('invalid_navigation');
  if(new Set(items.map(item=>item.id)).size!==items.length||items.some(item=>item.parentId&&!items.some(parent=>parent.id===item.parentId&&!parent.parentId)))throw new Error('unsupported_navigation_tree');
  const destination=(item:typeof items[number]):HeaderDestination=>{
    const page=bundle.release!.pages.find(page=>page.pageId===item.destination&&page.locale===bundle.page?.locale);
    const path=item.destinationKind==='page'?page?.slug:item.destination;
    if(!path)throw new Error('unresolved_navigation');safeDestination(path);
    if(item.metadata?.sectionId&&!/^[a-z][a-z0-9_-]*$/i.test(item.metadata.sectionId))throw new Error('invalid_section_target');
    if(item.metadata?.selectedRecordId&&!/^[0-9a-f-]{36}$/i.test(item.metadata.selectedRecordId))throw new Error('invalid_selected_target');
    const profile=path==='/'?'home':path.slice(1);
    const route=publicRoutes.includes(profile as PublicRoute)?profile:path;
    return {label:item.label,route,path,openInNewTab:item.openInNewTab,detail:item.metadata?.description||'',sectionId:item.metadata?.sectionId,selectedRecordId:item.metadata?.selectedRecordId};
  };
  const roots=items.filter(item=>item.visible&&!item.parentId).sort((a,b)=>a.position-b.position);
  return {nav:roots.map(item=>({...destination(item),route:key==='footer'?item.id:destination(item).route})),menus:Object.fromEntries(roots.map(item=>[key==='footer'?item.id:destination(item).route,{intro:item.metadata?.description||'',links:items.filter(child=>child.visible&&child.parentId===item.id).sort((a,b)=>a.position-b.position).map(destination)}]))};
}
