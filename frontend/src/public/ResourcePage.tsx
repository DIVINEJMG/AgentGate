import {lazy,Suspense} from 'react';
const PublicModel=lazy(()=>import('./PublicModel'));
import {payloadOf,safeDestination,type Page} from './content/contract';
import {usePublicContent} from './content/ContentContext';
import type {Entry,Section,MediaReference} from './content/publicContent.generated';

export function PublicMedia({reference}: {reference:MediaReference}) {
 const {release,page}=usePublicContent();const asset=release?.assets.find(item=>item.id===reference.asset_id);
 const poster=release?.assets.find(item=>item.id===reference.poster_asset_id);
 const fallback=release?.assets.find(item=>item.id===reference.fallback_asset_id);
 if(!asset)return <p>Media unavailable.</p>;
 if(reference.role==='model')return <Suspense fallback={poster?<img src={poster.url} alt={poster.altText||''}/>:null}><PublicModel url={asset.url} checksum={asset.checksumSha256||undefined} poster={poster?.url||fallback?.url||''} alt={fallback?.altText||asset.altText||'3D illustration'}/></Suspense>;
 if(reference.role==='video')return <video controls playsInline preload='metadata' poster={poster?.url}>{<source src={asset.url} type={asset.mediaType}/>} {(reference.caption_asset_ids||[]).map(id=>{const caption=release?.assets.find(item=>item.id===id);return caption?<track key={id} kind='captions' src={caption.url} label={caption.altText||'Captions'} srcLang={page?.locale||'en'}/>:null;})}Video playback is unavailable.</video>;
 if(reference.role==='download')return <a href={asset.url} download>{asset.altText||'Download'}</a>;
 const renditions=(reference.rendition_asset_ids||[]).map(id=>release?.assets.find(item=>item.id===id)).filter(item=>item?.mediaType.startsWith('image/')&&item.width);
 return <img src={asset.url} srcSet={renditions.map(item=>`${item!.url} ${item!.width}w`).join(',')||undefined} sizes={renditions.length?'(max-width: 768px) 100vw, 960px':undefined} alt={asset.altText||''} width={asset.width} height={asset.height} loading='lazy' onError={event=>{if(fallback&&event.currentTarget.dataset.fallbackUsed!=='true'){event.currentTarget.dataset.fallbackUsed='true';event.currentTarget.srcset='';event.currentTarget.src=fallback.url;}}}/>;
}


export function ResourcePage({page}:{page:Page}) {
 const payload=payloadOf(page);
 return <main className='public-container' style={{paddingBlock:64,maxWidth:960}}><a href='/resources'>Resources</a><h1>{page.title}</h1>{(payload.sections as Section[]||[]).map(section=><section key={section.id} id={section.id}><h2>{section.heading}</h2>{section.paragraphs?.map((paragraph,index)=><p key={index}>{paragraph}</p>)}{section.points&&<ul>{section.points.map((point,index)=><li key={index}>{point}</li>)}</ul>}{section.links?.map(link=><a key={link.destination} href={safeDestination(link.destination)}>{link.label}</a>)}{section.media?.map(reference=><PublicMedia key={reference.asset_id} reference={reference}/>)}<ReferencedRecords ids={section.record_ids||[]}/></section>)}<EditorialEntries entries={(payload.entries as Entry[]||[]).filter(item=>!item.label.startsWith('copy:'))}/></main>;
}

export function EditorialEntries({entries}:{entries:Entry[]}) {
 return <>{entries.map(entry=><section key={entry.id}><h2>{entry.heading||entry.label}</h2>{entry.summary&&<p>{entry.summary}</p>}{entry.description&&<p>{entry.description}</p>}{entry.detail&&<p>{entry.detail}</p>}{entry.points&&<ul>{entry.points.map((point,index)=><li key={index}>{point}</li>)}</ul>}{entry.links?.map(link=><a key={link.destination} href={safeDestination(link.destination)}>{link.label}</a>)}{entry.media?.map(media=><PublicMedia key={media.asset_id} reference={media}/>)}<ReferencedRecords ids={entry.record_ids||[]}/></section>)}</>;
}
function ReferencedRecords({ids}:{ids:string[]}) {
 const bundle=usePublicContent();
 return <>{ids.map(id=>{const record=bundle.records.find(item=>item.pageId===id);if(!record||payloadOf(record).profile==='feature')return null;return <article key={id}><a href={record.slug}>{record.title}</a>{(payloadOf(record).sections||[]).map(section=><div key={section.id}><h3>{section.heading}</h3>{section.paragraphs?.map((paragraph,index)=><p key={index}>{paragraph}</p>)}</div>)}</article>;})}</>;
}
