'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const status=JSON.parse(fs.readFileSync(path.join(process.argv[2],'desktop-status.json'),'utf8'));
const run=process.argv[3],pending=new Map();let ws,id=0;
async function api(route,body){const r=await fetch(status.url+route,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Workbench':'1'},body:JSON.stringify(body)});const result=await r.json();assert(r.ok,JSON.stringify(result));return result;}
function command(method,params={}){return new Promise((resolve,reject)=>{const key=++id;pending.set(key,{resolve,reject});ws.send(JSON.stringify({id:key,method,params}))});}
async function evaluate(fn,arg){const r=await command('Runtime.evaluate',{expression:`(${fn})(${JSON.stringify(arg)})`,returnByValue:true,awaitPromise:true});assert(!r.exceptionDetails,JSON.stringify(r.exceptionDetails));return r.result.value;}
async function wait(fn){const deadline=Date.now()+30000;while(true){const result=await fn();if(result)return result;assert(Date.now()<deadline,'UI condition timed out');await new Promise(r=>setTimeout(r,150));}}
async function click(selector){await wait(()=>evaluate(sel=>{const el=document.querySelector(sel);return Boolean(el&&!el.disabled)},selector));await evaluate(sel=>document.querySelector(sel).click(),selector);}
async function task(kind,accept=()=>true){return wait(async()=>{const s=await api('/api/state');assert(s.task.status!=='failed',JSON.stringify(s.task));return s.task.kind===kind&&s.task.status==='done'&&accept(s)?s:false;});}
async function key(name,code){await command('Input.dispatchKeyEvent',{type:'keyDown',key:name,code:name,windowsVirtualKeyCode:code});await command('Input.dispatchKeyEvent',{type:'keyUp',key:name,code:name,windowsVirtualKeyCode:code});}
async function setValue(selector,value){await wait(()=>evaluate(sel=>{const el=document.querySelector(sel);return el&&!el.disabled},selector));await evaluate(({selector,value})=>{const el=document.querySelector(selector);el.value=String(value);el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));},{selector,value});}
async function main(){
 const targets=await (await fetch('http://127.0.0.1:9343/json/list')).json();const target=targets.find(t=>t.type==='page'&&t.url===status.url+'/');assert(target);
 ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((resolve,reject)=>{ws.addEventListener('open',resolve,{once:true});ws.addEventListener('error',reject,{once:true})});
 ws.addEventListener('message',e=>{const m=JSON.parse(e.data);if(!m.id)return;const p=pending.get(m.id);if(!p)return;pending.delete(m.id);m.error?p.reject(Error(m.error.message)):p.resolve(m.result)});
 await wait(()=>evaluate(()=>document.querySelectorAll('.artwork-tile').length===1));
 await click('[data-step-target="3"]');
 assert.equal(await evaluate(()=>Number(document.querySelector('[data-placement="scale"]').value)),100);
 await evaluate(()=>{window.placementRequests=[];const original=window.fetch;window.fetch=async(url,opts)=>{if(opts?.method==='POST'&&['/api/editor','/api/preview'].includes(url))window.placementRequests.push({url,body:JSON.parse(opts.body)});const response=await original(url,opts);if(url==='/api/editor')await new Promise(resolve=>setTimeout(resolve,3400));return response;};});
 await click('[data-action="load-editor"]');
 const initial=await task('preview',s=>s.task.preview?.length===1);assert.equal(initial.task.preview_settings.scale,100);
 await wait(()=>evaluate(()=>document.querySelector('#placement-stage').classList.contains('ready')&&!document.querySelector('[data-placement="scale"]').disabled));
 const baseUrl=(await api('/api/state')).task.editor?.url;
 assert(baseUrl,'editor metadata retained after automatic preview');
 const rect=await evaluate(()=>{const r=document.querySelector('#placement-stage').getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height}});
 await command('Input.dispatchMouseEvent',{type:'mouseMoved',x:rect.x+rect.w*.5,y:rect.y+rect.h*.5});
 await command('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,x:rect.x+rect.w*.5,y:rect.y+rect.h*.5});
 await command('Input.dispatchMouseEvent',{type:'mouseMoved',button:'left',buttons:1,x:rect.x+rect.w*.65,y:rect.y+rect.h*.6});
 await command('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,x:rect.x+rect.w*.65,y:rect.y+rect.h*.6});
 const dragged=await task('preview',s=>s.task.preview_settings?.offset_x>10&&s.task.preview_settings?.offset_y>5);
 assert.equal(dragged.task.preview.length,1);assert(Math.abs(dragged.task.preview_settings.offset_x-15)<1);
 await wait(()=>evaluate(()=>{const handle=document.querySelector('[data-resize-artwork]');return handle&&!handle.disabled&&!handle.hidden&&!document.querySelector('#placement-stage').classList.contains('locked')}));
 await evaluate(()=>document.querySelector('[data-resize-artwork]').scrollIntoView({block:'center',inline:'nearest'}));
 await wait(()=>evaluate(()=>{const handle=document.querySelector('[data-resize-artwork]'),r=handle.getBoundingClientRect();return document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)===handle}));
 const handle=await evaluate(()=>{const r=document.querySelector('[data-resize-artwork]').getBoundingClientRect(),s=document.querySelector('#placement-stage').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2,inside:r.left>=s.left&&r.right<=s.right&&r.top>=s.top&&r.bottom<=s.bottom}});
 assert(handle.inside,'resize handle remains visible when cover extends outside stage');
 await command('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,x:handle.x,y:handle.y});
 await command('Input.dispatchMouseEvent',{type:'mouseMoved',button:'left',buttons:1,x:handle.x-25,y:handle.y-25});
 await command('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,x:handle.x-25,y:handle.y-25});
 await task('preview',s=>s.task.preview_settings?.scale<100);
 await setValue('input[type="range"][data-placement="scale"]',135);
 const scaled=await task('preview',s=>s.task.preview_settings?.scale===135);
 assert.equal(scaled.task.editor.url,baseUrl,'placement reuses prepared artwork');
 await setValue('#editor-scene',1);
 await task('preview',s=>s.task.editor?.scene===1&&s.task.preview?.length===1);
 await click('[data-action="preview"]');await task('preview',s=>s.task.preview?.length===3);
 await setValue('#artwork-brightness',12);
 const bright=await task('preview',s=>s.task.preview_settings?.brightness===12&&s.task.editor?.settings?.brightness===12);
 assert.equal(bright.task.preview.length,1);assert.notEqual(bright.task.editor.url,baseUrl);
 await wait(async()=>{const s=await api('/api/state');return s.preferences?.settings?.scale===135&&s.preferences?.settings?.brightness===12&&s.preferences?.settings?.offset_x>10;});
 const requests=await evaluate(()=>window.placementRequests);
 assert(requests.some(r=>r.url==='/api/preview'&&r.body.scene===1));assert.equal(requests.filter(r=>r.url==='/api/preview'&&r.body.scene===undefined).length,1);
 await wait(()=>evaluate(()=>!document.querySelector('[data-placement="scale"]').disabled));
 await wait(()=>evaluate(()=>{const image=document.querySelector('.preview-tile img');return Boolean(image?.complete&&image.naturalWidth>0)}));
 await new Promise(resolve=>setTimeout(resolve,200));
 const shot=await command('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(run,'placement-ui.png'),Buffer.from(shot.data,'base64'));
 await click('[data-action="placement-reset"]');await task('preview',s=>s.task.preview_settings?.scale===100&&s.task.preview_settings?.offset_x===0&&s.task.preview_settings?.offset_y===0);
 fs.writeFileSync(path.join(run,'ui-result.json'),JSON.stringify({drag:true,corner_resize:true,scale:135,partial_preview:true,full_scenes:3,brightness:12,persisted_preferences:true,reset:true,requests}));
 await evaluate(()=>{setTimeout(()=>window.desktop.window.action('close'),200);return true;});
 ws.close();console.log('PASS: drag, scale, current scene, full preview, brightness regeneration, preferences, reset');
}
main().catch(async error=>{
 console.error(error);
 try{
  fs.writeFileSync(path.join(run,'failure-state.json'),JSON.stringify(await api('/api/state'),null,2));
  if(ws?.readyState===WebSocket.OPEN){
   const ui=await evaluate(()=>({requests:window.placementRequests,toast:document.querySelector('#toast')?.textContent,stage:document.querySelector('#placement-stage')?.outerHTML,controls:[...document.querySelectorAll('[data-placement]')].map(el=>({key:el.dataset.placement,value:el.value,disabled:el.disabled}))}));
   fs.writeFileSync(path.join(run,'failure-ui.json'),JSON.stringify(ui,null,2));
   const shot=await command('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(run,'failure-ui.png'),Buffer.from(shot.data,'base64'));
  }
 }catch(diagnosticError){console.error('Diagnostics:',diagnosticError);}
 if(ws)ws.close();process.exitCode=1;
});
