import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';
import {renderToStaticMarkup} from 'react-dom/server';
import {createElement} from 'react';
import * as jsxRuntime from 'react/jsx-runtime';
globalThis.githubCardJSX = jsxRuntime;

// Render the actual card with saved API state. No browser, OAuth or provider calls.
const source = await readFile(new URL('../src/components/GitHubConnection.tsx', import.meta.url), 'utf8');
let code = ts.transpileModule(source, {compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.ESNext}}).outputText;
code = code.replace(/import \{([^}]+)\} from ["']react\/jsx-runtime["'];/, (_, bindings) => `const {${bindings.replace(/ as /g, ':')}} = globalThis.githubCardJSX;`).replace(/import \{ useEffect, useState \} from 'react';/, 'const {useEffect,useState} = globalThis.githubCardHooks;')
  .replace(/import \{ auth \} from [^;]+;/, 'const auth = {};')
  .replace(/import \{[^;]+from '..\/lib\/githubApi';/, 'const authorizeGitHub = () => {}, connectGitHubInstallation = () => {}, githubInstallations = () => {}, githubReadiness = () => {};');
let values;
globalThis.githubCardHooks = {useEffect:()=>{},useState:()=>[values.shift(),()=>{}]};
const {default: Card} = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
function render({linked=true,installations=[],loaded=true,url='https://github.com/apps/example/installations/new',setup=true}={}) {
  globalThis.window = {location:{search:setup?'?github_setup=saved':''}};
  values = [{enabled:true,loginEnabled:true,signInLinked:linked,authenticationConfigured:true,installationUrl:url},installations,loaded,'',false,''];
  return renderToStaticMarkup(createElement(Card,{organizationId:'org',version:'v2',connections:[],working:false}));
}
test('linked sign-in does not offer repeated OAuth linking',()=>{
  const html=render();assert.match(html,/GitHub is linked for sign-in/);assert.doesNotMatch(html,/Link GitHub for sign-in/);
});
test('unlinked users can still explicitly link sign-in',()=>assert.match(render({linked:false}),/Link GitHub for sign-in/));
test('empty access shows install instructions without dead selector',()=>{
  const html=render();assert.match(html,/No installation/);assert.match(html,/Install GitHub App/);assert.doesNotMatch(html,/<select/);assert.doesNotMatch(html,/Connect installation/);
});
test('missing App URL explains configuration without requesting another sign-in',()=>assert.match(render({url:null}),/App installation link is unavailable/));
test('loading does not assert no installation exists',()=>assert.doesNotMatch(render({loaded:false}),/No installation/));
test('usable and suspended installations remain distinct',()=>{
  assert.match(render({installations:[{id:'1',account:'owner',suspended:false}]}),/Choose your installation/);
  assert.match(render({installations:[{id:'1',account:'owner',suspended:true}]}),/All available installations are suspended/);
});
