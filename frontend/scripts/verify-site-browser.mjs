// Browser verification of the public site against the production build.
//   npm run build && node scripts/verify-site-browser.mjs
// Uses an existing Playwright installation (PUBLIC_CMS_BROWSER_RUNTIME) and an installed
// Chrome or Edge (PUBLIC_CMS_BROWSER_CHANNEL, default chrome); downloads nothing.
// Checks every public page for structure and accessibility basics, keyboard behaviour, phone
// layout, reduced motion, hydration and navigation, and the header and footer with navigation at
// its contract limits (on the Vite development preview). Exits non-zero on any failure.
import {createRequire} from 'node:module';
import {createServer as createNetServer} from 'node:net';
import path from 'node:path';
import {createServer} from 'vite';

const runtime = process.env.PUBLIC_CMS_BROWSER_RUNTIME || 'C:/Users/divin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright';
const {chromium} = createRequire(import.meta.url)(runtime);
const {createPublicPreview} = await import('./preview-public-site.mjs');

const server = createPublicPreview();
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({channel: process.env.PUBLIC_CMS_BROWSER_CHANNEL || 'chrome', headless: true, args: ['--disable-gpu']});
const PAGES = ['/', '/product', '/solutions', '/security', '/pricing', '/resources', '/company', '/privacy', '/terms'];
const results = [];
const check = (name, ok, detail = '') => results.push({ok: Boolean(ok), name, detail});

try {
  // Structure and accessibility basics on every page, desktop.
  const desktop = await (await browser.newContext({viewport: {width: 1440, height: 900}})).newPage();
  const errors = [];
  desktop.on('pageerror', error => errors.push(error.message));
  desktop.on('console', message => {if (message.type() === 'error' && !message.text().includes('status of 404')) errors.push(message.text());});
  for (const path of PAGES) {
    await desktop.goto(origin + path, {waitUntil: 'networkidle'});
    const audit = await desktop.evaluate(() => {
      const visible = element => !!(element.offsetWidth || element.offsetHeight || element.getClientRects().length);
      const name = element => (element.getAttribute('aria-label') || element.textContent || element.querySelector('img[alt]')?.getAttribute('alt') || '').trim();
      const headings = [...document.querySelectorAll('h1, h2, h3, h4')].filter(visible).map(element => Number(element.tagName[1]));
      return {
        lang: document.documentElement.lang,
        title: document.title,
        h1: document.querySelectorAll('h1').length,
        skipsLevel: headings.some((level, index) => index > 0 && level > headings[index - 1] + 1),
        main: document.querySelectorAll('main').length,
        navs: [...document.querySelectorAll('nav')].every(element => element.getAttribute('aria-label')),
        imagesWithoutAlt: [...document.querySelectorAll('img')].filter(element => !element.hasAttribute('alt')).length,
        unnamedControls: [...document.querySelectorAll('a[href], button')].filter(visible).filter(element => !name(element)).length,
        positiveTabindex: document.querySelectorAll('[tabindex]:not([tabindex="0"]):not([tabindex="-1"])').length,
        buttonsAsLinks: [...document.querySelectorAll('button')].filter(element => element.closest('nav')).length,
      };
    });
    check(`${path}: language, title, one h1, one main`, audit.lang === 'en' && audit.title && audit.h1 === 1 && audit.main === 1, JSON.stringify({lang: audit.lang, h1: audit.h1, main: audit.main}));
    check(`${path}: headings do not skip levels`, !audit.skipsLevel);
    check(`${path}: every image has alternative text`, audit.imagesWithoutAlt === 0, `${audit.imagesWithoutAlt}`);
    check(`${path}: every visible link and button has a name`, audit.unnamedControls === 0, `${audit.unnamedControls}`);
    check(`${path}: navigation is labelled and made of links`, audit.navs && audit.buttonsAsLinks === 0);
    check(`${path}: no positive tabindex`, audit.positiveTabindex === 0);
  }

  // Keyboard: skip link, menu, decision radio group, tabs, focus outline.
  await desktop.goto(origin + '/', {waitUntil: 'networkidle'});
  await desktop.keyboard.press('Tab');
  check('first Tab reaches the skip link', await desktop.evaluate(() => document.activeElement?.classList.contains('site-skip')));
  await desktop.focus('.site-nav a >> text=Product');
  await desktop.keyboard.press('ArrowDown');
  await desktop.waitForTimeout(150);
  check('Arrow Down opens a menu and moves focus into it', await desktop.isVisible('.site-menu-panel') && await desktop.evaluate(() => Boolean(document.activeElement?.closest('.site-menu-panel'))));
  await desktop.keyboard.press('Escape');
  check('Escape closes the menu and returns focus', !(await desktop.isVisible('.site-menu-panel')) && await desktop.evaluate(() => document.activeElement?.textContent === 'Product'));
  const radio = desktop.locator('#hero [role="radio"][aria-checked="true"]');
  await radio.focus();
  const before = await radio.textContent();
  await desktop.keyboard.press('ArrowRight');
  check('arrow keys change the decision and move focus', before !== await desktop.locator('#hero [role="radio"][aria-checked="true"]').textContent() && await desktop.evaluate(() => document.activeElement?.getAttribute('aria-checked') === 'true'));
  for (const list of ['#policy [role="tablist"]', '#solutions [role="tablist"]']) {
    await desktop.focus(`${list} [aria-selected="true"]`);
    const first = await desktop.locator(`${list} [aria-selected="true"]`).textContent();
    await desktop.keyboard.press('ArrowRight');
    check(`tabs ${list}: arrow keys select the next tab`, first !== await desktop.locator(`${list} [aria-selected="true"]`).textContent());
  }
  await desktop.focus('.site-header-cta');
  check('focus outline is drawn', await desktop.evaluate(() => getComputedStyle(document.activeElement).outlineStyle !== 'none'));

  // Navigation and hydration.
  await desktop.goto(origin + '/', {waitUntil: 'networkidle'});
  await desktop.click('.site-nav a:text-is("Pricing")');
  await desktop.waitForSelector('#plans .price-card', {timeout: 10000});
  check('in-page navigation renders the next page and its title', (await desktop.title()).startsWith('Pricing'));
  await desktop.goBack();
  await desktop.waitForSelector('#hero .cp', {timeout: 10000});
  check('back navigation restores the previous page', true);
  const missing = await desktop.goto(origin + '/does-not-exist', {waitUntil: 'networkidle'});
  check('unknown pages answer 404 with the not-found page', missing.status() === 404 && await desktop.isVisible('#not-found'));
  check('no script or hydration errors', errors.length === 0, errors.join(' | '));

  // Reduced motion: nothing animates.
  const calm = await (await browser.newContext({viewport: {width: 1440, height: 900}, reducedMotion: 'reduce'})).newPage();
  await calm.goto(origin + '/', {waitUntil: 'networkidle'});
  const typed = await calm.textContent('.home-typing-word');
  await calm.waitForTimeout(3000);
  check('reduced motion: the typing line and the decision stay still', typed === await calm.textContent('.home-typing-word') && await calm.locator('#hero [role="radio"][aria-checked="true"]').textContent() === 'Hold for review');
  check('reduced motion: CSS animations are effectively off', await calm.evaluate(() => document.getAnimations().every(animation => Number(animation.effect?.getTiming().duration ?? 0) <= 1)));

  // Phone layout.
  const phone = await (await browser.newContext({viewport: {width: 375, height: 812}, isMobile: true, hasTouch: true})).newPage();
  for (const path of PAGES) {
    await phone.goto(origin + path, {waitUntil: 'networkidle'});
    check(`${path}: no horizontal scroll at 375px`, await phone.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
  }
  await phone.click('.site-menu-button');
  const sheet = await phone.evaluate(() => {const box = document.querySelector('.site-sheet').getBoundingClientRect(); return {height: box.height, bottom: box.bottom, viewport: innerHeight};});
  check('phone menu fills the screen below the header', sheet.height > sheet.viewport * .5 && Math.abs(sheet.bottom - sheet.viewport) <= 1, JSON.stringify(sheet));
  check('phone menu opens and closes with Escape', await phone.isVisible('.site-sheet') && (await phone.keyboard.press('Escape'), !(await phone.isVisible('.site-sheet'))));

  // Navigation at its contract limits (8 menus, long labels, 6 links each, 6 footer groups, an
  // announcement), on the development preview, which can swap in that site document.
  // Vite reads port 0 as "the default" (5173), so ask the system for a free port instead.
  const free = await new Promise(resolve => {const probe = createNetServer().listen(0, '127.0.0.1', () => {const {port} = probe.address(); probe.close(() => resolve(port));});});
  const dev = await createServer({root: path.resolve(import.meta.dirname, '..'), logLevel: 'error', server: {port: free, strictPort: true, host: '127.0.0.1'}});
  await dev.listen();
  try {
    const devOrigin = `http://127.0.0.1:${dev.httpServer.address().port}`;
    const header = async (width, height, touch = false) => {
      const context = await browser.newContext({viewport: {width, height}, hasTouch: touch});
      const page = await context.newPage();
      await page.goto(devOrigin + '/site-preview.html?site=limits', {waitUntil: 'networkidle'});
      await page.waitForTimeout(200);
      const state = await page.evaluate(() => {
        const box = selector => document.querySelector(selector).getBoundingClientRect();
        const shown = selector => {const element = document.querySelector(selector); const style = getComputedStyle(element); return style.display !== 'none' && style.visibility !== 'hidden';};
        return {
          pageScroll: document.documentElement.scrollWidth > innerWidth, links: shown('.site-nav'), menuButton: shown('.site-menu-button'),
          gap: parseFloat(getComputedStyle(document.querySelector('.site-nav ul')).columnGap),
          clear: box('.site-nav ul').right <= box('.site-header-actions').left && box('.site-nav ul').left >= box('.site-wordmark').right,
          footerRows: new Set([...document.querySelectorAll('.site-footer-nav h2')].map(element => Math.round(element.getBoundingClientRect().top))).size,
        };
      });
      return {page, context, state};
    };
    let view = await header(1440, 900);
    check('limits, 1440px: all menus fit in the bar at full spacing', view.state.links && view.state.clear && view.state.gap === 28 && !view.state.pageScroll, JSON.stringify(view.state));
    check('limits, 1440px: six footer groups share one row', view.state.footerRows === 1, JSON.stringify(view.state));
    await view.page.hover('.site-nav li:first-child a');
    await view.page.waitForTimeout(600);
    check('limits, 1440px: a six-link menu panel keeps the height of a four-link panel (287px)', await view.page.evaluate(() => Math.abs(document.querySelector('.site-menu-panel').getBoundingClientRect().height - 287) <= 2));
    await view.page.mouse.move(5, 700);
    check('limits, 1440px: the footer grows only to its limit (at most 500px with 6 groups of 8 links)', await view.page.evaluate(() => document.querySelector('.site-footer').getBoundingClientRect().height <= 500));
    await view.context.close();
    view = await header(1180, 800);
    check('limits, 1180px: the spacing between menus compresses instead of the bar growing', view.state.links && view.state.clear && view.state.gap >= 12 && view.state.gap < 28 && !view.state.pageScroll, JSON.stringify(view.state));
    check('limits, 1180px: the footer keeps its groups on one row and stays within 530px', view.state.footerRows === 1 && await view.page.evaluate(() => document.querySelector('.site-footer').getBoundingClientRect().height <= 530), JSON.stringify(view.state));
    await view.context.close();
    view = await header(1100, 700);
    check('limits, 1100px: when the menus cannot fit, the bar switches to the Menu button', !view.state.links && view.state.menuButton && !view.state.pageScroll, JSON.stringify(view.state));
    await view.page.click('.site-menu-button');
    check('limits, 1100px: the Menu button opens every menu', await view.page.evaluate(() => {const sheet = document.querySelector('.site-sheet'); return !sheet.hidden && sheet.querySelectorAll('.site-sheet-top').length === 8 && sheet.getBoundingClientRect().bottom >= innerHeight - 1;}));
    await view.context.close();
    view = await header(1180, 820, true);
    check('touch tablet, 1180px: menus open from the Menu button (no hover to reveal them)', !view.state.links && view.state.menuButton && !view.state.pageScroll, JSON.stringify(view.state));
    await view.context.close();
  } finally {
    await dev.close();
  }
} finally {
  await browser.close();
  server.close();
}

const failed = results.filter(result => !result.ok);
for (const result of results) console.log(`${result.ok ? 'PASS' : 'FAIL'}  ${result.name}${result.ok || !result.detail ? '' : '  (' + result.detail + ')'}`);
console.log(`\n${results.length - failed.length}/${results.length} checks passed.`);
if (failed.length) process.exit(1);
