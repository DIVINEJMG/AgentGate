import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

// Exercise the real auth client with an isolated transport/browser, without real OAuth calls.
async function client(api, href = 'http://localhost:5173/?github_auth=flow#/login') {
  const local = new Map(), session = new Map();
  const storage = map => ({getItem:key=>map.get(key)||null,setItem:(key,value)=>map.set(key,value),removeItem:key=>map.delete(key)});
  globalThis.window = {location:{href,assign: url => {window.location.href=url;}},
    localStorage:storage(local),sessionStorage:storage(session),history:{replaceState:(_,__,url)=>{window.location.href=String(url);}}};
  globalThis.__githubTestApi = api;
  const source = (await readFile(new URL('../src/platform/authClient.ts', import.meta.url), 'utf8')).replace("import { api } from './apiClient';", 'const api = globalThis.__githubTestApi;');
  const code = ts.transpileModule(source, {compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText;
  const module = await import(`data:text/javascript;base64,${Buffer.from(code+'\n//'+Math.random()).toString('base64')}`);
  return {auth:module.auth,local,session};
}

test('callback is browser-bound and exchanges once under repeated rendering', async () => {
  let calls=0;
  const {auth,local,session} = await client({post:async(path,body)=>{
    calls++;assert.equal(path,'/api/v2/auth/github/exchange');assert.equal(body.browser_verifier,'browser-secret');
    return {data:{user:{id:'human'},accessToken:'audoryn-session',githubSetupId:'saved-setup'}};
  }});
  session.set('audoryn.github.verifier','browser-secret');
  const [a,b] = await Promise.all([auth.completeGitHub(),auth.completeGitHub()]);
  assert.equal(calls,1);assert.deepEqual(a,b);
  assert.equal(local.get('audoryn.access_token'),'audoryn-session');
  assert.equal(session.get('audoryn.github.setup'),'saved-setup');
  assert.equal(session.has('audoryn.github.verifier'),false);
  assert.equal(window.location.href.includes('github_auth'),false);
  assert.equal(window.location.href.includes('audoryn-session'),false);
  assert.equal(await auth.completeGitHub(),null);
  assert.equal(calls,1);
});

test('callback in another browser never exchanges or persists a session', async () => {
  let called=false;
  const {auth,local}=await client({post:async()=>{called=true;}});
  await assert.rejects(auth.completeGitHub(),/browser that started/);
  assert.equal(called,false);assert.equal(local.size,0);
});

test('email conflict is exposed and does not create a frontend session', async () => {
  const {auth,local,session}=await client({post:async()=>{throw new Error('An account with this email exists.');}});
  session.set('audoryn.github.verifier','browser-secret');
  await assert.rejects(auth.completeGitHub(),/email exists/);
  assert.equal(local.size,0);assert.equal(session.has('audoryn.github.verifier'),false);
});

test('OAuth start sends only browser challenge and rejects an unexpected destination', async () => {
  const {auth,session}=await client({post:async(path,body)=>{
    assert.equal(path,'/api/v2/auth/github/start');assert.equal(body.purpose,'signup');
    assert.match(body.browser_challenge,/^[a-f0-9]{64}$/);assert.equal('browser_verifier' in body,false);
    return {data:{authorizationUrl:'https://attacker.invalid/'}};
  }}, 'http://localhost:5173/#/signup');
  await assert.rejects(auth.startGitHub('signup'),/Unexpected/);
  assert.ok(session.get('audoryn.github.verifier').length>=43);
  assert.equal(window.location.href,'http://localhost:5173/#/signup');
});


test('installation return resumes saved setup but grants no login session', async () => {
  const id='12345678-1234-1234-1234-123456789abc';
  const {auth,local,session}=await client({post:()=>{throw new Error('OAuth must not run');}}, `http://localhost:5173/?github_installation=${id}&github_installation_id=8#/app`);
  assert.equal(await auth.completeGitHub(),null);
  assert.equal(session.get('audoryn.github.setup'),id);
  assert.deepEqual(JSON.parse(session.get('audoryn.github.installation')),{flowId:id,installationId:'8'});
  assert.equal(local.size,0);
  assert.equal(window.location.href.includes('github_installation'),false);
});

test('sign-out clears setup hints without disconnecting GitHub', async () => {
  const calls=[];
  const {auth,local,session}=await client({post:async(path)=>{calls.push(path);return {data:{}};}}, 'http://localhost:5173/#/app');
  local.set('audoryn.access_token','session');session.set('audoryn.github.setup','flow');session.set('audoryn.github.installation','hint');
  await auth.signOut();
  assert.deepEqual(calls,['/api/v2/auth/logout']);assert.equal(local.size,0);assert.equal(session.size,0);
});
