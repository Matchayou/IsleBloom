import {chromium} from '@playwright/test';

const [mode, base, id] = process.argv.slice(2);
const browser = await chromium.launch({headless: true});
try {
  const page = await browser.newPage();
  await page.goto(base);
  const value = await page.evaluate(async ({mode, id}) => {
    const url = mode === 'submit' ? '/api/task_run' :
      `/api/durable_status?job_id=${encodeURIComponent(id)}`;
    const response = await fetch(url, mode === 'submit' ? {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({task_id: id})
    } : undefined);
    return {status: response.status, body: await response.json()};
  }, {mode, id});
  process.stdout.write(JSON.stringify(value));
} finally {
  await browser.close();
}
