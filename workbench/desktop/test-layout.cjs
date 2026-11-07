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
 await wait(()=>evaluate(()=>document.querySelectorAll('.template-card').length===16));
 await command('Emulation.setDeviceMetricsOverride',{width:1504,height:920,deviceScaleFactor:1,mobile:false});
 await evaluate(()=>{document.querySelector('.template-cards').scrollTop=500});
 const before=await evaluate(()=>document.querySelector('.template-cards').scrollTop);
 await click('[data-select-template="fixture-10"]');
 const after=await evaluate(()=>document.querySelector('.template-cards').scrollTop);
 assert(Math.abs(after-before)<2,`Checkbox scroll jumped ${before} -> ${after}`);
 await click('[data-configure-template="fixture-10"]');
 assert.equal(await evaluate(()=>document.querySelector('#scene-settings').open),false);
 assert(Math.abs(await evaluate(()=>document.querySelector('.template-cards').scrollTop)-before)<2,'Preview click scroll jumped');
 await wait(()=>evaluate(()=>document.querySelectorAll('.window-controls button').length===3));
 await command('Emulation.clearDeviceMetricsOverride');
 await click('[data-window-action="maximize"]');await wait(()=>evaluate(()=>window.desktop.window.state().then(s=>s.maximized)));
 const collapsed=await evaluate(()=>document.querySelector('#scene-settings').offsetHeight);assert(collapsed<60,'Collapsed scene area grew');
 await click('[data-window-action="maximize"]');await wait(()=>evaluate(()=>window.desktop.window.state().then(s=>!s.maximized)));
 await click('[data-window-action="minimize"]');await wait(()=>evaluate(()=>window.desktop.window.state().then(s=>s.minimized)));
 require('node:child_process').execFileSync('powershell.exe',['-NoProfile','-ExecutionPolicy','Bypass','-File',path.resolve(__dirname,'window-test.ps1'),'-OwnerPid',String(status.desktop_pid),'-ExpectedExe',process.argv[4],'-Action','show']);
 const metrics=[];
 for(const [width,height] of [[1504,920],[1060,700],[1920,1009]]){
  await command('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:false});
  await new Promise(r=>setTimeout(r,150));
  const m=await evaluate(()=>{const grid=document.querySelector('.template-cards'),card=grid.firstElementChild;return {height:grid.clientHeight,cardHeight:card.offsetHeight,labelVisible:card.querySelector('label').getBoundingClientRect().bottom<=card.getBoundingClientRect().bottom,overflow:document.documentElement.scrollWidth>innerWidth,footer:document.querySelector('.workflow-footer').getBoundingClientRect().bottom,viewport:innerHeight}});
  assert(m.labelVisible,'Card label clipped');assert(m.cardHeight>120,'Cards squeezed');assert(m.height/m.cardHeight>=2,JSON.stringify(m));assert(!m.overflow);assert(m.footer<=height+1);metrics.push({width,height,...m});
  await evaluate(()=>document.querySelector('.template-cards').scrollTop=0);
  const shot=await command('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(run,`layout-${width}.png`),Buffer.from(shot.data,'base64'));
 }
 await command('Emulation.setDeviceMetricsOverride',{width:1520,height:930,deviceScaleFactor:1,mobile:false});
 await click('#scene-settings summary');assert(await evaluate(()=>document.querySelector('#scene-settings').open));
 console.log(await evaluate(()=>['.scene-settings','.scene-list','.template-cards','.editor-panel'].map(s=>{const e=document.querySelector(s);return {selector:s,rect:e.getBoundingClientRect().toJSON(),scroll:e.scrollTop,height:e.scrollHeight}})));
 const expanded=await command('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(run,'expanded.png'),Buffer.from(expanded.data,'base64'));
 await evaluate(()=>{document.querySelector('#scene-settings').scrollTop=120});
 const sceneTop=await evaluate(()=>document.querySelector('#scene-settings').scrollTop);
 await evaluate(()=>{const el=document.querySelector('[data-scene-mode]');el.value='fixed';el.dispatchEvent(new Event('change',{bubbles:true}))});
 assert.equal(await evaluate(()=>document.querySelector('#scene-settings').scrollTop),sceneTop);
 await click('#scene-settings summary');
 await click('[data-step-target="2"]');await evaluate(()=>{document.querySelector('.editor-panel').scrollTop=100});
 await click('[data-step-target="3"]');assert.equal(await evaluate(()=>document.querySelector('.editor-panel').scrollTop),0);
 fs.writeFileSync(path.join(run,'layout-result.json'),JSON.stringify(metrics,null,2));await click('[data-window-action="close"]');ws.close();console.log('PASS: checkbox and preview keep scroll; two full rows at both desktop sizes');
}
main().catch(error=>{console.error(error);if(ws)ws.close();process.exitCode=1});
