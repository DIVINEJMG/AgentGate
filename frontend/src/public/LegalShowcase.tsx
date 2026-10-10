import { usePublicText, usePublicContent } from './content/ContentContext';
import { payloadOf } from './content/contract';
import './legal-showcase.css';

type LegalKind = 'privacy' | 'terms';
type LegalSection = { id: string; title: string; paragraphs: string[]; points?: string[] };





export default function LegalShowcase({ kind, onNavigate }: { kind: LegalKind; onNavigate: (route: LegalKind) => void }) {
  const text = usePublicText('LegalShowcase');
const privacySections: LegalSection[] = [
  {
    id: 'information', title: text('field-information-audoryn-handles-5124aeb51d', "Information Audoryn handles"),
    paragraphs: [text('field-creating-an-account-provides-an-email-address-ffc9f70d85', "Creating an account provides an email address, an optional display name and a password. Within a workspace, Audoryn stores organization membership, worker and job settings, policies, approvals, incidents, conversations, memories, results, artifacts and audit records as those features are used.")],
    points: [text('field-account-and-membership-details-identify-peopl-6ad60cc9d6', "Account and membership details identify people who can enter a workspace."), text('field-work-content-can-include-instructions-prompts-5f9572074f', "Work content can include instructions, prompts, files, connected tool data and generated output."), text('field-operational-records-include-action-decisions-cced6304c6', "Operational records include action decisions, usage, failures and security events.")],
  },
  {
    id: 'purposes', title: text('field-why-it-is-used-2d3dfa8775', "Why it is used"),
    paragraphs: [text('field-audoryn-uses-this-information-to-authenticate-bf55b3420e', "Audoryn uses this information to authenticate users, run and supervise configured work, apply capability and policy checks, request human approvals, show results, enforce plan limits, and investigate reliability or security issues. Workspace content is available according to organization membership and product permissions.")],
  },
  {
    id: 'providers', title: text('field-ai-models-and-connected-services-b0e804c4f1', "AI models and connected services"),
    paragraphs: [text('field-when-ai-features-are-used-relevant-task-conte-81e646ec87', "When AI features are used, relevant task context can be sent to the configured NVIDIA model service for planning or vision analysis. A connected integration can send an authorized action and its necessary payload to the selected provider, such as GitHub, Slack, Gmail, Google Drive or Google Calendar. Those providers process information under their own terms and practices.")],
    points: [text('field-model-requests-are-limited-to-the-context-ass-b0d5f52a5a', "Model requests are limited to the context assembled for the task; they are not a grant of provider authority."), text('field-provider-calls-remain-subject-to-audoryn-capa-d008dba439', "Provider calls remain subject to Audoryn capability, policy, risk, approval and incident controls.")],
  },
  {
    id: 'credentials', title: text('field-credentials-and-browser-storage-cdcda368b7', "Credentials and browser storage"),
    paragraphs: [text('field-account-passwords-are-stored-as-salted-hashes-e0432354b5', "Account passwords are stored as salted hashes, rather than readable passwords. Connected service credentials are encrypted in the backend. After sign-in, the web app keeps an access token in browser local storage so it can make authenticated requests. A pending invitation code may be kept in session storage while you complete joining. Sign-out revokes the server session and removes the local token.")],
  },
  {
    id: 'retention', title: text('field-retention-and-deletion-852e206b28', "Retention and deletion"),
    paragraphs: [text('field-sessions-expire-or-can-be-revoked-operational-dc15cda002', "Sessions expire or can be revoked. Operational history, audit records, results and artifacts can remain available for governance and investigation. An expiry date on a memory entry limits its eligibility as future AI context; it does not by itself mean the underlying record or file has been physically deleted. Audoryn does not currently expose a single self-service control for deleting an entire account or workspace and all associated history.")],
  },
  {
    id: 'choices', title: text('field-access-and-requests-ffd808893b', "Access and requests"),
    paragraphs: [text('field-workspace-administrators-can-manage-membershi-63392c8bc9', "Workspace administrators can manage membership, workers, jobs and integrations within their permissions. Some records and connections can be removed individually in the product. For broader access, correction or deletion requests, use your organization’s existing SOT support or business contact. Requests may need to be handled alongside audit obligations and any applicable agreement.")],
  },
];
const termsSections: LegalSection[] = [
  {
    id: 'accounts', title: text('field-accounts-and-workspace-authority-4896d78894', "Accounts and workspace authority"),
    paragraphs: [text('field-use-audoryn-through-an-account-you-control-an-638060fd2d', "Use Audoryn through an account you control and only for an organization you are authorized to represent. Workspace owners and administrators manage human membership and roles. Keep credentials and invitation links private, and remove access when a person or integration no longer needs it.")],
  },
  {
    id: 'use', title: text('field-permitted-use-3e0212519d', "Permitted use"),
    paragraphs: [text('field-configure-workers-jobs-and-connected-systems-0827ef74a5', "Configure workers, jobs and connected systems only where you have permission to access the underlying data and take the requested actions. Do not use Audoryn to bypass another service’s access controls, collect data without authority, or interfere with Audoryn’s identity, policy, approval, audit or incident controls.")],
  },
  {
    id: 'autonomy', title: text('field-ai-work-and-human-decisions-32a1358468', "AI work and human decisions"),
    paragraphs: [text('field-audoryn-can-plan-work-and-prepare-or-execute-a0c875315d', "Audoryn can plan work and prepare or execute actions within configured capabilities and policy. A policy may allow, deny or require human approval for an action. Organizations remain responsible for the workers, instructions, policies, integrations and review practices they configure, including checking generated output before relying on it for consequential decisions.")],
    points: [text('field-a-template-creates-draft-records-it-does-not-d67cd13a9f', "A template creates draft records; it does not connect tools or authorize execution."), text('field-human-approval-applies-when-the-configured-po-cf19468164', "Human approval applies when the configured policy requires it. It is not a blanket approval step for every action.")],
  },
  {
    id: 'integrations', title: text('field-connected-providers-c55b09e798', "Connected providers"),
    paragraphs: [text('field-if-you-connect-a-third-party-service-you-auth-dfec19735e', "If you connect a third-party service, you authorize Audoryn to use the credentials and permissions you provide for the operations you configure and permit. The connected provider controls its own account, availability, permissions and terms. Disconnect integrations that should no longer be used.")],
  },
  {
    id: 'plans', title: text('field-plans-and-limits-5706ddd912', "Plans and limits"),
    paragraphs: [text('field-the-free-workspace-is-the-current-self-servic-23743ec954', "The Free workspace is the current self-service starting point. Team, Business and Scale appear in the product catalog, but the current backend has no connected checkout or self-service paid upgrade. Worker capacity and usage limits are enforced according to the plan assigned to the organization; a displayed catalog price alone does not activate a paid subscription.")],
  },
  {
    id: 'operations', title: text('field-service-changes-and-records-625e38267e', "Service changes and records"),
    paragraphs: [text('field-audoryn-uses-versioned-apis-and-may-change-av-edf6f83267', "Audoryn uses versioned APIs and may change available features, model configuration, providers or limits as the service develops. Work can pause or fail when a provider is unavailable, a session expires, a limit is reached, or a security control blocks execution. Audit and operational records may remain after an individual worker, job or integration is removed.")],
  },
];

  const privacy = kind === 'privacy';
  const bundle = usePublicContent();
  const sections = bundle.page && bundle.source !== 'static' ? payloadOf(bundle.page).sections!.map(item=>({id:item.id,title:item.heading,paragraphs:item.paragraphs||[],points:item.points})) : privacy ? privacySections : termsSections;
  const other: LegalKind = privacy ? 'terms' : 'privacy';

  function moveTo(id: string) {
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    document.getElementById(`legal-${id}`)?.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'start' });
  }

  return <div className='legal-page'>
    <header className='legal-hero public-container'>
      <div className='legal-hero-line'><span>{text('copy-audoryn-legal-f025b88de2', "AUDORYN / LEGAL")}</span><span>{text('copy-an-sot-product-35bf08381d', "AN SOT PRODUCT")}</span></div>
      <div className='legal-hero-copy'>
        <p className='legal-kicker'>{privacy ? text('copy-privacy-data-c65b3e3d93', "PRIVACY & DATA") : text('copy-product-use-097de84813', "PRODUCT USE")}</p>
        <h1>{privacy ? text('copy-your-work-your-data-clear-boundaries-7030613fee', "Your work. Your data. Clear boundaries.") : text('copy-clear-terms-for-controlled-work-f91df19b59', "Clear terms for controlled work.")}</h1>
        <p>{privacy ? text('copy-how-audoryn-handles-account-workspace-and-ope-b228a769f9', "How Audoryn handles account, workspace and operational information as the product runs.") : text('copy-how-accounts-connected-systems-and-ai-workers-bb28098e0a', "How accounts, connected systems and AI workers are expected to be used in Audoryn.")}</p>
      </div>
      <div className='legal-hero-base'><span>{privacy ? text('copy-privacy-overview-780df491d9', "Privacy overview") : text('copy-terms-of-use-9d1bd81eae', "Terms of use")}</span><span>{bundle.page&&payloadOf(bundle.page).effective_date!=='2026-09-30'?text('copy-current-product-behavior-30-september-2026-10aeb1a324', "Current product behavior / 30 September 2026").split(' / ')[0]+' / '+new Intl.DateTimeFormat('en-GB',{day:'numeric',month:'long',year:'numeric',timeZone:'UTC'}).format(new Date(String(payloadOf(bundle.page).effective_date))):text('copy-current-product-behavior-30-september-2026-10aeb1a324', "Current product behavior / 30 September 2026")}</span></div>
    </header>

    <div className='legal-layout public-container'>
      <aside className='legal-aside'>
        <nav aria-label={`${privacy ? text('copy-privacy-e55194bb5e', "Privacy") : text('copy-terms-6962999226', "Terms")} sections`}>
          <span className='legal-nav-label'>{text('copy-on-this-page-27dc9233a2', "ON THIS PAGE")}</span>
          {sections.map((section, index) => <button key={section.id} type='button' onClick={() => moveTo(section.id)}><span>{String(index + 1).padStart(2, '0')}</span>{section.title}</button>)}
        </nav>
      </aside>
      <div className='legal-content'>
        <p className='legal-context'>{privacy
          ? text('copy-this-overview-describes-the-audoryn-product-a-3d89308c47', "This overview describes the Audoryn product. An organization-specific agreement or an applicable legal notice may provide further details, including legal bases and jurisdiction-specific rights.")
          : text('copy-these-product-use-terms-describe-the-current-30792c8e05', "These product-use terms describe the current Audoryn service. A signed agreement with SOT, where one exists, may set additional or different commercial and legal terms.")}</p>
        {sections.map((section, index) => <section className='legal-section' id={`legal-${section.id}`} key={section.id} aria-labelledby={`legal-title-${section.id}`}>
          <div className='legal-section-heading'><span>{String(index + 1).padStart(2, '0')}</span><h2 id={`legal-title-${section.id}`}>{section.title}</h2></div>
          <div className='legal-section-copy'>{section.paragraphs.map((paragraph) => <p key={paragraph}>{paragraph}</p>)}{section.points&&<ul>{section.points.map((point) => <li key={point}>{point}</li>)}</ul>}</div>
        </section>)}
        <div className='legal-endnote'>
          <span>{privacy ? text('copy-related-document-d2c58582a5', "RELATED DOCUMENT") : text('copy-data-practices-ec00552c09', "DATA PRACTICES")}</span>
          <button type='button' onClick={() => onNavigate(other)}>{privacy ? text('copy-read-product-terms-e11a58e401', "Read product terms") : text('copy-read-privacy-overview-21c20a8cb9', "Read privacy overview")} <span aria-hidden='true'>↗</span></button>
        </div>
      </div>
    </div>
  </div>;
}
