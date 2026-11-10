'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const status=JSON.parse(fs.readFileSync(path.join(process.argv[2],'desktop-status.json'),'utf8'));
const run=process.argv[3],pending=new Map();let ws,id=0;
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function api(route){const response=await fetch(status.url+route);const data=await response.json();assert(response.ok,JSON.stringify(data));return data;}
function command(method,params={}){return new Promise((resolve,reject)=>{const key=++id;pending.set(key,{resolve,reject});ws.send(JSON.stringify({id:key,method,params}));});}
async function evaluate(fn,arg){const result=await command('Runtime.evaluate',{expression:`(${fn})(${JSON.stringify(arg)})`,returnByValue:true,awaitPromise:true});assert(!result.exceptionDetails,JSON.stringify(result.exceptionDetails));return result.result.value;}
async function wait(fn,label='UI condition',timeout=45000){const deadline=Date.now()+timeout;while(true){const value=await fn();if(value)return value;assert(Date.now()<deadline,label+' timed out');await pause(100);}}
async function assertNoImageDialog(label){const trace=await evaluate(()=>({open:document.querySelector('#image-dialog').open,clicks:window.liveClicks?.slice(-8),modals:window.liveModals}));assert.equal(trace.open,false,'Unexpected image modal '+label+': '+JSON.stringify(trace));}
async function click(selector){await assertNoImageDialog('before click '+selector);await wait(()=>evaluate(sel=>{const element=document.querySelector(sel);return !!element&&!element.disabled;},selector),'enabled '+selector);await evaluate(sel=>document.querySelector(sel).click(),selector);}
async function setValue(selector,value,change=true){await assertNoImageDialog('before setting '+selector);await evaluate(({selector,value,change})=>{const element=document.querySelector(selector);if(element.disabled)throw Error('Disabled: '+selector);element.value=String(value);element.dispatchEvent(new Event('input',{bubbles:true}));if(change)element.dispatchEvent(new Event('change',{bubbles:true}));},{selector,value,change});}
async function currentPreview({source,scene,quality='full',count=1}={}){
  return wait(()=>evaluate(({source,scene,quality,count})=>{const grid=document.querySelector('.preview-grid'),images=[...document.querySelectorAll('.preview-tile img')];return !!grid&&grid.dataset.previewCurrent==='true'&&grid.dataset.previewQuality===quality&&(!source||grid.dataset.previewSource===source)&&(scene===undefined||grid.dataset.previewScene===String(scene))&&images.length===count&&images.every(image=>image.complete&&image.naturalWidth>0)?{source:grid.dataset.previewSource,task:grid.dataset.previewTask,quality:grid.dataset.previewQuality,width:images[0].naturalWidth,height:images[0].naturalHeight}:false;},{source,scene,quality,count}),'current '+quality+' preview');
}
async function beginRange(selector,value){await evaluate(sel=>document.querySelector(sel).dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,pointerId:77,button:0})),selector);await setValue(selector,value,false);}
async function finishRange(selector){await evaluate(sel=>{const element=document.querySelector(sel);element.dispatchEvent(new PointerEvent('pointerup',{bubbles:true,pointerId:77,button:0}));element.dispatchEvent(new Event('change',{bubbles:true}));},selector);}
async function selectedThumbnailVisible(label){return evaluate(label=>{const strip=document.querySelector('.sample-strip'),rect=strip.getBoundingClientRect(),selected=document.querySelector('.sample-thumb.selected')?.getBoundingClientRect(),left=rect.left+strip.clientLeft,right=left+strip.clientWidth;window.liveGeometry.push({label,time:performance.now(),viewport:{width:innerWidth,height:innerHeight},visibility:document.visibilityState,hasFocus:document.hasFocus(),resizeEvents:window.liveResize?.length,helperRuns:window.liveHelperRuns?.length,strip:rect.toJSON(),thumb:selected?.toJSON(),clientWidth:strip.clientWidth,clientLeft:strip.clientLeft,scrollWidth:strip.scrollWidth,maxScroll:strip.scrollWidth-strip.clientWidth,scrollLeft:strip.scrollLeft,dialogOpen:document.querySelector('#image-dialog').open});return !!selected&&selected.width>0&&selected.left>=left-1&&selected.right<=right+1;},label);}
async function main(){
  const targets=await(await fetch('http://127.0.0.1:9344/json/list')).json(),target=targets.find(t=>t.type==='page'&&t.url===status.url+'/');assert(target,'desktop page found');
  ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((resolve,reject)=>{ws.addEventListener('open',resolve,{once:true});ws.addEventListener('error',reject,{once:true});});
  ws.addEventListener('message',event=>{const message=JSON.parse(event.data);if(!message.id)return;const entry=pending.get(message.id);if(!entry)return;pending.delete(message.id);message.error?entry.reject(Error(message.error.message)):entry.resolve(message.result);});
  await wait(()=>evaluate(()=>document.querySelectorAll('.artwork-tile').length===40),'40 artwork thumbnails');
  await evaluate(()=>{
    window.liveRequests=[];window.liveAdoptions=[];window.liveClicks=[];window.liveModals=[];window.liveGeometry=[];window.liveResize=[];window.liveHelperRuns=[];window.liveErrors=[];window.delayNextEditor=3400;window.delayNextPreview=0;
    const originalRect=Element.prototype.getBoundingClientRect;
    Element.prototype.getBoundingClientRect=function(){const rect=originalRect.call(this);if(this.classList.contains('sample-strip')&&new Error().stack.includes('revealSampleThumbnail')){const element=this,record={time:performance.now(),viewport:{width:innerWidth,height:innerHeight},visibility:document.visibilityState,hasFocus:document.hasFocus(),before:{rect:rect.toJSON(),clientWidth:this.clientWidth,clientLeft:this.clientLeft,scrollLeft:this.scrollLeft}};window.liveHelperRuns.push(record);queueMicrotask(()=>{record.after={rect:originalRect.call(element).toJSON(),clientWidth:element.clientWidth,clientLeft:element.clientLeft,scrollLeft:element.scrollLeft};});}return rect;};
    window.addEventListener('resize',()=>{const strip=document.querySelector('.sample-strip');window.liveResize.push({time:performance.now(),width:innerWidth,height:innerHeight,visibility:document.visibilityState,hasFocus:document.hasFocus(),strip:strip?{rect:originalRect.call(strip).toJSON(),clientWidth:strip.clientWidth,scrollLeft:strip.scrollLeft}:null});});
    window.addEventListener('error',event=>window.liveErrors.push({time:performance.now(),message:event.message,stack:event.error?.stack}));
    document.addEventListener('click',event=>{const button=event.target.closest('button');window.liveClicks.push({time:performance.now(),trusted:event.isTrusted,x:event.clientX,y:event.clientY,target:event.target.tagName,button:button?{id:button.id,dataset:{...button.dataset}}:null,sample:document.querySelector('.preview-selected strong')?.title});},true);
    const originalShowModal=HTMLDialogElement.prototype.showModal;HTMLDialogElement.prototype.showModal=function(){window.liveModals.push({time:performance.now(),id:this.id,sample:document.querySelector('.preview-selected strong')?.title,stack:new Error().stack,click:window.liveClicks.at(-1)});return originalShowModal.call(this);};
    const original=window.fetch;
    window.fetch=async(url,options)=>{
      const tracked=options?.method==='POST'&&['/api/editor','/api/preview'].includes(url),entry=tracked?{url,body:JSON.parse(options.body),started:performance.now()}:null;
      if(entry)window.liveRequests.push(entry);
      const response=await original(url,options);
      if(entry){const data=await response.clone().json();entry.taskId=data.task_id;entry.serverAccepted=performance.now();const delay=url==='/api/editor'?window.delayNextEditor:window.delayNextPreview;if(url==='/api/editor')window.delayNextEditor=0;else window.delayNextPreview=0;if(delay)await new Promise(resolve=>setTimeout(resolve,delay));entry.acknowledged=performance.now();}
      return response;
    };
    let previous='';new MutationObserver(()=>{const grid=document.querySelector('.preview-grid');if(!grid)return;const record={source:grid.dataset.previewSource,quality:grid.dataset.previewQuality,task:grid.dataset.previewTask,current:grid.dataset.previewCurrent==='true',sample:document.querySelector('.preview-selected strong')?.title,time:performance.now()},key=JSON.stringify({...record,time:0});if(key!==previous){previous=key;window.liveAdoptions.push(record);}}).observe(document.querySelector('#app'),{subtree:true,childList:true,attributes:true,attributeFilter:['data-preview-current','data-preview-task','data-preview-source','data-preview-quality']});
  });
  await click('[data-step-target="3"]');
  await wait(()=>evaluate(()=>window.liveRequests.some(request=>request.url==='/api/editor')),'automatic editor load');
  await pause(900);
  assert.equal(await evaluate(()=>document.querySelector('#placement-stage').classList.contains('ready')),false,'unacknowledged editor result cannot be adopted early');
  assert.equal(await evaluate(()=>document.querySelector('[data-placement="scale"]').disabled),false,'controls remain enabled before the delayed acknowledgement');
  const initial=await currentPreview({scene:0});
  await wait(()=>evaluate(()=>document.querySelector('#placement-stage').classList.contains('ready')),'ready left canvas');
  const initialRequests=await evaluate(()=>window.liveRequests);
  assert(initialRequests[0].acknowledged-initialRequests[0].serverAccepted>=3300,'editor acknowledgement was delayed');
  assert.equal(initialRequests.filter(request=>request.url==='/api/editor').length,1,'one editor request reserves the server until its acknowledgement');
  assert(initialRequests.find(request=>request.url==='/api/preview').started>=initialRequests[0].acknowledged,'preview waits for its own editor acknowledgement');

  await evaluate(()=>document.querySelector('#placement-stage').scrollIntoView({block:'center'}));
  const rect=await evaluate(()=>{const r=document.querySelector('#placement-stage').getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height};});
  const x=rect.x+rect.w*.5,y=rect.y+rect.h*.5;
  await evaluate(()=>{window.continuousMotionStart=performance.now();});
  await command('Input.dispatchMouseEvent',{type:'mouseMoved',x,y});
  await command('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,x,y});
  for(let step=1;step<=40;step++){await command('Input.dispatchMouseEvent',{type:'mouseMoved',button:'left',buttons:1,x:x+rect.w*.15*step/40,y:y+rect.h*.1*step/40});await pause(50);}
  const continuousDrafts=await evaluate(()=>[...new Set(window.liveAdoptions.filter(record=>record.time>=window.continuousMotionStart&&record.quality==='draft').map(record=>record.task))]);
  assert(continuousDrafts.length>=2,'at least two right-side draft images must appear during uninterrupted motion, before the final position settles');
  const draft=await currentPreview({source:initial.source,scene:0,quality:'draft'});
  assert(Math.max(draft.width,draft.height)<=720,'draft image is bounded before full-size composition');
  assert.equal(await evaluate(()=>document.querySelector('#placement-stage').classList.contains('dragging')),true,'right draft is visible before pointer release');
  assert.equal(await evaluate(()=>[...document.querySelectorAll('[data-placement],#artwork-brightness,#editor-scene,[data-sample-step]')].some(element=>element.disabled&&element.dataset.sampleStep!=='-1')),false,'preview does not disable editor or next-sample controls');
  await command('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,x:x+rect.w*.15,y:y+rect.h*.1});
  const settled=await currentPreview({source:initial.source,scene:0});assert(Math.max(settled.width,settled.height)>720,'settle restores exact full resolution');
  const moved=await api('/api/task');assert(Math.abs(moved.task.preview_settings.offset_x-15)<1);assert(Math.abs(moved.task.preview_settings.offset_y-10)<1);
  await assertNoImageDialog('after drag gesture');

  const editorCount=await evaluate(()=>window.liveRequests.filter(request=>request.url==='/api/editor').length);
  await beginRange('input[type="range"][data-placement="scale"]',135);await currentPreview({quality:'draft',scene:0});await finishRange('input[type="range"][data-placement="scale"]');await currentPreview({scene:0});
  await beginRange('#artwork-brightness',12);await currentPreview({quality:'draft',scene:0});await finishRange('#artwork-brightness');await currentPreview({scene:0});
  await assertNoImageDialog('after scale and brightness gestures');
  assert.equal(await evaluate(()=>window.liveRequests.filter(request=>request.url==='/api/editor').length),editorCount,'scale/brightness reuse the unbrightened geometry');
  await setValue('#editor-scene',1);await currentPreview({scene:1});
  await assertNoImageDialog('after scene switch');
  const shared=await api('/api/task');assert.equal(shared.task.preview_settings.scale,135);assert.equal(shared.task.preview_settings.brightness,12);assert.equal(shared.task.editor.settings.brightness,0,'prepared source remains unbrightened');

  const paths=await evaluate(()=>[...document.querySelectorAll('[data-sample-artwork]')].map(element=>element.dataset.sampleArtwork));
  assert.equal(paths.length,40);await evaluate(()=>{window.delayNextEditor=1200;window.rapidSwitchStart=performance.now();});
  await click('[data-sample-index="1"]');await wait(()=>evaluate(path=>window.liveRequests.some(request=>request.url==='/api/editor'&&request.body.source===path),paths[1]),'B editor in flight');
  await click('[data-sample-index="2"]');
  assert.equal(await evaluate(()=>document.querySelector('.preview-grid').dataset.previewCurrent),'false','old A image is visibly marked outdated for C');
  assert.match(await evaluate(()=>document.querySelector('#preview-status').textContent),/更新中|上一结果/);
  await currentPreview({source:paths[2],scene:1});
  await assertNoImageDialog('after rapid artwork switches');
  const rapidLog=await evaluate(()=>window.liveAdoptions.filter(record=>record.time>=window.rapidSwitchStart));
  assert(!rapidLog.some(record=>record.source===paths[1]),'superseded B result was never adopted');
  assert(!rapidLog.some(record=>record.current&&record.source!==record.sample),'no old result is labeled current');
  assert.equal(await evaluate(()=>document.querySelector('#editor-scene').value),'1');
  assert.equal(await evaluate(()=>document.querySelector('[data-placement="scale"]').value),'135');
  assert.equal(await evaluate(()=>document.querySelector('#artwork-brightness').value),'12');

  await click('[data-sample-index="39"]');await currentPreview({source:paths[39],scene:1});
  assert(await selectedThumbnailVisible('selected40'),'sample 40 is revealed when selected near the end of the strip');
  await click('[data-step-target="2"]');await evaluate(path=>[...document.querySelectorAll('[data-artwork]')].find(element=>element.dataset.artwork===path).click(),paths[39]);await click('[data-step-target="3"]');
  assert(await selectedThumbnailVisible('step2-to-step3-with40'),'entering confirmation after selecting sample 40 reveals its thumbnail');
  const scroll=await evaluate(()=>document.querySelector('.editor-panel').scrollTop);
  await click('[data-sample-step="1"]');await wait(()=>evaluate(()=>document.querySelector('#sample-position').textContent==='41 / 43'),'cross-page sample 41');
  const nextSource=await evaluate(()=>document.querySelector('.preview-selected strong').title);await currentPreview({source:nextSource,scene:1});
  assert.equal(await evaluate(()=>document.querySelectorAll('[data-sample-artwork]').length),3);
  assert(Math.abs(await evaluate(()=>document.querySelector('.editor-panel').scrollTop)-scroll)<2,'switch preserves editor scroll');
  await click('[data-sample-step="-1"]');await currentPreview({source:paths[39],scene:1});
  assert.equal(await evaluate(()=>document.querySelector('#sample-position').textContent),'40 / 43');
  assert(await selectedThumbnailVisible('returned40'),'returning across a page boundary reveals sample 40');
  await wait(async()=>{const data=await api('/api/state');return data.preferences?.settings?.scale===135&&data.preferences.settings.brightness===12&&data.preferences.settings.offset_x>10;},'persisted global settings');
  const layouts=[];
  for(const [width,height] of [[1520,960],[1060,700]]){
    const prior=await evaluate(()=>({width:innerWidth,height:innerHeight,events:window.liveResize.length}));
    await command('Page.bringToFront');
    await command('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:false});
    // An occluded Electron surface may defer resize delivery until its next frame.
    // Capture a frame and wait for the browser's real event; never call UI helpers.
    await command('Page.captureScreenshot',{format:'png'});
    await wait(()=>evaluate(({width,height,prior})=>innerWidth===width&&innerHeight===height&&(prior.width===width&&prior.height===height||window.liveResize.length>prior.events),{width,height,prior}),'native resize event '+width+'×'+height,8000);
    await assertNoImageDialog('after resize '+width+'×'+height);
    assert(await selectedThumbnailVisible('resize-'+width+'x'+height),'selected thumbnail remains visible after resize to '+width+'×'+height);
    await evaluate(()=>{document.querySelector('.editor-panel').scrollTop=0;document.querySelector('#preview-content').scrollTop=0;});
    const layout=await evaluate(()=>{const panel=document.querySelector('.editor-panel').getBoundingClientRect(),navigator=document.querySelector('#sample-navigator').getBoundingClientRect(),footer=document.querySelector('.workflow-footer').getBoundingClientRect();return {width:innerWidth,height:innerHeight,horizontalOverflow:document.documentElement.scrollWidth>innerWidth,footerBottom:footer.bottom,navigatorVisible:navigator.left>=panel.left&&navigator.right<=panel.right+1&&navigator.top>=panel.top&&navigator.bottom<=panel.bottom+1};});
    assert.equal(layout.horizontalOverflow,false,'no horizontal page overflow at '+width);assert(layout.footerBottom<=height+1,'workflow footer remains visible at '+height);assert(layout.navigatorVisible,'sample navigator is visible at the top of confirmation step');
    const controls=[];
    for(const selector of ['#editor-scene','input[type="range"][data-placement="scale"]','input[type="number"][data-placement="offset_x"]','#artwork-brightness']){
      const reachable=await evaluate(sel=>{const field=document.querySelector(sel);field.scrollIntoView({block:'center',inline:'nearest'});const r=field.getBoundingClientRect(),p=document.querySelector('.editor-panel').getBoundingClientRect();return !field.disabled&&r.width>0&&r.left>=p.left&&r.right<=p.right+1&&r.top>=p.top&&r.bottom<=p.bottom+1;},selector);
      assert(reachable,selector+' remains reachable at '+width+'×'+height);controls.push(selector);
    }
    await evaluate(()=>{document.querySelector('.editor-panel').scrollTop=0;document.querySelector('#preview-content').scrollTop=0;});await pause(100);
    const frame=await command('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(run,`live-preview-step3-${width}x${height}.png`),Buffer.from(frame.data,'base64'));layouts.push({...layout,controlsReachable:controls});
  }
  await command('Emulation.setDeviceMetricsOverride',{width:1520,height:960,deviceScaleFactor:1,mobile:false});
  await click('[data-action="preview"]');await currentPreview({source:paths[39],scene:'all',count:3});
  assert.match(await evaluate(()=>document.querySelector('#workflow-summary').textContent),/43 张印花/,'sample navigation does not change the batch count');
  const requests=await evaluate(()=>window.liveRequests),adoptions=await evaluate(()=>window.liveAdoptions),geometry=await evaluate(()=>window.liveGeometry),resizeEvents=await evaluate(()=>window.liveResize),helperRuns=await evaluate(()=>window.liveHelperRuns);
  assert(requests.filter(request=>request.url==='/api/editor').every(request=>request.body.settings.brightness===0));
  assert(requests.some(request=>request.url==='/api/preview'&&request.body.draft===true));
  assert(requests.some(request=>request.url==='/api/preview'&&request.body.draft===false&&request.body.scene===undefined));
  assert(!adoptions.some(record=>record.current&&record.source!==record.sample),'all displayed current previews match the selected source');
  const shot=await command('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(run,'live-preview-ui.png'),Buffer.from(shot.data,'base64'));
  fs.writeFileSync(path.join(run,'ui-result.json'),JSON.stringify({auto_load:true,delayed_acknowledgement:true,draft_before_release:true,continuous_motion_draft_count:continuousDrafts.length,exact_settling:true,scale_and_brightness_live:true,unbrightened_geometry_reuse:true,rapid_switch_without_stale_adoption:true,global_settings:true,pagination_boundary:true,controls_enabled:true,full_set:true,layouts,geometry,resizeEvents,helperRuns,requests,adoptions},null,2));
  await evaluate(()=>{setTimeout(()=>window.desktop.window.action('close'),200);return true;});ws.close();
  console.log('PASS: automatic load, delayed acknowledgement, live drafts/full settle, global settings, rapid switching, paging, stale result guards');
}
main().catch(async error=>{
  console.error(error);
  try{
    fs.writeFileSync(path.join(run,'failure-state.json'),JSON.stringify(await api('/api/state'),null,2));
    if(ws?.readyState===WebSocket.OPEN){
      const ui=await evaluate(()=>({requests:window.liveRequests,adoptions:window.liveAdoptions,clicks:window.liveClicks,modals:window.liveModals,geometry:window.liveGeometry,resizeEvents:window.liveResize,helperRuns:window.liveHelperRuns,errors:window.liveErrors,visibility:document.visibilityState,hasFocus:document.hasFocus(),dialogOpen:document.querySelector('#image-dialog').open,toast:document.querySelector('#toast')?.textContent,editorScroll:document.querySelector('.editor-panel')?.scrollTop,preview:document.querySelector('#preview-content')?.outerHTML,navigator:document.querySelector('#sample-navigator')?.outerHTML,stage:document.querySelector('#placement-stage')?.outerHTML,controls:[...document.querySelectorAll('[data-placement],#editor-scene,#artwork-brightness')].map(element=>({key:element.dataset.placement||element.id,value:element.value,disabled:element.disabled}))}));
      fs.writeFileSync(path.join(run,'failure-ui.json'),JSON.stringify(ui,null,2));const shot=await command('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(run,'failure-ui.png'),Buffer.from(shot.data,'base64'));
    }
  }catch(diagnosticError){console.error('Diagnostics:',diagnosticError);}
  if(ws)ws.close();process.exitCode=1;
});
