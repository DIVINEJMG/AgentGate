import './legal-showcase.css';

type LegalKind = 'privacy' | 'terms';
type LegalSection = { id: string; title: string; paragraphs: string[]; points?: string[] };

const privacySections: LegalSection[] = [
  {
    id: 'information', title: 'Information Audoryn handles',
    paragraphs: ['Creating an account provides an email address, an optional display name and a password. Within a workspace, Audoryn stores organization membership, worker and job settings, policies, approvals, incidents, conversations, memories, results, artifacts and audit records as those features are used.'],
    points: ['Account and membership details identify people who can enter a workspace.', 'Work content can include instructions, prompts, files, connected tool data and generated output.', 'Operational records include action decisions, usage, failures and security events.'],
  },
  {
    id: 'purposes', title: 'Why it is used',
    paragraphs: ['Audoryn uses this information to authenticate users, run and supervise configured work, apply capability and policy checks, request human approvals, show results, enforce plan limits, and investigate reliability or security issues. Workspace content is available according to organization membership and product permissions.'],
  },
  {
    id: 'providers', title: 'AI models and connected services',
    paragraphs: ['When AI features are used, relevant task context can be sent to the configured NVIDIA model service for planning or vision analysis. A connected integration can send an authorized action and its necessary payload to the selected provider, such as GitHub, Slack, Gmail, Google Drive or Google Calendar. Those providers process information under their own terms and practices.'],
    points: ['Model requests are limited to the context assembled for the task; they are not a grant of provider authority.', 'Provider calls remain subject to Audoryn capability, policy, risk, approval and incident controls.'],
  },
  {
    id: 'credentials', title: 'Credentials and browser storage',
    paragraphs: ['Account passwords are stored as salted hashes, rather than readable passwords. Connected service credentials are encrypted in the backend. After sign-in, the web app keeps an access token in browser local storage so it can make authenticated requests. A pending invitation code may be kept in session storage while you complete joining. Sign-out revokes the server session and removes the local token.'],
  },
  {
    id: 'retention', title: 'Retention and deletion',
    paragraphs: ['Sessions expire or can be revoked. Operational history, audit records, results and artifacts can remain available for governance and investigation. An expiry date on a memory entry limits its eligibility as future AI context; it does not by itself mean the underlying record or file has been physically deleted. Audoryn does not currently expose a single self-service control for deleting an entire account or workspace and all associated history.'],
  },
  {
    id: 'choices', title: 'Access and requests',
    paragraphs: ['Workspace administrators can manage membership, workers, jobs and integrations within their permissions. Some records and connections can be removed individually in the product. For broader access, correction or deletion requests, use your organization’s existing SOT support or business contact. Requests may need to be handled alongside audit obligations and any applicable agreement.'],
  },
];

const termsSections: LegalSection[] = [
  {
    id: 'accounts', title: 'Accounts and workspace authority',
    paragraphs: ['Use Audoryn through an account you control and only for an organization you are authorized to represent. Workspace owners and administrators manage human membership and roles. Keep credentials and invitation links private, and remove access when a person or integration no longer needs it.'],
  },
  {
    id: 'use', title: 'Permitted use',
    paragraphs: ['Configure workers, jobs and connected systems only where you have permission to access the underlying data and take the requested actions. Do not use Audoryn to bypass another service’s access controls, collect data without authority, or interfere with Audoryn’s identity, policy, approval, audit or incident controls.'],
  },
  {
    id: 'autonomy', title: 'AI work and human decisions',
    paragraphs: ['Audoryn can plan work and prepare or execute actions within configured capabilities and policy. A policy may allow, deny or require human approval for an action. Organizations remain responsible for the workers, instructions, policies, integrations and review practices they configure, including checking generated output before relying on it for consequential decisions.'],
    points: ['A template creates draft records; it does not connect tools or authorize execution.', 'Human approval applies when the configured policy requires it. It is not a blanket approval step for every action.'],
  },
  {
    id: 'integrations', title: 'Connected providers',
    paragraphs: ['If you connect a third-party service, you authorize Audoryn to use the credentials and permissions you provide for the operations you configure and permit. The connected provider controls its own account, availability, permissions and terms. Disconnect integrations that should no longer be used.'],
  },
  {
    id: 'plans', title: 'Plans and limits',
    paragraphs: ['The Free workspace is the current self-service starting point. Team, Business and Scale appear in the product catalog, but the current backend has no connected checkout or self-service paid upgrade. Worker capacity and usage limits are enforced according to the plan assigned to the organization; a displayed catalog price alone does not activate a paid subscription.'],
  },
  {
    id: 'operations', title: 'Service changes and records',
    paragraphs: ['Audoryn uses versioned APIs and may change available features, model configuration, providers or limits as the service develops. Work can pause or fail when a provider is unavailable, a session expires, a limit is reached, or a security control blocks execution. Audit and operational records may remain after an individual worker, job or integration is removed.'],
  },
];

export default function LegalShowcase({ kind, onNavigate }: { kind: LegalKind; onNavigate: (route: LegalKind) => void }) {
  const privacy = kind === 'privacy';
  const sections = privacy ? privacySections : termsSections;
  const other: LegalKind = privacy ? 'terms' : 'privacy';

  function moveTo(id: string) {
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    document.getElementById(`legal-${id}`)?.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'start' });
  }

  return <div className='legal-page'>
    <header className='legal-hero public-container'>
      <div className='legal-hero-line'><span>AUDORYN / LEGAL</span><span>AN SOT PRODUCT</span></div>
      <div className='legal-hero-copy'>
        <p className='legal-kicker'>{privacy ? 'PRIVACY & DATA' : 'PRODUCT USE'}</p>
        <h1>{privacy ? 'Your work. Your data. Clear boundaries.' : 'Clear terms for controlled work.'}</h1>
        <p>{privacy ? 'How Audoryn handles account, workspace and operational information as the product runs.' : 'How accounts, connected systems and AI workers are expected to be used in Audoryn.'}</p>
      </div>
      <div className='legal-hero-base'><span>{privacy ? 'Privacy overview' : 'Terms of use'}</span><span>Current product behavior / 30 September 2026</span></div>
    </header>

    <div className='legal-layout public-container'>
      <aside className='legal-aside'>
        <nav aria-label={`${privacy ? 'Privacy' : 'Terms'} sections`}>
          <span className='legal-nav-label'>ON THIS PAGE</span>
          {sections.map((section, index) => <button key={section.id} type='button' onClick={() => moveTo(section.id)}><span>{String(index + 1).padStart(2, '0')}</span>{section.title}</button>)}
        </nav>
      </aside>
      <div className='legal-content'>
        <p className='legal-context'>{privacy
          ? 'This overview describes the Audoryn product. An organization-specific agreement or an applicable legal notice may provide further details, including legal bases and jurisdiction-specific rights.'
          : 'These product-use terms describe the current Audoryn service. A signed agreement with SOT, where one exists, may set additional or different commercial and legal terms.'}</p>
        {sections.map((section, index) => <section className='legal-section' id={`legal-${section.id}`} key={section.id} aria-labelledby={`legal-title-${section.id}`}>
          <div className='legal-section-heading'><span>{String(index + 1).padStart(2, '0')}</span><h2 id={`legal-title-${section.id}`}>{section.title}</h2></div>
          <div className='legal-section-copy'>{section.paragraphs.map((paragraph) => <p key={paragraph}>{paragraph}</p>)}{section.points&&<ul>{section.points.map((point) => <li key={point}>{point}</li>)}</ul>}</div>
        </section>)}
        <div className='legal-endnote'>
          <span>{privacy ? 'RELATED DOCUMENT' : 'DATA PRACTICES'}</span>
          <button type='button' onClick={() => onNavigate(other)}>{privacy ? 'Read product terms' : 'Read privacy overview'} <span aria-hidden='true'>↗</span></button>
        </div>
      </div>
    </div>
  </div>;
}
