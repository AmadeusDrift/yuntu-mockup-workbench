'use strict';
// User-authorized script connection to this application's temporary local debug port.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {execFile}=require('node:child_process');
const {promisify}=require('node:util');const exec=promisify(execFile);
const root=path.resolve(__dirname,'../..');
const dataRoot=path.join(root,'.analysis-temp','desktop-userdata');
const outputRoot=path.join(root,'.analysis-temp','desktop-output');
const fixtures=path.join(root,'.analysis-temp','desktop-artworks');
const status=JSON.parse(fs.readFileSync(path.join(dataRoot,'desktop-status.json'),'utf8'));
const report={desktop_exe:true,checks:[],screenshots:[]};
let socket,sequence=0;const pending=new Map();
function command(method,params={}){return new Promise((resolve,reject)=>{
  const id=++sequence,timer=setTimeout(()=>{pending.delete(id);reject(new Error(method+' timed out'))},45000);
  pending.set(id,{resolve,reject,timer});socket.send(JSON.stringify({id,method,params}));
})}
async function evaluate(fn,arg){
  const result=await command('Runtime.evaluate',{expression:`(${fn.toString()})(${JSON.stringify(arg)})`,awaitPromise:true,returnByValue:true});
  if(result.exceptionDetails)throw new Error(result.exceptionDetails.exception?.description||result.exceptionDetails.text);
  return result.result?.value;
}
async function wait(fn,arg,timeout=30000){const start=Date.now();for(;;){const result=await evaluate(fn,arg);if(result)return result;if(Date.now()-start>timeout)throw new Error('UI wait timed out');await new Promise(r=>setTimeout(r,150));}}
async function api(route,body){const response=await fetch(status.url+route,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Workbench':'1'},body:JSON.stringify(body)});const result=await response.json();if(!response.ok)throw Error(result.error);return result;}
async function nativeFolder(action,folder){
  await wait(action=>!document.querySelector(`[data-action="${action}"]`).disabled,action);
  await evaluate(()=>{window.__qaFolder=window.desktop.choose('folder');return true});
  const result=await exec('powershell.exe',['-NoProfile','-ExecutionPolicy','Bypass','-File',path.join(__dirname,'choose-folder-test.ps1'),'-OwnerPid',String(status.desktop_pid),'-Folder',folder],{windowsHide:true,timeout:20000});
  process.stdout.write(result.stdout);
  await evaluate(async action=>{const result=await window.__qaFolder;const field=document.querySelector(action==='choose-input'?'#input-path':'#output-path');field.value=result.paths[0];field.dispatchEvent(new Event('input',{bubbles:true}));field.dispatchEvent(new Event('change',{bubbles:true}));if(action==='choose-input')field.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));return true},action);
}
async function screenshot(name){
  await evaluate(()=>{window.scrollTo(0,0);return true});
  const result=await command('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
  const file=path.join(root,'release',name+'.png');fs.writeFileSync(file,Buffer.from(result.data,'base64'));report.screenshots.push(file);
}
async function main(){
  const targets=await (await fetch('http://127.0.0.1:9338/json/list')).json();
  const target=targets.find(t=>t.type==='page'&&t.url===status.url+'/');assert(target,'desktop page target');
  socket=new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true})});
  socket.addEventListener('message',event=>{const message=JSON.parse(event.data);if(!message.id)return;const entry=pending.get(message.id);if(!entry)return;clearTimeout(entry.timer);pending.delete(message.id);message.error?entry.reject(new Error(message.error.message)):entry.resolve(message.result)});
  await command('Runtime.enable');await command('Page.enable');
  await command('Page.bringToFront');
  if(process.argv.includes('--inspect')) {
    const chooser=await command('Runtime.evaluate',{expression:'window.__qaFolder',returnByValue:false});
    if(chooser.result?.objectId){const properties=await command('Runtime.getProperties',{objectId:chooser.result.objectId,ownProperties:true});console.log(JSON.stringify(properties.internalProperties,null,2));}
    console.log(JSON.stringify(await evaluate(()=>({visibility:document.visibilityState,focus:document.hasFocus(),height:innerHeight,
      images:[...document.querySelectorAll('.preview-tile img')].map(img=>({src:img.src,complete:img.complete,width:img.naturalWidth,loading:img.loading,top:img.getBoundingClientRect().top})),
      progress:document.querySelector('#task-panel')?.innerText,errors:document.querySelector('#connection')?.innerText,
      toast:document.querySelector('#toast')?.innerText,inputDisabled:document.querySelector('[data-action="choose-input"]')?.disabled,
      outputDisabled:document.querySelector('[data-action="choose-output"]')?.disabled})),null,2));
    await screenshot('桌面版-检查');socket.close();return;
  }
  const loaded=new Promise(resolve=>{const listener=event=>{if(JSON.parse(event.data).method==='Page.loadEventFired'){socket.removeEventListener('message',listener);resolve()}};socket.addEventListener('message',listener)});
  await command('Page.reload');await loaded;
  await wait(()=>document.querySelectorAll('.template-card').length===13);
  const initial=await evaluate(()=>({desktop:window.desktop?.isDesktop,cards:document.querySelectorAll('.template-card').length,
    thumbnails:document.querySelectorAll('.template-card img').length,advanced:document.querySelector('#advanced-settings').open,
    fit:document.querySelector('[data-setting="fit"]').value,original:document.querySelector('[data-original-size]').checked}));
  assert.equal(initial.desktop,true);assert.equal(initial.thumbnails,13);assert.equal(initial.advanced,false);assert.equal(initial.fit,'auto');assert.equal(initial.original,true);
  report.checks.push('13 template thumbnails; desktop bridge; auto/original-size defaults; advanced collapsed');
  const rejection=await evaluate(async()=>{try{await window.desktop.choose('invalid');return false}catch{return true}});assert(rejection);
  report.checks.push('native IPC sender and argument validation');
  await nativeFolder('choose-input',fixtures);
  await wait(()=>document.querySelectorAll('[data-artwork]').length===3);
  assert.equal(await evaluate(()=>document.querySelector('#input-path').value),fixtures);
  await nativeFolder('choose-output',outputRoot);
  await wait(expected=>document.querySelector('#output-path').value===expected,outputRoot);
  await wait(async()=>{const state=await (await fetch('/api/state')).json();return Boolean(state.preferences.output)});
  report.checks.push('native folder dialogs populate input and output; three artwork thumbnails');
  const shirtId=await evaluate(()=>{
    const card=[...document.querySelectorAll('.template-card')].find(c=>c.querySelector('button').getAttribute('aria-label').includes('男款圣诞T恤'));
    const id=card.querySelector('[data-configure-template]').dataset.configureTemplate;
    card.querySelector('[data-configure-template]').click();
    const ids=[...document.querySelectorAll('[data-select-template]')].map(input=>input.dataset.selectTemplate);
    for(const candidateId of ids){const input=document.querySelector(`[data-select-template="${candidateId}"]`);if(input.checked!==(candidateId===id))input.click();}
    const details=document.querySelector('#scene-settings');details.open=false;
    return id;
  });
  assert.equal(await evaluate(()=>document.querySelectorAll('[data-select-template]:checked').length),1);
  const cases=[['01-透明印花.png','contain'],['02-满版接近比例.png','cover'],['03-横版大比例差.png','contain']];
  for(const [name,mode] of cases){
    await wait(()=>!document.querySelector('[data-action="preview"]').disabled&&![...document.querySelectorAll('[data-artwork]')].some(b=>b.disabled));
    console.log('Previewing '+name);
    await evaluate(name=>{const item=[...document.querySelectorAll('[data-artwork]')].find(b=>b.title===name);if(!item)throw Error('artwork missing '+name);item.click();document.querySelector('[data-action="preview"]').click();return true},name);
    await wait(async expected=>{const state=await (await fetch('/api/state')).json();if(state.task.status==='failed')throw Error(state.task.error);return state.task.status==='done'&&state.task.preview_source?.endsWith(expected)},name,45000);
    const state=await api('/api/state');assert.equal(state.task.preview.length,7);assert.equal(state.task.preview_template_id,shirtId);assert(state.task.preview_fit.length>0);assert.equal(state.task.preview_fit[0].mode,mode);
    report.checks.push(name+': seven scene previews; '+state.task.preview_fit.map(f=>f.reason).join(';'));
  }
  await wait(()=>document.querySelectorAll('.preview-tile').length===7);
  await wait(()=>!document.querySelector('[data-action="batch"]').disabled);
  await exec('powershell.exe',['-NoProfile','ExecutionPolicy','Bypass','-File',path.join(__dirname,'window-test.ps1'),'-OwnerPid',String(status.desktop_pid),'-ExpectedExe',path.join(root,'release','套图工作台-v0.2.0','套图工作台.exe')].map(value=>value==='ExecutionPolicy'?'-ExecutionPolicy':value),{windowsHide:true,timeout:10000});
  await evaluate(()=>{for(const image of document.querySelectorAll('.preview-tile img'))image.loading='eager';return true});
  await wait(()=>[...document.querySelectorAll('.preview-tile img')].every(img=>img.complete&&img.naturalWidth>0));
  await screenshot('桌面版-操作预览');
  await evaluate(()=>{document.querySelector('[data-action="batch"]').click();return true});
  await wait(async()=>{const state=await (await fetch('/api/state')).json();if(state.task.status==='failed')throw Error(state.task.error);return state.task.kind==='batch'&&state.task.status==='done'},undefined,60000);
  const final=await api('/api/state');const history=final.history[0];assert.equal(history.counts.success,3);assert.equal(history.counts.failed,0);
  const products=fs.readdirSync(history.path,{withFileTypes:true}).filter(p=>p.isDirectory());assert.equal(products.length,3);
  let count=0;for(const product of products)count+=fs.readdirSync(path.join(history.path,product.name)).filter(f=>f.endsWith('.jpg')).length;
  assert.equal(count,21);report.checks.push('three artworks batch produces 21 JPGs in selected output folder');
  report.output=history.path;report.status='PASS';
  fs.writeFileSync(path.join(root,'release','desktop-test.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify(report,null,2));socket.close();
}
main().catch(error=>{console.error(error);if(socket)socket.close();process.exitCode=1});
