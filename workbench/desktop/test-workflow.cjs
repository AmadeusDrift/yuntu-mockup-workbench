'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const status=JSON.parse(fs.readFileSync(path.join(process.argv[2],'desktop-status.json'),'utf8'));
const run=process.argv[3],baseline=process.argv[4],pending=new Map();let ws,id=0,intercepted;
async function api(route,body){const r=await fetch(status.url+route,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Workbench':'1'},body:JSON.stringify(body)});const result=await r.json();assert(r.ok,JSON.stringify(result));return result;}
function command(method,params={}){return new Promise((resolve,reject)=>{const key=++id;pending.set(key,{resolve,reject});ws.send(JSON.stringify({id:key,method,params}))});}
async function evaluate(fn,arg){const r=await command('Runtime.evaluate',{expression:`(${fn})(${JSON.stringify(arg)})`,returnByValue:true,awaitPromise:true});assert(!r.exceptionDetails,JSON.stringify(r.exceptionDetails));return r.result.value;}
async function wait(fn){const deadline=Date.now()+30000;while(true){const result=await fn();if(result)return result;assert(Date.now()<deadline,'UI condition timed out');await new Promise(r=>setTimeout(r,150));}}
async function click(selector){await wait(()=>evaluate(sel=>{const el=document.querySelector(sel);return Boolean(el&&!el.disabled)},selector));await evaluate(sel=>document.querySelector(sel).click(),selector);}
async function task(kind,accept=()=>true){return wait(async()=>{const s=await api('/api/state');assert(s.task.status!=='failed',JSON.stringify(s.task));return s.task.kind===kind&&s.task.status==='done'&&accept(s)?s:false;});}
async function key(name,code){await command('Input.dispatchKeyEvent',{type:'keyDown',key:name,code:name,windowsVirtualKeyCode:code});await command('Input.dispatchKeyEvent',{type:'keyUp',key:name,code:name,windowsVirtualKeyCode:code});}
async function main(){
 const targets=await (await fetch('http://127.0.0.1:9341/json/list')).json();const target=targets.find(t=>t.type==='page'&&t.url===status.url+'/');assert(target);
 ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((resolve,reject)=>{ws.addEventListener('open',resolve,{once:true});ws.addEventListener('error',reject,{once:true})});
 ws.addEventListener('message',e=>{const m=JSON.parse(e.data);if(m.method==='Input.dragIntercepted'){intercepted=m.params;return;}if(!m.id)return;const p=pending.get(m.id);if(!p)return;pending.delete(m.id);m.error?p.reject(Error(m.error.message)):p.resolve(m.result)});
 await command('Page.bringToFront');await command('Input.setInterceptDrags',{enabled:true});await wait(()=>evaluate(()=>window.desktop?.isDesktop&&document.querySelectorAll('.template-card').length===1));
 assert(await evaluate(()=>Boolean(document.querySelector('[data-setting="fit"] option[value="cover_bleed"]'))),'V7 cover option must be available');
 assert.equal(await evaluate(()=>document.querySelector('[data-setting="orientation"]').value),'auto');
 assert.equal(await evaluate(()=>document.querySelector('[data-setting="rotation"]').value),'0');
 await click('[data-select-template]');
 assert(await evaluate(()=>document.querySelector('.preview-scope')?.textContent.includes('未加入本批导出')));
 assert(await evaluate(()=>!document.querySelector('[data-step-target="3"]').classList.contains('complete')));
 await click('[data-select-template]');
 assert.equal(await evaluate(()=>document.querySelectorAll('.workflow-step').length),4);
 await evaluate(()=>{const search=document.querySelector('#template-search');search.value='不存在的模板QA';search.dispatchEvent(new Event('input',{bubbles:true}));});
 assert(await evaluate(()=>!document.querySelector('#template-no-match').hidden&&document.querySelector('.template-card').hidden));
 await evaluate(()=>{const search=document.querySelector('#template-search');search.value='';search.dispatchEvent(new Event('input',{bubbles:true}));});
 await click('[data-configure-template]');
 await click('#scene-settings summary');
 await evaluate(()=>{document.querySelector('.scene-list').scrollIntoView({block:'center'});document.querySelector('.scene-list').scrollTop=0;return true;});
 const drag=await evaluate(()=>{const rows=[...document.querySelectorAll('[data-scene-id]')];const a=rows[1].querySelector('[data-drag-scene]').getBoundingClientRect(),b=rows[0].getBoundingClientRect();return {from:{x:a.x+a.width/2,y:a.y+a.height/2},to:{x:b.x+15,y:b.y+8},ids:rows.map(r=>r.dataset.sceneId)}});
 console.log('Drag coordinates',JSON.stringify(drag));
 await evaluate(points=>{window.qaDragEvents=[];window.addEventListener('error',e=>window.qaDragEvents.push({error:e.message}));document.addEventListener('dragover',e=>window.qaDragEvents.push({bubble:true,prevented:e.defaultPrevented,row:e.target.closest('[data-scene-id]')?.className,dropEffect:e.dataTransfer.dropEffect}));for(const type of ['mousedown','mouseup','dragstart','dragover','drop','dragend'])document.addEventListener(type,e=>window.qaDragEvents.push({type,target:e.target.outerHTML?.slice(0,160),prevented:e.defaultPrevented}),true);return {from:document.elementFromPoint(points.from.x,points.from.y)?.outerHTML.slice(0,250),to:document.elementFromPoint(points.to.x,points.to.y)?.outerHTML.slice(0,250)}},drag).then(console.log);
 await command('Input.dispatchMouseEvent',{type:'mouseMoved',...drag.from});await command('Input.dispatchMouseEvent',{type:'mousePressed',...drag.from,button:'left',clickCount:1});
 for(let i=1;i<=12;i++){await command('Input.dispatchMouseEvent',{type:'mouseMoved',x:drag.from.x+(drag.to.x-drag.from.x)*i/12,y:drag.from.y+(drag.to.y-drag.from.y)*i/12,button:'left',buttons:1});await new Promise(r=>setTimeout(r,35));}
 await new Promise(r=>setTimeout(r,100));
 await wait(()=>intercepted);for(const type of ['dragEnter','dragOver','drop'])await command('Input.dispatchDragEvent',{type,...drag.to,data:intercepted.data});await command('Input.dispatchMouseEvent',{type:'mouseReleased',...drag.to,button:'left',clickCount:1});await command('Input.setInterceptDrags',{enabled:false});
 console.log('Drag events',JSON.stringify(await evaluate(()=>window.qaDragEvents)));
 const order=await evaluate(()=>[...document.querySelectorAll('[data-scene-id]')].map(r=>r.dataset.sceneId));assert.deepEqual(order,[drag.ids[1],drag.ids[0],...drag.ids.slice(2)],'Real mouse drag must reorder the scene cards');
 await click('[data-action="save-template"]');await wait(async()=>JSON.stringify((await api('/api/state')).templates[0].scenes.map(s=>s.id))===JSON.stringify(order));
 console.log('PASS: real mouse drag and saved scene order');
 const input=path.join(run,'assets','artworks'),output=path.join(run,'output-ui');
 await click('[data-next-step="2"]');
 await evaluate(value=>{const el=document.querySelector('#input-path');el.value=value;el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));return true},input);
 await wait(()=>evaluate(()=>document.querySelectorAll('[data-artwork]').length===2));
 await click('[data-step-target="4"]');
 await evaluate(value=>{const el=document.querySelector('#output-path');el.value=value;el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));return true},output);
 await evaluate(()=>{const el=document.querySelector('[data-naming-mode="folder"]');el.value='{index:03}';el.dispatchEvent(new Event('change',{bubbles:true}));return true});
 await wait(async()=>{const s=await api('/api/state');return s.preferences.settings.naming?.folder==='{index:03}'&&s.preferences.output===output;});
 await click('[data-action="preview"]');const preview=await task('preview',s=>s.task.preview?.[0]?.name==='2');assert.deepEqual(preview.task.preview.map(p=>p.name),['2','1','3','4','5','6','7']);
 await wait(()=>evaluate(()=>document.querySelectorAll('.preview-tile').length===7));await click('.preview-tile');
 assert.equal(await evaluate(()=>document.querySelector('#image-dialog').open),true);
 const first=await evaluate(()=>document.querySelector('#large-image').src);await key('ArrowRight',39);
 const second=await evaluate(()=>document.querySelector('#large-image').src);assert.notEqual(second,first);await key('ArrowLeft',37);assert.equal(await evaluate(()=>document.querySelector('#large-image').src),first);
 await key('ArrowLeft',37);assert.equal(await evaluate(()=>document.querySelector('#large-image').src),first);
 await click('#next-image');assert.equal(await evaluate(()=>document.querySelector('#large-image').src),second);await key('Escape',27);assert.equal(await evaluate(()=>document.querySelector('#image-dialog').open),false);
 console.log('PASS: preview follows scene order, left/right keys, buttons and Esc');
 await click('[data-action="batch"]');const batch=await task('batch');const dest=batch.task.results[0];
 for(const folder of ['001','002'])assert.deepEqual(fs.readdirSync(path.join(dest,folder)).sort(),Array.from({length:7},(_,i)=>String(i+1).padStart(2,'0')+'.jpg'));
 assert(fs.readFileSync(path.join(dest,'001','01.jpg')).equals(fs.readFileSync(baseline)),'First reordered output must contain the original scene 2 pixels');
 await click('[data-step-target="3"]');
 await click('[data-fit-choice="contain"]');assert(await evaluate(()=>document.querySelector('[data-fit-choice="contain"]').getAttribute('aria-pressed')==='true'));
 await click('[data-fit-choice="auto"]');
 await evaluate(()=>{const details=document.querySelector('#advanced-settings');details.open=true;const direction=document.querySelector('[data-setting="orientation"]');direction.value='original';direction.dispatchEvent(new Event('input',{bubbles:true}));direction.dispatchEvent(new Event('change',{bubbles:true}));});
 await evaluate(()=>{const el=document.querySelector('[data-setting="rotation"]');el.focus();el.select();});
 await command('Input.insertText',{text:'-22.5'});
 const stepPoint=await evaluate(()=>{const b=document.querySelector('[data-step-target="4"]').getBoundingClientRect();return {x:b.x+b.width/2,y:b.y+b.height/2}});
 await command('Input.dispatchMouseEvent',{type:'mousePressed',...stepPoint,button:'left',clickCount:1});
 await command('Input.dispatchMouseEvent',{type:'mouseReleased',...stepPoint,button:'left',clickCount:1});
 assert.equal(await evaluate(()=>document.querySelector('.workflow-step.current').dataset.stepTarget),'4','Typing then clicking a step must work on the first mouse click');
 assert.equal(await evaluate(()=>document.querySelector('[data-setting="rotation"]').value),'-22.5');
 await click('[data-step-target="3"]');
 await wait(async()=>{const s=await api('/api/state');return s.preferences.settings.rotation===-22.5&&s.preferences.settings.orientation==='original';});
 assert(await evaluate(()=>document.querySelector('#preview-content').textContent.includes('请重新生成预览')));
 await click('[data-action="preview"]');const rotated=await task('preview',s=>s.task.preview_settings?.rotation===-22.5);assert.equal(rotated.task.preview.length,7);assert(rotated.task.preview_fit.every(info=>info.rotation===337.5));
 await wait(()=>evaluate(()=>document.querySelector('.preview-placement')?.textContent.includes('逆时针旋转22.5°')));
 console.log('PASS: default automatic direction, persisted manual decimal angle, stale preview warning and actual rotated EXE preview');
 await wait(()=>evaluate(()=>!document.querySelector('#toast').textContent));
 for(const [width,height] of [[1520,960],[1060,700]]){
   await command('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:false});
   for(const step of [1,2,3,4]){
     await click('[data-step-target="'+step+'"]');
     await evaluate(()=>{document.querySelector('.editor-panel').scrollTop=0;document.querySelectorAll('img').forEach(i=>i.loading='eager');document.activeElement?.blur();});
     await wait(()=>evaluate(()=>[...document.querySelectorAll('.preview-tile img')].every(i=>i.complete&&i.naturalWidth)));
     await evaluate(()=>new Promise(r=>{requestAnimationFrame(()=>requestAnimationFrame(r));setTimeout(r,200)}));
     const dimensions=await evaluate(()=>({doc:document.documentElement.scrollWidth,width:innerWidth,footer:document.querySelector('.workflow-footer').getBoundingClientRect().bottom,height:innerHeight,editor:document.querySelector('.editor-panel').getBoundingClientRect().height,checkbox:getComputedStyle(document.querySelector('[data-setting="trim"]')).width}));
     assert.equal(dimensions.checkbox,'16px');
     assert(dimensions.doc<=width,'Horizontal overflow');assert(dimensions.footer<=height+1,'Primary action outside window');assert(dimensions.editor>150,'Editor too short');
     const shot=await command('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync(path.join(run,'ui-'+width+'-step'+step+'.png'),Buffer.from(shot.data,'base64'));
   }
 }
 await command('Emulation.clearDeviceMetricsOverride');
 console.log('PASS: four steps, template search, fit cards, 1520 and 1060 layouts, loaded preview images');
 fs.writeFileSync(path.join(run,'features.json'),JSON.stringify({first_click_after_edit:true,four_step_workflow:true,template_search:true,custom_fit_controls:true,responsive_viewports:[1520,1060],mouse_drag:true,saved_order:order,arrow_keys:true,numbered_folders:['001','002'],images:'01.jpg–07.jpg',reordered_pixels_match:true,automatic_direction:true,manual_decimal_rotation:-22.5,rotation_preview_scenes:7},null,2));
 console.log('PASS: UI export naming 001/01.jpg through 002/07.jpg; reordered output pixels match');
}
main().then(()=>ws?.close()).catch(error=>{console.error(error);ws?.close();process.exitCode=1});


