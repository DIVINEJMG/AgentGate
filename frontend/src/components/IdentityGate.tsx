import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, Check, Eye, EyeOff, LockKeyhole, Mail, ShieldCheck, UserRound } from 'lucide-react';
import type { AuthCredentials, AuthUser } from '../platform/client';
import type { ApiVersion } from '../lib/systemApi';
import type { OrganizationAccess } from '../lib/identityApi';

function Brand(){return <div className='identity-wordmark'><strong>Audoryn</strong><span>An SOT Product</span></div>}

export function SignInGate({onAuthenticate,error,mode='signin',onBack,onModeChange,pendingInvitation=false}:{onAuthenticate:(credentials:AuthCredentials,mode:'signin'|'signup')=>Promise<void>;error:string|null;mode?:'signin'|'signup';onBack?:()=>void;onModeChange:(mode:'signin'|'signup')=>void;pendingInvitation?:boolean}){
  const [busy,setBusy]=useState(false);
  const [email,setEmail]=useState('');
  const [password,setPassword]=useState('');
  const [name,setName]=useState('');
  const [showPassword,setShowPassword]=useState(false);
  const [capsLock,setCapsLock]=useState(false);
  const [dismissedError,setDismissedError]=useState(false);
  const errorRef=useRef<HTMLDivElement>(null);
  const signup=mode==='signup';
  const visibleError=error&&!dismissedError;

  useEffect(()=>{document.title=`${signup?'Create account':'Sign in'} · Audoryn`;},[signup]);
  useEffect(()=>{if(visibleError)errorRef.current?.focus();},[visibleError]);

  async function submit(event:React.FormEvent<HTMLFormElement>){
    event.preventDefault();
    if(busy)return;
    setDismissedError(false);
    setBusy(true);
    try{await onAuthenticate({email:email.trim(),password,name:signup?name.trim()||undefined:undefined},mode)}
    finally{setBusy(false)}
  }

  return <main className='auth-page'>
    <section className='auth-story' aria-label='Audoryn introduction'>
      <button className='auth-brand' type='button' onClick={onBack} aria-label='Audoryn home'><img src='/audoryn-mark.png' alt=''/>Audoryn</button>
      <div className='auth-story-main'>
        <p className='auth-eyebrow'>THE CONTROL PLANE FOR AI WORK</p>
        <h1>Work moves.<br/><em>Authority stays clear.</em></h1>
        <p className='auth-story-copy'>Give AI workers useful scope. Keep decisions, approvals and accountability visible to the people responsible.</p>
        <div className='auth-flow' aria-label='How work moves through Audoryn'>
          <div className='auth-flow-track' aria-hidden='true'><span/></div>
          <div className='auth-flow-step'><span className='auth-flow-number'>01</span><span className='auth-flow-node'><ArrowRight size={17}/></span><div><strong>Work begins</strong><small>A defined task and context</small></div></div>
          <div className='auth-flow-step'><span className='auth-flow-number'>02</span><span className='auth-flow-node'><ShieldCheck size={17}/></span><div><strong>Policy decides</strong><small>Capabilities set the boundary</small></div></div>
          <div className='auth-flow-step'><span className='auth-flow-number'>03</span><span className='auth-flow-node'><Check size={17}/></span><div><strong>People approve</strong><small>Judgment stays with your team</small></div></div>
        </div>
      </div>
      <div className='auth-story-foot'><span>AUDORYN / AN SOT PRODUCT</span><span>CONTROLLED AUTONOMY</span></div>
    </section>

    <section className='auth-entry' aria-labelledby='auth-title'>
      <div className='auth-entry-top'><span>WORKSPACE ACCESS</span><button className='auth-mobile-brand' type='button' onClick={onBack} aria-label='Audoryn home'><img src='/audoryn-mark.png' alt=''/>Audoryn</button><span>{signup?'NEW ACCOUNT':'WELCOME BACK'}</span></div>
      <div className='auth-entry-content'>
        {pendingInvitation&&<p className='auth-invite-note'><Mail size={16} aria-hidden='true'/> You have a workspace invitation waiting. {signup?'Create an account':'Sign in'} to continue.</p>}
        <p className='auth-kicker'>{signup?'01 / Create your account':'01 / Your workspace'}</p>
        <h2 id='auth-title'>{signup?'Start with Audoryn.':'Sign in to Audoryn.'}</h2>
        <p className='auth-intro'>{signup?'Create your account first. You can set up your organization and workers after signing in.':'Enter your account details to return to your workspace.'}</p>
        <div className='auth-mode-switch' aria-label='Account access'>
          <button type='button' className={!signup?'is-active':''} aria-current={!signup?'page':undefined} onClick={()=>onModeChange('signin')} disabled={busy}>Sign in</button>
          <button type='button' className={signup?'is-active':''} aria-current={signup?'page':undefined} onClick={()=>onModeChange('signup')} disabled={busy}>Create account</button>
        </div>
        {visibleError&&<div className='auth-error' role='alert' tabIndex={-1} ref={errorRef}><strong>We couldn’t {signup?'create your account':'sign you in'}.</strong><span>{error}</span></div>}
        <form className='auth-form' onSubmit={submit} aria-busy={busy}>
          {signup&&<div className='auth-field'><label htmlFor='auth-name'>Your name <span>Optional</span></label><div className='auth-input-wrap'><UserRound size={17} aria-hidden='true'/><input id='auth-name' value={name} onChange={event=>setName(event.target.value)} autoComplete='name' maxLength={160} placeholder='How should we address you?' disabled={busy}/></div></div>}
          <div className='auth-field'><label htmlFor='auth-email'>Email address</label><div className='auth-input-wrap'><Mail size={17} aria-hidden='true'/><input id='auth-email' type='email' inputMode='email' required value={email} onChange={event=>{setEmail(event.target.value);setDismissedError(true)}} autoComplete='email' maxLength={320} placeholder='you@company.com' spellCheck={false} disabled={busy}/></div></div>
          <div className='auth-field'><label htmlFor='auth-password'>{signup?'Create password':'Password'}</label><div className='auth-input-wrap'><LockKeyhole size={17} aria-hidden='true'/><input id='auth-password' type={showPassword?'text':'password'} required minLength={signup?10:1} maxLength={1024} value={password} onChange={event=>{setPassword(event.target.value);setDismissedError(true)}} onKeyUp={event=>setCapsLock(event.getModifierState('CapsLock'))} onBlur={()=>setCapsLock(false)} autoComplete={signup?'new-password':'current-password'} aria-describedby={signup?'auth-password-help':capsLock?'auth-caps-lock':undefined} placeholder={signup?'At least 10 characters':'Enter your password'} disabled={busy}/><button className='auth-password-toggle' type='button' onClick={()=>setShowPassword(value=>!value)} aria-label={showPassword?'Hide password':'Show password'} aria-pressed={showPassword} disabled={busy}>{showPassword?<EyeOff size={17}/>:<Eye size={17}/>}</button></div>{signup&&<p className='auth-field-help' id='auth-password-help'>Use 10 or more characters. You can use a password manager.</p>}{capsLock&&<p className='auth-field-help auth-caps' id='auth-caps-lock'>Caps Lock is on.</p>}</div>
          <button className='auth-submit' type='submit' disabled={busy}><span>{busy?(signup?'Creating your account…':'Signing you in…'):(signup?'Create account':'Sign in')}</span>{busy?<span className='auth-spinner' aria-hidden='true'/>:<ArrowRight size={18} aria-hidden='true'/>}</button>
        </form>
        <p className='auth-alternate'>{signup?'Already have an account?':'New to Audoryn?'} <button type='button' onClick={()=>onModeChange(signup?'signin':'signup')} disabled={busy}>{signup?'Sign in':'Create an account'} <ArrowRight size={14} aria-hidden='true'/></button></p>
        {signup&&<p className='auth-legal'>By creating an account, you agree to the <a href='#/terms'>terms</a> and acknowledge the <a href='#/privacy'>privacy notice</a>.</p>}
      </div>
      <div className='auth-entry-bottom'>{onBack&&<button type='button' onClick={onBack}><ArrowLeft size={15} aria-hidden='true'/> Back to website</button>}<span>Secure workspace access</span></div>
    </section>
  </main>
}

export function OrganizationOnboarding({user,apiVersion,onCreate}:{user:AuthUser;apiVersion:ApiVersion;onCreate:(name:string)=>Promise<void>}){
  const[name,setName]=useState('');const[error,setError]=useState<string|null>(null);const[busy,setBusy]=useState(false);
  async function submit(event:React.FormEvent){event.preventDefault();const trimmed=name.trim();if(trimmed.length<2){setError('Enter an organization name with at least 2 characters.');return}setBusy(true);setError(null);try{await onCreate(trimmed)}catch{setError('Audoryn could not create the organization.')}finally{setBusy(false)}}
  return <main className='identity-screen'><div className='identity-shell'><section className='identity-intro'><Brand/><div className='identity-statement'><p>New workspace</p><h1>Start with the organization you want Audoryn to protect.</h1><span>Your workspace becomes the boundary for people, AI identities, workers, jobs, policies and audit history.</span></div></section><section className='identity-action'><p className='identity-action-label'>Organization setup</p><h2>Create workspace</h2><form onSubmit={submit} className='org-form'><label htmlFor='org-name'>Organization name</label><input id='org-name' value={name} onChange={event=>setName(event.target.value)} placeholder='Acme Operations' maxLength={80} autoComplete='organization'/><p className='field-help'>You will be the Owner. Membership and permissions remain organization-scoped.</p>{error&&<p className='form-error' role='alert'>{error}</p>}<button className='primary-button' type='submit' disabled={busy}>{busy?'Creating workspace…':'Create organization'}</button></form><div className='signed-in-as'><span>Signed in as</span><strong>{user.name||user.email||'Authenticated user'}</strong><span className='api-mini'>API {apiVersion}</span></div></section></div></main>
}

export function IdentityLoading(){return <main className='auth-loading-page' aria-busy='true'><div className='auth-loading-content' role='status'><div className='auth-loading-brand'><img src='/audoryn-mark.png' alt=''/>Audoryn</div><span className='auth-loading-line' aria-hidden='true'><span/></span><strong>Opening your workspace</strong><span>Verifying your account and access…</span></div></main>}
export type{OrganizationAccess};
