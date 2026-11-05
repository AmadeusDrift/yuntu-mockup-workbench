'use strict';
const {app, BrowserWindow, ipcMain, dialog, Menu, screen} = require('electron');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');

app.setName('套图工作台');
app.setAppUserModelId('com.yuntu.mockup.workbench');
app.commandLine.appendSwitch('force-renderer-accessibility');
const explicitData = process.argv.find(arg => arg.startsWith('--data-dir='));
if (explicitData) app.setPath('userData', path.resolve(explicitData.slice(11)));
const dataRoot = app.getPath('userData');
let window, backend, origin = '', exiting = false, closing = false;

function api(route, body) {
  return new Promise((resolve, reject) => {
    const data = body === undefined ? null : Buffer.from(JSON.stringify(body));
    const req = http.request(origin + route, {method:data?'POST':'GET',headers:data?{
      'Content-Type':'application/json','X-Workbench':'1','Content-Length':data.length}:{}}, response => {
      const chunks=[];
      response.on('data', chunk=>chunks.push(chunk));
      response.on('end',()=>{try {
        const result=JSON.parse(Buffer.concat(chunks).toString('utf8'));
        if(response.statusCode>=400||result.error)reject(new Error(result.error||'本地处理未完成'));
        else resolve(result);
      } catch(error){reject(error)}});
    });
    req.setTimeout(15000,()=>req.destroy(new Error('本地处理响应超时')));
    req.on('error',reject);
    if(data)req.write(data);
    req.end();
  });
}

function trusted(event) {
  if (!window || event.sender !== window.webContents || !event.senderFrame ||
      event.senderFrame.url !== origin + '/') throw new Error('操作来源无效');
}

async function stopBackend() {
  if (!backend || backend.exitCode !== null) return;
  if (origin) {
    try {
      let state = await api('/api/state');
      if (['running','paused','stopping'].includes(state.task.status)) {
        if(window&&!window.isDestroyed())window.setTitle('正在保存套图进度，请稍候…');
        await api('/api/control',{action:'stop'});
        while (['running','paused','stopping'].includes(state.task.status)) {
          await new Promise(resolve=>setTimeout(resolve,200));
          state=await api('/api/state');
        }
      }
    } catch(error) {
      fs.appendFileSync(path.join(dataRoot,'desktop.log'),'Shutdown: '+error.message+'\n');
    }
  }
  const child=backend;
  await new Promise(resolve=>{
    if(child.exitCode!==null)return resolve();
    const timer=setTimeout(()=>{child.kill();resolve()},10000);
    child.once('exit',()=>{clearTimeout(timer);resolve()});
    child.stdin.end('shutdown\n');
  });
}

function seedData() {
  fs.mkdirSync(dataRoot,{recursive:true});
  const marker=path.join(dataRoot,'desktop-seeded.json');
  if(fs.existsSync(marker))return;
  const seed=path.join(__dirname,'seed');
  if(fs.existsSync(seed)) {
    for(const name of ['templates.json','thumbs']) {
      const source=path.join(seed,name),target=path.join(dataRoot,name);
      if(fs.existsSync(source)&&!fs.existsSync(target))fs.cpSync(source,target,{recursive:true});
    }
  }
  fs.writeFileSync(marker,JSON.stringify({version:app.getVersion()}));
}

function launchBackend() {
  return new Promise((resolve,reject)=>{
    const pythonRoot=path.join(__dirname,'python');
    const executable=path.join(pythonRoot,'runtime','python.exe');
    const script=path.join(__dirname,'workbench','server.py');
    backend=spawn(executable,['-I','-B',script,'--no-open','--desktop','--data-root',dataRoot],{
      cwd:__dirname,windowsHide:true,stdio:['pipe','pipe','pipe'],
      env:{...process.env,MOCKUP_RUNTIME_ROOT:pythonRoot,PYTHONIOENCODING:'utf-8'}
    });
    let output='',errorText='',settled=false;
    const timer=setTimeout(()=>finish(new Error('启动超时，请重新打开软件。')),45000);
    function finish(error,url){if(settled)return;settled=true;clearTimeout(timer);error?reject(error):resolve(url)}
    backend.on('error',error=>finish(error));
    backend.stdout.on('data',chunk=>{
      output+=chunk.toString('utf8');
      for(const line of output.split(/\r?\n/))if(/^http:\/\/127\.0\.0\.1:\d+$/.test(line))finish(null,line);
    });
    backend.stderr.on('data',chunk=>{errorText+=chunk.toString('utf8');fs.appendFileSync(path.join(dataRoot,'desktop.log'),chunk)});
    backend.on('exit',code=>{
      if(!settled)finish(new Error(errorText.slice(-1200)||'处理程序未能启动，退出码：'+code));
      else if(!exiting&&!closing&&window&&!window.isDestroyed()) {
        dialog.showErrorBox('本地处理程序已退出','请关闭软件后重新打开。已保存的模板设置和批次进度会保留。');
        exiting=true;app.quit();
      }
    });
  });
}

if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance',()=>{if(window){if(window.isMinimized())window.restore();window.show();window.focus()}});
  app.whenReady().then(async()=>{
    if(fs.existsSync(path.join(path.dirname(app.getPath('exe')),'.workbench-update.json')))throw new Error('上次更新尚未完成。请关闭软件，重新运行安装包，恢复完成后再打开。');
    seedData();Menu.setApplicationMenu(null);
    const area=screen.getPrimaryDisplay().workAreaSize;
    window=new BrowserWindow({width:Math.min(1520,area.width),height:Math.min(960,area.height),minWidth:1060,minHeight:700,
      title:'套图工作台 v'+app.getVersion(),show:false,backgroundColor:'#f3f8fe',icon:path.join(__dirname,'app.ico'),
      webPreferences:{preload:path.join(__dirname,'preload.cjs'),nodeIntegration:false,contextIsolation:true,sandbox:true}});
    window.webContents.setWindowOpenHandler(()=>({action:'deny'}));
    window.on('page-title-updated',event=>event.preventDefault());
    window.webContents.session.setPermissionRequestHandler((_contents,_permission,callback)=>callback(false));
    window.webContents.on('will-navigate',(event,url)=>{if(url!==origin+'/')event.preventDefault()});
    window.on('close',event=>{
      if(exiting)return;
      event.preventDefault();if(closing)return;closing=true;
      stopBackend().finally(()=>{exiting=true;window.destroy();app.quit()});
    });
    ipcMain.handle('choose',async(event,kind)=>{
      trusted(event);
      if(!['folder','psd','image'].includes(kind))throw new Error('选择类型无效');
      const config=kind==='folder'?{title:'选择文件夹',properties:['openDirectory','createDirectory']}:
        kind==='psd'?{title:'选择 PSD 模板',properties:['openFile','multiSelections'],filters:[{name:'Photoshop',extensions:['psd','psb']}]}:
        {title:'选择印花图片',properties:['openFile'],filters:[{name:'图片',extensions:['png','jpg','jpeg','tif','tiff','webp','bmp']}]};
      const result=await dialog.showOpenDialog(window,config);
      return {paths:result.canceled?[]:result.filePaths};
    });
    ipcMain.handle('open-output',async(event,folder)=>{trusted(event);return api('/api/open-output',{path:folder})});
    origin=await launchBackend();
    fs.writeFileSync(path.join(dataRoot,'desktop-status.json'),JSON.stringify({desktop_pid:process.pid,backend_pid:backend.pid,url:origin}));
    await window.loadURL(origin+'/');window.show();
  }).catch(async error=>{
    dialog.showErrorBox('套图工作台未能打开',error.message);
    exiting=true;await stopBackend();app.quit();
  });
  app.on('window-all-closed',()=>{if(exiting)app.quit()});
  app.on('before-quit',event=>{
    if(exiting)return;event.preventDefault();
    if(window&&!window.isDestroyed())window.close();
    else stopBackend().finally(()=>{exiting=true;app.quit()});
  });
}
