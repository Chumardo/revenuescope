/* Browser QA for a running local RevenueScope app. Install Playwright for Node,
 * then run: node scripts/verify_workspace_browser.cjs [http://localhost:8516] [before|after]
 * The installed Chrome channel is used. Set BROWSER_CHANNEL to use another channel.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const appUrl = new URL(process.argv[2] || 'http://127.0.0.1:8516');
assert.ok(['localhost', '127.0.0.1', '[::1]'].includes(appUrl.hostname), 'Use a local app URL');
const stage = process.argv[3] || 'after';
assert.ok(['before', 'after'].includes(stage), 'Choose a before or after capture set');
const screenshots = process.argv[4] ? path.resolve(process.argv[4]) :
  path.join(__dirname, '..', 'docs', 'screenshots', 'phase2_5', stage);
const screens = {
  overview: 'Revenue Overview', queue: 'Analyst Review Queue',
  detail: 'Investigation Detail', evaluation: 'Evaluation',
};
const headings = { 'Investigation Detail': 'Investigation detail', Evaluation: 'Detector evaluation' };
const data = (page, name) => page.getByTestId(name);
const main = page => data(page, 'stMain');
const exactText = (page, name) => page.getByText(name, { exact: true });

async function show(locator, timeout = 25000) {
  await locator.first().waitFor({ state: 'visible', timeout });
}

async function stable(page) {
  await page.waitForFunction(() => {
    const app = document.querySelector('[data-testid="stApp"]');
    return app?.getAttribute('data-test-script-state') === 'notRunning' && !app.querySelector('[data-stale="true"]');
  }, undefined, { timeout: 25000 });
  assert.equal(await data(page, 'stException').count(), 0, 'Streamlit raised an exception');
  assert.equal(await data(page, 'stFileUploader').count(), 0, 'A file upload widget was exposed');
  await show(exactText(page, 'Synthetic demo'));
}

async function armRerun(page) {
  await page.evaluate(() => {
    const app = document.querySelector('[data-testid="stApp"]');
    window.revenueScopeQaObserver?.disconnect();
    window.revenueScopeQaSawRerun = false;
    window.revenueScopeQaObserver = new MutationObserver(mutations => {
      if (app.dataset.testScriptState !== 'notRunning' || mutations.some(entry => entry.oldValue !== 'notRunning')) {
        window.revenueScopeQaSawRerun = true;
      }
    });
    window.revenueScopeQaObserver.observe(app, { attributes: true, attributeFilter: ['data-test-script-state'],
      attributeOldValue: true });
  });
}

async function reran(page) {
  await page.waitForFunction(() => window.revenueScopeQaSawRerun === true, undefined, { timeout: 12000 });
  await stable(page);
}

async function screen(page, name) {
  await show(main(page).getByRole('heading', { name: headings[name] || name, exact: true }));
  await stable(page);
}

async function load(page) {
  const started = Date.now();
  const response = await page.goto(appUrl.toString(), { waitUntil: 'domcontentloaded' });
  assert.ok(response && response.ok(), 'Local Streamlit HTTP response failed');
  await screen(page, screens.overview);
  await chart(page);
  console.log(`Overview first chart ready in ${Date.now() - started} ms (${page.viewportSize().width}px viewport)`);
}

async function navigate(page, name, { touch = false } = {}) {
  const nav = page.locator('[data-testid="stRadio"], [data-testid="stButtonGroup"]').getByText(name, { exact: true });
  if (!await nav.first().isVisible()) {
    const open = page.locator('[data-testid="stSidebarCollapsedControl"], [data-testid="stExpandSidebarButton"]').first();
    if (await open.isVisible()) await open.click();
  }
  await main(page).evaluate(element => element.scrollTo(0, 0));
  await nav.first().scrollIntoViewIfNeeded();
  await armRerun(page);
  if (touch) await nav.first().tap();
  else await nav.first().click();
  await reran(page);
  await screen(page, name);
}

async function select(page, label, choice, { firstOption, alreadySelected = false } = {}) {
  const widget = data(page, 'stSelectbox').filter({ has: exactText(page, label) });
  const combo = widget.getByRole('combobox');
  await combo.scrollIntoViewIfNeeded();
  await combo.click();
  const option = page.getByRole('option', { name: choice, exact: true });
  if (!await option.isVisible()) {
    await combo.press('ArrowDown');
  }
  await show(option);
  if (firstOption) assert.equal((await page.getByRole('option').first().innerText()).trim(), firstOption);
  const selected = await option.getAttribute('aria-selected') === 'true';
  if (alreadySelected) assert.ok(selected, `${choice} should already be selected in ${label}`);
  if (selected) {
    await page.keyboard.press('Escape');
    return;
  }
  await armRerun(page);
  await option.click();
  await reran(page);
}

async function dateInput(page, label, value) {
  const widget = data(page, 'stDateInput').filter({ has: exactText(page, label) });
  const input = widget.locator('input');
  await armRerun(page);
  await input.fill(value);
  await main(page).getByRole('heading', { level: 1 }).first().click();
  await reran(page);
}

function dateValue(page, label) {
  return data(page, 'stDateInput').filter({ has: exactText(page, label) }).locator('input').inputValue();
}

async function pressButton(page, name) {
  await armRerun(page);
  await page.getByRole('button', { name, exact: true }).click();
  await reran(page);
}

async function verifyCaptionContrast(page) {
  const captions = await data(page, 'stCaptionContainer').evaluateAll(nodes => nodes.map(node => ({
    opacity: getComputedStyle(node).opacity,
    foreground: getComputedStyle(node.querySelector('p') || node).color,
  })));
  assert.ok(captions.length > 0);
  assert.ok(captions.every(caption => caption.opacity === '1' && caption.foreground === 'rgb(82, 103, 124)'));
}

async function screenshot(page, file) {
  await stable(page);
  await page.evaluate(() => document.fonts.ready);
  await page.evaluate(() => {
    document.querySelector('[data-testid="stMain"]')?.scrollTo(0, 0);
    window.scrollTo(0, 0);
  });
  await page.screenshot({ path: path.join(screenshots, file), animations: 'disabled' });
}

async function layout(page) {
  const measurements = await main(page).evaluate(element => ({
    documentWidth: document.documentElement.scrollWidth,
    windowWidth: window.innerWidth,
    viewWidth: element.clientWidth,
    mainWidth: element.scrollWidth,
  }));
  assert.ok(measurements.documentWidth <= measurements.windowWidth + 2, JSON.stringify(measurements));
  assert.ok(measurements.mainWidth <= measurements.viewWidth + 2, JSON.stringify(measurements));
}

async function chart(page) {
  await show(main(page).locator('.js-plotly-plot'));
}

async function overviewValues(page, expectedTotal, expectedDates, expectedWarmup, expectedFlags) {
  await chart(page);
  await page.waitForFunction(({ expectedTotal, expectedDates, expectedWarmup, expectedFlags }) => {
    const traces = document.querySelector('[data-testid="stMain"] .js-plotly-plot')?.data;
    const actual = traces?.find(trace => trace.name === 'Actual');
    const flags = traces?.find(trace => trace.name === 'Detector flag');
    if (!actual?.customdata || actual.customdata.length !== expectedDates) return false;
    const cents = actual.customdata.reduce((sum, row) => sum + Number(row[0].replace(/[$,.]/g, '').replace('−', '-')), 0);
    const missing = actual.customdata.filter(row => row[1] === '—').length;
    return cents === expectedTotal && missing === expectedWarmup && (flags?.customdata.length || 0) === expectedFlags;
  }, { expectedTotal, expectedDates, expectedWarmup, expectedFlags }, { timeout: 25000 });
}

function captureErrors(page) {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {
    if (message.type() === 'error') errors.push(`console: ${message.text()}`);
  });
  page.on('response', response => {
    if (response.url().startsWith(appUrl.origin) && response.status() >= 500) {
      errors.push(`${response.status()} ${response.url()}`);
    }
  });
  return errors;
}

async function desktop(browser) {
  const page = await browser.newPage({ viewport: { width: 1560, height: 1080 }, deviceScaleFactor: 1 });
  const errors = captureErrors(page);
  await load(page);
  await chart(page);
  await layout(page);
  await show(exactText(main(page), '$4,461,841.22'));
  await overviewValues(page, 446184122, 731, 56, 31);
  if (stage === 'after') {
    await show(exactText(main(page), '$4,229,422.48'));
    await show(exactText(main(page), '$4,176,481.29'));
    await show(exactText(main(page), '+$52,941.19'));
    await verifyCaptionContrast(page);
  }
  await screenshot(page, '01-overview.png');
  if (stage === 'after') {
    await pressButton(page, 'Last 90 days');
    assert.equal(await dateValue(page, 'Start date'), '2024-10-03');
    assert.equal(await dateValue(page, 'End date'), '2024-12-31');
    await show(main(page).getByText(/90 of 90 days scored/));
    await pressButton(page, 'Full snapshot');
    assert.equal(await dateValue(page, 'Start date'), '2023-01-01');
    await overviewValues(page, 446184122, 731, 56, 31);
  }
  await dateInput(page, 'Start date', '2024-07-01');
  await dateInput(page, 'End date', '2024-07-31');
  await show(exactText(main(page), '$207,956.32'));
  await overviewValues(page, 20795632, 31, 0, 3);
  await select(page, 'Dimension', 'Region');
  await select(page, 'Segment', 'Northeast');
  await show(exactText(main(page), '$52,769.45'));
  await overviewValues(page, 5276945, 31, 31, 0);
  if (stage === 'after') {
    await navigate(page, screens.queue);
    await navigate(page, screens.overview);
    await show(exactText(main(page), '$52,769.45'));
    await overviewValues(page, 5276945, 31, 31, 0);
  }
  await select(page, 'Dimension', 'All revenue');
  await select(page, 'Revenue basis', 'Order-day net revenue · retrospective');
  await show(exactText(main(page), '$208,321.33'));
  await show(main(page).getByText(/restat[es]+ the original order day/i));
  await select(page, 'Revenue basis', 'Posted-day activity');
  await show(exactText(main(page), '$207,956.32'));
  console.log('PASS Overview: exact full/July posted ledger values, warmup gaps, flags, Northeast slice, and separately labeled retrospective total');
  if (stage === 'after') {
    await pressButton(page, 'Open review queue');
    await screen(page, screens.queue);
    assert.equal(await dateValue(page, 'Start date'), '2024-07-01');
    assert.equal(await dateValue(page, 'End date'), '2024-07-31');
  } else {
    await navigate(page, screens.queue);
  }
  if (stage === 'after') {
    await dateInput(page, 'Start date', '2023-01-01');
    await dateInput(page, 'End date', '2023-01-31');
    await show(main(page).getByText(/no detector flags match/i));
    await navigate(page, screens.overview);
    await show(exactText(main(page), '$130,680.66'));
    await overviewValues(page, 13068066, 31, 31, 0);
    await navigate(page, screens.queue);
    await dateInput(page, 'End date', '2024-07-31');
    await dateInput(page, 'Start date', '2024-07-01');
  }
  await select(page, 'Direction', 'Increase');
  if (stage === 'before') {
    await select(page, 'Impact', 'Low');
    await show(main(page).getByText(/no detector flags match/i));
    await select(page, 'Impact', 'High');
  }
  await show(main(page).getByRole('heading', { name: /2 dates to review/ }));
  const gapOrder = stage === 'after' ? 'Largest gap' : 'Largest absolute deviation';
  await select(page, 'Sort by', gapOrder, { alreadySelected: stage === 'after' });
  await select(page, 'Sort by', 'Oldest first');
  await select(page, 'Sort by', gapOrder);
  await select(page, 'Flagged date', '2024-07-15', { firstOption: '2024-07-15' });
  await screenshot(page, '02-review-queue.png');
  await page.getByRole('button', { name: 'Open investigation', exact: true }).click();
  await screen(page, screens.detail);
  await show(exactText(main(page), '$11,531.62'));
  await show(exactText(main(page), '$6,905.85'));
  await chart(page);
  await armRerun(page);
  await exactText(page.locator('.st-key-detail_dimension'), 'Product').click();
  await reran(page);
  await show(main(page).getByText(/reconciled to the ledger/i));
  await show(main(page).getByText(/product-level history can be sparse/i));
  await layout(page);
  await screenshot(page, '03-investigation.png');
  console.log('PASS Queue and detail: direction/impact/sort/empty state, selected 2024-07-15, and reconciled Product evidence');
  await page.getByRole('button', { name: 'Back to review queue', exact: true }).click();
  await screen(page, screens.queue);
  await show(main(page).getByRole('heading', { name: /2 dates to review/ }));
  await select(page, 'Flagged date', '2024-07-15', { alreadySelected: true });
  await navigate(page, screens.evaluation);
  await show(main(page).getByRole('heading', { name: 'Original vs. locked detector' }));
  await show(exactText(data(page, 'stMetricValue'), '83'));
  if (stage === 'after') {
    await show(exactText(data(page, 'stMetricValue'), '7.95%'));
    await show(exactText(data(page, 'stMetricValue'), '46.67%'));
    await show(exactText(main(page), '7 of 88 alert episodes matched'));
    await show(exactText(main(page), '7 of 15 eligible events detected'));
  }
  await layout(page);
  await screenshot(page, '04-evaluation.png');
  await exactText(data(page, 'stRadio').filter({ has: exactText(page, 'Evaluation revenue basis') }),
    'Order-day retrospective').click();
  await show(exactText(data(page, 'stMetricValue'), '72'));
  await stable(page);
  assert.deepEqual(errors, []);
  await page.close();
  console.log('PASS desktop 1560 × 1080: Overview → filtered/sorted queue → investigation → queue → evaluation');
}

async function mobile(browser) {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2,
    hasTouch: true, isMobile: true });
  const errors = captureErrors(page);
  await load(page);
  await chart(page);
  await layout(page);
  await overviewValues(page, 446184122, 731, 56, 31);
  assert.equal(await page.evaluate(() => window.innerWidth), 390, 'Mobile CSS viewport width');
  await screenshot(page, '05-mobile-overview.png');
  await navigate(page, screens.queue, { touch: true });
  await layout(page);
  if (stage === 'after') {
    await select(page, 'Sort by', 'Largest gap', { alreadySelected: true });
    await select(page, 'Flagged date', '2024-07-15', { firstOption: '2024-07-15' });
  }
  await screenshot(page, '06-mobile-review-queue.png');
  const open = page.getByRole('button', { name: 'Open investigation', exact: true });
  await open.scrollIntoViewIfNeeded();
  await open.tap();
  await screen(page, screens.detail);
  if (stage === 'after') await show(exactText(main(page), '$11,531.62'));
  await layout(page);
  await screenshot(page, '07-mobile-investigation.png');
  await navigate(page, screens.evaluation, { touch: true });
  await layout(page);
  if (stage === 'after') await show(exactText(data(page, 'stMetricValue'), '7.95%'));
  await screenshot(page, '08-mobile-evaluation.png');
  if (stage === 'after') {
    const comparison = page.locator('.rs-comparison');
    await comparison.scrollIntoViewIfNeeded();
    await show(comparison.getByRole('cell', { name: '7.95%' }));
    assert.ok(await comparison.evaluate(table => table.scrollWidth <= table.clientWidth + 1));
  }
  assert.deepEqual(errors, []);
  await page.close();
  console.log('PASS mobile 390 × 844: Overview → review queue → investigation → evaluation, no page overflow');
}

async function run() {
  fs.mkdirSync(screenshots, { recursive: true });
  const browser = await chromium.launch({ channel: process.env.BROWSER_CHANNEL || 'chrome', headless: true });
  try {
    console.log(`Chrome ${browser.version()}`);
    await desktop(browser);
    await mobile(browser);
  } finally {
    await browser.close();
  }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
