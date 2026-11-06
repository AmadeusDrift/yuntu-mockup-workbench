'use strict';
const fs=require('node:fs'),fsp=fs.promises,path=require('node:path'),crypto=require('node:crypto');
const ORIGIN='https://taotu.htcerp.app',FEED=ORIGIN+'/updates/stable/latest.json';
const versionPattern=/^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/;
function isNewer(a,b){if(!versionPattern.test(a)||!versionPattern.test(b))return false;const x=a.split('.').map(Number),y=b.split('.').map(Number);for(let i=0;i<3;i++)if(x[i]!==y[i])return x[i]>y[i];return false}
function day(date=new Date()){return `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`}
function dueToday(last,date=new Date()){return last!==day(date)}
function verifyManifest(envelope,key){
  if(!envelope||typeof envelope.payload!=='string'||typeof envelope.signature!=='string'||envelope.payload.length>65536)throw Error('更新清单格式无效');
  const raw=Buffer.from(envelope.payload,'base64');
  if(!crypto.verify(null,raw,key,Buffer.from(envelope.signature,'base64')))throw Error('更新签名校验失败');
  const p=JSON.parse(raw.toString('utf8'));
  if(p.product!=='yuntu-mockup-workbench'||!versionPattern.test(p.version)||p.url!==`${ORIGIN}/updates/stable/Taotu-Workbench-Setup-${p.version}-x64.exe`||!Number.isSafeInteger(p.size)||p.size<1||p.size>1073741824||!/^[a-f0-9]{64}$/.test(p.sha256)||typeof p.notes!=='string'||p.notes.length>6000)throw Error('更新清单内容无效');
  return p;
}
async function smallJSON(response){if(!response.ok)throw Error('更新服务暂不可用');const chunks=[];let size=0;for await(const chunk of response.body){size+=chunk.length;if(size>65536)throw Error('更新清单过大');chunks.push(chunk)}return JSON.parse(Buffer.concat(chunks).toString('utf8'))}
async function fileHash(file){const hash=crypto.createHash('sha256');for await(const chunk of fs.createReadStream(file))hash.update(chunk);return hash.digest('hex')}
class Updater {
  constructor({dataRoot,version,publicKey,fetch=globalThis.fetch,onChange=()=>{}}){this.dir=path.join(dataRoot,'updates');fs.mkdirSync(this.dir,{recursive:true});this.version=version;this.key=publicKey;this.fetch=fetch;this.onChange=onChange;this.state={status:'idle',current:version};this.busy=false;this.release=null;this.file=null;this.controller=null;try{this.lastDay=JSON.parse(fs.readFileSync(path.join(this.dir,'check.json'))).day}catch{this.lastDay=''}}
  set(values){this.state={...this.state,...values};this.onChange({...this.state})}
  async check(manual=false){
    if(this.busy||this.state.status==='installing'||(!manual&&!dueToday(this.lastDay)))return this.state;
    this.busy=true;this.set({status:'checking',message:'正在检查更新…',manual});
    try{
      this.lastDay=day();fs.writeFileSync(path.join(this.dir,'check.json'),JSON.stringify({day:this.lastDay}));
      const result=await smallJSON(await this.fetch(FEED,{redirect:'error',cache:'no-store',signal:AbortSignal.timeout(12000)}));
      if(result?.product==='yuntu-mockup-workbench'&&result.available===false){this.set({status:'current',message:'暂时没有可用更新'});return this.state}
      const release=verifyManifest(result,this.key);
      if(!isNewer(release.version,this.version)){this.release=null;this.set({status:'current',message:'当前已是最新版本'});return this.state}
      this.release=release;this.file=path.join(this.dir,`Taotu-Workbench-Setup-${release.version}-x64.exe`);
      this.set({status:'available',version:release.version,notes:release.notes,size:release.size,progress:0,message:'发现新版本'});
    }catch(error){this.set({status:'offline',message:'暂时无法检查更新，可继续离线使用。',detail:error.message})}finally{this.busy=false}
    return this.state;
  }
  async download(){
    if(this.busy||!this.release||this.state.status==='installing')return this.state;
    this.busy=true;this.controller=new AbortController();const timer=setTimeout(()=>this.controller.abort(),15*60*1000);const part=this.file+'.part';let handle;
    this.set({status:'downloading',progress:0,message:'正在下载更新…'});
    try{
      try{await this.verifiedInstaller();this.set({status:'ready',progress:100,message:'下载完成，可以安装'});return this.state}catch{}
      const response=await this.fetch(this.release.url,{redirect:'error',signal:this.controller.signal});
      if(response.status!==200)throw Error('下载服务暂不可用');
      if(response.headers.has('content-length')&&Number(response.headers.get('content-length'))!==this.release.size)throw Error('安装包大小不匹配');
      handle=await fsp.open(part,'w');const hash=crypto.createHash('sha256');let bytes=0,last=0;
      for await(const chunk of response.body){bytes+=chunk.length;if(bytes>this.release.size)throw Error('安装包超过声明大小');hash.update(chunk);await handle.writeFile(chunk);if(Date.now()-last>200){this.set({progress:Math.floor(bytes/this.release.size*100)});last=Date.now()}}
      await handle.close();handle=null;
      if(bytes!==this.release.size||hash.digest('hex')!==this.release.sha256)throw Error('安装包校验失败，请重新下载');
      await fsp.rename(part,this.file);this.set({status:'ready',progress:100,message:'下载完成，可以安装'});
    }catch(error){this.set({status:'error',message:this.controller.signal.aborted?'下载已取消，可以稍后重试。':error.message})}
    finally{clearTimeout(timer);if(handle)await handle.close();await fsp.unlink(part).catch(()=>{});this.controller=null;this.busy=false}
    return this.state;
  }
  cancel(){this.controller?.abort()}
  async verifiedInstaller(){if(!this.release||!this.file)throw Error('请先下载更新');const stat=await fsp.stat(this.file);if(stat.size!==this.release.size||await fileHash(this.file)!==this.release.sha256)throw Error('安装包校验失败，请重新下载');return this.file}
}
module.exports={Updater,verifyManifest,isNewer,dueToday,fileHash};
