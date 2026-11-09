'use strict';
// Run with node tests/test_automation_ui.js; uses the actual UI and a mock bridge.
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {pathToFileURL}=require('node:url');
const {spawnSync}=require('node:child_process');
const candidates=[process.env.ACW_TEST_BROWSER,
  path.join(process.env['ProgramFiles(x86)']||'','Microsoft/Edge/Application/msedge.exe'),
  path.join(process.env.ProgramFiles||'','Google/Chrome/Application/chrome.exe'),
  '/usr/bin/chromium','/usr/bin/chromium-browser','/usr/bin/google-chrome'];
const browser=candidates.find(candidate=>candidate&&fs.existsSync(candidate));
if(!browser)throw Error('Set ACW_TEST_BROWSER to an installed Chromium browser executable.');
const ui=path.resolve(__dirname,'../ui');
const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'acw-automation-ui-'));
const scenario=async function(){
  const check=(condition,message)=>{if(!condition)throw Error(message)};
  const settle=async()=>{for(let i=0;i<12;i++)await new Promise(resolve=>setTimeout(resolve,0))};
  const state={groups:[],schedules:[],runs:[],error:null};
  const scripts=[{name:'first',label:'First script'},{name:'second',label:'Second script'}];
  let capturedSchedule=null;
  try{
    window.confirm=()=>true;
    window.pywebview={api:{
      catalog:async()=>scripts,registered_scripts:async()=>[],
      automation_state:async()=>JSON.parse(JSON.stringify(state)),
      save_group:async(name,members,mode,id)=>{
        if(members.length<2)throw Error('Select at least two scripts.');
        const group={id:id||'group-1',name,scripts:[...members],mode,available:true};
        state.groups=[group];return group;
      },
      start_group:async id=>{
        state.runs=[{id:'run-1',group_id:id,name:state.groups[0].name,mode:'sequential',
          scripts:['second','first'],job_ids:['job-1'],status:'running',started:new Date().toISOString()}];
        return state.runs[0];
      },
      cancel_group:async()=>{state.runs[0].status='cancelled'},
      create_schedule:async(kind,target,start,repeat)=>{
        capturedSchedule={kind,target,start,repeat};
        state.schedules.push({id:'schedule-'+state.schedules.length,target_kind:kind,target_id:target,
          target_name:kind==='group'?state.groups[0].name:'First script',repeat,next_run:start,
          enabled:true,last_result:'Waiting'});
      },
      set_schedule_enabled:async(id,enabled)=>{state.schedules.find(item=>item.id===id).enabled=enabled},
      delete_schedule:async id=>{state.schedules=state.schedules.filter(item=>item.id!==id)},
      delete_group:async id=>{state.groups=[];state.schedules.forEach(item=>{
        if(item.target_kind==='group'&&item.target_id===id)item.enabled=false;
      })},
    }};
    bridgeReady=true;setPage('manage');await settle();
    check($('group-script-picker').querySelectorAll('input').length===2,'Script choices missing');
    $('group-name').value='Reports';
    $('group-script-picker').querySelector('[data-group-script="first"]').click();
    $('group-form').requestSubmit();await settle();
    check($('automation-message').textContent.includes('at least two'),'Group validation error missing');
    $('group-script-picker').querySelector('[data-group-script="second"]').click();
    $('group-order').querySelector('[data-direction="1"]').click();
    $('group-mode').value='parallel';$('group-form').requestSubmit();await settle();
    check(state.groups[0].mode==='parallel','Parallel mode was not saved');
    check(state.groups[0].scripts.join(',')==='second,first','Group order was not saved');
    check($('groups-list').textContent.includes('Reports'),'Saved group not rendered');
    $('groups-list').querySelector('[data-auto-action="edit-group"]').click();
    $('group-name').value='Updated reports';$('group-mode').value='sequential';
    $('group-form').requestSubmit();await settle();
    check(state.groups[0].id==='group-1'&&state.groups[0].name==='Updated reports','Group edit lost identity');
    check(state.groups[0].mode==='sequential','Sequential edit not saved');
    $('groups-list').querySelector('[data-auto-action="run-group"]').click();await settle();
    check($('groups-list').querySelector('[data-auto-action="run-group"]').disabled,'Active group allowed duplicate start');
    check($('group-runs-list').textContent.includes('running'),'Group run status missing');
    $('group-runs-list').querySelector('[data-auto-action="stop-group"]').click();await settle();
    check(state.runs[0].status==='cancelled','Stop group did not call bridge');
    const future=new Date(Date.now()+3600000);
    const localInput=new Date(future.getTime()-future.getTimezoneOffset()*60000).toISOString().slice(0,16);
    for(const [kind,target,repeat] of [['script','first','once'],['group','group-1','hourly'],['script','first','daily']]){
      $('schedule-target').value=JSON.stringify([kind,target]);
      $('schedule-start').value=localInput;$('schedule-repeat').value=repeat;
      $('schedule-form').requestSubmit();await settle();
      check(capturedSchedule.kind===kind&&capturedSchedule.target===target,'Wrong schedule target');
      check(capturedSchedule.repeat===repeat,'Wrong repeat interval');
      check(capturedSchedule.start.endsWith('Z')&&Date.parse(capturedSchedule.start)>Date.now(),'Local date not converted to future UTC');
    }
    check(state.schedules.length===3,'Schedules missing');
    $('schedules-list').querySelector('[data-auto-action="pause-schedule"]').click();await settle();
    check(!state.schedules[0].enabled,'Pause did not persist');
    $('schedules-list').querySelector('[data-auto-action="resume-schedule"]').click();await settle();
    check(state.schedules[0].enabled,'Resume did not persist');
    $('schedules-list').querySelector('[data-auto-action="delete-schedule"]').click();await settle();
    check(state.schedules.length===2,'Schedule deletion failed');
    $('groups-list').querySelector('[data-auto-action="delete-group"]').click();await settle();
    check(state.groups.length===0,'Group deletion failed');
    check(!state.schedules.find(item=>item.target_kind==='group').enabled,'Deleted group schedule was left enabled');
    document.documentElement.dataset.automationTest='passed';
  }catch(error){
    document.documentElement.dataset.automationTest='failed';
    const failure=document.createElement('pre');failure.id='test-failure';failure.textContent=error.stack;
    document.body.append(failure);
  }
};
try{
  let html=fs.readFileSync(path.join(ui,'index.html'),'utf8');
  html=html.replace('<head>',`<head><base href="${pathToFileURL(ui+path.sep).href}">`);
  html=html.replace('</body>',`<script>(${scenario.toString()})();</script></body>`);
  const fixture=path.join(temporary,'automation.html');fs.writeFileSync(fixture,html);
  const result=spawnSync(browser,['--headless','--disable-gpu','--allow-file-access-from-files',
    `--user-data-dir=${path.join(temporary,'profile')}`,'--window-size=1100,900',
    '--virtual-time-budget=10000','--dump-dom',pathToFileURL(fixture).href],
    {encoding:'utf8',timeout:30000,windowsHide:true,maxBuffer:4*1024*1024});
  if(result.error)throw result.error;
  if(result.status!==0||!result.stdout.includes('data-automation-test="passed"')){
    const failure=result.stdout.match(/<pre id="test-failure">([\s\S]*?)<\/pre>/);
    throw Error(failure?failure[1]:result.stderr||'Browser test did not complete.');
  }
  console.log('Group and schedule browser checks passed.');
}finally{
  const target=path.resolve(temporary);
  if(path.dirname(target)!==path.resolve(os.tmpdir())||!path.basename(target).startsWith('acw-automation-ui-'))
    throw Error('Unexpected temporary directory; refusing cleanup.');
  fs.rmSync(target,{recursive:true,force:true,maxRetries:5,retryDelay:100});
}
