import ts from 'typescript';
import {readFileSync,writeFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import path from 'node:path';
const root = path.resolve(import.meta.dirname,'..');
const dir = path.join(root,'content-seed');
const copies = JSON.parse(readFileSync(path.join(dir,'static-copy.json'),'utf8'));
copies.HomeGateScene={...copies.HomeGateScene,'stage-defined':'Work is defined','stage-policy':'Policy is evaluated','stage-approval':'A person decides','stage-recorded':'Execution is recorded'};
copies.ProductShowcase={...copies.ProductShowcase,'tab-workforce':'Workforce','tab-jobs':'Jobs','tab-approvals':'Approvals'};
writeFileSync(path.join(dir,'static-copy.json'),JSON.stringify(copies,null,2)+'\n');
const uuid = key => { const hex=createHash('sha256').update(`audoryn-public-v1:${key}`).digest('hex').slice(0,32).split(''); hex[12]='5'; hex[16]='8'; const s=hex.join(''); return `${s.slice(0,8)}-${s.slice(8,12)}-${s.slice(12,16)}-${s.slice(16,20)}-${s.slice(20)}`; };
function literal(node) {
  if (ts.isAsExpression(node) || ts.isParenthesizedExpression(node)) return literal(node.expression);
  if (ts.isStringLiteral(node) || ts.isNumericLiteral(node)) return ts.isNumericLiteral(node)?Number(node.text):node.text;
  if (ts.isIdentifier(node)) return node.text;
  if (ts.isArrayLiteralExpression(node)) return node.elements.map(literal);
  if (ts.isObjectLiteralExpression(node)) return Object.fromEntries(node.properties.map(p=>{if(!ts.isPropertyAssignment(p))throw Error('nonliteral_seed');return [p.name.getText().replace(/['"]/g,''),literal(p.initializer)];}));
  throw Error('unsupported_seed_literal');
}
function collection(scope,name) {
  const ast=ts.createSourceFile(scope+'.tsx',readFileSync(path.join(dir,'baseline',scope+'.tsx'),'utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
  let result;
  const visit=n=>{if(ts.isVariableDeclaration(n)&&n.name.getText(ast)===name)result=literal(n.initializer);ts.forEachChild(n,visit);}; visit(ast);
  if(!result)throw Error(`missing_seed_collection:${scope}:${name}`);return result;
}
const pages=[];const records=[];const collections={};
const media=[];
const ids={};
function makePage(key,profile,slug,payload) {
  return {pageId:uuid('page/'+key),revisionId:uuid('baseline-revision/'+key),locale:'en',slug,title:profile==='home'?'Home':profile[0].toUpperCase()+profile.slice(1),contentType:'page',rendererContract:'audoryn.public.v1',blocks:[{id:uuid('block/'+key),type:'PublicContent',variant:'content',theme:'content',content:{payload:{contract:'audoryn.public.v1',profile,...payload}}}],seo:{metaTitle:profile==='home'?'Audoryn · AI workforce, under control':`${profile[0].toUpperCase()+profile.slice(1)} · Audoryn`,metaDescription:'Audoryn is an SOT product for creating, operating and governing an AI workforce with explicit human control.'}};
}
for(const [scope,fields] of Object.entries(copies)) {
  ids[scope]=[];
  const pairs=Object.entries(fields);
  for(let offset=0;offset<pairs.length;offset+=64) {
    const key=`copy/${scope}/${pairs[offset][0]}`;
    const record=makePage(key,'feature',`/content-fields/${scope.toLowerCase()}-${offset/64+1}`,{sections:pairs.slice(offset,offset+64).map(([id,value])=>({id,heading:id.replace(/-[a-f0-9]{10}$/,'').replace(/-/g,' '),paragraphs:[value]})),entries:[]});
    ids[scope].push(record.pageId);records.push(record);
  }
}
const scopes={home:['PublicSite','HomeHero','HomeJourney','HomeOperatingView','HomeGateScene'],product:['PublicSite','ProductShowcase'],solutions:['PublicSite','SolutionsShowcase'],security:['PublicSite','SecurityShowcase'],pricing:['PublicSite','PricingShowcase'],resources:['PublicSite','ResourcesShowcase'],company:['PublicSite','CompanyShowcase'],privacy:['PublicSite','LegalShowcase'],terms:['PublicSite','LegalShowcase']};
function bind(profile,scope,name,target,mapping,extras=()=>({})) {
  const originals=collection(scope,name);
  const items=Array.isArray(originals)?originals:Object.entries(originals).map(([key,value])=>({...value,semantic:key}));
  const descriptors=[];
  const result=items.map(item=>{
    const identity=item.semantic||item.area||item.name||item.label||item.title||item.id;
    const id=uuid(`${scope}/${name}/${identity}`);
    const value={id,label:item.name||item.label||item.title||identity,...extras(item,descriptors.length)};
    for(const [from,to] of Object.entries(mapping))value[to]=item[from];
    const descriptor={id,identity,defaults:item,fields:mapping};
    descriptors.push(descriptor); return value;
  });
  collections[`${scope}/${name}`]={profile,target,items:descriptors};
  return result;
}
for(const [profile,pageScopes] of Object.entries(scopes)) {
  const payload={sections:[],entries:pageScopes.map(scope=>({id:uuid('scope/'+scope),label:'copy:'+scope,record_ids:ids[scope]}))};
  if(profile==='home') {
    payload.hero_scenes=bind(profile,'HomeHero','HERO_SCENES','hero_scenes',{word:'word',label:'label',title:'heading',body:'description'},(_,i)=>({scene:['autonomy','control','oversight'][i]}));
    payload.journey=bind(profile,'HomeJourney','CHAPTERS','journey',{label:'label',title:'heading',body:'description'},(_,i)=>({phase:['defined','policy','approval','recorded'][i]}));
    payload.operating_areas=bind(profile,'HomeOperatingView','AREAS','operating_areas',{label:'label',title:'heading',description:'description',summary:'summary',path:'path',signal:'signal'},(_,i)=>({area:['workers','jobs','governance','supervision'][i]}));
  }
  if(profile==='solutions')payload.solutions=bind(profile,'SolutionsShowcase','solutions','solutions',{name:'label',line:'heading',detail:'detail',worker:'worker',job:'job',output:'output'},item=>({icon:({Operations:'operations',Research:'research','Customer support':'support',Sales:'sales',Marketing:'marketing',Engineering:'engineering'})[item.name],tools:item.tools.split(' · ')}));
  if(profile==='company')payload.entries.push(...bind(profile,'CompanyShowcase','portfolio','entries',{name:'label',description:'description',category:'summary'},item=>({category:'portfolio',links:[{label:item.name,destination:item.href}]})));
  if(profile==='resources')payload.entries.push(...bind(profile,'ResourcesShowcase','guides','entries',{title:'heading',description:'description',topic:'summary'},item=>({category:'directory',links:[{label:item.title,destination:'/'+item.route}]})));
  if(profile==='security')payload.entries.push(...bind(profile,'SecurityShowcase','controls','entries',{title:'heading',text:'description'},()=>({category:'control'})));
  if(profile==='security')payload.security_decisions=bind(profile,'SecurityShowcase','decisions','security_decisions',{label:'label',title:'heading',detail:'description',note:'note'},item=>({decision:item.semantic}));
  if(profile==='pricing')payload.plans=bind(profile,'PricingShowcase','plans','plans',{name:'label',audience:'description',state:'status_label'},item=>({plan_key:item.name.toLowerCase(),price_minor:Number(item.price)*100,currency:'USD',billing_period:'month',availability:item.name==='Free'?'free_signup':'unavailable',worker_capacity_display:Number(item.limit)}));
  if(profile==='product'){
   payload.entries.push(...bind(profile,'ProductShowcase','integrations','entries',{name:'label',use:'description'},()=>({category:'integration'})));
   payload.demonstration={disclosure:'Illustrative workspace view · Example data',organization_label:'NORTHSTAR OPERATIONS',workers:[{id:uuid('demo/research'),label:'Research analyst',summary:'Insights team',category:'Research',detail:'Ada M.',status_label:'Active'},{id:uuid('demo/operations'),label:'Operations coordinator',summary:'Operations',category:'Operations',detail:'Daniel K.',status_label:'Active'}],jobs:[{id:uuid('demo/research-job'),label:'Compile weekly research brief',summary:'Research analyst · Scheduled',status_label:'Completed'},{id:uuid('demo/release-job'),label:'Review release activity',summary:'Operations coordinator · Running',status_label:'Running'},{id:uuid('demo/update-job'),label:'Prepare stakeholder update',summary:'Operations coordinator · Queued',status_label:'Queued'}],approvals:[{id:uuid('demo/approval'),label:'Post stakeholder update',description:'Operations coordinator requested a Slack message. The run is paused while a reviewer checks the request and its context.',summary:'Operations coordinator',detail:'Daniel K.',status_label:'ACTION HELD'}],run_steps:[]};
  }
  if(profile==='privacy'||profile==='terms') {
    payload.sections=collection('LegalShowcase',profile==='privacy'?'privacySections':'termsSections').map(({id,title,paragraphs,points})=>({id,heading:title,paragraphs,...points?{points}:{}}));
    payload.effective_date='2026-09-30';payload.document_version='2026-09-30';
  }
  pages.push(makePage(profile,profile,profile==='home'?'/':'/'+profile,payload));
}
const navigation=collection('PublicSite','NAV');const menus=collection('PublicSite','NAV_MENUS');
const navItems=[];
for(const [position,item]of navigation.entries()){
 const id=uuid('header/'+item.route);navItems.push({id,parentId:null,position,label:item.label,destinationKind:'page',destination:pages.find(p=>p.blocks[0].content.payload.profile===item.route).pageId,visible:true,openInNewTab:false,metadata:{description:menus[item.route]?.intro||''}});
 for(const [childPosition,child]of (menus[item.route]?.links||[]).entries())navItems.push({id:uuid('header/'+item.route+'/'+child.label),parentId:id,position:childPosition,label:child.label,destinationKind:'page',destination:pages.find(p=>p.blocks[0].content.payload.profile===child.route).pageId,visible:true,openInNewTab:false,metadata:{description:child.detail,sectionId:child.sectionId||null,selectedRecordId:child.solutionIndex===undefined?null:pages.find(p=>p.slug==='/solutions').blocks[0].content.payload.solutions[child.solutionIndex].id}});
}
for(const name of ['human-oversight.webp','operations-work.webp','research-work.webp']){
 const file=path.join(root,'src/public/assets',name);const bytes=readFileSync(file);
 const kind=bytes.toString('ascii',12,16);let width,height;if(kind==='VP8X'){width=bytes.readUIntLE(24,3)+1;height=bytes.readUIntLE(27,3)+1;}else if(kind==='VP8 '){width=bytes.readUInt16LE(26)&16383;height=bytes.readUInt16LE(28)&16383;}else if(kind==='VP8L'){const dimensions=bytes.readUInt32LE(21);width=(dimensions&16383)+1;height=((dimensions>>>14)&16383)+1;}else throw Error('unsupported_baseline_webp');
 media.push({id:uuid('asset/'+name),path:'src/public/assets/'+name,filename:name,width,height,mediaType:'image/webp',sizeBytes:bytes.length,checksumSha256:createHash('sha256').update(bytes).digest('hex'),altText:name==='human-oversight.webp'?'Two colleagues reviewing a decision together at a table':name==='operations-work.webp'?'A logistics operations floor with a supervisor overseeing the work':'Research materials being reviewed at a worktable'});
}
for(const page of pages){
 const profile=page.blocks[0].content.payload.profile;
 const required=profile==='home'?media:profile==='product'?media.filter(m=>m.filename==='human-oversight.webp'):profile==='solutions'?media.filter(m=>m.filename==='operations-work.webp'):[];
 page.blocks[0].content.payload.sections.push(...required.map(m=>({id:'media-'+m.filename.replace('.webp',''),heading:m.altText,media:[{asset_id:m.id,role:'image'}]})));
}
const footerItems=[];
const publicAst=ts.createSourceFile('PublicSite.tsx',readFileSync(path.join(dir,'baseline/PublicSite.tsx'),'utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
function footer(node){
 if(ts.isJsxSelfClosingElement(node)&&node.tagName.getText(publicAst)==='FooterGroup'){
  const title=node.attributes.properties.find(p=>p.name?.text==='title').initializer.text;
  const items=literal(node.attributes.properties.find(p=>p.name?.text==='items').initializer.expression);
  const id=uuid('footer/'+title);footerItems.push({id,parentId:null,label:title,position:footerItems.filter(item=>!item.parentId).length,destinationKind:'page',destination:pages.find(page=>page.slug==='/'+items[0][1]).pageId,visible:true,openInNewTab:false,metadata:{}});
  items.forEach(([label,route],position)=>footerItems.push({id:uuid('footer/'+title+'/'+label),parentId:id,label,position,destinationKind:'page',destination:pages.find(page=>page.slug==='/'+route).pageId,visible:true,openInNewTab:false,metadata:{}}));
 }ts.forEachChild(node,footer);
}footer(publicAst);
const seed={schemaVersion:1,contract:'audoryn.public.v1',status:'draft',publicationAllowed:false,pages,records,media,navigations:[{key:'header',name:'Public header',locale:'en',items:navItems},{key:'footer',name:'Public footer',locale:'en',items:footerItems}],globals:[{key:'public-branding',locale:'en',value:{contract:'audoryn.public.v1',profile:'branding',sections:[{id:'identity',heading:'Audoryn',paragraphs:['An SOT Product','Audoryn · Controlled autonomous workforce infrastructure']}],entries:[]}}]};
// Section identities are part of the publication graph even when their visual
// content is rendered by specialised components instead of generic sections.
for(const item of navItems){
 if(!item.metadata.sectionId)continue;
 const page=pages.find(page=>page.pageId===item.destination);
 const sections=page.blocks[0].content.payload.sections;
 if(!sections.some(section=>section.id===item.metadata.sectionId))sections.push({id:item.metadata.sectionId,heading:item.metadata.sectionId==='solution-index'?page.title:item.label});
}
seed.globals[0].value.entries.push({id:uuid('global/public-copy'),label:'copy:PublicSite',record_ids:ids.PublicSite});
seed.globals[0].value.sections.push({id:'primary-signin',heading:'Sign in',links:[{label:'Sign in',destination:'/login'}]},{id:'primary-signup',heading:'Get started',links:[{label:'Get started',destination:'/signup'}]});
writeFileSync(path.join(dir,'audoryn-public.seed.json'),JSON.stringify(seed,null,2)+'\n');
writeFileSync(path.join(dir,'media-manifest.json'),JSON.stringify(media,null,2)+'\n');
writeFileSync(path.join(dir,'collection-bindings.json'),JSON.stringify(collections,null,2)+'\n');
writeFileSync(path.join(dir,'demonstration-baseline.json'),JSON.stringify(pages.find(page=>page.slug==='/product').blocks[0].content.payload.demonstration,null,2)+'\n');
console.log(JSON.stringify({pages:pages.length,records:records.length,media:media.length,copyFields:Object.values(copies).reduce((n,v)=>n+Object.keys(v).length,0)}));
