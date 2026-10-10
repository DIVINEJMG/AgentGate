// Browser checks for the user workspace, run against the sample-data preview (no backend needed).
// Usage: node scripts/verify-workspace-browser.mjs
//   WORKSPACE_BROWSER_RUNTIME     path to a playwright package (defaults to the local Codex runtime)
//   WORKSPACE_BROWSER_EXECUTABLE  optional Chromium executable; otherwise the installed Chrome channel is used
import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import { createServer } from 'vite';

const root = path.resolve(import.meta.dirname, '..');
const require = createRequire(import.meta.url);
const runtime = process.env.WORKSPACE_BROWSER_RUNTIME || 'C:/Users/divin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright';
const { chromium } = require(runtime);
const PORT = 5194;
const BASE = `http://127.0.0.1:${PORT}/workspace-preview`;
const output = path.join(root, 'artifacts/workspace-browser');
await mkdir(output, { recursive: true });

const server = await createServer({ root, logLevel: 'error', server: { host: '127.0.0.1', port: PORT, strictPort: true } });
await server.listen();
const browser = await chromium.launch(process.env.WORKSPACE_BROWSER_EXECUTABLE ? { executablePath: process.env.WORKSPACE_BROWSER_EXECUTABLE, headless: true } : { channel: 'chrome', headless: true });

const results = [];
async function check(name, run) {
  const started = Date.now();
  try { await run(); results.push({ name, ok: true, ms: Date.now() - started }); console.log(`ok   ${name}`); }
  catch (error) { results.push({ name, ok: false, error: String(error?.message ?? error).slice(0, 600) }); console.log(`FAIL ${name}\n     ${String(error?.message ?? error).split('\n')[0]}`); }
}
async function open(width = 1440, { theme = 'light' } = {}) {
  const context = await browser.newContext({ viewport: { width, height: 900 }, colorScheme: theme, reducedMotion: 'reduce' });
  const page = await context.newPage();
  // "Network idle" can arrive before React has mounted; every load waits for the workspace shell.
  const rawGoto = page.goto.bind(page);
  page.goto = async (url, options) => { const response = await rawGoto(url, options); if (url.startsWith(BASE)) await page.locator('.ws-app').waitFor({ timeout: 15000 }); await page.waitForTimeout(150); return response; };
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (message) => { if (message.type() === 'error' && !/favicon/.test(message.text())) errors.push(message.text()); });
  return { context, page, errors };
}
const path_ = (page) => { const url = new URL(page.url()); return url.pathname.replace('/workspace-preview', '') + url.search; };
async function settle(page) { await page.waitForLoadState('networkidle'); await page.waitForTimeout(250); }

// Warm the dev server so the first navigation does not race dependency optimisation.
{
  const { context, page } = await open();
  await page.goto(BASE, { waitUntil: 'load', timeout: 180000 });
  await page.locator('.ws-app').waitFor({ timeout: 180000 });
  await page.waitForTimeout(3000);
  await context.close();
}

const PAGES = ['', '/inbox', '/workers', '/workers/preview-worker-1', '/workers/preview-worker-1/jobs', '/conversations', '/conversations/preview-worker-2', '/jobs', '/jobs?view=queue', '/jobs?view=automation', '/results', '/results/preview-result-1', '/memory', '/memory?view=policy', '/activity', '/runs', '/performance', '/policies', '/risk', '/incidents', '/audit', '/connections', '/connections/directory', '/connections/directory/github', '/connections/directory/browser', '/connections/preview-integration-1', '/connections/preview-integration-1?tab=resources', '/connections/preview-integration-1?tab=access', '/connections/preview-integration-1?tab=settings', '/connections/identities', '/connections/identities/preview-agent-1', '/connections/access', '/settings', '/settings/billing', '/settings/developer'];

try {
  for (const width of [1440, 390]) {
    await check(`every page renders cleanly at ${width}px`, async () => {
      const { context, page, errors } = await open(width);
      const problems = [];
      for (const route of PAGES) {
        await page.goto(BASE + route, { waitUntil: 'networkidle' });
        await page.locator('.ws-app').waitFor({ timeout: 5000 }).catch(() => {});
        await page.waitForTimeout(200);
        const state = await page.evaluate(() => {
          const h1 = [...document.querySelectorAll('.ws-main h1')].find((el) => el.offsetParent !== null && !el.classList.contains('sr-only'));
          return { overflow: document.documentElement.scrollWidth > innerWidth + 1, h1: h1 ? parseFloat(getComputedStyle(h1).fontSize) : null, shell: Boolean(document.querySelector('.ws-app')) };
        });
        if (!state.shell) problems.push(`${route}: workspace shell missing`);
        if (state.overflow) problems.push(`${route}: horizontal overflow`);
        if (state.h1 !== null && state.h1 > 32) problems.push(`${route}: heading ${state.h1}px`);
        if (errors.length) { problems.push(`${route}: ${errors.join(' | ').slice(0, 200)}`); errors.length = 0; }
      }
      await context.close();
      assert.deepEqual(problems, []);
    });
  }

  await check('deep links open the exact record', async () => {
    const { context, page } = await open();
    await page.goto(`${BASE}/results/preview-result-1`, { waitUntil: 'networkidle' });
    await page.getByRole('heading', { level: 1, name: 'Weekly research brief' }).waitFor();
    await page.goto(`${BASE}/activity/preview-action-3`, { waitUntil: 'networkidle' });
    assert.match(await page.locator('.ws-detail').innerText(), /Blocked/);
    await page.goto(`${BASE}/runs/preview-run`, { waitUntil: 'networkidle' });
    await page.getByRole('heading', { name: /Review source material/ }).waitFor();
    await page.goto(`${BASE}/jobs/preview-job-2`, { waitUntil: 'networkidle' });
    await page.getByRole('dialog', { name: 'Daily operations review' }).waitFor();
    await page.goto(`${BASE}/inbox/approval/preview-approval-1`, { waitUntil: 'networkidle' });
    assert.equal(await page.locator('.ws-inbox-row[aria-current="true"]').count(), 1);
    await context.close();
  });

  await check('back and forward follow navigation', async () => {
    const { context, page } = await open();
    await page.goto(BASE, { waitUntil: 'networkidle' });
    await page.getByRole('link', { name: 'Workers', exact: true }).first().click(); await settle(page);
    await page.getByRole('link', { name: 'Policies', exact: true }).first().click(); await settle(page);
    assert.equal(path_(page), '/policies');
    await page.goBack(); await settle(page);
    assert.equal(path_(page), '/workers');
    await page.getByRole('heading', { level: 1, name: 'Workers' }).waitFor();
    await page.goForward(); await settle(page);
    await page.getByRole('heading', { level: 1, name: 'Policies' }).waitFor();
    await context.close();
  });

  await check('filters live in the URL and survive refresh', async () => {
    const { context, page } = await open();
    await page.goto(`${BASE}/results`, { waitUntil: 'networkidle' });
    await page.getByLabel('Status').selectOption('attention'); await settle(page);
    assert.equal(path_(page), '/results?status=attention');
    await page.reload({ waitUntil: 'networkidle' });
    assert.equal(await page.getByLabel('Status').inputValue(), 'attention');
    await page.goto(`${BASE}/activity?filter=blocked`, { waitUntil: 'networkidle' });
    assert.equal(await page.locator('.ws-inbox-list .ws-rows > li').count(), 1);
    await context.close();
  });

  await check('conversation worker and thread are addressable', async () => {
    const { context, page, errors } = await open();
    await page.goto(`${BASE}/conversations`, { waitUntil: 'networkidle' });
    await page.getByRole('button', { name: 'Open conversations with Research analyst' }).click(); await settle(page);
    assert.equal(path_(page), '/conversations/preview-worker-1/preview-thread-1');
    await page.getByRole('button', { name: 'Source review' }).click(); await settle(page);
    assert.equal(path_(page), '/conversations/preview-worker-1/preview-thread-2');
    await page.goBack(); await settle(page);
    assert.equal(await page.locator('.cf-history-strip .is-active').innerText(), 'Weekly research brief');
    await page.getByRole('button', { name: 'Start a new chat' }).click(); await settle(page);
    assert.equal(path_(page), '/conversations/preview-worker-1/new');
    await page.reload({ waitUntil: 'networkidle' }); await page.waitForTimeout(300);
    assert.equal(await page.locator('.cf-chat-empty').count(), 1);
    await page.goto(`${BASE}/conversations/preview-worker-2`, { waitUntil: 'networkidle' }); await page.waitForTimeout(400);
    assert.equal(await page.locator('.cf-plan').count(), 1, 'live plan shown for the running task');
    assert.equal(await page.locator('.cf-gov[data-outcome="blocked"]').count(), 1, 'governance chip shown');
    assert.deepEqual(errors, []);
    await context.close();
  });

  await check('theme choice persists across reloads', async () => {
    const { context, page } = await open();
    await page.goto(BASE, { waitUntil: 'networkidle' });
    await page.locator('.ws-account').click();
    await page.getByRole('radio', { name: 'Dark' }).click();
    assert.equal(await page.evaluate(() => document.documentElement.dataset.wsTheme), 'dark');
    await page.reload({ waitUntil: 'domcontentloaded' });
    assert.equal(await page.evaluate(() => document.documentElement.dataset.wsTheme), 'dark', 'applied before paint');
    await page.locator('.ws-app').waitFor();
    const background = await page.evaluate(() => getComputedStyle(document.querySelector('.ws-app')).backgroundColor);
    assert.equal(background, 'rgb(17, 19, 17)');
    await context.close();
  });

  await check('command palette jumps to a page', async () => {
    const { context, page } = await open();
    await page.goto(BASE, { waitUntil: 'networkidle' });
    await page.keyboard.press('Control+k');
    await page.keyboard.type('Results');
    await page.keyboard.press('Enter'); await settle(page);
    assert.equal(path_(page), '/results');
    await context.close();
  });

  await check('old links land in the new places', async () => {
    const { context, page } = await open();
    await page.goto(`${BASE}/approvals`, { waitUntil: 'networkidle' });
    assert.match(path_(page), /^\/inbox/);
    assert.equal(await page.getByRole('button', { name: /Approvals/ }).first().getAttribute('aria-pressed'), 'true');
    await page.goto(`${BASE}/supervision`, { waitUntil: 'networkidle' });
    assert.equal(await page.getByRole('button', { name: /Escalations/ }).first().getAttribute('aria-pressed'), 'true');
    await context.close();
  });

  await check('phone navigation drawer opens and closes on navigation', async () => {
    const { context, page } = await open(390);
    await page.goto(BASE, { waitUntil: 'networkidle' });
    await page.getByRole('button', { name: 'Open navigation' }).click();
    await page.waitForTimeout(300);
    assert.equal(await page.locator('#ws-sidebar').getAttribute('data-open'), 'true');
    await page.locator('#ws-sidebar').getByRole('link', { name: 'Jobs', exact: true }).click(); await settle(page);
    assert.equal(path_(page), '/jobs');
    assert.equal(await page.locator('#ws-sidebar').getAttribute('data-open'), null);
    await context.close();
  });

  await check('a refused approval is rolled back with a message', async () => {
    const { context, page } = await open();
    await page.goto(BASE, { waitUntil: 'networkidle' });
    const before = await page.locator('.ws-nav-badge').innerText();
    await page.getByRole('button', { name: 'Approve', exact: true }).first().click();
    await page.locator('.ws-toast').first().waitFor({ timeout: 5000 });
    await page.waitForTimeout(300);
    assert.equal(await page.getByRole('button', { name: 'Approve', exact: true }).count() > 0, true, 'item restored');
    assert.equal(await page.locator('.ws-nav-badge').innerText(), before);
    await context.close();
  });


  await check('connections: cards open the account page, tabs live in the URL', async () => {
    const { context, page, errors } = await open();
    await page.goto(`${BASE}/connections`, { waitUntil: 'networkidle' });
    assert.equal(await page.locator('.ws-status-strip > div').count(), 4, 'status strip');
    assert.equal(await page.locator('.ws-conn-card button').count(), 0, 'no buttons on account cards');
    await page.locator('.ws-conn-card').first().click(); await settle(page);
    assert.match(path_(page), /^\/connections\/preview-integration-1$/);
    assert.equal(await page.locator('.ws-chain > li').count(), 5, 'identity to policy chain');
    await page.getByRole('link', { name: 'Resources', exact: true }).click(); await settle(page);
    assert.equal(path_(page), '/connections/preview-integration-1?tab=resources');
    assert.ok(await page.locator('.ws-table tbody tr').count() > 0, 'actions with rule outcome');
    await page.goBack(); await settle(page);
    assert.equal(path_(page), '/connections/preview-integration-1');
    await page.goto(`${BASE}/connections/preview-integration-1?tab=settings`, { waitUntil: 'networkidle' });
    await page.getByRole('button', { name: 'Disconnect', exact: true }).click();
    await page.getByRole('dialog', { name: /Disconnect Sample repository/ }).waitFor();
    await page.keyboard.press('Escape');
    assert.deepEqual(errors, []);
    await context.close();
  });

  await check('connections: directory to tool page to step-by-step connect', async () => {
    const { context, page } = await open();
    await page.goto(`${BASE}/connections/directory`, { waitUntil: 'networkidle' });
    assert.match(await page.locator('.ws-later').innerText(), /Not available yet/);
    await page.getByRole('link', { name: /Governed Browser/ }).click(); await settle(page);
    assert.equal(path_(page), '/connections/directory/browser');
    await page.getByRole('button', { name: /Connect/ }).first().click();
    const dialog = page.getByRole('dialog', { name: 'Connect Governed Browser' });
    await dialog.waitFor();
    await dialog.getByRole('button', { name: 'Continue' }).click();
    const next = dialog.getByRole('button', { name: 'Continue' });
    assert.equal(await next.isDisabled(), true, 'needs a start page');
    await dialog.getByLabel('Start page').fill('https://portal.example.com/home');
    await dialog.getByLabel('Allowed sites').fill('login.example.com');
    await dialog.getByLabel('Allowed sites').press('Enter');
    assert.deepEqual(await dialog.locator('.ws-chip').allInnerTexts(), ['https://portal.example.com', 'https://login.example.com']);
    await next.click();
    await dialog.getByRole('button', { name: 'Connect Governed Browser' }).click();
    await dialog.locator('.ws-checks li[data-state="failed"]').waitFor();
    assert.match(await dialog.innerText(), /read-only/, 'preview refuses the real write and says so');
    await context.close();
  });

  await check('connections: GitHub setup return continues at the installation step', async () => {
    const { context, page } = await open();
    await page.goto(`${BASE}/connections/directory/github?github_setup=preview-session`, { waitUntil: 'networkidle' });
    const dialog = page.getByRole('dialog', { name: 'Connect GitHub' });
    await dialog.waitFor();
    await dialog.getByRole('radio', { name: /sample-org/ }).waitFor();
    assert.equal(await dialog.getByRole('radio', { name: /old-team/ }).isDisabled(), true, 'suspended installation');
    await context.close();
  });

  await check('connections: access map cell edits one identity on one connection', async () => {
    const { context, page } = await open();
    await page.goto(`${BASE}/connections/access`, { waitUntil: 'networkidle' });
    assert.ok(await page.locator('.ws-matrix-cell').count() >= 4);
    await page.locator('.ws-matrix-cell').first().click();
    const sheet = page.getByRole('dialog');
    await sheet.waitFor();
    assert.ok(await sheet.locator('input[type="checkbox"]').count() > 0);
    await page.goto(`${BASE}/connections/identities/preview-agent-1`, { waitUntil: 'networkidle' });
    assert.ok(await page.locator('.ws-access-row').count() > 0, 'identity lists its access');
    await context.close();
  });

  await check('loader: busy link, progress bar and delayed spinner on slow pages', async () => {
    const { context, page } = await open();
    await page.goto(BASE, { waitUntil: 'networkidle' });
    await page.evaluate(() => sessionStorage.setItem('audoryn.preview.latency', '1500'));
    await page.locator('.ws-nav-item', { hasText: 'Performance' }).click();
    assert.equal(await page.locator('.ws-nav-item[data-pending="true"]').count(), 1, 'clicked link marked busy');
    assert.equal(await page.locator('.ws-progress').getAttribute('data-phase'), 'loading');
    await page.locator('.ws-nav-spinner').waitFor({ timeout: 2000 });
    await page.locator('.ws-nav-spinner').waitFor({ state: 'detached', timeout: 8000 });
    assert.equal(await page.locator('[data-pending="true"]').count(), 0, 'busy mark cleared');
    await page.evaluate(() => sessionStorage.removeItem('audoryn.preview.latency'));
    await page.locator('.ws-nav-item', { hasText: 'Audit' }).click();
    await page.waitForTimeout(700);
    assert.equal(await page.locator('.ws-nav-spinner').count(), 0, 'no spinner on fast pages');
    await context.close();
  });


  await check('top bar: in-app back and forward, breadcrumbs and recent pages', async () => {
    const { context, page, errors } = await open();
    await page.goto(`${BASE}/connections`, { waitUntil: 'networkidle' });
    const backButton = page.locator('.ws-topbar .ws-history button').first();
    const forwardButton = page.locator('.ws-topbar .ws-history button').nth(1);
    assert.equal(await backButton.isDisabled(), true, 'nothing behind the first workspace page');
    await page.locator('.ws-conn-card').first().click(); await settle(page);
    assert.deepEqual(await page.locator('.ws-crumbs li').allInnerTexts(), ['Connections', 'Sample repository']);
    await page.locator('.ws-feed .ws-row-link').first().click(); await settle(page);
    assert.match(path_(page), /^\/activity\//);
    assert.equal(await backButton.getAttribute('aria-label'), 'Back to Sample repository');
    await backButton.click({ button: 'right' });
    assert.ok(await page.locator('.ws-history-menu [role="menuitem"]').count() >= 2, 'recent pages menu');
    await page.locator('.ws-history-menu [role="menuitem"]').nth(1).click(); await settle(page);
    assert.equal(path_(page), '/connections');
    assert.equal(await forwardButton.isDisabled(), false);
    await page.keyboard.press('Control+]'); await settle(page);
    assert.equal(path_(page), '/connections/preview-integration-1');
    await page.keyboard.press('Control+['); await settle(page);
    assert.equal(path_(page), '/connections');
    await page.keyboard.press('Control+['); await settle(page);
    assert.equal(path_(page), '/connections', 'never steps out of the workspace');
    await page.goto(`${BASE}/connections/preview-integration-1?tab=resources`, { waitUntil: 'networkidle' });
    await page.locator('.ws-crumbs a', { hasText: 'Sample repository' }).click(); await settle(page);
    assert.equal(path_(page), '/connections/preview-integration-1', 'crumbs go up a level');
    assert.deepEqual(errors, []);
    await context.close();
  });

  await check('scroll position comes back with Back', async () => {
    const { context, page } = await open();
    await page.goto(`${BASE}/performance`, { waitUntil: 'networkidle' });
    await page.evaluate(() => window.scrollTo(0, 400)); await page.waitForTimeout(150);
    await page.locator('.ws-cell-link').first().click(); await settle(page);
    assert.equal(await page.evaluate(() => window.scrollY), 0, 'new pages open at the top');
    await page.goBack(); await page.waitForTimeout(1200);
    assert.ok(Math.abs(await page.evaluate(() => window.scrollY) - 400) < 40, 'returns to where you were');
    await context.close();
  });

  await check('scrollbars: custom page rail with section ticks, no stray bars', async () => {
    const { context, page } = await open();
    await page.goto(`${BASE}/performance`, { waitUntil: 'networkidle' });
    assert.equal(await page.evaluate(() => document.documentElement.dataset.wsScrollbar), 'custom');
    assert.equal(await page.evaluate(() => getComputedStyle(document.documentElement).scrollbarWidth), 'none', 'native page bar hidden');
    await page.mouse.wheel(0, 500); await page.waitForTimeout(200);
    assert.equal(await page.locator('.ws-scrollbar').getAttribute('data-visible'), 'true', 'appears while scrolling');
    assert.ok(await page.locator('.ws-scrollbar-tick').count() >= 3, 'section ticks');
    await page.waitForTimeout(1500);
    assert.equal(await page.locator('.ws-scrollbar').getAttribute('data-visible'), null, 'fades after 1.2 seconds');
    await page.mouse.move(1435, 400); await page.waitForTimeout(150);
    await page.locator('.ws-scrollbar-tick').nth(1).click(); await page.waitForTimeout(900);
    const landed = await page.evaluate(() => { const heading = [...document.querySelectorAll('.ws-main .ws-section-head h2')].filter((el) => el.offsetParent)[1]; return heading ? Math.round(heading.getBoundingClientRect().top) : -1; });
    assert.ok(landed > 40 && landed < 160, `tick jumps to its section (heading at ${landed}px)`);
    const stray = await page.evaluate(() => [...document.querySelectorAll('.ws-tabs, .ws-nav')].filter((el) => getComputedStyle(el).scrollbarWidth !== 'none').length);
    assert.equal(stray, 0, 'tabs and sidebar hide their bars');
    await page.goto(`${BASE}/jobs`, { waitUntil: 'networkidle' });
    assert.equal(await page.evaluate(() => { const tabs = document.querySelector('.ws-tabs'); return tabs.scrollHeight > tabs.clientHeight; }), false, 'tab row has no 1px overflow');
    await context.close();
    const touch = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
    const phone = await touch.newPage();
    await phone.goto(`${BASE}/performance`, { waitUntil: 'networkidle' });
    assert.equal(await phone.locator('.ws-scrollbar').count(), 0, 'phones keep the system bar');
    await touch.close();
  });

  await check('job sheet closes back to the list URL', async () => {
    const { context, page } = await open();
    await page.goto(`${BASE}/jobs`, { waitUntil: 'networkidle' });
    await page.getByRole('link', { name: /Weekly research brief/ }).first().click(); await settle(page);
    assert.equal(path_(page), '/jobs/preview-job-1');
    await page.keyboard.press('Escape'); await settle(page);
    assert.equal(path_(page), '/jobs');
    await context.close();
  });
} finally {
  await browser.close();
  await server.close();
}

await writeFile(path.join(output, 'results.json'), JSON.stringify(results, null, 2));
const failed = results.filter((result) => !result.ok);
console.log(`\n${results.length - failed.length}/${results.length} workspace checks passed`);
process.exit(failed.length ? 1 : 0);
