import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';
import * as jsx from 'react/jsx-runtime';
import * as icons from 'lucide-react';
import {renderToStaticMarkup} from 'react-dom/server';

let runtime;
globalThis.setupTestAPI = {
  get: async path => {runtime.calls.push({method:'GET',path});return {data:{status:'connector_pending',organizationId:'org'}};},
  post: async (path,body) => {runtime.calls.push({method:'POST',path,body});return runtime.post(path,body);},
};
globalThis.setupTestHooks = {
  useState(initial) {const i=runtime.cursor++;if (!(i in runtime.state)) runtime.state[i]=typeof initial==='function'?initial():initial;return [runtime.state[i],value=>{runtime.state[i]=typeof value==='function'?value(runtime.state[i]):value;}];},
  useRef(initial) {const i=runtime.cursor++;if (!(i in runtime.state)) runtime.state[i]={current:initial};return runtime.state[i];},
  useEffect(fn) {const i=runtime.cursor++;if (!(i in runtime.state)) {runtime.state[i]=true;runtime.effects.push(fn);}},
};
globalThis.setupTestJSX=jsx;globalThis.setupTestIcons=icons;
const compile=source=>ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.ReactJSX}}).outputText;
const helper=compile((await readFile(new URL('../src/lib/githubSetup.ts',import.meta.url),'utf8')).replace("import {api} from '../platform/apiClient';",'const api=globalThis.setupTestAPI;'));
globalThis.setupTestHelpers=await import(`data:text/javascript;base64,${Buffer.from(helper).toString('base64')}`);
let code=compile(await readFile(new URL('../src/components/GitHubSignupConnection.tsx',import.meta.url),'utf8'));
code=code.replace(/import \{([^}]+)\} from ["']react\/jsx-runtime["'];/,(_,bindings)=>`const {${bindings.replace(/ as /g,':')}}=globalThis.setupTestJSX;`)
 .replace(/import \{([^}]+)\} from 'react';/,(_,bindings)=>`const {${bindings}}=globalThis.setupTestHooks;`)
 .replace(/import \{([^}]+)\} from 'lucide-react';/,(_,bindings)=>`const {${bindings}}=globalThis.setupTestIcons;`)
 .replace(/import \{[^;]+from '..\/platform\/apiClient';/,'const api=globalThis.setupTestAPI;')
 .replace(/import \{([^}]+)\} from '..\/lib\/githubSetup';/,(_,bindings)=>`const {${bindings}}=globalThis.setupTestHelpers;`);
const {default:Screen}=await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
const flowId='12345678-1234-1234-1234-123456789abc';
function setup(post) {
 const session=new Map();
 globalThis.window={sessionStorage:{getItem:key=>session.get(key)||null,removeItem:key=>session.delete(key)},location:{assign:url=>{runtime.destination=url;}}};
 runtime={state:[],cursor:0,effects:[],calls:[],finished:[],post,session};return runtime;
}
function render() {runtime.cursor=0;return Screen({flowId,organizations:[{id:'org',name:'Engineering'}],onFinished:org=>runtime.finished.push(org)});}
const flush=async()=>{await new Promise(setImmediate);await new Promise(setImmediate);};
async function start() {render();runtime.cleanup=runtime.effects[0]();await flush();}
function button(tree,text) {if (!tree||typeof tree!=='object') return null;if (tree.type==='button' && renderToStaticMarkup(tree).includes(text)) return tree;for (const child of [tree.props?.children].flat(Infinity)) {const found=button(child,text);if (found)return found;}return null;}

test('missing installation stays on optional screen and starts verified return',async()=>{
 const r=setup(async path=>({data:path.endsWith('/install')?{installationUrl:'https://github.com/apps/audoryn/installations/new?state=nonce'}:{status:'installation_required',installationUrl:'https://github.com/apps/audoryn/installations/new',installations:[]}}));
 await start();const tree=render();const html=renderToStaticMarkup(tree);
 assert.match(html,/OPTIONAL CONNECTION/);assert.match(html,/provider-marks\/github.svg/);assert.match(html,/Skip for now/);assert.equal(r.finished.length,0);
 button(tree,'Continue to GitHub').props.onClick();await flush();assert.match(r.destination,/github.com\/apps\/audoryn\/installations\/new\?state=nonce/);
});
test('return verifies exact installation and finishes without a connect click',async()=>{
 const r=setup(async()=>({data:{status:'connected',connectionId:'verified'}}));r.session.set('audoryn.github.installation',JSON.stringify({flowId,installationId:'8'}));
 await start();assert.equal(r.finished[0].id,'org');assert.deepEqual(r.calls.find(c=>c.method==='POST').body,{installation_id:'8'});assert.equal(r.session.has('audoryn.github.installation'),false);
});
test('skip is available after failure without disconnecting or signing out',async()=>{
 const r=setup(async path=>{if(path.endsWith('/dismiss'))return {data:{status:'dismissed'}};throw new Error('GitHub is temporarily unavailable');});
 await start();const tree=render();assert.match(renderToStaticMarkup(tree),/GitHub is temporarily unavailable/);
 assert.equal(button(tree,'Skip for now').props.disabled,false);button(tree,'Skip for now').props.onClick();await flush();
 assert.deepEqual(r.finished,[null]);assert.ok(r.calls.some(c=>c.path.endsWith('/dismiss')));assert.ok(r.calls.every(c=>!c.path.includes('logout')&&!c.path.includes('disconnect')));
});
test('late result cannot undo skip',async()=>{
 let finishRequest;const r=setup(path=>path.endsWith('/dismiss')?Promise.resolve({data:{}}):new Promise(resolve=>{finishRequest=resolve;}));
 await start();assert.equal(button(render(),'Skip for now').props.disabled,false);button(render(),'Skip for now').props.onClick();await flush();
 finishRequest({data:{status:'connected'}});await flush();assert.deepEqual(r.finished,[null]);
});
test('in-flight checks are shared but later access checks are fresh',async()=>{
 const r=setup(async()=>({data:{status:'installation_required'}}));const {continueGitHubSetup}=globalThis.setupTestHelpers;
 await Promise.all([continueGitHubSetup('org',flowId),continueGitHubSetup('org',flowId)]);assert.equal(r.calls.length,1);
 await continueGitHubSetup('org',flowId);assert.equal(r.calls.length,2);
});
test('installation hints cannot leak between flows',()=>{
 const r=setup(async()=>({data:{}}));r.session.set('audoryn.github.installation',JSON.stringify({flowId:'another-flow',installationId:'8'}));
 assert.equal(globalThis.setupTestHelpers.installationReturn(flowId),undefined);
});


test('skip never blocks workspace access while dismissal is pending',async()=>{
 const r=setup(path=>path.endsWith('/dismiss')?new Promise(()=>{}):Promise.resolve({data:{status:'installation_required'}}));
 await start();button(render(),'Skip for now').props.onClick();assert.deepEqual(r.finished,[null]);
});
