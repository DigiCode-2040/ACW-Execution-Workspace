'use strict';
let automationState={groups:[],schedules:[],runs:[]},automationLoading=false;
let automationRefreshPending=false;
let editingGroupId=null,selectedGroupScripts=[],automationCatalog=[],automationSignature='';
const localTime=value=>value?new Date(value).toLocaleString():'—';

function automationMessage(message,error=false){
  $('automation-message').textContent=message;
  $('automation-message').classList.toggle('error',error);
}
function renderGroupSelection(){
  const focusedScript=document.activeElement?.dataset.groupScript;
  const choices=[...new Set([...automationCatalog.map(item=>item.name),...selectedGroupScripts])];
  const labels=new Map(automationCatalog.map(item=>[item.name,item.label]));
  $('group-script-picker').innerHTML=choices.map(name=>
    `<label class="group-script-choice"><input type="checkbox" data-group-script="${escapeHTML(name)}" ${selectedGroupScripts.includes(name)?'checked':''}><span>${escapeHTML(labels.get(name)||name)}${labels.has(name)?'':' (unavailable)'}</span></label>`
  ).join('')||'<p class="subtle">No available scripts. Register scripts above or reload the catalog.</p>';
  $('group-order').innerHTML=selectedGroupScripts.map((name,index)=>
    `<li>${escapeHTML(labels.get(name)||name)} <button type="button" data-move-script="${index}" data-direction="-1" aria-label="Move ${escapeHTML(name)} up" ${index===0?'disabled':''}>↑</button><button type="button" data-move-script="${index}" data-direction="1" aria-label="Move ${escapeHTML(name)} down" ${index===selectedGroupScripts.length-1?'disabled':''}>↓</button></li>`
  ).join('')||'<li class="subtle">Choose at least two scripts.</li>';
  if(focusedScript)$('group-script-picker').querySelector(`[data-group-script="${CSS.escape(focusedScript)}"]`)?.focus({preventScroll:true});
}
function renderScheduleTargets(){
  const target=$('schedule-target'),selected=target.value;
  target.replaceChildren(new Option('Choose a script or group',''));
  for(const [label,kind,items] of [['Scripts','script',automationCatalog],['Groups','group',automationState.groups]]){
    const section=document.createElement('optgroup');section.label=label;
    for(const item of items){
      const option=new Option(item.label||item.name,JSON.stringify([kind,item.id||item.name]));
      option.disabled=item.available===false;section.append(option);
    }
    target.append(section);
  }
  target.value=selected;
}
function renderAutomation(){
  const page=$('manage-work-page'),top=page.scrollTop;
  const button=(action,id,label,disabled=false)=>`<button type="button" data-auto-action="${action}" data-auto-id="${escapeHTML(id)}" ${disabled?'disabled':''}>${label}</button>`;
  const labels=new Map(automationCatalog.map(item=>[item.name,item.label]));
  $('groups-list').innerHTML=automationState.groups.map(group=>{
    const active=automationState.runs.some(run=>run.group_id===group.id&&run.status==='running');
    return `<article class="automation-card"><div><h3>${escapeHTML(group.name)}</h3><p>${group.mode==='sequential'?'Sequential':'Parallel'} · ${group.scripts.length} scripts</p><p>${group.scripts.map(name=>escapeHTML(labels.get(name)||name)).join(group.mode==='sequential'?' → ':' + ')}</p>${group.available?'':`<p>${escapeHTML(group.reason)}</p>`}</div><div class="automation-actions">${button('run-group',group.id,active?'Running…':'Run group',active||!group.available)}${button('edit-group',group.id,'Edit')}${button('delete-group',group.id,'Delete',active)}</div></article>`;
  }).join('')||'<p class="subtle">No saved groups yet.</p>';
  $('group-runs-list').innerHTML=automationState.runs.slice(0,12).map(run=>
    `<article class="automation-card"><div><h3>${escapeHTML(run.name)} · ${escapeHTML(run.status)}</h3><p>${run.job_ids.length} of ${run.scripts.length} scripts submitted · ${escapeHTML(localTime(run.started))}</p>${run.message?`<p>${escapeHTML(run.message)}</p>`:''}<p>${run.job_ids.map(id=>`<button type="button" data-auto-action="audit-job" data-auto-id="${escapeHTML(id)}">Audit ${escapeHTML(id)}</button>`).join(' ')}</p></div><div class="automation-actions">${run.status==='running'?button('stop-group',run.id,'Stop run'):''}</div></article>`
  ).join('')||'<p class="subtle">Group runs appear here while the app is open. Individual jobs remain available in Log Audit.</p>';
  const repeats={once:'Once',hourly:'Every 1 hour',daily:'Every 24 hours'};
  $('schedules-list').innerHTML=automationState.schedules.map(item=>{
    const expiredOnce=item.repeat==='once'&&Date.parse(item.next_run)<=Date.now();
    return `<article class="automation-card"><div><h3>${escapeHTML(item.target_name)} · ${item.enabled?'Active':'Inactive'}</h3><p>${repeats[item.repeat]} · ${item.enabled?'Next run':'Scheduled time'}: ${escapeHTML(localTime(item.next_run))}</p><p>${escapeHTML(item.last_result||'Waiting')}${item.last_run?` · ${escapeHTML(localTime(item.last_run))}`:''}</p></div><div class="automation-actions">${button(item.enabled?'pause-schedule':'resume-schedule',item.id,item.enabled?'Pause':'Resume',!item.enabled&&expiredOnce)}${button('delete-schedule',item.id,'Delete')}</div></article>`;
  }).join('')||'<p class="subtle">No schedules yet.</p>';
  renderScheduleTargets();
  page.scrollTop=top;
}
async function loadAutomation(refreshChoices=false){
  if(!bridgeReady)return;
  if(automationLoading){automationRefreshPending=automationRefreshPending||refreshChoices;return}
  automationLoading=true;
  try{
    const state=await window.pywebview.api.automation_state();
    if(refreshChoices){
      automationCatalog=await window.pywebview.api.catalog();
      renderGroupSelection();
    }
    const signature=JSON.stringify(state);
    automationState=state;
    if(refreshChoices||signature!==automationSignature){renderAutomation();automationSignature=signature}
    if(state.error)automationMessage(state.error,true);
  }catch(error){automationMessage(error.message||String(error),true)}
  finally{
    automationLoading=false;
    if(automationRefreshPending){automationRefreshPending=false;loadAutomation(true)}
  }
}
function resetGroupForm(){
  editingGroupId=null;selectedGroupScripts=[];$('group-form').reset();
  $('save-group').textContent='Create group';$('reset-group').hidden=true;
  renderGroupSelection();
}
async function automationAction(button,action){
  button.disabled=true;
  try{await action();await loadAutomation(true)}
  catch(error){automationMessage(error.message||String(error),true)}
  finally{if(button.isConnected)button.disabled=false}
}
function setupAutomation(){
  $('group-script-picker').onchange=event=>{
    const input=event.target.closest('[data-group-script]');if(!input)return;
    const name=input.dataset.groupScript;
    if(input.checked)selectedGroupScripts.push(name);
    else selectedGroupScripts=selectedGroupScripts.filter(script=>script!==name);
    renderGroupSelection();
  };
  $('group-order').onclick=event=>{
    const button=event.target.closest('[data-move-script]');if(!button)return;
    const index=Number(button.dataset.moveScript),next=index+Number(button.dataset.direction);
    if(next<0||next>=selectedGroupScripts.length)return;
    [selectedGroupScripts[index],selectedGroupScripts[next]]=[selectedGroupScripts[next],selectedGroupScripts[index]];
    renderGroupSelection();
  };
  $('reset-group').onclick=resetGroupForm;
  $('group-form').onsubmit=event=>{
    event.preventDefault();
    if(!$('group-form').reportValidity())return;
    automationAction($('save-group'),async()=>{
      await window.pywebview.api.save_group($('group-name').value.trim(),selectedGroupScripts,$('group-mode').value,editingGroupId);
      resetGroupForm();automationMessage('Group saved. Run it below or add a schedule.');
    });
  };
  $('schedule-form').onsubmit=event=>{
    event.preventDefault();
    if(!$('schedule-form').reportValidity())return;
    automationAction($('save-schedule'),async()=>{
      const [kind,target]=JSON.parse($('schedule-target').value);
      const date=new Date($('schedule-start').value);
      if(!Number.isFinite(date.getTime())||date.getTime()<=Date.now())throw Error('Choose a start time in the future.');
      await window.pywebview.api.create_schedule(kind,target,date.toISOString(),$('schedule-repeat').value);
      $('schedule-form').reset();automationMessage('Schedule saved. Keep ACW open for it to run.');
    });
  };
  for(const id of ['groups-list','group-runs-list','schedules-list'])$(id).onclick=event=>{
    const button=event.target.closest('[data-auto-action]');if(!button)return;
    const action=button.dataset.autoAction,target=button.dataset.autoId;
    if(action==='edit-group'){
      const group=automationState.groups.find(item=>item.id===target);if(!group)return;
      editingGroupId=target;selectedGroupScripts=[...group.scripts];
      $('group-name').value=group.name;$('group-mode').value=group.mode;
      $('save-group').textContent='Save changes';$('reset-group').hidden=false;
      renderGroupSelection();$('group-form').scrollIntoView({block:'start'});return;
    }
    if(action==='audit-job'){openJobAudit(target);return}
    if(action.startsWith('delete-')&&!confirm('Delete this '+(action==='delete-group'?'group? Its schedules will be paused.':'schedule?')))return;
    automationAction(button,async()=>{
      const api=window.pywebview.api;
      if(action==='run-group')await api.start_group(target);
      else if(action==='stop-group')await api.cancel_group(target);
      else if(action==='delete-group'){await api.delete_group(target);if(editingGroupId===target)resetGroupForm()}
      else if(action==='delete-schedule')await api.delete_schedule(target);
      else await api.set_schedule_enabled(target,action==='resume-schedule');
      automationMessage(action==='run-group'?'Group started. Jobs also appear in the workspace.':'Updated.');
    });
  };
  window.addEventListener('pywebviewready',()=>{if(!$('manage-work-page').hidden)loadAutomation(true)});
}
setupAutomation();
