// The home page, below the navigation. Order and surfaces follow docs/public-site/design-direction.md.
import type {PageOf} from '../../contract/contract.generated';
import {Closing, Connections, Hero, HowItWorks, OperatingModel, Oversight, PolicyCode, Principle, Product, RecordConsole, Security, Solutions} from './HomeSections';

export function HomePage({page}: {page: PageOf<'home'>}) {
  const s = page.sections;
  const demo = s.decision.demo;
  return (
    <>
      <Hero section={s.hero} demo={demo} activity={s.activity} connections={s.connections} />
      <Product section={s.product} />
      <Principle section={s.decision} />
      <HowItWorks section={s.controlPath} demo={demo} />
      <PolicyCode section={s.policy} />
      <OperatingModel section={s.operatingModel} />
      <Oversight section={s.oversight} demo={demo} />
      <RecordConsole section={s.activity} />
      <Connections section={s.connections} />
      <Solutions section={s.solutions} />
      <Security section={s.controls} />
      <Closing section={s.closing} />
    </>
  );
}
