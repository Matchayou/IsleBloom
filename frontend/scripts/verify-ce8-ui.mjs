import {chromium, expect} from '@playwright/test';
import {readFile, writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
const root = new URL('../../', import.meta.url);
const fixture = JSON.parse(await readFile(new URL('.ce8/entrances-restored/live.json', root), 'utf8'));
const evidence = new URL('docs/ce8-evidence/', root);
const marker = fixture.original_source.match(/Marker: ([^.]+)\./)[1];
const browser = await chromium.launch({headless: true});
const page = await browser.newPage({viewport: {width: 1440, height: 1050}});
const errors = [];
page.on('pageerror', e => errors.push(e.message));
try {
  await page.goto('http://127.0.0.1:8796/');
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  await expect(page.getByRole('heading', {name: /^CE2 live /})).toBeVisible();
  await page.locator(`[data-id="task:${fixture.experts[0].task_id}"]`).click();
  await expect(page.getByRole('region', {name: '专家对话'})).toBeVisible();
  await page.getByRole('button', {name: '查看完整报告', exact: true}).first().click();
  await expect(page.getByText(marker, {exact: false}).first()).toBeVisible();
  await expect(page.getByRole('button', {name: '正式采用已复核结果', exact: true})).toHaveCount(0);
  expect(errors).toEqual([]);
  await page.screenshot({path: fileURLToPath(new URL('browser-saved-report.png', evidence)), fullPage: true});
  await writeFile(new URL('browser.json', evidence), JSON.stringify({status: 'passed',
    server: 'canonical agent-research CLI, PostgreSQL, no API key',
    source: 'fresh CE8 four-entrance real-model scenario',
    checks: ['saved project visible after process restart', 'expert thread opens',
      'complete report contains exact source marker', 'pending report cannot be adopted as reviewed'],
    page_errors: errors}, null, 2));
  console.log('CE8 browser passed');
} catch (error) {
  await page.screenshot({path: fileURLToPath(new URL('browser-failure.png', evidence)), fullPage: true});
  throw error;
} finally {
  await browser.close();
}
