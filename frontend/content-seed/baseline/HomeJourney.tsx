import HomeGateScene from './HomeGateScene';

const CHAPTERS = [
  { number: '01', label: 'WORK', title: 'Give each worker a clear job.', body: 'Define the objective, expected result and limits of the work before it enters the queue.' },
  { number: '02', label: 'POLICY', title: 'Make authority explicit.', body: 'Connected tools expose possibilities. Capabilities, risk and policy determine what the worker may actually do.' },
  { number: '03', label: 'APPROVAL', title: 'Pause where judgment matters.', body: 'A sensitive action waits for a person. Approval is checked against current authority before work continues.' },
  { number: '04', label: 'EXECUTION', title: 'See the result and the reasoning.', body: 'The action, decision and outcome remain connected in an inspectable record for the team.' },
];

export default function HomeJourney() {
  return <section className="home-journey public-section">
    <div className="public-container home-journey-intro">
      <p className="public-kicker">HOW CONTROLLED WORK MOVES</p>
      <h2>Autonomy with a visible boundary.</h2>
      <p>From the first instruction to the final action, the worker can move forward without becoming its own authority.</p>
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
