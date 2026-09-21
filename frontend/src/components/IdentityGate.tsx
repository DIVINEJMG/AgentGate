import { useState } from 'react';
import type { AuthUser } from '@appdeploy/client';
import type { ApiVersion } from '../lib/systemApi';
import type { OrganizationAccess } from '../lib/identityApi';

function Brand(){return <div className='identity-wordmark'><strong>Audoryn</strong><span>An SOT Product</span></div>}

export function SignInGate({onSignIn,error,mode='signin',onBack}:{onSignIn:()=>Promise<void>;error:string|null;mode?:'signin'|'signup';onBack?:()=>void}){
  const[busy,setBusy]=useState(false);
  async function submit(){setBusy(true);try{await onSignIn()}finally{setBusy(false)}}
  const signup=mode==='signup';
  return <main className='identity-screen'><div className='identity-shell'>
    <section className='identity-intro'><Brand/><div className='identity-statement'><p>{signup?'Start an Audoryn workspace':'Controlled autonomous workforce infrastructure'}</p><h1>{signup?'Start with a small AI workforce. Keep control from day one.':'Put your AI workforce to work—with control.'}</h1><span>{signup?'Create your account, set up an organization, then add workers only when you are ready to define their authority.':'Coordinate workers, jobs, approvals and connected tools from one calm operating layer. Audoryn keeps human authority explicit when actions become sensitive.'}</span></div><div className='identity-principles'><span>Human oversight</span><span>Tenant isolation</span><span>Deterministic authorization</span></div></section>
    <section className='identity-action'><p className='identity-action-label'>{signup?'Get started':'Workspace access'}</p><h2>{signup?'Create account':'Sign in'}</h2><p>{signup?'Continue with your organization identity. New users can create an Audoryn workspace after authentication.':'Continue with your organization identity.'}</p>{error&&<p className='form-error' role='alert'>{error}</p>}<button className='primary-button' onClick={submit} disabled={busy}>{busy?'Opening secure sign in…':signup?'Continue to create account':'Continue securely'}</button><p className='legal-note'>By continuing, you agree to Audoryn&apos;s <a href='#/terms'>terms</a> and acknowledge the <a href='#/privacy'>privacy notice</a>.</p>{onBack&&<button className='identity-back' type='button' onClick={onBack}>← Back to Audoryn</button>}</section>
  </div></main>
}

export function OrganizationOnboarding({user,apiVersion,onCreate}:{user:AuthUser;apiVersion:ApiVersion;onCreate:(name:string)=>Promise<void>}){
  const[name,setName]=useState('');const[error,setError]=useState<string|null>(null);const[busy,setBusy]=useState(false);
  async function submit(event:React.FormEvent){event.preventDefault();const trimmed=name.trim();if(trimmed.length<2){setError('Enter an organization name with at least 2 characters.');return}setBusy(true);setError(null);try{await onCreate(trimmed)}catch{setError('Audoryn could not create the organization.')}finally{setBusy(false)}}
  return <main className='identity-screen'><div className='identity-shell'><section className='identity-intro'><Brand/><div className='identity-statement'><p>New workspace</p><h1>Start with the organization you want Audoryn to protect.</h1><span>Your workspace becomes the boundary for people, AI identities, workers, jobs, policies and audit history.</span></div></section><section className='identity-action'><p className='identity-action-label'>Organization setup</p><h2>Create workspace</h2><form onSubmit={submit} className='org-form'><label htmlFor='org-name'>Organization name</label><input id='org-name' value={name} onChange={event=>setName(event.target.value)} placeholder='Acme Operations' maxLength={80} autoComplete='organization'/><p className='field-help'>You will be the Owner. Membership and permissions remain organization-scoped.</p>{error&&<p className='form-error' role='alert'>{error}</p>}<button className='primary-button' type='submit' disabled={busy}>{busy?'Creating workspace…':'Create organization'}</button></form><div className='signed-in-as'><span>Signed in as</span><strong>{user.name||user.email||'Authenticated user'}</strong><span className='api-mini'>API {apiVersion}</span></div></section></div></main>
}

export function IdentityLoading(){return <main className='identity-screen'><div className='identity-loading'><Brand/><strong>Opening your workspace</strong><span>Verifying identity and organization access…</span></div></main>}
export type{OrganizationAccess};
