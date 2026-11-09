'use strict';
// Run with node tests/test_audit_scroll.js; uses an installed Chromium browser.
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const {spawnSync}=require('node:child_process');
const candidates=[process.env.ACW_TEST_BROWSER,
  path.join(process.env['ProgramFiles(x86)']||'', 'Microsoft/Edge/Application/msedge.exe'),
  path.join(process.env.ProgramFiles||'', 'Google/Chrome/Application/chrome.exe'),
  '/usr/bin/chromium','/usr/bin/chromium-browser','/usr/bin/google-chrome'];
const browser=candidates.find(candidate=>candidate&&fs.existsSync(candidate));
if(!browser)throw Error('Set ACW_TEST_BROWSER to an installed Chromium browser executable.');
const ui=path.resolve(__dirname,'../ui');
const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'acw-audit-scroll-'));
const scenario=async function(){
  const check=(condition,message)=>{if(!condition)throw Error(message)};
  const near=(actual,expected,message)=>check(Math.abs(actual-expected)<=2,`${message}: ${actual} vs ${expected}`);
  const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
  try{
    setPage('audit');
    const lines=Array.from({length:120},(_,index)=>`Log line ${index}`);
    auditCurrentJobs=Array.from({length:8},(_,index)=>({
      id:`scroll-${index}`,name:'scroll-test',label:`Scroll test ${index}`,
      status:'running',runtime:10,output:[...lines],
      events:lines.map(message=>({kind:'stage',message})),
    }));
    auditDetailsOpen.add('scroll-0');
    renderAuditCurrent();
    await tick();
    const page=$('log-audit-page');
    const root=$('audit-current-results');
    const first=root.firstElementChild;
    first.querySelector('summary').focus({preventScroll:true});
    page.scrollTop=150;
    let output=first.querySelector('[data-audit-scroll=output]');
    let events=first.querySelector('[data-audit-scroll=events]');
    output.scrollTop=350;events.scrollTop=250;
    const pageTop=page.scrollTop;
    for(let i=0;i<4;i++)renderAuditCurrent();
    check(root.firstElementChild===first,'Unchanged records were replaced');
    near(output.scrollTop,350,'Unchanged output scroll moved');
    near(events.scrollTop,250,'Unchanged events scroll moved');
    near(page.scrollTop,pageTop,'Unchanged page scroll moved');
    for(let i=0;i<4;i++){
      auditCurrentJobs[0].runtime++;
      auditCurrentJobs[0].output.push(`New output ${i}`);
      renderAuditCurrent();
      await tick();
      output=first.querySelector('[data-audit-scroll=output]');
      events=first.querySelector('[data-audit-scroll=events]');
      near(output.scrollTop,350,'Live output scroll moved');
      near(events.scrollTop,250,'Live events scroll moved');
      near(page.scrollTop,pageTop,'Live page scroll moved');
      check(first.querySelector('details').open,'Expanded details closed');
      check(document.activeElement===first.querySelector('summary'),'Summary focus lost');
    }
    const anchor=root.children[3];
    page.scrollTop+=anchor.getBoundingClientRect().top-page.getBoundingClientRect().top;
    const anchorTop=anchor.getBoundingClientRect().top;
    auditCurrentJobs.unshift({...auditCurrentJobs[1],id:'new-job'});
    renderAuditCurrent();
    near(anchor.getBoundingClientRect().top,anchorTop,'New job shifted the visible record');
    auditCurrentJobs=[];renderAuditCurrent();
    check(root.querySelector('.empty'),'Empty message missing');
    auditCurrentJobs=[{id:'next',name:'next',status:'completed'}];renderAuditCurrent();
    check(root.children.length===1&&!root.querySelector('.empty'),'Stale empty message retained');
    selectAuditView('prior');
    auditPriorJobs=[{id:'prior',status:'failed',output:lines}];
    auditDetailsOpen.add('prior');renderAuditPrior();
    const prior=$('audit-prior-results').querySelector('pre');
    prior.scrollTop=200;renderAuditPrior();
    check($('audit-prior-results').querySelector('pre')===prior,'Stored record was replaced');
    near(prior.scrollTop,200,'Stored output scroll moved');
    document.documentElement.dataset.auditTest='passed';
  }catch(error){
    document.documentElement.dataset.auditTest='failed';
    const failure=document.createElement('pre');failure.id='test-failure';
    failure.textContent=error.stack;document.body.append(failure);
  }
};
try{
  let html=fs.readFileSync(path.join(ui,'index.html'),'utf8');
  html=html.replace('<head>',`<head><base href="${pathToFileURL(ui+path.sep).href}">`);
  html=html.replace('</body>',`<script>(${scenario.toString()})();</script></body>`);
  const fixture=path.join(temporary,'audit.html');fs.writeFileSync(fixture,html);
  const result=spawnSync(browser,['--headless','--disable-gpu','--allow-file-access-from-files',
    `--user-data-dir=${path.join(temporary,'profile')}`,'--window-size=1100,900',
    '--virtual-time-budget=3000','--dump-dom',pathToFileURL(fixture).href],
    {encoding:'utf8',timeout:30000,windowsHide:true,maxBuffer:4*1024*1024});
  if(result.error)throw result.error;
  if(result.status!==0||!result.stdout.includes('data-audit-test="passed"')){
    const failure=result.stdout.match(/<pre id="test-failure">([\s\S]*?)<\/pre>/);
    throw Error(failure?failure[1]:result.stderr||'Browser test did not complete.');
  }
  console.log('Audit scroll browser checks passed.');
}finally{
  const target=path.resolve(temporary);
  if(path.dirname(target)!==path.resolve(os.tmpdir())||!path.basename(target).startsWith('acw-audit-scroll-'))
    throw Error('Unexpected temporary directory; refusing cleanup.');
  fs.rmSync(target,{recursive:true,force:true,maxRetries:5,retryDelay:100});
}
