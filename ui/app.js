'use strict';
const defaults={theme:'cyan',layout:'connected',historyCount:5,opacity:78,glow:true,compact:false,monitors:['cpu','systemmem','taskmem','started','ended','runtime','events','quality','output']};
let prefs={...defaults}, jobs=[], history=[], catalogEntries=[], bridgeReady=false, busy=false;
let commandHistory=[],commandHistoryIndex=0,commandDraft='',jobStatusFilter='all',jobSearch='';
try{prefs={...defaults,...JSON.parse(localStorage.getItem('acw.preferences')||'{}')}}catch{}
const $=id=>document.getElementById(id);
const escapeHTML=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const monLabels={cpu:'CPU utilization',systemmem:'System memory*',taskmem:'Task memory',started:'Started',ended:'Ended',runtime:'Runtime',events:'Events',quality:'Quality',output:'Output'};
const palettes={cyan:['#4ee6ef','#071019','#112736'],violet:['#bca3ff','#100d1c','#29203b'],green:['#78e4ac','#091712','#16362b'],amber:['#ffc36c','#1a130b','#3a2b1b']};
function save(){try{localStorage.setItem('acw.preferences',JSON.stringify(prefs))}catch{}}
function apply(){const [accent,bg,surface]=palettes[prefs.theme]||palettes.cyan;const r=document.documentElement.style;r.setProperty('--accent',accent);r.setProperty('--bg',bg);r.setProperty('--surface',surface);r.setProperty('--glow',prefs.glow?'1':'0');r.setProperty('--glass',prefs.opacity+'%');r.setProperty('--surface',`color-mix(in srgb, ${surface} ${prefs.opacity}%, transparent)`);document.body.classList.toggle('compact',prefs.compact);$('jobs').className='jobs '+prefs.layout;$('theme').value=prefs.theme;$('history-count').value=prefs.historyCount;$('hist-val').textContent=prefs.historyCount;$('opacity').value=prefs.opacity;$('opacity-val').textContent=prefs.opacity+'%';$('glow').checked=prefs.glow;$('compact').checked=prefs.compact;renderHistory();renderJobs();save()}
function log(s){history.push({time:new Date().toLocaleTimeString(),text:s});history=history.slice(-200);renderHistory()}
function renderHistory(){$('history').innerHTML=history.slice(-prefs.historyCount).map(x=>`<div class="entry"><time>${escapeHTML(x.time)}</time><span>${escapeHTML(x.text)}</span></div>`).join('')||'<span class="subtle">Ready. Type help for commands.</span>'}
function metric(label,value){return `<div class="metric"><span>${escapeHTML(label)}</span><span>${escapeHTML(value)}</span></div>`}
function renderJobs(){
  const el=$('jobs');
  el.className='jobs '+prefs.layout;
  const matches=jobs.filter(job=>{
    const statusMatches=jobStatusFilter==='all'||(jobStatusFilter==='active'?['queued','running','cancelling'].includes(job.status):job.status===jobStatusFilter);
    const searchable=[job.label,job.name,job.id,job.status,job.stage,...(job.output||[]),...(job.events||[]).map(event=>event.message),...(job.artifacts||[])].join(' ').toLowerCase();
    return statusMatches&&searchable.includes(jobSearch.toLowerCase());
  });
  $('job-filter-count').textContent=`${matches.length} of ${jobs.length} jobs`;
  if(!jobs.length){el.innerHTML='<div class="empty">No executions yet. Select a script above or type run &lt;command&gt;.</div>';return}
  if(!matches.length){el.innerHTML='<div class="empty">No jobs match the current filters.</div>';return}
  el.innerHTML=matches.map(j=>{
    const m=prefs.monitors,show=(k,label,val)=>m.includes(k)?metric(label,val):'';
    const output=(j.output||[]).slice(-6).map(escapeHTML).join('\n');
    const events=(j.events||[]).slice(-6).map(e=>escapeHTML(e.kind+': '+e.message)).join('\n');
    const artifacts=(j.artifacts||[]).map(artifact=>`<div class="artifact-row"><span title="${escapeHTML(artifact)}">${escapeHTML(artifact)}</span><button class="artifact-action" aria-label="Open artifact ${escapeHTML(artifact)}" data-action="open" data-job-id="${escapeHTML(j.id)}" data-artifact="${escapeHTML(artifact)}">Open</button><button class="artifact-action" aria-label="Reveal artifact ${escapeHTML(artifact)}" data-action="reveal" data-job-id="${escapeHTML(j.id)}" data-artifact="${escapeHTML(artifact)}">Reveal</button></div>`).join('');
    return `<article class="job"><div class="job-head"><div><b>${escapeHTML(j.label)}</b> <span class="meta">#${escapeHTML(j.id)}</span></div><div><span class="pill ${escapeHTML(j.status)}">${escapeHTML(j.status)}</span> ${j.status==='running'?`<button class="cancel" data-id="${escapeHTML(j.id)}">Stop</button>`:''}</div></div><div class="panels"><div class="pane"><h3>TASK / OVERVIEW</h3>${metric('Script',j.name)}${show('started','Started',j.started||'—')}${show('ended','Ended',j.ended||'—')}${show('runtime','Runtime',j.runtime+' s')}${show('cpu','CPU',j.cpu+' %')}${show('taskmem','Task memory',j.memory_mb+' MB')}${show('systemmem','System memory','Not collected')}</div><div class="pane"><h3>WORKING PARTS</h3>${metric('Stage',j.stage)}${metric('Progress',j.progress+'%')}<div class="bar"><i style="width:${Math.max(0,Math.min(100,Number(j.progress)||0))}%"></i></div>${m.includes('events')?`<div class="logs">${events||'Waiting for events…'}</div>`:''}</div><div class="pane"><h3>OUTPUT / RESULTS</h3>${show('quality','Quality',j.quality==null?'Not evaluated':j.quality+'%')}${metric('Exit code',j.exit_code??'—')}${m.includes('output')?`<div class="logs">${output||'Awaiting output…'}</div>`:''}${artifacts?`<div class="artifact-list"><h4>ARTIFACTS</h4>${artifacts}</div>`:''}</div></div></article>`;
  }).join('');
  el.querySelectorAll('.cancel').forEach(button=>button.onclick=async()=>{
    try{await window.pywebview.api.cancel_job(button.dataset.id);log('Cancellation requested: '+button.dataset.id)}
    catch(error){log('Error: '+(error.message||error))}
  });
  el.querySelectorAll('.artifact-action').forEach(button=>button.onclick=async()=>{
    try{
      const method=button.dataset.action==='open'?'open_artifact':'reveal_artifact';
      await window.pywebview.api[method](button.dataset.jobId,button.dataset.artifact);
      log(`${button.dataset.action==='open'?'Opened':'Revealed'} artifact: ${button.dataset.artifact}`);
    }catch(error){log('Artifact action failed: '+(error.message||error))}
  });
}
function closeMenus(){
  document.querySelectorAll('.popup').forEach(p=>{p.hidden=true});
  document.querySelectorAll('[data-menu]').forEach(button=>button.setAttribute('aria-expanded','false'));
}
function setupMenus(){
  document.querySelectorAll('[data-menu]').forEach(button=>{
    const popup=$(button.dataset.menu);
    button.setAttribute('aria-controls',popup.id);
    button.setAttribute('aria-expanded','false');
    popup.setAttribute('role','group');
    popup.setAttribute('aria-label',button.getAttribute('aria-label')||button.textContent.replace(/[▾☷▦]/g,'').trim());
    button.onclick=()=>{
      const opening=popup.hidden;
      closeMenus();
      if(opening){
        popup.hidden=false;
        button.setAttribute('aria-expanded','true');
        popup.querySelector('input,select,button:not([disabled])')?.focus();
      }
    };
  });
  document.addEventListener('click',event=>{
    if(!event.target.closest('.menu-wrap'))closeMenus();
  });
  document.addEventListener('keydown',event=>{
    if(event.key==='Escape'&&document.querySelector('.popup:not([hidden])')){
      const focusedMenu=document.activeElement.closest('.menu-wrap')?.querySelector('[data-menu]');
      closeMenus();
      focusedMenu?.focus();
    }
  });
  $('monitor-options').innerHTML=Object.entries(monLabels).map(([k,v])=>`<label><input type="checkbox" data-monitor="${k}" ${prefs.monitors.includes(k)?'checked':''}>${escapeHTML(v)}</label>`).join('');
  document.querySelectorAll('[data-monitor]').forEach(c=>c.onchange=()=>{prefs.monitors=Array.from(document.querySelectorAll('[data-monitor]:checked')).map(x=>x.dataset.monitor);apply()});
  $('layout-options').innerHTML=['connected','flow','layered','collapsed'].map(k=>`<label><input type="radio" name="layout" value="${k}" ${prefs.layout===k?'checked':''}>${k[0].toUpperCase()+k.slice(1)}</label>`).join('');
  document.querySelectorAll('[name=layout]').forEach(c=>c.onchange=()=>{prefs.layout=c.value;apply()});
  $('theme').onchange=e=>{prefs.theme=e.target.value;apply()};
  $('history-count').oninput=e=>{prefs.historyCount=Number(e.target.value);apply()};
  $('opacity').oninput=e=>{prefs.opacity=Number(e.target.value);apply()};
  $('glow').onchange=e=>{prefs.glow=e.target.checked;apply()};
  $('compact').onchange=e=>{prefs.compact=e.target.checked;apply()};
}
function parseCommandLine(line){
  const tokens=[];
  let token='',quote=null,started=false;
  for(let i=0;i<line.length;i++){
    const char=line[i];
    if(quote){
      if(char===quote){quote=null;started=true}
      else if(char==='\\'&&(line[i+1]===quote||line[i+1]==='\\'))token+=line[++i];
      else token+=char;
    }else if(char==='"'||char==="'"){quote=char;started=true}
    else if(/\s/.test(char)){
      if(started){tokens.push(token);token='';started=false}
    }else{token+=char;started=true}
  }
  if(quote)throw Error('Unclosed quote in command.');
  if(started)tokens.push(token);
  return tokens;
}
async function command(line){
  if(!line.trim())return;
  log('> '+line);
  try{
    const [verb,...args]=parseCommandLine(line);
    if(verb==='help')log('Commands: help, catalog [reload], run <command> [arguments...], list, layout connected|flow|layered|collapsed, stop <id>, clear');
    else if(verb==='catalog'){
      if(args.length>1||(args.length===1&&args[0]!=='reload'))throw Error('Usage: catalog [reload]');
      if(args.includes('reload'))await refreshCatalog(true);
      else await refreshCatalog();
      const catalogText=catalogEntries.map(entry=>entry.name+': '+entry.description).join(' | ')||'No runnable scripts found';
      log((args.includes('reload')?'Catalog reloaded: ':'')+catalogText);
    }else if(verb==='run'){
      const name=args.shift();
      if(!name)throw Error('Specify a command shown by catalog.');
      const result=await window.pywebview.api.start_job(name,args);
      log('Job queued: '+result.id);
    }else if(verb==='stop'){
      if(!args[0])throw Error('Specify a job ID.');
      await window.pywebview.api.cancel_job(args[0]);
      log('Cancellation requested');
    }else if(verb==='list')log(jobs.map(job=>job.id+':'+job.status).join(' | ')||'No jobs');
    else if(verb==='layout'){
      if(!['connected','flow','layered','collapsed'].includes(args[0]))throw Error('Unknown layout');
      prefs.layout=args[0];apply();log('Layout: '+args[0]);
    }else if(verb==='clear'){history=[];renderHistory()}
    else log('Unknown command. Type help.');
  }catch(error){log('Error: '+(error.message||error))}
}
async function refreshCatalog(reload=false){
  const previousSelection=$('run-script').value;
  catalogEntries=reload?await window.pywebview.api.reload_catalog():await window.pywebview.api.catalog();
  const select=$('run-script');
  select.replaceChildren(...catalogEntries.map(entry=>{
    const option=document.createElement('option');
    option.value=entry.name;option.textContent=`${entry.label} (${entry.name})`;option.title=entry.description;
    return option;
  }));
  if(catalogEntries.some(entry=>entry.name===previousSelection))select.value=previousSelection;
  select.disabled=!catalogEntries.length;$('run-demo').disabled=!catalogEntries.length;
  $('command').placeholder=catalogEntries.length?`help · catalog reload · run ${catalogEntries[0].name} · list`:'No runnable scripts found';
  const selected=catalogEntries.find(entry=>entry.name===select.value);
  $('catalog-description').textContent=selected?.description||'Add scripts with ACW metadata to the scripts folder';
  select.onchange=()=>{$('catalog-description').textContent=catalogEntries.find(entry=>entry.name===select.value)?.description||''};
  $('jobs').querySelector('.empty')?.replaceChildren(document.createTextNode('No executions yet. Select a script above or type run <command>.'));
}
const jobFilters=document.createElement('div');jobFilters.id='job-filters';jobFilters.innerHTML='<select id="job-status-filter" aria-label="Filter jobs by status"><option value="all">All statuses</option><option value="active">Active</option><option value="queued">Queued</option><option value="running">Running</option><option value="cancelling">Cancelling</option><option value="completed">Completed</option><option value="failed">Failed</option><option value="cancelled">Cancelled</option></select><input id="job-search" type="search" aria-label="Search jobs" placeholder="Search jobs, output, and artifacts"><span id="job-filter-count" class="subtle" aria-live="polite">0 of 0 jobs</span>';document.querySelector('.workspace-head').after(jobFilters);$('job-status-filter').onchange=e=>{jobStatusFilter=e.target.value;renderJobs()};$('job-search').oninput=e=>{jobSearch=e.target.value.trim();renderJobs()};
const scriptSelect=document.createElement('select');scriptSelect.id='run-script';scriptSelect.disabled=true;scriptSelect.setAttribute('aria-label','Select an approved script');const runControls=document.createElement('div');runControls.className='run-controls';runControls.append(scriptSelect,$('run-demo'));document.querySelector('.workspace-head').append(runControls);const catalogDescription=document.createElement('span');catalogDescription.id='catalog-description';catalogDescription.className='subtle';document.querySelector('.workspace-head').firstElementChild.append(catalogDescription);$('run-demo').textContent='▶ Run selected';$('command').setAttribute('aria-label','Command terminal');$('command').title='Enter runs a command. Shift+Enter inserts a new line. Use Up and Down to browse command history.';document.querySelector('.hint').textContent='Enter run · Shift+Enter newline';
$('command').addEventListener('input',e=>{e.target.style.height='29px';e.target.style.height=Math.min(e.target.scrollHeight,180)+'px'});
$('command').addEventListener('keydown',e=>{
  const input=e.target;
  if((e.key==='ArrowUp'||e.key==='ArrowDown')&&!e.altKey&&!e.ctrlKey&&!e.metaKey){
    const before=input.value.slice(0,input.selectionStart),after=input.value.slice(input.selectionEnd);
    const atBoundary=e.key==='ArrowUp'?!before.includes('\n'):!after.includes('\n');
    if(atBoundary&&commandHistory.length){
      e.preventDefault();
      if(e.key==='ArrowUp'){
        if(commandHistoryIndex===commandHistory.length)commandDraft=input.value;
        commandHistoryIndex=Math.max(0,commandHistoryIndex-1);
        input.value=commandHistory[commandHistoryIndex];
        input.setSelectionRange(input.value.length,input.value.length);
      }else if(commandHistoryIndex<commandHistory.length-1){
        commandHistoryIndex++;
        input.value=commandHistory[commandHistoryIndex];
        input.setSelectionRange(input.value.length,input.value.length);
      }else if(commandHistoryIndex===commandHistory.length-1){
        commandHistoryIndex=commandHistory.length;
        input.value=commandDraft;
        input.setSelectionRange(input.value.length,input.value.length);
      }
      input.style.height='29px';input.style.height=Math.min(input.scrollHeight,180)+'px';
      return;
    }
  }
  if(e.key==='Enter'&&!e.shiftKey){
    e.preventDefault();
    const line=input.value;
    input.value='';input.style.height='29px';
    if(line.trim()&&commandHistory.at(-1)!==line){commandHistory.push(line);commandHistory=commandHistory.slice(-100)}
    commandHistoryIndex=commandHistory.length;commandDraft='';
    if(!bridgeReady){log('Python bridge not available. Launch with python main.py');return}
    command(line);
  }
});
$('run-demo').onclick=()=>bridgeReady&&scriptSelect.value?command('run '+scriptSelect.value):log(bridgeReady?'No runnable scripts found. Add ACW metadata to a script.':'Launch with python main.py');
function refreshClock(){$('clock').textContent=new Date().toLocaleString()};refreshClock();setInterval(refreshClock,1000);setupMenus();apply();
async function poll(){if(!bridgeReady||busy)return;busy=true;try{jobs=await window.pywebview.api.jobs();renderJobs();if(!jobs.length)$('jobs').querySelector('.empty').textContent='No executions yet. Select a script above or type run <command>.';$('connection').textContent=`Local Python connected · ${jobs.filter(j=>j.status==='running').length} running · ${jobs.length} total` }catch(e){$('connection').textContent='Bridge error: '+e}finally{busy=false}}
window.addEventListener('pywebviewready',()=>{bridgeReady=true;log('Python bridge connected');refreshCatalog().catch(e=>log('Catalog error: '+(e.message||e)));poll();setInterval(poll,850)});
setTimeout(()=>{if(!bridgeReady)$('connection').textContent='Open this file through python main.py (not a regular browser tab)'},2500);

const backgroundStyle=document.createElement('style');
backgroundStyle.textContent=`
  #ambient-background{position:fixed;inset:0;z-index:0;width:100%;height:100%;pointer-events:none}
  body::before{content:"";position:fixed;inset:0;z-index:0;pointer-events:none;background-image:linear-gradient(color-mix(in srgb,var(--accent) 4.5%,transparent) 1px,transparent 1px),linear-gradient(90deg,color-mix(in srgb,var(--accent) 4.5%,transparent) 1px,transparent 1px);background-size:32px 32px}
  body>header{z-index:10}
  body>main{position:relative;z-index:1}
  .run-controls{display:flex;align-items:center;gap:8px;margin-left:auto}
  .run-controls select{min-width:190px;max-width:320px}
  #job-filters{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:0 0 14px;padding:11px 14px;border:1px solid var(--border);border-radius:var(--radius);background:var(--surface)}
  #job-filters input{flex:1;min-width:180px;background:#0b1b27;color:var(--text);border:1px solid var(--border);border-radius:8px;padding:7px 10px}
  #job-filter-count{margin-left:auto}
  .artifact-list{margin-top:12px}.artifact-list h4{margin:0 0 6px;color:var(--accent);font-size:11px;letter-spacing:.1em}
  .artifact-row{display:flex;align-items:center;gap:5px;padding:4px 0;border-bottom:1px solid #ffffff0c}
  .artifact-row span{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--muted);font:11px ui-monospace,Consolas,monospace}
  .artifact-row button{padding:3px 7px;font-size:11px}
  @media(max-width:600px){.workspace-head{align-items:flex-start;flex-wrap:wrap}.run-controls{width:100%}.run-controls select{flex:1;min-width:0}}
  @media(max-width:600px){#job-filters input{min-width:100%}#job-filter-count{margin-left:0}}
`;
document.head.append(backgroundStyle);

const ambientCanvas=document.createElement('canvas');
ambientCanvas.id='ambient-background';
ambientCanvas.setAttribute('aria-hidden','true');
document.body.prepend(ambientCanvas);
const ambientContext=ambientCanvas.getContext('2d');
if(!ambientContext)throw new Error('Unable to initialize the ambient background canvas');

const reducedMotion=window.matchMedia('(prefers-reduced-motion: reduce)');
let ambientParticles=[],ambientWidth=0,ambientHeight=0,ambientFrame=0,ambientRequest=0;
function resizeAmbient(){
  const pixelRatio=Math.min(window.devicePixelRatio||1,2);
  ambientWidth=window.innerWidth;
  ambientHeight=window.innerHeight;
  ambientCanvas.width=Math.round(ambientWidth*pixelRatio);
  ambientCanvas.height=Math.round(ambientHeight*pixelRatio);
  ambientContext.setTransform(pixelRatio,0,0,pixelRatio,0,0);
  ambientParticles=Array.from({length:Math.min(55,Math.floor(ambientWidth*ambientHeight/23000))},()=>({
    x:Math.random()*ambientWidth,
    y:Math.random()*ambientHeight,
    radius:.5+Math.random()*1.1,
    speed:.08+Math.random()*.27
  }));
}
function drawAmbient(){
  ambientRequest=0;
  if(reducedMotion.matches||document.hidden){
    ambientContext.clearRect(0,0,ambientWidth,ambientHeight);
    return;
  }
  ambientRequest=requestAnimationFrame(drawAmbient);
  if(++ambientFrame%2)return;
  ambientContext.clearRect(0,0,ambientWidth,ambientHeight);
  ambientContext.fillStyle=getComputedStyle(document.documentElement).getPropertyValue('--accent').trim();
  ambientContext.globalAlpha=.25;
  for(const particle of ambientParticles){
    particle.y-=particle.speed;
    if(particle.y<0){
      particle.y=ambientHeight;
      particle.x=Math.random()*ambientWidth;
    }
    ambientContext.beginPath();
    ambientContext.arc(particle.x,particle.y,particle.radius,0,Math.PI*2);
    ambientContext.fill();
  }
  ambientContext.globalAlpha=1;
}
function syncAmbient(){
  if(reducedMotion.matches||document.hidden){
    if(ambientRequest)cancelAnimationFrame(ambientRequest);
    ambientRequest=0;
    ambientContext.clearRect(0,0,ambientWidth,ambientHeight);
  }else if(!ambientRequest){
    ambientRequest=requestAnimationFrame(drawAmbient);
  }
}
window.addEventListener('resize',resizeAmbient);
document.addEventListener('visibilitychange',syncAmbient);
reducedMotion.addEventListener('change',syncAmbient);
resizeAmbient();
syncAmbient();

const exitButton=document.createElement('button');
exitButton.type='button';
exitButton.className='danger';
exitButton.textContent='Exit App';
exitButton.style.width='100%';
exitButton.addEventListener('click',async()=>{
  if(!bridgeReady||!window.pywebview?.api?.exit_app){
    log('Exit App is available when ACW is running as a desktop app.');
    return;
  }
  try{
    await window.pywebview.api.exit_app();
  }catch(error){
    log('Unable to exit app: '+(error.message||error));
  }
});
$('settings').append(exitButton);
