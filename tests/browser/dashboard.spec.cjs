const {test, expect} = require('@playwright/test');
const fs = require('node:fs/promises');
const {Buffer} = require('node:buffer');

test.beforeEach(async ({page}) => {
  page.browserErrors=[];
  page.on('pageerror', error=>page.browserErrors.push(error.message));
  await page.goto('/dashboard');
  await expect(page.locator('#connection-text')).toContainText('Live');
});
test.afterEach(async ({page})=>{
  expect(page.browserErrors).toEqual([]);
});

test('preview, real upload, precise column sorting, search, and full JSON export',async({page},info)=>{
  const name=`Precise upload ${info.project.name}`;
  const content='Label,Small,Large,Missing\nfirst,9007199254740992,9007199254740993,\nsecond,9007199254740992,9007199254740993,1\n';
  await page.locator('#file').setInputFiles({name:'precise.csv',mimeType:'text/csv',buffer:Buffer.from(content)});
  await expect(page.locator('#preview-body tr')).toHaveCount(2);
  await expect(page.locator('#preview-head')).toContainText('Large');
  await page.getByLabel('Analysis name').fill(name);
  await page.getByRole('button',{name:'Create analysis'}).click();
  await expect(page.locator('#analysis-title')).toHaveText(name);
  await expect(page.locator('#analysis-status')).toHaveText('Completed');
  await expect(page.locator('#time-duration')).not.toHaveText('Not recorded');
  await page.getByLabel('Sort columns').selectOption('mean_desc');
  await expect(page.locator('#statistics-body tr').first()).toContainText('Large');
  await page.getByLabel('Search column names').fill('small');
  await expect(page.locator('#statistics-body tr')).toHaveCount(1);
  await expect(page.locator('#column-count')).toHaveText('1 of 4 columns');
  const downloadEvent=page.waitForEvent('download');
  await page.getByRole('button',{name:'Export JSON'}).click();
  const download=await downloadEvent;
  const data=JSON.parse(await fs.readFile(await download.path(),'utf8'));
  expect(data.results.columns).toHaveLength(4);
  expect(data.results.columns[2].numeric.mean).toBe('9007199254740993');
  await page.getByLabel('Search column names').fill('nothing matches');
  await expect(page.locator('#columns-empty')).toBeVisible();
  await page.getByLabel('Search column names').fill('');
  await expect(page.locator('#statistics-body tr')).toHaveCount(4);
});

test('invalid preview explains the failure and creates no job',async({page})=>{
  const before=(await(await page.request.get('/dashboard/data')).json()).total;
  await page.locator('#file').setInputFiles({name:'invalid.csv',mimeType:'text/csv',buffer:Buffer.from('a,b\n1\n')});
  await expect(page.locator('#preview-message')).toContainText('CSV validation failed');
  await expect(page.getByRole('button',{name:'Create analysis'})).toBeDisabled();
  expect((await(await page.request.get('/dashboard/data')).json()).total).toBe(before);
});

test('sample data, job filters, quality view, and keyboard tab navigation',async({page})=>{
  await page.getByRole('button',{name:'Try sample data'}).click();
  await expect(page.locator('#analysis-status')).toHaveText('Completed');
  await expect(page.locator('#analysis-title')).toHaveText('Sales sample analysis');
  await expect(page.locator('#result-rows')).toHaveText('4');
  await expect(page.locator('#findings')).toContainText('401.25');
  await page.getByLabel('Search job names').fill('Sales sample');
  await page.getByLabel('Filter by job status').selectOption('completed');
  await expect(page.locator('#jobs-body')).toContainText('Sales sample analysis');
  await page.getByRole('tab',{name:'Data quality'}).click();
  await expect(page.locator('#quality-bars')).toContainText('100.0% present');
  await page.getByRole('tab',{name:'Data quality'}).press('ArrowRight');
  await expect(page.getByRole('tab',{name:'JSON',exact:true})).toHaveAttribute('aria-selected','true');
  await expect(page.locator('#json-results')).toContainText('401.25');
});

test('processing and delivery failures recover through the real worker',async({page},info)=>{
  for(const stage of ['Processing','Delivery']){
    const name=`${stage} recovery ${info.project.name}`;
    await page.getByLabel('Search job names').fill(name);
    const source=page.locator('#jobs-body .job-name').filter({hasText:name});
    await expect(source).toHaveCount(1);
    await source.click();
    await expect(page.locator('#recovery-panel')).toBeVisible();
    await expect(page.locator('#recovery-title')).toContainText(stage);
    await expect(page.locator('#recovery-message')).not.toContainText('private fixture');
    await page.getByRole('button',{name:stage==='Processing'?'Retry analysis':'Retry delivery',exact:true}).click();
    await expect(page.locator('#analysis-status')).toHaveText('Completed');
    await expect(page.locator('#result-rows')).toHaveText('4');
  }
});

test('theme choice persists and both palettes fit the viewport',async({page},info)=>{
  await expect(page.locator('html')).toHaveAttribute('data-theme','dark');
  await page.getByRole('button',{name:'Use light theme'}).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme','light');
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme','light');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  await page.screenshot({path:info.outputPath('light.png'),fullPage:true});
  await page.getByRole('button',{name:'Use dark theme'}).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme','dark');
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme','dark');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  await page.screenshot({path:info.outputPath('dark.png'),fullPage:true});
});

test('blocked preference storage does not break the dashboard',async({page})=>{
  await page.addInitScript(()=>{
    Storage.prototype.getItem=function(){throw new Error('storage unavailable')};
    Storage.prototype.setItem=function(){throw new Error('storage unavailable')};
  });
  await page.reload();
  await expect(page.locator('#connection-text')).toContainText('Live');
  await page.getByRole('button',{name:'Use light theme'}).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme','light');
});
