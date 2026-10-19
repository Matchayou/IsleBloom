import {defineConfig} from '@playwright/test';
export default defineConfig({testDir:'./tests',testMatch:'projects.spec.js',workers:1,
  use:{baseURL:'http://127.0.0.1:8782',browserName:'chromium',viewport:{width:1440,height:900}},reporter:'list'});
