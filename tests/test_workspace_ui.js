'use strict';
// Run with node tests/test_workspace_ui.js; exercises the real workspace UI.
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {pathToFileURL}=require('node:url');
const {spawnSync}=require('node:child_process');
const browser=[process.env.ACW_TEST_BROWSER,
  path.join(process.env['ProgramFiles(x86)']||'','Microsoft/Edge/Application/msedge.exe'),
  path.join(process.env.ProgramFiles||'','Google/Chrome/Application/chrome.exe'),
  '/usr/bin/chromium','/usr/bin/chromium-browser','/usr/bin/google-chrome']
  .find(candidate=>candidate&&fs.existsSync(candidate));
if(!browser)throw Error('Set ACW_TEST_BROWSER to an installed Chromium browser executable.');
const ui=path.resolve(__dirname,'../ui');
const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'acw-workspace-ui-'));
const scenario=async function(){
  const check=(condition,message)=>{if(!condition)throw Error(message)};
  const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
  const now=Date.now(),date=age=>new Date(now-age).toISOString();
  const job=(id,status,age=null)=>({id,name:id,label:id,status,started:date(5*3600000),
    ended:age===null?null:date(age),output:[],events:[],artifacts:[]});
  try{
    const active=job('active','running');
    active.events=Array.from({length:30},(_,index)=>({id:`event-${index}`,time:date(index*1000),kind:'stage',message:`event ${index}`}));
    jobs=[active,job('queued','queued'),job('cancelling','cancelling'),
      job('recent','completed',10*60000),job('old','completed',16*60000),
      job('failure','failed',24*3600000),{...job('dismissed','failed',60000),failure_dismissed:true},
      job('cancelled-recent','cancelled',2*60000)];
    jobs.find(item=>item.id==='old').events=[{id:'old-event',time:date(0),kind:'completed',message:'HISTORICAL EVENT'}];
    jobs.find(item=>item.id==='failure').events=[{id:'failure-event',time:date(0),kind:'error',message:'OLD FAILURE EVENT'}];
    renderJobs();
    check(!document.getElementById('job-view-filter'),'Redundant view control is still present');
    check($('job-time-filter').value==='recent','Last 15 minutes is not the default');
    check($('jobs').querySelectorAll('.job').length===6,'Default active/recent/failure scope is wrong');
    check(!$('jobs').textContent.includes('dismissed'),'Dismissed failure shown in default scope');
    check($('jobs').textContent.includes('recent'),'Recent finish of a long job hidden');
    check($('live-events-list').querySelectorAll('.live-event').length===20,'Feed is not limited to 20 events');
    check(!$('live-events-list').textContent.includes('HISTORICAL EVENT'),'Historical completed job entered live feed');
    check(!$('live-events-list').textContent.includes('OLD FAILURE EVENT'),'Old pinned failure entered live feed');
    $('job-time-filter').value='all';$('job-time-filter').dispatchEvent(new Event('change'));
    check($('jobs').querySelectorAll('.job').length===8,'All jobs does not include historical or dismissed runs');
    check(!$('live-events-list').textContent.includes('HISTORICAL EVENT'),'All jobs changed live event eligibility');
    $('workspace-nav').click();
    let dismissed=null;
    window.pywebview={api:{dismiss_failure:async id=>{dismissed=id;return {ok:true}}}};
    $('jobs').querySelector('[data-dismiss-failure="failure"]').click();await tick();
    check(dismissed==='failure','Dismiss did not reach Python bridge');
    check($('jobs').querySelectorAll('.job').length===5,'Dismissed failure stayed visible');
    check(jobs.some(item=>item.id==='failure'),'Dismiss removed job history');
    $('job-time-filter').value='all';$('job-time-filter').dispatchEvent(new Event('change'));
    check($('jobs').querySelectorAll('.job').length===8,'Dismissed failure missing from All jobs');
    const feed=$('live-events-list'),first=feed.firstElementChild;
    $('live-events-pause').click();
    active.events.push({id:'paused-event',time:new Date(now+1000).toISOString(),kind:'stage',message:'arrived while paused'});
    renderJobs();
    check(feed.firstElementChild===first&&!feed.textContent.includes('arrived while paused'),'Paused feed changed');
    check($('live-events-pause').textContent==='Resume','Pause control did not switch');
    $('live-events-pause').click();
    check(feed.textContent.includes('arrived while paused'),'Resume did not show new events');
    const loggedEvents=active.events.length;
    $('live-events-pause').click();$('live-events-clear').click();$('live-events-pause').click();
    check(feed.querySelectorAll('.live-event').length===0,'Cleared events replayed on resume');
    check(active.events.length===loggedEvents,'Clear feed deleted job events');
    active.events.push({id:'same-second-new',time:new Date(now+1000).toISOString(),kind:'stage',message:'arrived while paused'});
    renderJobs();
    check(feed.querySelectorAll('.live-event').length===1,'New same-second event was incorrectly cleared');
    for(let index=0;index<25;index++)active.events.push({id:`scroll-${index}`,
      time:new Date(now+2000+index).toISOString(),kind:'stage',message:`Scroll event ${index}`});
    renderJobs();
    feed.style.height='160px';feed.style.maxHeight='160px';feed.style.flex='none';
    feed.scrollTop=120;
    const visibleTop=feed.getBoundingClientRect().top;
    const anchor=[...feed.children].find(node=>node.getBoundingClientRect().bottom>visibleTop);
    const anchorTop=anchor.getBoundingClientRect().top;
    active.events.push({id:'newest',time:new Date(now+3000).toISOString(),kind:'stage',message:'Newest event'});
    renderJobs();
    check(anchor.isConnected,'Visible event was unnecessarily replaced');
    check(Math.abs(anchor.getBoundingClientRect().top-anchorTop)<=2,'Live feed moved while reading');
    check(feed.querySelectorAll('.live-event').length===20,'Feed grew beyond 20 after updates');
    const layout=document.querySelector('[data-menu="layouts"]').closest('.menu-wrap');
    check(layout.nextElementSibling===$('workspace-nav')&&$('workspace-nav').nextElementSibling===$('manage-work-nav'),'Workspace button is in the wrong location');
    jobStatusFilter='failed';jobTimeFilter='hour';jobSearch='no match';
    $('job-search').value='no match';setPage('audit');$('workspace-nav').click();
    check(!$('execution-page').hidden&&$('log-audit-page').hidden,'Workspace navigation did not leave Audit');
    check(jobStatusFilter==='all'&&jobTimeFilter==='recent'&&jobSearch==='','Workspace did not restore default filters');
    check($('workspace-nav').getAttribute('aria-pressed')==='true','Workspace navigation state missing');
    prefs.layout='canvas';jobs.find(item=>item.id==='failure').failure_dismissed=false;apply();
    check($('jobs').querySelector('[data-dismiss-failure="failure"]'),'Canvas failure cannot be dismissed');
    jobs=jobs.filter(item=>item.status==='completed'&&item.id==='old');renderJobs();
    check($('jobs').textContent.includes('No active jobs'),'Empty focused workspace is unclear');
    document.documentElement.dataset.workspaceTest='passed';
  }catch(error){
    document.documentElement.dataset.workspaceTest='failed';
    const failure=document.createElement('pre');failure.id='test-failure';failure.textContent=error.stack;
    document.body.append(failure);
  }
};
try{
  let html=fs.readFileSync(path.join(ui,'index.html'),'utf8');
  html=html.replace('<head>',`<head><base href="${pathToFileURL(ui+path.sep).href}">`);
  html=html.replace('</body>',`<script>(${scenario.toString()})();</script></body>`);
  const fixture=path.join(temporary,'workspace.html');fs.writeFileSync(fixture,html);
  const result=spawnSync(browser,['--headless','--disable-gpu','--allow-file-access-from-files',
    `--user-data-dir=${path.join(temporary,'profile')}`,'--window-size=1100,900',
    '--virtual-time-budget=5000','--dump-dom',pathToFileURL(fixture).href],
    {encoding:'utf8',timeout:30000,windowsHide:true,maxBuffer:4*1024*1024});
  if(result.error)throw result.error;
  if(result.status!==0||!result.stdout.includes('data-workspace-test="passed"')){
    const failure=result.stdout.match(/<pre id="test-failure">([\s\S]*?)<\/pre>/);
    throw Error(failure?failure[1]:result.stderr||'Browser test did not complete.');
  }
  console.log('Workspace filtering, failures, live feed and navigation checks passed.');
}finally{
  const target=path.resolve(temporary);
  if(path.dirname(target)!==path.resolve(os.tmpdir())||!path.basename(target).startsWith('acw-workspace-ui-'))
    throw Error('Unexpected temporary directory; refusing cleanup.');
  fs.rmSync(target,{recursive:true,force:true,maxRetries:5,retryDelay:100});
}
