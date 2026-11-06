// Public downloads are read-only; publishing requires a server-side secret.
import {timingSafeEqual} from 'node:crypto';
const PRODUCT = 'yuntu-mockup-workbench';
function json(value, status=200) {
  return new Response(JSON.stringify(value), {status,headers:{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}});
}
export default {
  async fetch(request, env) {
    const pathname=new URL(request.url).pathname;
    if(pathname.startsWith('/publish/'))return publish(request,env);
    if (!['GET','HEAD'].includes(request.method)) return new Response(null,{status:405,headers:{Allow:'GET, HEAD'}});
    const path = new URL(request.url).pathname;
    if(path==='/' || path==='/health') {
      const response=json({product:PRODUCT,service:'release-downloads'});
      return request.method==='HEAD'?new Response(null,response):response;
    }
    const manifest = path==='/updates/stable/latest.json';
    const artifact = /^\/updates\/stable\/Taotu-Workbench-Setup-\d+\.\d+\.\d+-x64\.exe$/.test(path);
    if(!manifest&&!artifact)return json({error:'Not found'},404);
    const key=path.slice('/updates/'.length);
    try {
      const object=await env.RELEASES[request.method==='HEAD'?'head':'get'](key);
      if(!object) {
        const response=manifest?json({product:PRODUCT,available:false,reason:'No release published'}):json({error:'Not found'},404);
        return request.method==='HEAD'?new Response(null,response):response;
      }
      const headers=new Headers({'Content-Type':manifest?'application/json; charset=utf-8':'application/octet-stream','Content-Length':String(object.size),'Cache-Control':manifest?'no-store':'public, max-age=31536000, immutable','ETag':object.httpEtag,'X-Content-Type-Options':'nosniff'});
      // Full responses are streamed, including when the caller requests a range.
      // Returning 200 lets clients safely fall back to a complete download.
      return new Response(request.method==='HEAD'?null:object.body,{headers});
    } catch {
      console.error(JSON.stringify({event:'release_storage_unavailable',path}));
      return json({error:'Release storage unavailable'},503);
    }
  }
};

async function boundedBody(request,limit){let size=0;const chunks=[];for await(const c of request.body||[]){size+=c.length;if(size>limit)throw Error('Request too large');chunks.push(c)}const result=new Uint8Array(size);let offset=0;for(const c of chunks){result.set(c,offset);offset+=c.length}return result}
async function publish(request,env){
  const token=request.headers.get('authorization')?.replace(/^Bearer /,'')||'';
  const enc=new TextEncoder(),expected=enc.encode(env.PUBLISH_TOKEN||''),actual=enc.encode(token);
  if(expected.length<64||actual.length!==expected.length||!timingSafeEqual(expected,actual))return json({error:'Forbidden'},403);
  if(request.method!=='POST')return json({error:'Method not allowed'},405);
  try{
    const url=new URL(request.url),action=url.pathname.slice('/publish/'.length);
    if(action==='manifest'){
      const body=await boundedBody(request,65536);const envelope=JSON.parse(new TextDecoder().decode(body));
      // The desktop verifies the Ed25519 signature. Check artifact identity before publishing.
      const p=JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(envelope.payload),c=>c.charCodeAt(0))));
      if(p.product!==PRODUCT||!/^\d+\.\d+\.\d+$/.test(p.version)||p.url!==`https://taotu.htcerp.app/updates/stable/Taotu-Workbench-Setup-${p.version}-x64.exe`||typeof envelope.signature!=='string')return json({error:'Invalid manifest'},400);
      const object=await env.RELEASES.head(`stable/Taotu-Workbench-Setup-${p.version}-x64.exe`);
      if(!object||object.size!==p.size||object.customMetadata?.sha256!==p.sha256)return json({error:'Upload matching artifact first'},409);
      await env.RELEASES.put('stable/latest.json',body,{httpMetadata:{contentType:'application/json',cacheControl:'no-store'}});return json({ok:true});
    }
    const name=url.searchParams.get('name');
    if(!/^Taotu-Workbench-Setup-\d+\.\d+\.\d+-x64\.exe$/.test(name||''))return json({error:'Invalid release name'},400);
    const key='stable/'+name;
    const claim='.claims/'+name;
    if(action==='start'){
      if(await env.RELEASES.head(key))return json({error:'Version already published'},409);
      const meta=JSON.parse(new TextDecoder().decode(await boundedBody(request,4096)));
      if(!/^[a-f0-9]{64}$/.test(meta.sha256)||!Number.isSafeInteger(meta.size)||meta.size<1||meta.size>1073741824)return json({error:'Invalid metadata'},400);
      // Conditional creation is atomic across Worker instances. Keep successful claims.
      const locked=await env.RELEASES.put(claim,'pending',{onlyIf:new Headers({'If-None-Match':'*'})});
      if(!locked)return json({error:'Version is already being published'},409);
      let upload;
      try{upload=await env.RELEASES.createMultipartUpload(key,{httpMetadata:{contentType:'application/octet-stream'},customMetadata:{sha256:meta.sha256,size:String(meta.size)}});await env.RELEASES.put(claim,upload.uploadId)}catch(error){await env.RELEASES.delete(claim);throw error}
      return json({uploadId:upload.uploadId});
    }
    const id=url.searchParams.get('id');if(!id||id.length>512)return json({error:'Invalid upload'},400);
    const owner=await env.RELEASES.get(claim);if(!owner||await owner.text()!==id)return json({error:'Upload ownership mismatch'},409);
    const upload=env.RELEASES.resumeMultipartUpload(key,id);
    if(action==='part'){
      const number=Number(url.searchParams.get('part'));if(!Number.isInteger(number)||number<1||number>200)return json({error:'Invalid part'},400);
      return json(await upload.uploadPart(number,await boundedBody(request,8*1024*1024)));
    }
    if(action==='complete'){
      if(await env.RELEASES.head(key))return json({error:'Version already published'},409);
      const parts=JSON.parse(new TextDecoder().decode(await boundedBody(request,65536)));const result=await upload.complete(parts);return json({size:result.size});
    }
    if(action==='abort'){if(await env.RELEASES.head(key))return json({error:'Version already published'},409);await upload.abort();await env.RELEASES.delete(claim);return json({ok:true})}
    return json({error:'Not found'},404);
  }catch{return json({error:'Release publishing failed'},400)}
}
