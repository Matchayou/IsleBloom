import {defineConfig} from '@playwright/test';
export default defineConfig({
 testDir:'./acceptance',workers:1,
 use:{baseURL:'http://127.0.0.1:8745',browserName:'chromium',viewport:{width:1440,height:1050}},
 reporter:[['list'],['json',{outputFile:'../docs/r3-failure-browser-tests.json'}]],
});
