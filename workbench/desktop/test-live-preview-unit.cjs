'use strict';
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const {test}=require('node:test');

// Run the real app without its network bootstrap. DOM/image adapters only replace
// browser facilities; the queue, identities and response ordering are production code.
function boot(){
  const events=new Map(),windowEvents=new Map(),timers=new Map(),requests=[];let timerId=0;
  const element={addEventListener(type,fn){if(!events.has(type))events.set(type,[]);events.get(type).push(fn);},classList:{toggle(){},add(){},remove(){}},dataset:{},style:{},matches(){return false;},hasAttribute(){return false;},setAttribute(){}};
  const sandbox={console,structuredClone,JSON,Map,Set,Promise,Date,Number,Math,Boolean,String,Array,Uint8ClampedArray,
    window:{addEventListener(type,fn){windowEvents.set(type,fn);}},document:{querySelector(){return element;},querySelectorAll(){return [];},activeElement:null},
    setTimeout(fn,delay){const id=++timerId;timers.set(id,{fn,delay});return id;},clearTimeout(id){timers.delete(id);},
    Image:class{set src(value){this.url=value;this.complete=true;this.naturalWidth=16;this.naturalHeight=16;queueMicrotask(()=>this.onload?.());}},
    fetch(){throw Error('Unexpected bootstrap fetch');}};
  const ctx=vm.createContext(sandbox),run=code=>vm.runInContext(code,ctx);
  const file=path.join(__dirname,'../web/app.js');
  run(fs.readFileSync(file,'utf8').replace(/\npoll\(\);\r?\n/,'\n'));
  run(`paintEditor=()=>{}; updateLocks=()=>{}; renderTask=()=>{}; updateWorkflow=()=>{}; notify=()=>{}; renderPage=()=>{};
    if(typeof paintPreviewStatus==='function')paintPreviewStatus=()=>{};
    if(typeof updatePreviewContent==='function')updatePreviewContent=()=>{};
    persistPreferences=async()=>{}; refresh=async()=>{};
    connected=true; initialized=true; editorEnabled=true; selectedId='template'; source='A.png'; input='artworks';
    state={templates:[{id:'template',digest:'v1',scenes:[{id:'one',name:'One',mode:'replace',targets:['print'],artwork_roles:{}},{id:'two',name:'Two',mode:'replace',targets:['print'],artwork_roles:{}}]}],task:{status:'idle'},history:[],cache:{}};
    editorImage={complete:true,naturalWidth:16}; editorBase={url:'base',stage_width:100,stage_height:100}; editorBaseKey=editorKey();`);
  sandbox.send=(url,body)=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});requests.push({url,body,resolve,reject});return promise;};
  run(`request=(url,body)=>send(url,body);`);
  return {run,requests,timers,events,windowEvents,ctx,async drain(){for(let i=0;i<8;i++)await Promise.resolve();}};
}

test('known incompatible template never queues PSD work and report escapes layer names',async()=>{
  const b=boot();b.run(`state.templates[0].compatibility={status:'blocked',issues:[{message:'1/<unsupported>'}],warnings:[]};queueEditorPreview(false);`);
  await b.run('flushEditor()');
  assert.equal(b.requests.length,0);
  assert.equal(b.run('compatibilityBlocked(currentTemplate())'),true);
  const html=b.run('renderCompatibility(currentTemplate())');
  assert.match(html,/&lt;unsupported&gt;/);assert.doesNotMatch(html,/<unsupported>/);
});

test('pending and review compatibility reports keep live editing available',()=>{
  const b=boot();
  for(const status of ['pending','review','ready']){
    b.run(`state.templates[0].compatibility={status:'${status}',issues:[],warnings:[]};`);
    assert.equal(b.run('editorBlocked()'),false);
  }
});

test('drag movement dispatches a draft while pointer is still held',async()=>{
  const b=boot();b.run(`editorDrag={pointer:1};settings.offset_x=17;queueEditorPreview();`);
  const running=b.run('flushEditor()');await b.drain();
  assert.equal(b.requests.length,1,'a pointer-held movement must send the first right-side preview');
  assert.equal(b.requests[0].url,'/api/preview');assert.equal(b.requests[0].body.draft,true);
  assert.equal(b.requests[0].body.settings.offset_x,17);void running;
});

test('brightness and placement preserve the unbrightened editor geometry',()=>{
  const b=boot(),key=b.run('editorKey()');
  b.run('settings.brightness=14;settings.scale=145;settings.offset_y=-7');
  assert.equal(b.run('editorKey()'),key,'brightness must not request another geometry image');
  b.run('settings.rotation=12');assert.notEqual(b.run('editorKey()'),key);
});

test('only the newest request follows an in-flight draft and settle requests full quality',async()=>{
  const b=boot();b.run('settings.scale=110;queueEditorPreview();');b.run('flushEditor()');await b.drain();
  assert.equal(b.requests.length,1);
  b.run('settings.scale=120;queueEditorPreview();settings.scale=135;queueEditorPreview(false);');
  await b.run('flushEditor()');assert.equal(b.requests.length,1,'requests must never overlap');
  b.requests[0].resolve({task_id:'old'});await b.drain();
  b.run(`state.task={task_id:'old',kind:'preview',status:'done',preview:[],preview_quality:'draft'};receiveEditor();`);
  b.run('flushEditor()');await b.drain();
  assert.equal(b.requests.length,2);assert.equal(b.requests[1].body.settings.scale,135);assert.equal(b.requests[1].body.draft,false);
});

test('late task snapshots cannot replace a newer task',()=>{
  const b=boot();
  assert.equal(b.run(`typeof acceptTaskSnapshot`),'function','snapshot ordering must be enforced for both polling routes');
  assert.equal(b.run(`acceptTaskSnapshot({snapshot_id:12,task:{task_id:'C',status:'running'}})`),true);
  assert.equal(b.run(`acceptTaskSnapshot({snapshot_id:9,task:{task_id:'A',status:'done'}})`),false);
  assert.equal(b.run('state.task.task_id'),'C');
});

test('a delayed editor acknowledgement cannot adopt the previous task',async()=>{
  const b=boot();b.run(`editorBaseKey='';queueEditorPreview(false);`);b.run('flushEditor()');await b.drain();
  assert.equal(b.requests[0].url,'/api/editor');
  b.run(`state.task={task_id:'previous',kind:'editor',status:'done',editor:{template_id:'template',source:'A.png',scene:0,url:'wrong'}};receiveEditor();`);
  assert.notEqual(b.run('editorFlight'),null,'the unacknowledged flight remains reserved');
  assert.notEqual(b.run('editorBase?.url'),'wrong');
  b.requests[0].resolve({task_id:'new'});await b.drain();
  b.run('receiveEditor()');assert.notEqual(b.run('editorFlight'),null,'an unrelated task cannot complete the flight');
});

test('rapid sample changes keep global placement, brightness and scene',()=>{
  const b=boot();assert.equal(b.run('typeof selectSample'),'function');
  b.run(`paintSampleNavigator=()=>{}; updateSampleSelection=()=>{}; settings.scale=137;settings.offset_x=18;settings.offset_y=-9;settings.brightness=12;editorSceneIndex=1;selectSample('B.png',1);selectSample('C.png',2);`);
  assert.equal(b.run('source'),'C.png');assert.equal(b.run('editorSceneIndex'),1);
  assert.deepEqual(JSON.parse(b.run('JSON.stringify([settings.scale,settings.offset_x,settings.offset_y,settings.brightness])')),[137,18,-9,12]);
});

test('preview settings and sample controls remain editable while an automatic task runs',()=>{
  const b=boot();assert.equal(b.run('typeof editorBlocked'),'function');
  b.run(`state.task={kind:'preview',status:'running'};editorFlight={kind:'preview'};`);
  assert.equal(b.run('editorBlocked()'),false);assert.equal(b.run('blocked()'),true,'batch/destructive controls stay locked');
  b.run(`state.task={kind:'batch',status:'running'};`);assert.equal(b.run('editorBlocked()'),true);
});

test('next and previous cross the 40-item page without resetting global parameters',async()=>{
  const b=boot();b.run(`paintSampleNavigator=()=>{};updateSampleSelection=()=>{};
    artworkTotal=43;sampleIndex=39;sampleTargetIndex=39;source='art39';settings.scale=135;settings.brightness=12;editorSceneIndex=1;
    artworkPages.set(0,Array.from({length:40},(_,i)=>({path:'art'+i,name:'Art'+i})));`);
  const next=b.run('stepSample(1)');await b.drain();assert.equal(b.requests[0].url,'/api/artworks');assert.equal(b.requests[0].body.offset,40);
  b.requests[0].resolve({total:43,offset:40,files:[40,41,42].map(i=>({path:'art'+i,name:'Art'+i}))});await next;
  assert.equal(b.run('sampleIndex'),40);assert.equal(b.run('source'),'art40');
  await b.run('stepSample(-1)');assert.equal(b.run('source'),'art39');assert.equal(b.requests.length,1,'return uses the known page');
  assert.equal(b.run('settings.scale'),135);assert.equal(b.run('settings.brightness'),12);assert.equal(b.run('editorSceneIndex'),1);
});

test('a decoded result is discarded when A changes through B to C before adoption',async()=>{
  const b=boot();b.run(`paintSampleNavigator=()=>{};updateSampleSelection=()=>{};queueEditorPreview(false);
    const flight={...editorDesired,kind:'preview',taskId:'A-result'};
    const finished={task_id:'A-result',status:'done',kind:'preview',preview_quality:'full',preview_template_id:'template',preview_source:'A.png',preview_settings:structuredClone(settings),preview_scenes:previewSceneSettings(),preview:[{url:'A-preview',scene_index:0,name:'One'}]};
    adoptPreview(finished,flight);selectSample('B.png',1);selectSample('C.png',2);`);
  await b.drain();assert.equal(b.run('displayedPreview'),null);assert.equal(b.run('editorDesired.body.source'),'C.png');
});

test('result adoption checks task identity, scene, settings and desired quality',()=>{
  const b=boot();b.run(`queueEditorPreview(false);
    const flight={...editorDesired,kind:'preview',taskId:'full'};
    const result={task_id:'full',status:'done',kind:'preview',preview_quality:'full',preview_template_id:'template',preview_source:'A.png',preview_settings:structuredClone(settings),preview_scenes:previewSceneSettings(),preview:[{url:'current',scene_index:0,name:'One'}]};`);
  assert.equal(b.run('previewMatchesFlight(result,flight)'),true);
  for(const change of [`task_id:'older'`,`preview_quality:'draft'`,`preview_source:'B.png'`,`preview_template_id:'other'`,`preview:[{url:'other-scene',scene_index:1}]`,`preview_settings:{...settings,brightness:12}`]){
    assert.equal(b.run(`previewMatchesFlight({...result,${change}},flight)`),false,change);
  }
  b.run('queueEditorPreview(true)');assert.equal(b.run('previewMatchesFlight(result,flight)'),false,'full result is not relabeled as a requested draft');
});

test('brightness lookup preserves black, highlights and expected midtones',()=>{
  const b=boot();assert.deepEqual(JSON.parse(b.run('JSON.stringify([0,64,128,249,255].map(v=>brightnessLUT(12)[v]))')),[0,73,140,249,255]);
  assert.equal(b.run('brightnessLUT(-30)[0]'),0);assert.equal(b.run('brightnessLUT(30)[255]'),255);
});

test('an old full-set image is never labeled as a quick draft during a scene change',()=>{
  const b=boot();b.run(`queueEditorPreview(false,true);displayedPreview={key:editorDesired.key,allScenes:true,preview_quality:'full',preview_source:'A.png'};editorSceneIndex=1;queueEditorPreview(false);`);
  // Restore the actual status painter after the harness disabled routine DOM work.
  const code=fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8');
  const statusCode=code.slice(code.indexOf('function paintPreviewStatus(){'),code.indexOf('\nfunction renderTemplates()',code.indexOf('function paintPreviewStatus(){')));
  b.run(statusCode);b.run('paintPreviewStatus()');
  assert.doesNotMatch(b.ctx.document.querySelector('#preview-status').textContent,/暂显示快速预览/,'the visible full-resolution image must retain its quality label');
});

test('polling and draft scheduling preserve an in-progress numeric edit',()=>{
  const b=boot(),field={dataset:{placement:'offset_x'},value:'1.'};
  b.ctx.document.activeElement=field;b.ctx.document.querySelectorAll=()=>[field];b.ctx.document.querySelector=()=>null;
  const code=fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8');
  b.run(code.slice(code.indexOf('function paintEditor('),code.indexOf('\nfunction brightnessLUT(',code.indexOf('function paintEditor('))));
  b.run('settings.offset_x=1;paintEditor()');
  assert.equal(field.value,'1.','a decimal being typed must not be normalized by a background paint');
});

test('uninterrupted movement displays successive same-context drafts as updating',async()=>{
  const b=boot();b.run(`editorDrag={pointer:1};settings.offset_x=1;queueEditorPreview();
    const first={...editorDesired,kind:'preview',taskId:'draft-1'};
    const result1={task_id:'draft-1',kind:'preview',status:'done',preview_quality:'draft',preview_source:'A.png',preview_template_id:'template',preview_settings:structuredClone(settings),preview_scenes:previewSceneSettings(),preview:[{url:'draft-1',scene_index:0,name:'One'}]};
    settings.offset_x=2;queueEditorPreview();adoptPreview(result1,first);`);
  await b.drain();assert.equal(b.run('displayedPreview?.task_id'),'draft-1','a completed same-context draft is useful while the pointer keeps moving');
  assert.notEqual(b.run('displayedPreview.key'),b.run('currentPreviewKey()'),'the following draft is accurately marked behind current settings');
  b.run(`const second={...editorDesired,kind:'preview',taskId:'draft-2'};
    const result2={...result1,task_id:'draft-2',preview_settings:structuredClone(settings),preview:[{url:'draft-2',scene_index:0,name:'One'}]};
    settings.offset_x=3;queueEditorPreview();adoptPreview(result2,second);`);
  await b.drain();assert.equal(b.run('displayedPreview?.task_id'),'draft-2');
  b.run(`editorDrag=null;queueEditorPreview(false);`);
  assert.equal(b.run('previewMatchesFlight(result2,second)'),false,'after settling no old draft may replace the exact full result');
  b.run(`queueEditorPreview();source='B.png';queueEditorPreview();`);
  assert.equal(b.run('previewMatchesFlight(result2,second)'),false,'recent drafts never cross artwork context');
});

test('replacing the sample strip preserves vertical editor scroll',()=>{
  const b=boot(),panel={scrollTop:240},strip={scrollLeft:90};
  const section={querySelector(){return strip;},set outerHTML(value){panel.scrollTop=228;}};
  b.ctx.document.querySelector=selector=>selector==='#sample-navigator'?section:selector==='.editor-panel'?panel:selector==='#sample-navigator .sample-strip'?strip:null;
  b.run('paintSampleNavigator()');assert.equal(panel.scrollTop,240,'browser scroll anchoring must not change the current editor viewport');
});

test('an older rejected POST preserves the newest desired state without overlapping unknown work',async()=>{
  const b=boot();b.run('settings.offset_x=1;queueEditorPreview(false)');const old=b.run('flushEditor()');await b.drain();
  b.run('settings.offset_x=9;queueEditorPreview(false)');b.requests[0].reject(Error('Old request rejected'));await old;
  assert.equal(b.run('editorPending'),true,'the newer desired state must survive an old acknowledgement failure');
  await b.run('flushEditor()');assert.equal(b.requests.length,1,'recover task status before sending another work request');
  const timer=[...b.timers.values()].find(timer=>timer.delay===150);assert(timer);const polling=timer.fn();await b.drain();
  assert.equal(b.requests[1].url,'/api/task');b.requests[1].resolve({snapshot_id:2,task:{status:'idle'}});await polling;
  b.run('flushEditor()');await b.drain();assert.equal(b.requests[2].body.settings.offset_x,9);
});

test('a persistent rejection of the latest state does not auto-retry forever',async()=>{
  const b=boot();b.run(`editorBaseKey='';queueEditorPreview(false)`);const request=b.run('flushEditor()');await b.drain();
  b.requests[0].reject(Error('Cannot prepare this artwork'));await request;
  assert.equal(b.run('editorPending'),false);assert.equal(b.run('editorAttemptKey'),'','an explicit retry can prepare the same base again');
  await b.run('flushEditor()');assert.equal(b.requests.length,1);
});

test('rereading the same folder invalidates cached pages after inserted artworks',async()=>{
  const b=boot();b.run(`paintSampleNavigator=()=>{};updateSampleSelection=()=>{};artworkTotal=80;sampleIndex=38;sampleTargetIndex=38;source='art38';
    artworkPages.set(0,Array.from({length:40},(_,i)=>({path:'art'+i,name:'Art'+i})));
    artworkPages.set(40,Array.from({length:40},(_,i)=>({path:'art'+(40+i),name:'Art'+(40+i)})));
    for(let i=0;i<80;i++)artworkIndices.set('art'+i,i);`);
  const reload=b.run('loadArtworks(0,false)');await b.drain();
  b.requests[0].resolve({offset:0,total:81,files:[{path:'new',name:'New'},...Array.from({length:39},(_,i)=>({path:'art'+i,name:'Art'+i}))]});await reload;
  assert.equal(b.run('source'),'art38');assert.equal(b.run('sampleIndex'),39);
  const next=b.run('stepSample(1)');await b.drain();
  assert.equal(b.requests.length,2,'page 40 must be fetched again after the folder was reread');
  assert.equal(b.requests[1].body.offset,40);b.requests[1].resolve({offset:40,total:81,files:Array.from({length:40},(_,i)=>({path:'art'+(39+i),name:'Art'+(39+i)}))});await next;
  assert.equal(b.run('source'),'art39','navigation follows the refreshed ordering');
});

test('selected thumbnail is revealed when its scroll parent has a nonzero offset',()=>{
  const b=boot(),panel={scrollTop:240},strip={scrollLeft:0,clientWidth:300,offsetLeft:248,getBoundingClientRect(){return {left:248,right:248+this.clientWidth};}},selected={offsetLeft:400,offsetWidth:56,getBoundingClientRect(){return {left:648-strip.scrollLeft,right:704-strip.scrollLeft};}};
  const section={querySelector(){return strip;},set outerHTML(value){}};
  b.ctx.document.querySelector=selector=>selector==='#sample-navigator'?section:selector==='.editor-panel'?panel:selector==='#sample-navigator .sample-strip'?strip:selector==='#sample-navigator .sample-thumb.selected'?selected:null;
  b.run('paintSampleNavigator()');assert(selected.getBoundingClientRect().right<=strip.getBoundingClientRect().right,'offsetLeft is already local to the positioned scroll parent');
});

test('viewport resize reveals the selected thumbnail without changing vertical scroll',()=>{
  const b=boot(),panel={scrollTop:240},strip={scrollLeft:0,clientWidth:180,getBoundingClientRect(){return {left:248,right:248+this.clientWidth};}},selected={getBoundingClientRect(){return {left:492-strip.scrollLeft,right:548-strip.scrollLeft};}};
  b.ctx.document.querySelector=selector=>selector==='.editor-panel'?panel:selector==='#sample-navigator .sample-strip'?strip:selector==='#sample-navigator .sample-thumb.selected'?selected:null;
  assert.equal(typeof b.windowEvents.get('resize'),'function');b.windowEvents.get('resize')();
  assert(selected.getBoundingClientRect().right<=strip.getBoundingClientRect().right);assert.equal(panel.scrollTop,240);
});

test('selected thumbnail stays inside the client viewport when the strip reserves a scrollbar gutter',()=>{
  const b=boot(),strip={scrollLeft:0,clientWidth:300,clientLeft:2,getBoundingClientRect(){return {left:248,right:568,width:320};}},selected={getBoundingClientRect(){return {left:520-strip.scrollLeft,right:566-strip.scrollLeft};}};
  b.ctx.document.querySelector=selector=>selector==='#sample-navigator .sample-strip'?strip:selector==='#sample-navigator .sample-thumb.selected'?selected:null;
  b.run('revealSampleThumbnail()');assert.equal(selected.getBoundingClientRect().right,550,'the reserved gutter is outside the visible thumbnail viewport');
});
