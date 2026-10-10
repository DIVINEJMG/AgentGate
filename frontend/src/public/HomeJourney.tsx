import { usePublicText, usePublicCollection } from './content/ContentContext';
import HomeGateScene from './HomeGateScene';



export default function HomeJourney() {
  const text = usePublicText('HomeJourney');
const CHAPTERS = usePublicCollection('HomeJourney','CHAPTERS', [
  { number: '01', label: text('field-work-b31450866e', "WORK"), title: text('field-give-each-worker-a-clear-job-f3a0e2295b', "Give each worker a clear job."), body: text('field-define-the-objective-expected-result-and-limi-ad639d21d6', "Define the objective, expected result and limits of the work before it enters the queue.") },
  { number: '02', label: text('field-policy-94b6449539', "POLICY"), title: text('field-make-authority-explicit-4a9b2cb19b', "Make authority explicit."), body: text('field-connected-tools-expose-possibilities-capabili-e1c2e4b3b1', "Connected tools expose possibilities. Capabilities, risk and policy determine what the worker may actually do.") },
  { number: '03', label: text('field-approval-3393f2da9c', "APPROVAL"), title: text('field-pause-where-judgment-matters-98f76d679e', "Pause where judgment matters."), body: text('field-a-sensitive-action-waits-for-a-person-approva-25784a7e2a', "A sensitive action waits for a person. Approval is checked against current authority before work continues.") },
  { number: '04', label: text('field-execution-0680feabc0', "EXECUTION"), title: text('field-see-the-result-and-the-reasoning-b32a16e4ed', "See the result and the reasoning."), body: text('field-the-action-decision-and-outcome-remain-connec-ec16f3e0dc', "The action, decision and outcome remain connected in an inspectable record for the team.") },
]);

  return <section className="home-journey public-section">
    <div className="public-container home-journey-intro">
      <p className="public-kicker">{text('copy-how-controlled-work-moves-54524090c7', "HOW CONTROLLED WORK MOVES")}</p>
      <h2>{text('copy-autonomy-with-a-visible-boundary-4914ec64c2', "Autonomy with a visible boundary.")}</h2>
      <p>{text('copy-from-the-first-instruction-to-the-final-actio-4ab9f48c0f', "From the first instruction to the final action, the worker can move forward without becoming its own authority.")}</p>
    </div>
    <div className="public-container home-journey-grid">
      <div className="home-journey-visual"><HomeGateScene mode="journey" /></div>
      <div className="home-journey-chapters">
        {CHAPTERS.map((chapter) => <article className="home-journey-chapter" key={chapter.number}>
          <span>{chapter.number} / {chapter.label}</span>
          <h3>{chapter.title}</h3>
          <p>{chapter.body}</p>
        </article>)}
      </div>
    </div>
  </section>;
}
