"use strict";
// Records actual application interactions against the isolated Python harness.
// The pauses below are presentation pacing, not test synchronization.
const {chromium, devices, expect} = require('@playwright/test');
const fs = require('node:fs/promises');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
if (process.env.FLOWFORGE_BROWSER_TESTS !== '1' || !/^http:\/\/127\.0\.0\.1:\d+$/.test(process.env.FLOWFORGE_BROWSER_URL || '')) {
  throw new Error('Use python scripts/run_browser_tests.py --portfolio');
}
async function main() {
  const media=path.join(root,'docs','media');
  const raw=path.join(root,'test-results','portfolio-video');
  await fs.mkdir(media,{recursive:true});
  await fs.mkdir(raw,{recursive:true});
  const browser=await chromium.launch();
  let context;
  try {
    context=await browser.newContext({baseURL:process.env.FLOWFORGE_BROWSER_URL,
      viewport:{width:1440,height:1000},deviceScaleFactor:1,
      recordVideo:{dir:raw,size:{width:1440,height:1000}}});
    const page=await context.newPage();
    const errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    await page.goto('/dashboard');
    await expect(page.locator('#connection-text')).toContainText('Live');
    await page.waitForTimeout(2500);
    await page.locator('#file').setInputFiles(path.join(root,'sample_data','sales.csv'));
    await expect(page.locator('#preview-body tr')).toHaveCount(4);
    await page.locator('#name').pressSequentially('Sales performance',{delay:70});
    await page.waitForTimeout(3000);
    await page.getByRole('button',{name:'Create analysis'}).click();
    await expect(page.locator('#analysis-title')).toHaveText('Sales performance');
    await expect(page.locator('#analysis-status')).toHaveText('Completed',{timeout:30000});
    await expect(page.locator('#result-rows')).toHaveText('4');
    await page.waitForTimeout(3500);
    await page.locator('#analysis-panel').scrollIntoViewIfNeeded();
    await page.getByLabel('Search column names').pressSequentially('Price',{delay:150});
    await expect(page.locator('#statistics-body tr')).toHaveCount(1);
    await page.waitForTimeout(3000);
    await page.getByLabel('Search column names').fill('');
    await page.getByLabel('Sort columns').selectOption('mean_desc');
    await page.waitForTimeout(2500);
    await page.getByRole('tab',{name:'Data quality'}).click();
    await page.waitForTimeout(3500);
    await page.getByRole('tab',{name:'JSON',exact:true}).click();
    await page.waitForTimeout(2000);
    const downloadEvent=page.waitForEvent('download');
    await page.getByRole('button',{name:'Export JSON'}).click();
    const download=await downloadEvent;
    await download.path();
    await page.getByRole('tab',{name:'Column statistics'}).click();
    await page.getByLabel('Sort columns').selectOption('original');
    await page.getByRole('button',{name:'Use light theme'}).click();
    await page.waitForTimeout(3000);
    await page.getByRole('button',{name:'Use dark theme'}).click();
    // Clean screen captures retain real results, with an empty upload form.
    await page.reload();
    await expect(page.locator('#analysis-status')).toHaveText('Completed');
    await expect(page.locator('#statistics-body tr')).toHaveCount(3);
    await page.mouse.move(1400,80);
    await page.evaluate(()=>window.scrollTo(0,0));
    await page.screenshot({path:path.join(media,'dashboard-dark.png'),fullPage:true,animations:'disabled'});
    await page.getByRole('button',{name:'Use light theme'}).click();
    await page.screenshot({path:path.join(media,'dashboard-light.png'),fullPage:true,animations:'disabled'});
    await page.getByRole('button',{name:'Use dark theme'}).click();
    await page.waitForTimeout(3000);
    const video=page.video();
    await context.close();context=null;
    await video.saveAs(path.join(media,'flowforge-demo.webm'));
    const mobile=await browser.newContext({...devices['Pixel 7'],baseURL:process.env.FLOWFORGE_BROWSER_URL});
    try {
      const mobilePage=await mobile.newPage();
      await mobilePage.goto('/dashboard');
      await expect(mobilePage.locator('#analysis-status')).toHaveText('Completed');
      await expect(mobilePage.locator('#statistics-body tr')).toHaveCount(3);
      expect(await mobilePage.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
      await mobilePage.screenshot({path:path.join(media,'dashboard-mobile.png'),fullPage:true,animations:'disabled'});
    } finally {await mobile.close();}
    expect(errors).toEqual([]);
    console.log('Captured real desktop dark/light screenshots, mobile layout, and a silent workflow video.');
  } finally {
    if(context)await context.close();
    await browser.close();
  }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
