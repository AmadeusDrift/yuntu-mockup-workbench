'use strict';
// node publish.cjs <installer> <private-key.pem> <publisher-token.txt> <notes.txt>
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {fileHash,verifyManifest}=require('../desktop/updater.cjs');
async function main(){
 const [file,keyFile,tokenFile,notesFile]=process.argv.slice(2);if(!notesFile)throw Error('Provide installer, signing key, publishing token and release notes');
 const version=require('../desktop/package.json').version,name=`Taotu-Workbench-Setup-${version}-x64.exe`,origin='https://taotu.htcerp.app';
 const token=fs.readFileSync(tokenFile,'utf8').trim(),key=fs.readFileSync(keyFile),size=fs.statSync(file).size,sha256=await fileHash(file);
 const p={product:'yuntu-mockup-workbench',version,url:origin+'/updates/stable/'+name,size,sha256,notes:fs.readFileSync(notesFile,'utf8'),publishedAt:new Date().toISOString()};
 const raw=Buffer.from(JSON.stringify(p)),envelope={payload:raw.toString('base64'),signature:crypto.sign(null,raw,key).toString('base64')};
 verifyManifest(envelope,fs.readFileSync(path.join(__dirname,'../desktop/update-public-key.pem')));
 async function post(action,data,params={}){const url=new URL('/publish/'+action,origin);for(const [k,v]of Object.entries({name,...params}))url.searchParams.set(k,v);const r=await fetch(url,{method:'POST',headers:{Authorization:'Bearer '+token},body:data,signal:AbortSignal.timeout(120000)});const result=await r.json();if(!r.ok)throw Error(`${action}: ${r.status} ${result.error}`);return result}
 const existing=await fetch(p.url,{method:'HEAD'});
 if(existing.status===200){if(Number(existing.headers.get('content-length'))!==size)throw Error('Existing version size differs');console.log('Release exists; validating full remote SHA256 before updating manifest');const response=await fetch(p.url),hash=crypto.createHash('sha256');for await(const chunk of response.body)hash.update(chunk);if(hash.digest('hex')!==sha256)throw Error('Existing version hash differs');}
 else{
   const {uploadId}=await post('start',JSON.stringify({size,sha256}));const handle=fs.openSync(file,'r'),parts=[];
   try{for(let offset=0,number=1;offset<size;number++){
     const buffer=Buffer.alloc(Math.min(8*1024*1024,size-offset));fs.readSync(handle,buffer,0,buffer.length,offset);let result;
     for(let attempt=0;attempt<3;attempt++){try{result=await post('part',buffer,{id:uploadId,part:number});break}catch(error){if(attempt===2)throw error}}
     parts.push(result);offset+=buffer.length;console.log(`Uploaded ${Math.floor(offset/size*100)}%`);
   }const done=await post('complete',JSON.stringify(parts),{id:uploadId});if(done.size!==size)throw Error('Uploaded size differs');}
   catch(error){await post('abort','',{id:uploadId}).catch(()=>{});throw error}finally{fs.closeSync(handle)}
 }
 // Check bytes served by the public endpoint before making the release discoverable.
 const response=await fetch(p.url,{signal:AbortSignal.timeout(300000)});if(!response.ok)throw Error('Public installer unavailable');const hash=crypto.createHash('sha256');for await(const chunk of response.body)hash.update(chunk);if(hash.digest('hex')!==sha256)throw Error('Public installer hash mismatch');
 await post('manifest',JSON.stringify(envelope));
 const published=await (await fetch(origin+'/updates/stable/latest.json',{cache:'no-store'})).json();verifyManifest(published,crypto.createPublicKey(key));if(published.payload!==envelope.payload)throw Error('Published manifest differs');
 fs.writeFileSync(path.resolve('release/remote-update-verification.json'),JSON.stringify({version,url:p.url,sha256,size,signature_verified:true,remote_file_verified:true,published:true},null,2));console.log('Published and verified '+version);
}
main().catch(error=>{console.error(error.message);process.exitCode=1});
