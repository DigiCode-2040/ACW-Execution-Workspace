'use strict';
const defaults={theme:'cyan',layout:'collapsed',historyCount:5,opacity:78,glow:true,compact:false,showWorkspaceNote:false,canvasZoom:1,canvasPositions:{},monitors:['cpu','systemmem','taskmem','started','ended','runtime','events','quality','output']};
let prefs={...defaults}, jobs=[], history=[], catalogEntries=[], bridgeReady=false, busy=false;
let commandHistory=[],commandHistoryIndex=0,commandDraft='',jobStatusFilter='all',jobSearch='';
try{prefs={...defaults,...JSON.parse(localStorage.getItem('acw.preferences')||'{}')}}catch{}
if(prefs.layout==='connected')prefs.layout='collapsed';
if(!prefs.canvasPositions||typeof prefs.canvasPositions!=='object')prefs.canvasPositions={};
if(!Number.isFinite(prefs.canvasZoom))prefs.canvasZoom=1;
prefs.canvasZoom=Math.max(.5,Math.min(1.5,prefs.canvasZoom));
if(typeof prefs.showWorkspaceNote!=='boolean')prefs.showWorkspaceNote=false;
let canvasExpanded=new Set(),connectedExpanded=new Set(),notesExpanded=new Set(),canvasDragging=false,notesSaveErrorLogged=false;
const $=id=>document.getElementById(id);
const brandLogo=document.querySelector('.logo');
brandLogo.src='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAEYAAAA+CAMAAABe1z3LAAAABGdBTUEAALGPC/xhBQAAACBjSFJNAAB6JgAAgIQAAPoAAACA6AAAdTAAAOpgAAA6mAAAF3CculE8AAABa1BMVEUAAAAADBgADxgADxkAEBkAERoAEhsAExwAFB0AFR4AFh8AFyAAFyEAFyIAFyMAFyQAFyUAFyYAFycAFygAFykAFyoAFysAFywAFy0AFy4AFy8AFzAAFzEAFzIAFzMAFzQAFzUAFzYAFzcAFzgAFzkAFzoAFzsAFzwAFz0AFz4AFz8AF0AAF0EAF0IAF0MAF0QAF0UAF0YAF0cAF0gAF0kAF0oAF0sAF0wAF00AF04AF08AF1AAF1EAF1IAF1MAF1QAF1UAF1YAF1cAF1gAF1kAF1oAF1sAF1wAF10AF14AF18AF2AAF2EAF2IAF2MAF2QAF2UAF2YAF2cAF2gAF2kAF2oAF2sAF2wAF20AF24AF28AF3AAF3EAF3MAF3QAF3UAF3YAF3cAF3gAF3kAF3oAF3sAF3wAF30AF34AF38AGIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAF38AGIAAAAAFzkAFz0AFz4AFz8AF0AAF0EAF0IAF0MAF0QAF0UAF0YAF0cAF0gAF0kAF0oAF0sAF0wAF00AF04AF08AF1AAF1EAF1IAF1MAF1QAF1UAF1YAF1cAF1gAF1kAF1oAF1sAF1wAF10AF14AF18AF2AAF2EAF2IAF2MAF2QAF2UAF2YAF2cAF2gAF2kAF2oAF2sAF2wAF20AF24AF28AF3AAF3EAF3MAF3QAF3UAF3YAF3cAF3gAF3kAF3oAF3sAF3wAF30AF34AF38AAAAAFzkAFz0AFz4AFz8AF0AAF0EAF0IAF0MAF0QAF0UAF0YAF0cAF0gAF0kAF0oAF0sAF0wAF00AF04AF08AF1AAF1EAF1IAF1MAF1QAF1UAF1YAF1cAF1gAF1kAF1oAF1sAF1wAF10AF14AF18AF2AAF2EAF2IAF2MAF2QAF2UAF2YAF2cAF2gAF2kAF2oAF2sAF2wAF20AF24AF28AF3AAF3EAF3IAF3MAF3QAF3UAF3YAF3cAF3gAF3kAF3oAF3sAF3wAF30AF34AF38AAAAA';
const escapeHTML=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
brandLogo.src='acw-logo.png';
let jobNotes={},workspaceNote='';
try{
  const savedNotes=JSON.parse(localStorage.getItem('acw.notes')||'{}');
  if(savedNotes&&typeof savedNotes==='object'&&!Array.isArray(savedNotes)){
    jobNotes=savedNotes.jobs&&typeof savedNotes.jobs==='object'&&!Array.isArray(savedNotes.jobs)
      ?Object.fromEntries(Object.entries(savedNotes.jobs).filter(([,note])=>typeof note==='string'))
      :{};
    workspaceNote=typeof savedNotes.workspace==='string'?savedNotes.workspace:'';
  }
}catch(error){console.error('Unable to load saved notes:',error)}
const monLabels={cpu:'CPU utilization',systemmem:'System memory*',taskmem:'Task memory',started:'Started',ended:'Ended',runtime:'Runtime',events:'Events',quality:'Quality',output:'Output'};
const statusSymbols={running:'●',queued:'○',cancelling:'Ⅱ',completed:'✓',failed:'!',cancelled:'—'};
function statusSymbol(status){return statusSymbols[status]||'·'}
const palettes={cyan:['#4ee6ef','#071019','#112736'],violet:['#bca3ff','#100d1c','#29203b'],green:['#78e4ac','#091712','#16362b'],amber:['#ffc36c','#1a130b','#3a2b1b']};
function save(){try{localStorage.setItem('acw.preferences',JSON.stringify(prefs))}catch{}}
function saveNotes(){try{localStorage.setItem('acw.notes',JSON.stringify({jobs:jobNotes,workspace:workspaceNote}));notesSaveErrorLogged=false}catch(error){if(!notesSaveErrorLogged){log('Unable to save notes: '+(error.message||error));notesSaveErrorLogged=true}}}
function apply(){const [accent,bg,surface]=palettes[prefs.theme]||palettes.cyan;const r=document.documentElement.style;r.setProperty('--accent',accent);r.setProperty('--bg',bg);r.setProperty('--surface',surface);r.setProperty('--glow',prefs.glow?'1':'0');r.setProperty('--glass',prefs.opacity+'%');r.setProperty('--surface',`color-mix(in srgb, ${surface} ${prefs.opacity}%, transparent)`);document.body.classList.toggle('compact',prefs.compact);document.body.classList.toggle('connected-layout',prefs.layout==='collapsed');$('jobs').className='jobs '+(prefs.layout==='collapsed'?'connected':prefs.layout);document.querySelectorAll('[name=layout]').forEach(input=>input.checked=input.value===prefs.layout);$('theme').value=prefs.theme;$('history-count').value=prefs.historyCount;$('hist-val').textContent=prefs.historyCount;$('opacity').value=prefs.opacity;$('opacity-val').textContent=prefs.opacity+'%';$('glow').checked=prefs.glow;$('compact').checked=prefs.compact;$('show-workspace-note').checked=prefs.showWorkspaceNote;$('workspace-note').hidden=!prefs.showWorkspaceNote;renderHistory();renderJobs();save()}
function log(s){history.push({time:new Date().toLocaleTimeString(),text:s});history=history.slice(-200);renderHistory()}
function renderHistory(){$('history').innerHTML=history.slice(-prefs.historyCount).map(x=>`<div class="entry"><time>${escapeHTML(x.time)}</time><span>${escapeHTML(x.text)}</span></div>`).join('')||'<span class="subtle">Ready. Type help for commands.</span>'}
function metric(label,value){return `<div class="metric"><span>${escapeHTML(label)}</span><span>${escapeHTML(value)}</span></div>`}
function jobNotesPane(j){
  return `<div class="pane job-note"><h3>NOTES</h3><textarea class="job-note-text" data-job-note="${escapeHTML(j.id)}" rows="5" spellcheck="false" aria-label="Notes for ${escapeHTML(j.label)}" placeholder="Add notes for this job…">${escapeHTML(jobNotes[j.id]||'')}</textarea></div>`;
}
function jobDetails(j,notesDrawer=false){
  const m=prefs.monitors,show=(k,label,val)=>m.includes(k)?metric(label,val):'';
  const output=(j.output||[]).slice(-6).map(escapeHTML).join('\n');
  const events=(j.events||[]).slice(-6).map(e=>escapeHTML(e.kind+': '+e.message)).join('\n');
  const artifacts=(j.artifacts||[]).map(artifact=>`<div class="artifact-row"><span title="${escapeHTML(artifact)}">${escapeHTML(artifact)}</span><button class="artifact-action" aria-label="Open artifact ${escapeHTML(artifact)}" data-action="open" data-job-id="${escapeHTML(j.id)}" data-artifact="${escapeHTML(artifact)}">Open</button><button class="artifact-action" aria-label="Reveal artifact ${escapeHTML(artifact)}" data-action="reveal" data-job-id="${escapeHTML(j.id)}" data-artifact="${escapeHTML(artifact)}">Reveal</button></div>`).join('');
  const notesOpen=notesExpanded.has(j.id),notes=jobNotesPane(j);
  const panels=`<div class="panels"><div class="pane"><h3>TASK / OVERVIEW</h3>${metric('Script',j.name)}${show('started','Started',j.started||'—')}${show('ended','Ended',j.ended||'—')}${show('runtime','Runtime',j.runtime+' s')}${show('cpu','CPU',j.cpu+' %')}${show('taskmem','Task memory',j.memory_mb+' MB')}${show('systemmem','System memory','Not collected')}</div><div class="pane"><h3>WORKING PARTS</h3>${metric('Stage',j.stage)}${metric('Progress',j.progress+'%')}<div class="bar"><i style="width:${Math.max(0,Math.min(100,Number(j.progress)||0))}%"></i></div>${m.includes('events')?`<div class="logs">${events||'Waiting for events…'}</div>`:''}</div><div class="pane"><h3>OUTPUT / RESULTS</h3>${show('quality','Quality',j.quality==null?'Not evaluated':j.quality+'%')}${metric('Exit code',j.exit_code??'—')}${m.includes('output')?`<div class="logs">${output||'Awaiting output…'}</div>`:''}${artifacts?`<div class="artifact-list"><h4>ARTIFACTS</h4>${artifacts}</div>`:''}</div>${!notesDrawer&&notesOpen?notes:''}</div>`;
  return panels+(notesDrawer&&notesOpen?`<section class="job-note-drawer">${notes}</section>`:'');
}
function canvasPosition(job,index){
  const saved=prefs.canvasPositions[job.id];
  if(saved&&Number.isFinite(saved.x)&&Number.isFinite(saved.y))return {x:Math.max(16,saved.x),y:Math.max(16,saved.y)};
  return {x:32+(index%3)*310,y:32+Math.floor(index/3)*190};
}
function resizeCanvasWorld(node){
  const world=$('jobs').querySelector('.canvas-world');
  if(!world)return;
  const zoom=prefs.canvasZoom;
  const x=parseFloat(node.style.getPropertyValue('--node-x'))||0;
  const y=parseFloat(node.style.getPropertyValue('--node-y'))||0;
  const right=(x+(node.classList.contains('expanded')?900:278)+40)*zoom;
  const bottom=(y+(node.classList.contains('expanded')?540:210))*zoom;
  world.style.width=`${Math.ceil(Math.max(parseFloat(world.style.width)||0,$('jobs').clientWidth-2,right))}px`;
  world.style.height=`${Math.ceil(Math.max(parseFloat(world.style.height)||0,$('jobs').clientHeight-2,bottom))}px`;
}
function renderConnectedOverview(){
  const counts={
    running:jobs.filter(job=>job.status==='running').length,
    completed:jobs.filter(job=>job.status==='completed').length,
    failed:jobs.filter(job=>job.status==='failed').length,
    active:jobs.filter(job=>['queued','running','cancelling'].includes(job.status)).length
  };
  document.querySelectorAll('[data-total]').forEach(card=>{
    const key=card.dataset.total;
    card.querySelector('.total-value').textContent=counts[key];
    card.querySelector('.total-caption').textContent=key==='active'?`of ${jobs.length} total jobs`:'jobs';
    card.setAttribute('aria-pressed',String(jobStatusFilter===(key==='active'?'active':key)));
  });
  const events=jobs.flatMap(job=>(job.events||[]).map(event=>({...event,jobLabel:job.label,jobId:job.id})))
    .sort((a,b)=>(Date.parse(b.time)||0)-(Date.parse(a.time)||0)).slice(0,14);
  $('live-events-list').innerHTML=events.map(event=>{
    const time=Date.parse(event.time);
    const kind=String(event.kind||'info').toLowerCase();
    const status=kind==='error'||kind==='failed'?'failed':kind==='warning'?'warning':
      ['completed','running','queued','cancelled','cancelling'].includes(kind)?kind:'info';
    return `<li class="live-event"><time>${Number.isNaN(time)?'--:--:--':escapeHTML(new Date(time).toLocaleTimeString())}</time><span class="event-dot ${status}" aria-hidden="true"></span><div class="event-copy"><b>${escapeHTML(event.jobLabel||event.jobId)}</b><span class="event-message">${escapeHTML(event.message)}</span></div></li>`;
  }).join('')||'<li class="live-events-empty">Events will appear here as jobs run.</li>';
}
function formatRuntime(seconds){
  const total=Math.max(0,Math.floor(Number(seconds)||0));
  const hours=Math.floor(total/3600),minutes=Math.floor(total%3600/60),remainder=total%60;
  return hours?`${hours}:${String(minutes).padStart(2,'0')}:${String(remainder).padStart(2,'0')}`:`${String(minutes).padStart(2,'0')}:${String(remainder).padStart(2,'0')}`;
}
function renderJobs(){
  const el=$('jobs');
  const activeNote=document.activeElement?.matches('textarea[data-job-note]')?{
    jobId:document.activeElement.dataset.jobNote,
    start:document.activeElement.selectionStart,
    end:document.activeElement.selectionEnd
  }:null;
  const previousScroll=prefs.layout==='canvas'?{left:el.scrollLeft,top:el.scrollTop}:null;
  el.className='jobs '+(prefs.layout==='collapsed'?'connected':prefs.layout);
  el.classList.toggle('has-expanded',prefs.layout==='canvas'&&canvasExpanded.size>0);
  const matches=jobs.filter(job=>{
    const statusMatches=jobStatusFilter==='all'||(jobStatusFilter==='active'?['queued','running','cancelling'].includes(job.status):job.status===jobStatusFilter);
    const searchable=[job.label,job.name,job.id,job.status,job.stage,jobNotes[job.id]||'',...(job.output||[]),...(job.events||[]).map(event=>event.message),...(job.artifacts||[])].join(' ').toLowerCase();
    return statusMatches&&searchable.includes(jobSearch.toLowerCase());
  });
  $('job-filter-count').textContent=`${matches.length} of ${jobs.length} jobs`;
  renderConnectedOverview();
  const controls=$('canvas-controls');
  controls.hidden=prefs.layout!=='canvas';
  $('canvas-zoom-label').textContent=`${Math.round(prefs.canvasZoom*100)}%`;
  if(!jobs.length){el.innerHTML='<div class="empty">No executions yet. Select a script above or type run &lt;command&gt;.</div>';return}
  if(!matches.length){el.innerHTML='<div class="empty">No jobs match the current filters.</div>';return}
  if(prefs.layout==='collapsed'){
    el.innerHTML=matches.map(job=>{
      const progress=Math.max(0,Math.min(100,Number(job.progress)||0));
      const expanded=connectedExpanded.has(job.id)||notesExpanded.has(job.id);
      const hasNote=Boolean((jobNotes[job.id]||'').trim());
      return `<article class="job connected-job status-${escapeHTML(job.status)}"><div class="connected-job-row"><div class="connected-job-name"><span class="connected-status-dot" aria-hidden="true"></span><div><b>${escapeHTML(job.label)}</b><span>${escapeHTML(job.name)} · ${escapeHTML(job.stage||job.status)}</span></div></div><span class="connected-job-runtime">${formatRuntime(job.runtime)}</span><div class="connected-progress" role="progressbar" aria-label="Progress for ${escapeHTML(job.label)}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${progress}"><span><i style="width:${progress}%"></i></span><b>${progress}%</b></div><span class="pill ${escapeHTML(job.status)}"><span class="status-symbol" aria-hidden="true">${statusSymbol(job.status)}</span>${escapeHTML(job.status)}</span><div class="connected-job-actions">${job.status==='running'?`<button class="cancel" aria-label="Stop ${escapeHTML(job.label)}" data-id="${escapeHTML(job.id)}">Stop</button>`:''}<button class="note-toggle${hasNote?' has-note':''}" type="button" data-note-toggle="${escapeHTML(job.id)}" aria-label="${notesExpanded.has(job.id)?'Close':'Open'} notes for ${escapeHTML(job.label)}" aria-expanded="${notesExpanded.has(job.id)}" title="${hasNote?'Notes saved':'Add a note'}">▤</button><button class="connected-details-toggle" type="button" data-job-details="${escapeHTML(job.id)}" aria-label="${expanded?'Hide':'Show'} details for ${escapeHTML(job.label)}" aria-expanded="${expanded}">${expanded?'−':'+'}</button></div></div><div class="connected-job-details"${expanded?'':' hidden'}>${jobDetails(job)}</div></article>`;
    }).join('');
  }else if(prefs.layout==='canvas'){
    const positions=matches.map(job=>canvasPosition(job,Math.max(0,jobs.findIndex(candidate=>candidate.id===job.id))));
    const width=Math.max(el.clientWidth-2,...positions.map((position,index)=>position.x+(canvasExpanded.has(matches[index].id)?900:278)+40));
    const height=Math.max(el.clientHeight-2,...positions.map((position,index)=>position.y+(canvasExpanded.has(matches[index].id)?540:210)));
    const zoom=prefs.canvasZoom;
    el.innerHTML=`<div class="canvas-world" style="width:${Math.ceil(width*zoom)}px;height:${Math.ceil(height*zoom)}px"><div class="canvas-nodes" style="width:${width}px;height:${height}px;transform:scale(${zoom})">${matches.map((job,index)=>{
      const progress=Math.max(0,Math.min(100,Number(job.progress)||0));
      const position=positions[index];
      const expanded=canvasExpanded.has(job.id);
      const hasNote=Boolean((jobNotes[job.id]||'').trim());
      return `<article class="job canvas-node ${escapeHTML(job.status)} status-${escapeHTML(job.status)}${expanded?' expanded':''}" data-job-id="${escapeHTML(job.id)}" style="--node-x:${position.x}px;--node-y:${position.y}px;--progress-angle:${progress*3.6}deg"><div class="canvas-node-head"><div class="canvas-node-grab" title="Drag to arrange this job"><button class="canvas-expand" aria-expanded="${expanded}" aria-label="${expanded?'Collapse':'Expand'} details for ${escapeHTML(job.label)}"><span class="progress-ring" aria-hidden="true"></span><span class="canvas-node-copy"><b>${escapeHTML(job.label)}</b><span class="canvas-stage">${escapeHTML(job.stage||job.status)} · ${progress}%</span></span></button><span class="canvas-node-status"><span class="status-symbol" aria-hidden="true">${statusSymbol(job.status)}</span>${escapeHTML(job.status)}</span></div><div class="canvas-node-actions">${job.status==='running'?`<button class="cancel" aria-label="Stop ${escapeHTML(job.label)}" data-id="${escapeHTML(job.id)}">Stop</button>`:''}<button class="note-toggle${hasNote?' has-note':''}" type="button" data-note-toggle="${escapeHTML(job.id)}" aria-label="${notesExpanded.has(job.id)?'Close':'Open'} notes for ${escapeHTML(job.label)}" aria-expanded="${notesExpanded.has(job.id)}" title="${hasNote?'Notes saved':'Add a note'}">▤</button><span class="meta">#${escapeHTML(job.id)}</span></div></div>${jobDetails(job,true)}</article>`;
    }).join('')}</div></div>`;
    el.querySelectorAll('.canvas-node').forEach(node=>{
      const id=node.dataset.jobId;
      const button=node.querySelector('.canvas-expand');
      button.onclick=()=>{
        const wasExpanded=canvasExpanded.has(id);
        canvasExpanded.clear();
        if(!wasExpanded)canvasExpanded.add(id);
        renderJobs();
        if(!wasExpanded){
          const expandedNode=el.querySelector(`.canvas-node[data-job-id="${CSS.escape(id)}"]`);
          const index=jobs.findIndex(job=>job.id===id);
          const position=canvasPosition(matches.find(job=>job.id===id),Math.max(0,index));
          el.scrollLeft=Math.max(0,position.x*prefs.canvasZoom-(el.clientWidth-expandedNode.offsetWidth*prefs.canvasZoom)/2);
          el.scrollTop=Math.max(0,position.y*prefs.canvasZoom-(el.clientHeight-expandedNode.offsetHeight*prefs.canvasZoom)/2);
        }
      };
      const grab=node.querySelector('.canvas-node-grab');
      grab.tabIndex=0;
      grab.setAttribute('role','group');
      grab.setAttribute('aria-label',`Drag ${node.querySelector('.canvas-node-copy b').textContent} to arrange. Use arrow keys to move.`);
      grab.onkeydown=event=>{
        const offsets={ArrowLeft:[-20,0],ArrowRight:[20,0],ArrowUp:[0,-20],ArrowDown:[0,20]};
        const offset=offsets[event.key];
        if(!offset)return;
        event.preventDefault();
        const index=jobs.findIndex(job=>job.id===id);
        const position=canvasPosition(matches[index],index);
        const x=Math.max(16,position.x+offset[0]),y=Math.max(16,position.y+offset[1]);
        prefs.canvasPositions[id]={x,y};
        node.style.setProperty('--node-x',`${x}px`);
        node.style.setProperty('--node-y',`${y}px`);
        resizeCanvasWorld(node);
        save();
      };
      grab.onpointerdown=event=>{
        if(event.target.closest('button')||event.button!==0)return;
        event.preventDefault();
        canvasDragging=true;
        grab.setPointerCapture(event.pointerId);
        const startX=event.clientX,startY=event.clientY;
        const index=jobs.findIndex(job=>job.id===id);
        const start=canvasPosition(matches.find(job=>job.id===id),Math.max(0,index));
        const move=moveEvent=>{
          const x=Math.max(16,start.x+(moveEvent.clientX-startX)/zoom);
          const y=Math.max(16,start.y+(moveEvent.clientY-startY)/zoom);
          node.style.setProperty('--node-x',`${x}px`);
          node.style.setProperty('--node-y',`${y}px`);
          resizeCanvasWorld(node);
        };
        const finish=()=>{
          if(!canvasDragging)return;
          canvasDragging=false;
          grab.removeEventListener('pointermove',move);
          grab.removeEventListener('pointerup',finish);
          grab.removeEventListener('pointercancel',finish);
          prefs.canvasPositions[id]={x:parseFloat(node.style.getPropertyValue('--node-x')),y:parseFloat(node.style.getPropertyValue('--node-y'))};
          save();
          renderJobs();
        };
        grab.addEventListener('pointermove',move);
        grab.addEventListener('pointerup',finish);
        grab.addEventListener('pointercancel',finish);
      };
    });
    if(previousScroll){el.scrollLeft=previousScroll.left;el.scrollTop=previousScroll.top}
  }else{
    el.innerHTML=matches.map(job=>{
      const hasNote=Boolean((jobNotes[job.id]||'').trim());
      return `<article class="job status-${escapeHTML(job.status)}"><div class="job-head"><div><b>${escapeHTML(job.label)}</b> <span class="meta">#${escapeHTML(job.id)}</span></div><div><span class="pill ${escapeHTML(job.status)}"><span class="status-symbol" aria-hidden="true">${statusSymbol(job.status)}</span>${escapeHTML(job.status)}</span> ${job.status==='running'?`<button class="cancel" data-id="${escapeHTML(job.id)}">Stop</button>`:''} <button class="note-toggle${hasNote?' has-note':''}" type="button" data-note-toggle="${escapeHTML(job.id)}" aria-label="${notesExpanded.has(job.id)?'Close':'Open'} notes for ${escapeHTML(job.label)}" aria-expanded="${notesExpanded.has(job.id)}" title="${hasNote?'Notes saved':'Add a note'}">▤</button></div></div>${jobDetails(job)}</article>`;
    }).join('');
  }
  el.querySelectorAll('.cancel').forEach(button=>button.onclick=async()=>{
    try{await window.pywebview.api.cancel_job(button.dataset.id);log('Cancellation requested: '+button.dataset.id)}
    catch(error){log('Error: '+(error.message||error))}
  });
  el.querySelectorAll('[data-job-details]').forEach(button=>button.onclick=()=>{
    const id=button.dataset.jobDetails;
    if(connectedExpanded.has(id))connectedExpanded.delete(id);
    else connectedExpanded.add(id);
    renderJobs();
    el.querySelector(`[data-job-details="${CSS.escape(id)}"]`)?.focus();
  });
  el.querySelectorAll('.artifact-action').forEach(button=>button.onclick=async()=>{
    try{
      const method=button.dataset.action==='open'?'open_artifact':'reveal_artifact';
      await window.pywebview.api[method](button.dataset.jobId,button.dataset.artifact);
      log(`${button.dataset.action==='open'?'Opened':'Revealed'} artifact: ${button.dataset.artifact}`);
    }catch(error){log('Artifact action failed: '+(error.message||error))}
  });
  el.querySelectorAll('[data-note-toggle]').forEach(button=>button.onclick=()=>{
    const id=button.dataset.noteToggle;
    const opening=!notesExpanded.has(id);
    if(opening)notesExpanded.add(id);
    else notesExpanded.delete(id);
    if(prefs.layout==='canvas'&&opening){
      canvasExpanded.clear();
      canvasExpanded.add(id);
    }
    renderJobs();
    if(opening){
      const editor=el.querySelector(`textarea[data-job-note="${CSS.escape(id)}"]`);
      editor?.focus();
      if(prefs.layout==='canvas'&&editor){
        const node=editor.closest('.canvas-node');
        const x=parseFloat(node.style.getPropertyValue('--node-x'))||0;
        const y=parseFloat(node.style.getPropertyValue('--node-y'))||0;
        el.scrollLeft=Math.max(0,x*prefs.canvasZoom-(el.clientWidth-node.offsetWidth*prefs.canvasZoom)/2);
        el.scrollTop=Math.max(0,y*prefs.canvasZoom-(el.clientHeight-node.offsetHeight*prefs.canvasZoom)/2);
      }
    }else el.querySelector(`[data-note-toggle="${CSS.escape(id)}"]`)?.focus();
  });
  el.querySelectorAll('textarea[data-job-note]').forEach(editor=>editor.oninput=()=>{
    const id=editor.dataset.jobNote;
    jobNotes[id]=editor.value;
    saveNotes();
    const hasNote=Boolean(editor.value.trim());
    const toggle=el.querySelector(`[data-note-toggle="${CSS.escape(id)}"]`);
    toggle?.classList.toggle('has-note',hasNote);
    if(toggle)toggle.title=hasNote?'Notes saved':'Add a note';
  });
  if(activeNote){
    const editor=el.querySelector(`textarea[data-job-note="${CSS.escape(activeNote.jobId)}"]`);
    if(editor){
      editor.focus();
      editor.setSelectionRange(activeNote.start,activeNote.end);
    }
  }
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
  $('layout-options').innerHTML=['collapsed','flow','layered','canvas'].map(k=>`<label><input type="radio" name="layout" value="${k}" ${prefs.layout===k?'checked':''}>${k[0].toUpperCase()+k.slice(1)}</label>`).join('');
  document.querySelectorAll('[name=layout]').forEach(c=>c.onchange=()=>setLayout(c.value));
  $('theme').onchange=e=>{prefs.theme=e.target.value;apply()};
  $('history-count').oninput=e=>{prefs.historyCount=Number(e.target.value);apply()};
  $('opacity').oninput=e=>{prefs.opacity=Number(e.target.value);apply()};
  $('glow').onchange=e=>{prefs.glow=e.target.checked;apply()};
  $('compact').onchange=e=>{prefs.compact=e.target.checked;apply()};
  $('show-workspace-note').onchange=e=>{prefs.showWorkspaceNote=e.target.checked;apply()};
  $('workspace-note-text').value=workspaceNote;
  $('workspace-note-text').oninput=e=>{workspaceNote=e.target.value;saveNotes()};
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
    if(verb==='help')log('Commands: help, catalog [reload], run <command> [arguments...], list, layout collapsed|flow|layered|canvas, stop <id>, clear (connected is an alias for collapsed)');
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
      if(!['connected','flow','layered','collapsed','canvas'].includes(args[0]))throw Error('Unknown layout');
      const layout=args[0]==='connected'?'collapsed':args[0];
      setLayout(layout);log('Layout: '+layout);
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
const jobFilters=document.createElement('div');jobFilters.id='job-filters';jobFilters.innerHTML='<strong class="jobs-title">JOBS</strong><select id="job-status-filter" aria-label="Filter jobs by status"><option value="all">All statuses</option><option value="active">Active</option><option value="queued">Queued</option><option value="running">Running</option><option value="cancelling">Cancelling</option><option value="completed">Completed</option><option value="failed">Failed</option><option value="cancelled">Cancelled</option></select><input id="job-search" type="search" aria-label="Search jobs" placeholder="Search jobs, output, and artifacts"><span id="job-filter-count" class="subtle" aria-live="polite">0 of 0 jobs</span>';document.querySelector('.workspace-head').after(jobFilters);$('job-status-filter').onchange=e=>{jobStatusFilter=e.target.value;renderJobs()};$('job-search').oninput=e=>{jobSearch=e.target.value.trim();renderJobs()};
const connectedSummary=document.createElement('section');
connectedSummary.id='connected-summary';
connectedSummary.className='connected-summary';
connectedSummary.setAttribute('aria-label','Job totals');
connectedSummary.innerHTML='<button class="total-card total-running" type="button" data-total="running" aria-pressed="false"><span class="total-icon" aria-hidden="true">●</span><span class="total-label">RUNNING</span><b class="total-value">0</b><small class="total-caption">jobs</small></button><button class="total-card total-completed" type="button" data-total="completed" aria-pressed="false"><span class="total-icon" aria-hidden="true">✓</span><span class="total-label">COMPLETED</span><b class="total-value">0</b><small class="total-caption">jobs</small></button><button class="total-card total-failed" type="button" data-total="failed" aria-pressed="false"><span class="total-icon" aria-hidden="true">!</span><span class="total-label">FAILED</span><b class="total-value">0</b><small class="total-caption">jobs</small></button><button class="total-card total-active" type="button" data-total="active" aria-pressed="false"><span class="total-icon" aria-hidden="true">⌁</span><span class="total-label">ACTIVE JOBS</span><b class="total-value">0</b><small class="total-caption">of 0 total jobs</small></button>';
const connectedDashboard=document.createElement('div');
connectedDashboard.id='connected-dashboard';
connectedDashboard.className='connected-dashboard';
const jobsColumn=document.createElement('section');
jobsColumn.className='jobs-column';
jobsColumn.setAttribute('aria-label','Jobs');
const liveEvents=document.createElement('aside');
liveEvents.id='live-events';
liveEvents.className='live-events';
liveEvents.setAttribute('aria-label','Recent job events');
liveEvents.innerHTML='<div class="live-events-head"><h2>LIVE EVENTS</h2><span><i></i> Live</span></div><ol id="live-events-list"><li class="live-events-empty">Events will appear here as jobs run.</li></ol>';
const workspaceHead=document.querySelector('.workspace-head');
workspaceHead.after(connectedSummary);
connectedSummary.after($('workspace-note'));
connectedSummary.nextElementSibling.after(connectedDashboard);
connectedDashboard.append(jobsColumn,liveEvents);
jobsColumn.append(jobFilters,$('jobs'));
connectedSummary.querySelectorAll('[data-total]').forEach(card=>card.onclick=()=>{
  jobStatusFilter=card.dataset.total==='active'?'active':card.dataset.total;
  $('job-status-filter').value=jobStatusFilter;
  renderJobs();
});
const canvasControls=document.createElement('div');
canvasControls.id='canvas-controls';
canvasControls.setAttribute('role','group');
canvasControls.setAttribute('aria-label','Canvas controls');
canvasControls.hidden=true;
canvasControls.innerHTML='<button type="button" data-canvas-zoom="out" aria-label="Zoom out canvas">−</button><span id="canvas-zoom-label" aria-live="polite">100%</span><button type="button" data-canvas-zoom="in" aria-label="Zoom in canvas">+</button><button type="button" data-canvas-action="reset">Reset view</button><button type="button" data-canvas-action="arrange">Auto-arrange</button><span class="subtle">Drag nodes · Ctrl+scroll to zoom · Drag empty space to pan</span>';
jobFilters.after(canvasControls);
jobsColumn.insertBefore(canvasControls,$('jobs'));
function setCanvasZoom(value){
  const el=$('jobs');
  const centerX=(el.scrollLeft+el.clientWidth/2)/prefs.canvasZoom;
  const centerY=(el.scrollTop+el.clientHeight/2)/prefs.canvasZoom;
  prefs.canvasZoom=Math.max(.5,Math.min(1.5,value));
  renderJobs();
  el.scrollLeft=Math.max(0,centerX*prefs.canvasZoom-el.clientWidth/2);
  el.scrollTop=Math.max(0,centerY*prefs.canvasZoom-el.clientHeight/2);
  save();
}
canvasControls.querySelector('[data-canvas-zoom=out]').onclick=()=>setCanvasZoom(prefs.canvasZoom-.1);
canvasControls.querySelector('[data-canvas-zoom=in]').onclick=()=>setCanvasZoom(prefs.canvasZoom+.1);
canvasControls.querySelector('[data-canvas-action=reset]').onclick=()=>{
  prefs.canvasZoom=1;
  renderJobs();
  $('jobs').scrollTo({left:0,top:0});
  save();
};
canvasControls.querySelector('[data-canvas-action=arrange]').onclick=()=>{
  prefs.canvasPositions={};
  canvasExpanded.clear();
  renderJobs();
  $('jobs').scrollTo({left:0,top:0});
  save();
};
let canvasPan=null;
const jobsViewport=$('jobs');
function setLayout(layout){
  canvasDragging=false;
  canvasPan=null;
  jobsViewport.classList.remove('panning');
  prefs.layout=layout==='connected'?'collapsed':layout;
  apply();
}
jobsViewport.addEventListener('pointerdown',event=>{
  if(prefs.layout!=='canvas'||event.button!==0||event.target.closest('.canvas-node'))return;
  canvasPan={pointerId:event.pointerId,x:event.clientX,y:event.clientY,left:jobsViewport.scrollLeft,top:jobsViewport.scrollTop};
  jobsViewport.classList.add('panning');
  jobsViewport.setPointerCapture(event.pointerId);
});
jobsViewport.addEventListener('pointermove',event=>{
  if(!canvasPan||event.pointerId!==canvasPan.pointerId)return;
  jobsViewport.scrollLeft=canvasPan.left-(event.clientX-canvasPan.x);
  jobsViewport.scrollTop=canvasPan.top-(event.clientY-canvasPan.y);
});
function finishCanvasPan(event){
  if(!canvasPan||event.pointerId!==canvasPan.pointerId)return;
  canvasPan=null;
  jobsViewport.classList.remove('panning');
}
jobsViewport.addEventListener('pointerup',finishCanvasPan);
jobsViewport.addEventListener('pointercancel',finishCanvasPan);
jobsViewport.addEventListener('wheel',event=>{
  if(prefs.layout==='canvas'&&event.ctrlKey){
    event.preventDefault();
    setCanvasZoom(prefs.canvasZoom+(event.deltaY<0?.05:-.05));
  }
},{passive:false});
window.addEventListener('resize',()=>{if(prefs.layout==='canvas'&&!canvasDragging)renderJobs()});
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
async function poll(){if(!bridgeReady||busy)return;busy=true;try{jobs=await window.pywebview.api.jobs();if(!(prefs.layout==='canvas'&&canvasDragging))renderJobs();if(!jobs.length)$('jobs').querySelector('.empty').textContent='No executions yet. Select a script above or type run <command>.';$('connection').textContent=`Local Python connected · ${jobs.filter(j=>j.status==='running').length} running · ${jobs.length} total` }catch(e){$('connection').textContent='Bridge error: '+e}finally{busy=false}}
window.addEventListener('pywebviewready',()=>{bridgeReady=true;log('Python bridge connected');refreshCatalog().catch(e=>log('Catalog error: '+(e.message||e)));poll();setInterval(poll,850)});
setTimeout(()=>{if(!bridgeReady)$('connection').textContent='Open this file through python main.py (not a regular browser tab)'},2500);

const backgroundStyle=document.createElement('style');
backgroundStyle.textContent=`
  :root{--logo-size:80px;--logo-cyan:#22d3ee}
  header{height:auto;min-height:56px;padding:6px 22px}
  #ambient-background{position:fixed;inset:0;z-index:0;width:100%;height:100%;pointer-events:none}
  body::before{content:"";position:fixed;inset:0;z-index:0;pointer-events:none;background-image:linear-gradient(color-mix(in srgb,var(--accent) 4.5%,transparent) 1px,transparent 1px),linear-gradient(90deg,color-mix(in srgb,var(--accent) 4.5%,transparent) 1px,transparent 1px);background-size:32px 32px}
  body>header{z-index:10}
  body>main{position:relative;z-index:1}
  .brand{display:flex;align-items:center;gap:9px}
  .logo{width:var(--logo-size);height:auto;display:block;flex:none;filter:drop-shadow(0 0 8px rgba(34,211,238,.45))}
  .brand-copy{display:flex;flex-direction:column;line-height:1}
  .brand-copy b{font-size:21px;letter-spacing:.09em}
  .brand-copy small{margin-top:4px;color:var(--muted);font-size:8px;letter-spacing:.11em;white-space:nowrap}
  .run-controls{display:flex;align-items:center;gap:8px;margin-left:auto}
  .run-controls select{min-width:190px;max-width:320px}
  #connected-summary{display:none}
  #connected-dashboard{display:contents}
  .live-events{display:none}
  .connected-layout #connected-summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:0 0 12px}
  .total-card{display:grid;grid-template-columns:26px 1fr;grid-template-rows:auto 1fr auto;align-items:center;column-gap:8px;min-width:0;min-height:116px;padding:12px 15px;text-align:left;border-radius:12px;background:linear-gradient(145deg,color-mix(in srgb,var(--status-color) 8%,transparent),transparent 75%),#0c1c2ac9;border-color:color-mix(in srgb,var(--status-color) 27%,transparent);transition:border-color .18s,box-shadow .18s,transform .18s}
  .total-card:hover{border-color:color-mix(in srgb,var(--status-color) 65%,transparent);box-shadow:0 0 18px color-mix(in srgb,var(--status-color) 17%,transparent);transform:translateY(-1px)}
  .total-card[aria-pressed=true]{border-color:var(--status-color);box-shadow:0 0 16px color-mix(in srgb,var(--status-color) 24%,transparent)}
  .total-icon{grid-column:1;grid-row:1;color:var(--status-color);font-size:17px;font-weight:800}
  .total-label{grid-column:2;grid-row:1;color:var(--text);font-size:11px;font-weight:700;letter-spacing:.1em}
  .total-value{grid-column:1/-1;grid-row:2;color:var(--status-color);font-size:36px;line-height:1.1;text-align:center;font-variant-numeric:tabular-nums;text-shadow:0 0 18px color-mix(in srgb,var(--status-color) 30%,transparent)}
  .total-caption{grid-column:1/-1;grid-row:3;color:var(--muted);font-size:11px;text-align:center}
  .total-running{--status-color:#4ee6ef}.total-completed{--status-color:#72e0b4}.total-failed{--status-color:#ff6674}.total-active{--status-color:#9fc6f4}
  .connected-layout #connected-dashboard{display:grid;grid-template-columns:minmax(0,1fr) minmax(260px,310px);align-items:start;gap:12px}
  .jobs-column{min-width:0}
  .connected-layout #job-filters{margin-bottom:8px}
  .jobs-title{color:var(--text);font-size:12px;letter-spacing:.12em}
  .connected-layout #job-filter-count{margin-left:0}
  .connected-layout .jobs.connected{gap:8px}
  .connected-job{padding:0;overflow:hidden}
  .connected-job-row{display:grid;grid-template-columns:minmax(150px,1.45fr) 58px minmax(90px,1fr) auto auto;align-items:center;gap:12px;min-height:64px;padding:9px 12px}
  .connected-job-name{display:flex;align-items:center;gap:10px;min-width:0}
  .connected-job-name>div{display:flex;flex-direction:column;min-width:0}
  .connected-job-name b{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px}
  .connected-job-name>div>span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--muted);font-size:11px}
  .connected-status-dot{width:10px;height:10px;flex:none;border-radius:50%;background:var(--status-color,#829faf);box-shadow:0 0 10px color-mix(in srgb,var(--status-color,#829faf) 55%,transparent)}
  .status-running .connected-status-dot{--status-color:#4ee6ef}
  .status-queued .connected-status-dot{--status-color:#ffc36c}
  .status-cancelling .connected-status-dot{--status-color:#bca3ff}
  .status-completed .connected-status-dot{--status-color:#72e0b4}
  .status-failed .connected-status-dot{--status-color:#ff6674}
  .connected-job-runtime{color:var(--muted);font:12px ui-monospace,Consolas,monospace;text-align:right}
  .connected-progress{display:flex;align-items:center;gap:8px;min-width:0}
  .connected-progress>span{height:7px;flex:1;overflow:hidden;border-radius:8px;background:#233f50}
  .connected-progress i{display:block;height:100%;border-radius:inherit;background:var(--progress-color,#4ee6ef);transition:width .3s}
  .status-completed .connected-progress i{--progress-color:#4fd18a}
  .status-failed .connected-progress i{--progress-color:#ff4b59}
  .connected-progress>b{width:34px;color:var(--text);font-size:11px;font-weight:500;text-align:right}
  .connected-job .pill{white-space:nowrap}
  .connected-job-actions{display:flex;align-items:center;gap:5px}
  .connected-job-actions button{min-width:30px;padding:3px 6px}
  .connected-details-toggle{font-size:16px}
  .connected-job-details{padding:0 12px 12px}
  .connected-job-details[hidden]{display:none}
  .connected-job-details .panels{grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}
  .connected-job-details .pane{min-height:130px;padding:12px}
  .connected-job-details .pane:not(:last-child):after{display:none}
  .live-events{min-width:0;overflow:hidden;border:1px solid var(--border);border-radius:var(--radius);background:linear-gradient(160deg,color-mix(in srgb,var(--accent) 5%,transparent),transparent 38%),#081721e8}
  .connected-layout .live-events{display:flex;flex-direction:column}
  .live-events-head{display:flex;align-items:center;justify-content:space-between;min-height:47px;padding:0 13px;border-bottom:1px solid var(--border)}
  .live-events-head h2{margin:0;color:var(--text);font-size:12px;letter-spacing:.12em}
  .live-events-head>span{display:flex;align-items:center;gap:6px;padding:3px 8px;border:1px solid color-mix(in srgb,#72e0b4 30%,transparent);border-radius:20px;color:#72e0b4;font-size:10px}
  .live-events-head>span i{width:7px;height:7px;border-radius:50%;background:#72e0b4;box-shadow:0 0 8px #72e0b4}
  #live-events-list{display:flex;flex-direction:column;max-height:calc(100vh - 400px);min-height:220px;overflow-y:auto;margin:0;padding:0;list-style:none;scrollbar-color:color-mix(in srgb,var(--accent) 30%,transparent) transparent}
  .live-event{display:grid;grid-template-columns:58px 8px minmax(0,1fr);align-items:start;gap:7px;padding:10px 11px;border-bottom:1px solid #ffffff10}
  .live-event time{padding-top:1px;color:var(--muted);font:10px ui-monospace,Consolas,monospace}
  .event-dot{width:8px;height:8px;margin-top:4px;border-radius:50%;background:#4ee6ef}
  .event-dot.completed{background:#72e0b4}.event-dot.failed{background:#ff6674}.event-dot.warning{background:#ffc36c}.event-dot.cancelling,.event-dot.cancelled{background:#bca3ff}
  .event-copy{display:flex;flex-direction:column;min-width:0;gap:2px}
  .event-copy b{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--text);font-size:11px}
  .event-message{display:-webkit-box;overflow:hidden;color:var(--muted);font-size:10px;line-height:1.4;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow-wrap:anywhere}
  .live-events-empty{padding:18px 12px;color:var(--muted);font-size:12px;text-align:center}
  #job-filters{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:0 0 14px;padding:11px 14px;border:1px solid var(--border);border-radius:var(--radius);background:var(--surface)}
  #job-filters input{flex:1;min-width:180px;background:#0b1b27;color:var(--text);border:1px solid var(--border);border-radius:8px;padding:7px 10px}
  #job-filter-count{margin-left:auto}
  #canvas-controls{display:flex;align-items:center;justify-content:flex-end;gap:8px;flex-wrap:wrap;margin:0 0 12px}
  #canvas-controls[hidden]{display:none}
  #canvas-controls button{padding:5px 10px}
  #canvas-zoom-label{min-width:42px;text-align:center;color:var(--muted);font:12px monospace}
  .jobs.canvas{display:block;position:relative;height:70vh;min-height:440px;max-height:calc(100vh - 300px);overflow:auto;overscroll-behavior:contain;border:1px solid var(--border);border-radius:var(--radius);background-color:color-mix(in srgb,var(--bg) 84%,transparent);background-image:linear-gradient(color-mix(in srgb,var(--accent) 8%,transparent) 1px,transparent 1px),linear-gradient(90deg,color-mix(in srgb,var(--accent) 8%,transparent) 1px,transparent 1px),radial-gradient(ellipse at 50% 30%,color-mix(in srgb,var(--accent) 8%,transparent),transparent 70%);background-size:32px 32px,32px 32px,100% 100%}
  .jobs.canvas.panning{cursor:grabbing;user-select:none}
  .canvas-world{position:relative}
  .canvas-nodes{position:absolute;left:0;top:0;transform-origin:top left}
  .canvas-node{position:absolute;left:var(--node-x);top:var(--node-y);width:278px;margin:0;padding:0;overflow:hidden;transition:border-color .2s,box-shadow .2s;box-shadow:0 12px 32px #0007}
  .canvas-node.running{--node-color:#4ee6ef;border-color:color-mix(in srgb,var(--node-color) 70%,transparent);box-shadow:0 0 22px color-mix(in srgb,var(--node-color) 30%,transparent),0 12px 32px #0007;animation:canvas-pulse 2.4s ease-in-out infinite}
  .canvas-node.queued{--node-color:#ffc36c}
  .canvas-node.completed{--node-color:#72e0b4}
  .canvas-node.failed{--node-color:#ff838e}
  .canvas-node.cancelled{--node-color:#829faf}
  .canvas-node.cancelling{--node-color:#bca3ff}
  .canvas-node:not(.running){border-color:color-mix(in srgb,var(--node-color) 52%,transparent)}
  .canvas-node.status-completed{border:2px solid #72e0b4;background:color-mix(in srgb,#72e0b4 8%,var(--bg))}
  .canvas-node.status-failed{border:2px solid #ff6674;background:color-mix(in srgb,#ff6674 9%,var(--bg))}
  .canvas-node.status-queued,.canvas-node.status-cancelling{border-style:dashed}
  .canvas-node.status-cancelled{background:color-mix(in srgb,#829faf 5%,var(--bg))}
  @keyframes canvas-pulse{50%{box-shadow:0 0 30px color-mix(in srgb,var(--node-color) 42%,transparent),0 12px 32px #0007}}
  @keyframes running-ring-pulse{50%{filter:brightness(1.3);box-shadow:0 0 12px color-mix(in srgb,var(--node-color) 55%,transparent)}}
  @keyframes running-wash{50%{box-shadow:0 0 22px color-mix(in srgb,#4ee6ef 25%,transparent),0 12px 32px #0007}}
  @media(prefers-reduced-motion:reduce){.canvas-node.running{animation:none}}
  .canvas-node-head{padding:11px 12px;background:color-mix(in srgb,var(--surface) 90%,#000 10%)}
  .canvas-node-grab{display:flex;align-items:center;justify-content:space-between;gap:8px;cursor:grab}
  .canvas-node-grab:active{cursor:grabbing}
  .canvas-node-grab:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
  .canvas-expand{display:flex;align-items:center;gap:10px;min-width:0;padding:0;border:0;background:transparent;text-align:left}
  .canvas-expand:hover{border-color:transparent}
  .progress-ring{display:block;flex:0 0 36px;width:36px;height:36px;border-radius:50%;background:conic-gradient(var(--node-color) var(--progress-angle),#28414d 0);position:relative}
  .canvas-node.running .progress-ring{animation:running-ring-pulse 1.8s ease-in-out infinite}
  .progress-ring:after{content:'';position:absolute;inset:4px;border-radius:50%;background:#0b1a24}
  .canvas-node.status-completed .progress-ring:after{content:'✓';display:grid;place-items:center;color:#72e0b4;font-size:18px;font-weight:800}
  .canvas-node.status-failed .progress-ring:after{content:'!';display:grid;place-items:center;color:#ff838e;font-size:18px;font-weight:800}
  .canvas-node-copy{display:flex;flex-direction:column;min-width:0;gap:2px}
  .canvas-node-copy b{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px}
  .canvas-stage{max-width:165px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--muted);font-size:11px}
  .canvas-node-status{color:var(--node-color);font-size:10px;text-transform:uppercase}
  .status-symbol{display:inline-block;margin-right:5px;font-weight:800}
  .status-completed .status-symbol{color:#72e0b4}
  .status-failed .status-symbol{color:#ff6674}
  .status-running .status-symbol{color:#4ee6ef}
  .status-queued .status-symbol{color:#ffc36c}
  .status-cancelling .status-symbol{color:#bca3ff}
  .status-cancelled .status-symbol{color:#829faf}
  .jobs:not(.canvas)>.job.status-running{border-color:color-mix(in srgb,#4ee6ef 68%,transparent);background:linear-gradient(100deg,color-mix(in srgb,#4ee6ef 7%,transparent),transparent 55%),#0c1c2ac9;animation:running-wash 2.4s ease-in-out infinite}
  .job.status-completed{border:2px solid #72e0b4;background:linear-gradient(100deg,color-mix(in srgb,#72e0b4 8%,transparent),transparent 60%),#0c1c2ac9;box-shadow:0 0 16px color-mix(in srgb,#72e0b4 30%,transparent),0 12px 30px #0003}
  .job.status-failed{border:2px solid #ff6674;background:linear-gradient(100deg,color-mix(in srgb,#ff6674 9%,transparent),transparent 60%),#0c1c2ac9;box-shadow:0 0 18px color-mix(in srgb,#ff6674 36%,transparent),0 12px 30px #0003}
  .jobs:not(.canvas)>.job.status-queued,.jobs:not(.canvas)>.job.status-cancelling{border-style:dashed}
  .job.status-queued{background:linear-gradient(100deg,color-mix(in srgb,#ffc36c 6%,transparent),transparent 60%),#0c1c2ac9}
  .job.status-cancelling{background:linear-gradient(100deg,color-mix(in srgb,#bca3ff 6%,transparent),transparent 60%),#0c1c2ac9}
  .job.status-cancelled{border-color:#829faf;background:linear-gradient(100deg,color-mix(in srgb,#829faf 5%,transparent),transparent 60%),#0c1c2ac9}
  .pill.running{color:#4ee6ef}
  .pill.queued{color:#ffc36c;border-style:dashed}
  .pill.cancelling{color:#bca3ff;border-style:dashed}
  .pill.cancelled{color:#829faf}
  @media(prefers-reduced-motion:reduce){.canvas-node.running .progress-ring,.jobs:not(.canvas)>.job.status-running{animation:none}}
  .canvas-node-actions{display:flex;align-items:center;justify-content:space-between;margin:5px 0 0 46px}
  .canvas-node-actions button{padding:2px 7px;font-size:11px}
  .canvas-node .meta{color:var(--muted);font:10px monospace}
  .canvas-node .panels{display:none}
  .canvas-node .job-note-drawer{display:none}
  .canvas-node.expanded{width:min(860px,calc(100vw - 100px));z-index:5;box-shadow:0 22px 65px #000b}
  .canvas-node.expanded{background:var(--bg)}
  .jobs.canvas.has-expanded .canvas-node:not(.expanded){opacity:.24}
  .canvas-node.expanded .panels{display:grid;padding:12px}
  .canvas-node.expanded .job-note-drawer{display:block}
  .canvas-node.expanded .panels{grid-template-columns:repeat(3,minmax(0,1fr))}
  .canvas-node.expanded .panels{gap:14px}
  .canvas-node.expanded .pane{padding:12px;min-height:120px}
  .artifact-list{margin-top:12px}.artifact-list h4{margin:0 0 6px;color:var(--accent);font-size:11px;letter-spacing:.1em}
  .artifact-row{display:flex;align-items:center;gap:5px;padding:4px 0;border-bottom:1px solid #ffffff0c}
  .artifact-row span{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--muted);font:11px ui-monospace,Consolas,monospace}
  .artifact-row button{padding:3px 7px;font-size:11px}
  @media(max-width:900px){.canvas-node.expanded{width:min(620px,calc(100vw - 55px))}}
  @media(max-width:1200px){.connected-layout #connected-dashboard{grid-template-columns:minmax(0,1fr) 270px}.connected-layout #connected-summary{grid-template-columns:repeat(2,minmax(0,1fr))}.connected-job-row{grid-template-columns:minmax(120px,1.3fr) 50px minmax(80px,1fr) auto auto;gap:8px}.connected-job .pill{font-size:9px;padding:2px 7px}}
  @media(max-width:900px){header{padding:6px 9px}:root{--logo-size:52px}.connected-layout #connected-dashboard{grid-template-columns:minmax(0,1fr)}.connected-layout .live-events{order:2}.connected-layout #live-events-list{max-height:340px;min-height:100px}.brand-copy b{font-size:18px}.brand-copy small{font-size:7px}}
  @media(max-width:600px){.connected-layout #connected-summary{gap:7px}.total-card{min-height:94px;padding:9px}.total-value{font-size:29px}.connected-job-row{grid-template-columns:minmax(0,1fr) auto auto;gap:6px;padding:9px}.connected-job-name{grid-column:1/3}.connected-job-runtime{grid-column:3;grid-row:1}.connected-progress{grid-column:1;grid-row:2}.connected-job-row>.pill{grid-column:2;grid-row:2}.connected-job-actions{grid-column:3;grid-row:2}.connected-job-details .panels{grid-template-columns:1fr}.connected-layout #job-filters{gap:7px}.jobs-title{width:100%}}
  @media(max-width:600px){.workspace-head{align-items:flex-start;flex-wrap:wrap}.run-controls{width:100%}.run-controls select{flex:1;min-width:0}.jobs.canvas{height:65vh;min-height:360px;max-height:none}}
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
