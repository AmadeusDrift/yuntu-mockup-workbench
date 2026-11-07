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
 const targets=await (await fetch('http://127.0.0.1:9342/json/list')).json();const target=targets.find(t=>t.type==='page'&&t.url===status.url+'/');assert(target);
 ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((resolve,reject)=>{ws.addEventListener('open',resolve,{once:true});ws.addEventListener('error',reject,{once:true})});
 ws.addEventListener('message',e=>{const m=JSON.parse(e.data);if(!m.id)return;const p=pending.get(m.id);if(!p)return;pending.delete(m.id);m.error?p.reject(Error(m.error.message)):p.resolve(m.result)});
 await wait(()=>evaluate(()=>document.querySelectorAll('.artwork-tile').length===6));
 await click('[data-step-target="2"]');
 await evaluate(()=>{const boxes=document.querySelectorAll('[data-overview-artwork]');boxes[0].click()});
 await evaluate(()=>document.querySelectorAll('[data-overview-artwork]')[5].click());
 await click('[data-action="overview-toggle"]');
 await evaluate(()=>{const field=document.querySelector('#overview-scene');field.value='2';field.dispatchEvent(new Event('change',{bubbles:true}))});
 await click('[data-action="overview-many"]');const many=await task('overview',s=>s.task.overview?.count===2);
 assert(many.task.overview.title.includes('场景 3'));assert.equal(many.task.overview.count,2);
 await wait(()=>evaluate(()=>Boolean(document.querySelector('.overview-result img')?.complete)));
 const saved=await api('/api/save-overview',{folder:run});
 await click('[data-action="overview-set"]');const set=await task('overview',s=>s.task.overview?.count===5);assert.equal(set.task.overview.count,5);
 const cacheDir=path.join(process.argv[2],'previews','overview-cache');assert.equal(fs.readdirSync(cacheDir).filter(p=>p.endsWith('.png')).length,6);
 await click('[data-action="overview-toggle"]');await click('[data-action="overview-many"]');await task('overview',s=>s.task.overview?.count===2&&s.task.overview.url!==many.task.overview.url);
 assert.equal(fs.readdirSync(cacheDir).filter(p=>p.endsWith('.png')).length,6);
 await wait(()=>evaluate(()=>document.querySelector('.overview-result strong')?.textContent.includes('场景 3')&&!document.querySelector('.overview-options')&&!document.querySelector('[data-action="overview-set"]').disabled));
 const shot=await command('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(run,'overview-ui.png'),Buffer.from(shot.data,'base64'));
 await click('[data-step-target="4"]');await click('[data-action="batch"]');const done=await task('batch');assert.equal(done.history[0].counts.success,6);assert.equal(done.history[0].counts.failed,0);
 fs.writeFileSync(path.join(run,'ui-result.json'),JSON.stringify({output:done.history[0].path,saved:saved.path}));ws.close();console.log('PASS: multi-select, chosen scene, five-scene overview, cache reuse, save JPG and grouped batch');
}
main().catch(error=>{console.error(error);if(ws)ws.close();process.exitCode=1});
