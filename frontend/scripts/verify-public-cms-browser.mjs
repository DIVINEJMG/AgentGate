import {createServer as httpServer} from 'node:http';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createServer} from 'vite';
import {seed,releaseFixture} from './public-content-fixture.mjs';
const root=path.resolve(import.meta.dirname,'..');
const require=createRequire(import.meta.url);
const browserRuntime=process.env.PUBLIC_CMS_BROWSER_RUNTIME||'C:/Users/divin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright';
const {chromium}=require(browserRuntime);
const {PNG}=require(path.join(path.dirname(require.resolve('playwright-core/package.json',{paths:[path.dirname(require.resolve(browserRuntime))]})),'lib/utilsBundle.js'));
function comparePixels(actual,expected,key) {
 if(actual.equals(expected))return 0;
 const a=PNG.sync.read(actual),b=PNG.sync.read(expected);
 assert.equal(a.width,b.width,key+' width');assert.equal(a.height,b.height,key+' height');
 let edgePixels=0;
 for(let offset=0;offset<a.data.length;offset+=4){
  let delta=0;for(let channel=0;channel<4;channel++)delta=Math.max(delta,Math.abs(a.data[offset+channel]-b.data[offset+channel]));
  if(!delta)continue;
  // Edge rasterization changes eight right-edge shadow pixels by 1/255.
  // No tolerance applies to text, media or any other part of the page.
  assert.ok(offset/4%a.width>=a.width-6&&delta<=1,key+' content pixels');edgePixels++;
 }
 assert.ok(edgePixels<=16,key+' edge rasterization');return edgePixels;
}
const output=path.join(root,'artifacts/public-cms');await mkdir(output,{recursive:true});
let fixture;let requests=0,bytes=0;
const storage=httpServer(async(req,res)=>{const pathname=new URL(req.url,'http://localhost').pathname;let value=fixture.objects.get(pathname);if(pathname.includes('/assets/')){const media=seed.media.find(item=>pathname.endsWith('/'+item.filename));if(media)value=await readFile(path.resolve(root,media.path));}if(!value){res.writeHead(404);res.end();return;}requests++;bytes+=value.length;res.setHeader('content-type',pathname.includes('/assets/')?'image/webp':'application/json');res.end(value);});
await new Promise(resolve=>storage.listen(0,'127.0.0.1',resolve));const contentOrigin='http://127.0.0.1:'+storage.address().port;fixture=releaseFixture(contentOrigin);
process.env.PUBLIC_CONTENT_ORIGIN=contentOrigin;process.env.PUBLIC_CONTENT_ENABLED='false';process.env.PUBLIC_SITE_ORIGIN='http://localhost:5193';
const production=process.argv.includes('--production');
const server=production?(await import('./preview-public-site.mjs')).createPublicPreview():await createServer({root,server:{host:'127.0.0.1',port:5193,strictPort:true}});
if(production)await new Promise(resolve=>server.listen(5193,'127.0.0.1',resolve));else await server.listen();
const browser=await chromium.launch({channel:process.env.PUBLIC_CMS_BROWSER_CHANNEL||'chrome',headless:true,args:['--disable-gpu','--disable-dev-shm-usage']});
const results=[];const errors=[];const edgeRasterization=[];
try{
 for(const width of [1440,768,390]){
  const context=await browser.newContext({viewport:{width,height:900},reducedMotion:'reduce'});
  const page=await context.newPage();page.on('pageerror',error=>{errors.push(error.message);console.error('Browser error:',error.message);});page.on('console',message=>{if(message.type()==='error')console.error('Browser console:',message.text().slice(0,500));});
  // Freeze presentation animation for reproducible comparisons; interactions remain enabled.
  for(const source of ['static','published']){
   process.env.PUBLIC_CONTENT_ENABLED=source==='published'?'true':'false';
   for(const item of seed.pages){
    const response=await page.goto('http://127.0.0.1:5193'+item.slug,{waitUntil:'domcontentloaded',timeout:60000});assert.equal(response.status(),200);
    await page.waitForFunction(()=>document.querySelector('.public-header')&&getComputedStyle(document.querySelector('.public-header')).display==='grid',{},{timeout:120000});
    await page.waitForTimeout(300);
    // Scroll-control timing and shadow compositing vary by one colour level.
    // Exclude the existing fixed rail without changing document geometry.
    await page.addStyleTag({content:'*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}.audoryn-scroll-control{visibility:hidden!important}html{scrollbar-color:transparent transparent}::-webkit-scrollbar,::-webkit-scrollbar-thumb,::-webkit-scrollbar-track{background:transparent!important}'});
    await page.evaluate(()=>document.fonts.ready);
    await page.mouse.move(0,0);
    await page.evaluate(async()=>{for(const img of document.images){if(img.loading==='lazy')img.loading='eager';}await Promise.all([...document.images].map(img=>img.complete?Promise.resolve():new Promise(resolve=>{img.onload=resolve;img.onerror=resolve;})));});
    const text=await page.locator('.public-site').innerText();const links=await page.locator('.public-site a').evaluateAll(items=>items.map(item=>[item.textContent,item.getAttribute('href')]));
    const labels=await page.locator('.public-site [aria-label]').evaluateAll(items=>items.map(item=>item.getAttribute('aria-label')));
    const key=width+':'+item.slug;const prior=results.find(result=>result.key===key);
    const screenshot=await page.screenshot({path:path.join(output,`${item.slug==='/'?'home':item.slug.slice(1)}-${width}-${source}.png`),fullPage:true,animations:'disabled',mask:[page.locator('canvas')]});
    if(prior){assert.equal(text,prior.text,key+' text');assert.deepEqual(links,prior.links,key+' links');assert.deepEqual(labels,prior.labels,key+' accessibility');const pixels=comparePixels(screenshot,prior.screenshot,key);if(pixels)edgeRasterization.push({key,pixels,maxChannelDifference:1});prior.equivalent=true;}else results.push({key,text,links,labels,screenshot});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,key+' overflow');
   }
  }
  await page.goto('http://127.0.0.1:5193/pricing');await page.locator('details').first().locator('summary').click();assert.equal(await page.locator('details').first().getAttribute('open'),'');
  await page.goto('http://127.0.0.1:5193/product');await page.getByRole('button',{name:'Jobs',exact:true}).click();await page.getByText('Compile weekly research brief',{exact:true}).first().waitFor();
  await page.getByRole('button',{name:'Approvals',exact:true}).click();await page.getByText('Post stakeholder update',{exact:true}).waitFor();
  await page.goto('http://127.0.0.1:5193/');await page.getByRole('button',{name:'Show control scene'}).click();assert.equal(await page.locator('.home-hero-display h1').textContent(),'CONTROL');
  const tabs=page.getByRole('tab');await tabs.nth(2).click();assert.equal(await tabs.nth(2).getAttribute('aria-selected'),'true');await tabs.nth(2).press('ArrowRight');assert.equal(await tabs.nth(3).getAttribute('aria-selected'),'true');
  await page.locator('.home-journey').scrollIntoViewIfNeeded();assert.equal(await page.locator('.home-journey-chapter').count(),4);
  await page.goto('http://127.0.0.1:5193/solutions');await page.locator('.solutions-index button').nth(2).click();assert.ok(new URL(page.url()).searchParams.get('selected'));await page.reload();assert.ok(new URL(page.url()).searchParams.get('selected'));
  await page.goto('http://127.0.0.1:5193/security');await page.locator('.security-decision-options button').last().click();assert.equal(await page.locator('.security-decision-options button').last().getAttribute('aria-pressed'),'true');
  await page.goto('http://127.0.0.1:5193/privacy#legal-information');await page.locator('.legal-aside button').first().click();assert.ok(await page.locator('#legal-information').isVisible());
  if(width===390){await page.getByRole('button',{name:'Open navigation'}).click();assert.equal(await page.getByRole('button',{name:'Open navigation'}).getAttribute('aria-expanded'),'true');}
  await context.close();
 }
 assert.deepEqual(errors,[],'browser runtime errors');
 const report={mode:production?'production':'development',pages:9,widths:[1440,768,390],comparisons:results.length,equivalent:results.filter(item=>item.equivalent).length,edgeRasterization,requests,bytes,errors};await writeFile(path.join(output,'report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
}finally{await browser.close();if(production)await new Promise(resolve=>server.close(resolve));else await server.close();await new Promise(resolve=>storage.close(resolve));}
