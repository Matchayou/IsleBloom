import {defineConfig} from '@playwright/test';
export default defineConfig({testDir:'./tests',workers:1,use:{baseURL:process.env.PLAYWRIGHT_BASE_URL||'http://127.0.0.1:18744',browserName:'chromium',viewport:{width:1440,height:1050},screenshot:'only-on-failure'},reporter:[['list'],['json',{outputFile:'../docs/full-regression-evidence/browser-results.json'}]]});
