const app = document.querySelector('#app');
const $ = (selector) => document.querySelector(selector);
const escapeHTML = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const attr = escapeHTML;
const bytes = (value) => { const n = Number(value) || 0; return n >= 1073741824 ? `${(n / 1073741824).toFixed(2)} GB` : `${(n / 1048576).toFixed(1)} MB`; };
const activeStatuses = ['running', 'paused', 'stopping'];
let state = {templates:[],task:{status:'idle'},cache:{},history:[],preferences:{}};
let workflowStep=1, templateSearch='';
let overviewSelected=new Set(), overviewScene=0, overviewOpen=false, artworkExcluded=[];
let page = 'workbench', selectedId = '', selectedIds = new Set(), sceneDraft = new Map(), dirty = new Set();
let input = '', output = '', source = '', settings = {width:0,height:0,ppi:72,quality:100,fit:'auto',format:'jpg',trim:false,fit_policy:2,orientation:'auto',rotation:0,brightness:0,scale:100,offset_x:0,offset_y:0,input_layout:'folders',input_exclude:['预览','参考','preview','reference'],naming:{folder:'{name}',image:'{scene:02}',start:1}}, limit = 0;
let customSize = {width:1254,height:1254}, lastTaskSignature = '';
let initialized = false, busyRequest = false, connected = false, catalogSignature = '', previewSignature = '', toastTimer;
let artworkFiles = [], artworkTotal = 0, artworkOffset = 0, artworkFolder = '', artworkLoading = false, artworkError = '', artworkRequest = 0;
let artworkPages=new Map(), artworkIndices=new Map(), sampleIndex=-1, sampleTargetIndex=-1, sampleRequest=0, sampleLoading=false;
let editorEnabled=false, editorSceneIndex=0, editorBase=null, editorImage=null, editorBaseKey='', editorAttemptKey='', editorObservedKey='', editorPending=false, editorTimer, editorDrag=null, editorFlight=null;
let editorDesired=null, editorSettleTimer, editorRangePointer=null, editorPixels=null, editorToneCanvas=null, editorToneAmount=null;
let editorRecovering=false, editorRecoveryVersion=0;
let displayedPreview=null, previewLoadToken=0, previewSequence=0, latestSnapshot=-1, taskPollTimer, taskPolling=false;
let sceneSettingsOpen = false, advancedOpen = false, preferenceTimer, preferenceRequest=Promise.resolve();
let customFolderRule = false, customImageRule = false, draggedScene = null, imageItems = [], imageIndex = 0;
const titles = {workbench:'套图工作台', templates:'模板管理', history:'批次记录', cache:'缓存管理'};
const blocked = () => !!window.workbenchUpdateInstalling || !connected || activeStatuses.includes(state.task?.status) || busyRequest || !!editorFlight || editorRecovering;
const editorBlocked = () => !!window.workbenchUpdateInstalling || !connected || busyRequest || compatibilityBlocked(currentTemplate()) || (activeStatuses.includes(state.task?.status)&&!['editor','preview'].includes(state.task?.kind));
const currentTemplate = () => state.templates.find((t) => t.id === selectedId);
const scenesFor = (template) => sceneDraft.get(template.id) || template.scenes || [];
function compatibilityBlocked(template){return !!template&&(!!template.stale||template.compatibility?.status==='blocked');}
function renderCompatibility(template){
  if(!template)return '';
  const report=template.compatibility;
  if(report?.status==='blocked')return `<div class="notice warning compatibility-report" role="alert"><div><strong>此模板暂不能套图</strong><p>请处理下面列出的图层后重新导入，或在场景设置中跳过对应场景。</p><ul class="warning-list">${(report.issues||[]).map(issue=>`<li>${escapeHTML(issue.message)}</li>`).join('')}</ul><button data-action="refresh-template" data-edit>重新检查模板</button></div></div>`;
  if(!report||report.status==='pending')return '<p class="preview-scope">首次使用前会自动检查模板兼容性，完成后复用缓存。</p>';
  if(report.warnings?.length)return `<div class="notice warning compatibility-report"><div><strong>可以套图，请先核对以下效果</strong><ul class="warning-list">${report.warnings.map(message=>`<li>${escapeHTML(message)}</li>`).join('')}</ul></div></div>`;
  return '';
}
function notify(message, error = false) {
  clearTimeout(toastTimer);
  $('#toast').className = `toast${error ? ' error' : ''}`;
  $('#toast').textContent = message;
  toastTimer = setTimeout(() => { $('#toast').className = ''; $('#toast').textContent = ''; }, error ? 7000 : 3500);
}
async function request(path, body) {
  const response = await fetch(path, body === undefined ? {cache:'no-store'} : {method:'POST',headers:{'Content-Type':'application/json','X-Workbench':'1'},body:JSON.stringify(body)});
  let data;
  try { data = await response.json(); } catch { throw new Error(`读取数据失败（${response.status}）`); }
  if (!response.ok || data.error) throw new Error(data.error || `请求失败（${response.status}）`);
  return data;
}
async function action(path, body, message) {
  if (busyRequest) return;
  busyRequest = true;
  updateLocks();
  try { if(body?.settings)await persistPreferences(body.settings); await request(path, body); if (message) notify(message); await refresh(); }
  catch (error) { notify(error.message, true); }
  finally { busyRequest = false; updateLocks(); }
}
function heading(title, subtitle, tools = '') {
  return `<div class="page-heading"><div><h1>${title}</h1><p>${subtitle}</p></div>${tools ? `<div class="page-tools">${tools}</div>` : ''}</div>`;
}
function empty(title, description, actionHTML = '') {
  return `<div class="empty compact"><h3>${title}</h3><p>${description}</p>${actionHTML}</div>`;
}
function icon(name) {
  const shapes={grid:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',layers:'<path d="m12 3 10 5-10 5L2 8Z"/><path d="m2 12 10 5 10-5M2 16l10 5 10-5"/>',history:'<path d="M3 11a9 9 0 1 1 2.6 7M3 4v7h7m2-4v5l3 2"/>',cache:'<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v7c0 4 18 4 18 0V5M3 12v7c0 4 18 4 18 0v-7"/>',folder:'<path d="M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11H3Z"/>',image:'<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8" cy="8" r="1.5"/><path d="m3 17 5-5 4 4 4-6 5 7"/>',check:'<path d="m5 12 4 4L19 6"/>',arrow:'<path d="M4 12h16m-6-6 6 6-6 6"/>',plus:'<path d="M12 5v14M5 12h14"/>',search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',download:'<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',chevron:'<path d="m9 5 7 7-7 7"/>'};
  return `<svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${shapes[name]||shapes.grid}</svg>`;
}
function renderShell() {
  app.innerHTML = `<div class="app"><aside class="sidebar"><div class="brand"><span class="brand-mark">${icon('grid')}</span><b>套图工作台</b></div><nav aria-label="主导航">${[['workbench','grid'],['templates','layers'],['history','history'],['cache','cache']].map(([key,symbol]) => `<button type="button" data-page="${key}" class="${page===key?'active':''}" title="${titles[key]}" ${page===key?'aria-current="page"':''}><span class="nav-symbol" aria-hidden="true">${icon(symbol)}</span><span class="nav-text">${titles[key]}</span></button>`).join('')}</nav><div class="service"><i class="${connected?'green':'red'}"></i><span>${connected?'桌面版 · 离线可用':'正在重新连接'}</span></div></aside><div class="workspace"><header class="topbar"><span>套图工具 <em>/</em> <strong>${titles[page]}</strong></span><div class="top-actions"><span class="local-tag"><i></i> 桌面版 · 离线可用</span></div></header><main data-surface="${page}"><div id="connection"></div><div id="task-panel"></div><div id="page-content"></div></main></div></div>`;
  renderPage(); renderTask();
}
function renderPage() {
  if (!$('#page-content')) return;
  const sameStep=$('.workbench-page')?.dataset.workflowStep===String(workflowStep);
  const scrollSelectors=['.template-cards','#preview-content','.scene-list','#scene-settings',...(sameStep?['.editor-panel']:[])];
  const scrollPositions=scrollSelectors.map(selector=>[selector,$(selector)?.scrollTop||0]);
  const sampleScrollLeft=$('#sample-navigator .sample-strip')?.scrollLeft||0;
  const active=document.activeElement;
  const focusAttribute=['data-select-template','data-configure-template','data-target-scene','data-scene-mode'].find(name=>active?.hasAttribute(name));
  const focusValue=focusAttribute?active.getAttribute(focusAttribute):null;
  $('#page-content').innerHTML = ({workbench:renderWorkbench,templates:renderTemplates,history:renderHistory,cache:renderCache}[page])();
  updateLocks(); filterTemplates();
  paintEditor(); observeEditor();paintPreviewStatus();
  if(focusAttribute){
    const replacement=[...document.querySelectorAll(`[${focusAttribute}]`)].find(el=>el.getAttribute(focusAttribute)===focusValue&&(!active.value||el.value===active.value));
    replacement?.focus({preventScroll:true});
  }
  for(const [selector,top] of scrollPositions)if($(selector))$(selector).scrollTop=top;
  const sampleStrip=$('#sample-navigator .sample-strip');if(sampleStrip)sampleStrip.scrollLeft=sampleScrollLeft;
  revealSampleThumbnail();
}
function templateName(template) {
  const name=template.name.replace(/\.psd$/i,'').replace(/^\d+[_-]/,'');
  const parts=name.split(/[_-]+/).filter(part=>!/(?:七图联动|母版|定稿|修正版|修复版)|^v\d+$/i.test(part)).map(part=>part==='轮廓适配版'?'轮廓版':part);
  return parts.slice(0,3).join(' · ') || name;
}
function renderTemplateCards() {
  return `<div class="template-cards" aria-label="选择套图模板">${state.templates.map(t=>{
    const thumb=t.scenes?.find(scene=>scene.thumbnail)?.thumbnail;
    return `<article data-template-name="${attr(t.name.toLowerCase())}" class="template-card ${selectedIds.has(t.id)?'selected':''} ${selectedId===t.id?'current':''}"><button class="template-card-cover" data-configure-template="${attr(t.id)}" data-edit aria-label="预览 ${attr(t.name)}" ${selectedId===t.id?'aria-current="true"':''}>${thumb?`<img src="${attr(thumb)}" alt="${attr(t.name)}" loading="lazy">`:'<span>PSD 模板</span>'}${selectedId===t.id?'<span class="configuring">当前预览</span>':''}</button><label title="${attr(t.name)}"><input type="checkbox" data-select-template="${attr(t.id)}" ${selectedIds.has(t.id)?'checked':''} data-edit><strong>${escapeHTML(templateName(t))}</strong></label><small>${t.scenes?.length||0} 个场景${dirty.has(t.id)?' · 未保存':''}</small></article>`;
  }).join('')}</div>`;
}
function renderArtworks() {
  if(artworkLoading) return '<div class="artwork-empty" role="status">正在读取印花缩略图…</div>';
  if(artworkError) return `<div class="notice danger" role="alert">${escapeHTML(artworkError)}<button data-action="reload-artworks" data-edit>重试</button></div>`;
  if(!input.trim()) return '<div class="artwork-empty">选择文件夹后自动读取子目录；支持任意名称、任意数量的印花，可按子文件夹分款。</div>';
  if(!artworkFiles.length) return `<div class="artwork-empty">没有可用图片${artworkExcluded.length?'，部分图片已按关键词排除，请调整上方规则':'，请选择其他印花文件夹'}。</div>`;
  return `<div class="artwork-summary"><span>共 <strong>${artworkTotal}</strong> 项印花</span><small>每张印花独立套图 · 点击预览</small></div>${artworkExcluded.length?`<details class="excluded-artworks"><summary>已按关键词排除 ${artworkExcluded.length}${artworkExcluded.length===100?'+':''} 张图片</summary><p>${artworkExcluded.map(escapeHTML).join('<br>')}</p><small>若有需要处理的图片，请修改上方排除关键词。</small></details>`:''}<div class="artwork-grid">${artworkFiles.map(file=>`<div class="artwork-choice"><label class="overview-check"><input type="checkbox" data-overview-artwork="${attr(file.path)}" ${overviewSelected.has(file.path)?'checked':''} data-edit>加入总览</label><button class="artwork-tile ${file.path===source?'selected':''}" data-artwork="${attr(file.path)}" data-edit aria-pressed="${file.path===source}" title="${attr(file.name)}"><span class="artwork-image"><img src="${attr(file.url)}" alt="${attr(file.name)}" loading="lazy">${file.path===source?'<span class="artwork-check">✓</span>':''}</span><span class="artwork-name">${escapeHTML(file.name)}${file.kind==='triptych'?'<small>左 · 中 · 右（一套）</small>':''}</span></button></div>`).join('')}</div>${artworkTotal>40?`<div class="artwork-pagination"><button data-action="artworks-prev" ${artworkOffset===0?'disabled':''} data-edit>上一页</button><small>${artworkOffset+1}–${Math.min(artworkOffset+40,artworkTotal)} / ${artworkTotal}</small><button data-action="artworks-next" ${artworkOffset+40>=artworkTotal?'disabled':''} data-edit>下一页</button></div>`:''}`;
}
function renderSampleNavigator(){
  const offset=Math.max(0,Math.floor(Math.max(0,sampleIndex)/40)*40),files=artworkPages.get(offset)||artworkFiles;
  const filename=source.split(/[\\/]/).pop()||'请选择印花';
  return `<section id="sample-navigator" class="sample-navigator" aria-label="切换预览印花"><div class="sample-heading"><div><small>预览样本 · 全批统一参数</small><strong title="${attr(source)}">${escapeHTML(filename)}</strong></div><span id="sample-position">${sampleIndex<0?'外部图片':sampleIndex+1} / ${artworkTotal}</span></div><div class="sample-navigation"><button data-sample-step="-1" data-edit aria-label="上一张印花">← 上一张</button><p>${sampleLoading?'正在读取下一页印花…':'切换仅更换预览样本，整批印花仍全部导出。'}</p><button data-sample-step="1" data-edit aria-label="下一张印花">下一张 →</button></div><div class="sample-strip" role="group" aria-label="印花缩略图">${files.map((file,i)=>`<button class="sample-thumb ${file.path===source?'selected':''}" data-sample-artwork="${attr(file.path)}" data-sample-index="${artworkIndices.get(file.path)??offset+i}" data-edit aria-pressed="${file.path===source}" title="${attr(file.name)}"><img src="${attr(file.url)}" alt="${attr(file.name)}" loading="lazy"><span>${(artworkIndices.get(file.path)??offset+i)+1}</span></button>`).join('')}</div><p class="global-settings-note">位置、缩放、亮度与适配使用同一套设置，对本批所有印花生效。</p></section>`;
}
function paintSampleNavigator(){
  const section=$('#sample-navigator');if(!section)return;
  const panel=$('.editor-panel'),top=panel?.scrollTop;
  const strip=section.querySelector('.sample-strip'),left=strip?.scrollLeft||0;
  section.outerHTML=renderSampleNavigator();const replacement=$('#sample-navigator .sample-strip');if(replacement)replacement.scrollLeft=left;
  revealSampleThumbnail();
  if(panel)panel.scrollTop=top;
  updateLocks();
}
function revealSampleThumbnail(){
  const strip=$('#sample-navigator .sample-strip'),selected=$('#sample-navigator .sample-thumb.selected');
  if(!strip?.clientWidth||!selected)return;
  const viewport=strip.getBoundingClientRect(),thumb=selected.getBoundingClientRect();
  const left=viewport.left+(strip.clientLeft||0),right=left+strip.clientWidth;
  if(thumb.left<left)strip.scrollLeft+=thumb.left-left;
  else if(thumb.right>right)strip.scrollLeft+=thumb.right-right;
}
function updateSampleSelection(){
  const label=$('.preview-selected strong');if(label){label.textContent=source.split(/[\\/]/).pop()||'还没有选择印花';label.title=source;}
  document.querySelectorAll('[data-artwork]').forEach(button=>{const selected=button.dataset.artwork===source;button.classList.toggle('selected',selected);button.setAttribute('aria-pressed',String(selected));const check=button.querySelector('.artwork-check');if(check&&!selected)check.remove();});
  paintSampleNavigator();paintEditor();paintPreviewStatus();updateWorkflow();
}
function selectSample(path,index=artworkIndices.get(path)??-1){
  source=path;sampleIndex=index;sampleTargetIndex=index;sampleRequest++;sampleLoading=false;
  updateSampleSelection();observeEditor();
}
async function stepSample(delta){
  if(editorBlocked()||!artworkTotal)return;
  const index=Math.max(0,Math.min(artworkTotal-1,(sampleTargetIndex<0?-1:sampleTargetIndex)+delta)),offset=Math.floor(index/40)*40,token=++sampleRequest,folder=input.trim();
  sampleTargetIndex=index;sampleLoading=!artworkPages.has(offset);paintSampleNavigator();
  try{
    let files=artworkPages.get(offset);
    if(!files){
      const data=await request('/api/artworks',{folder,offset,limit:40,exclude:settings.input_exclude});
      if(token!==sampleRequest||folder!==input.trim())return;
      files=data.files||[];artworkPages.set(offset,files);files.forEach((file,i)=>artworkIndices.set(file.path,offset+i));artworkTotal=data.total||0;
    }
    if(token!==sampleRequest)return;
    const file=files[index-offset];if(!file)throw new Error('这张印花已不在列表中，请重新读取文件夹。');
    artworkFiles=files;artworkOffset=offset;
    if($('#artwork-browser'))$('#artwork-browser').innerHTML=renderArtworks();
    selectSample(file.path,index);
  }catch(error){if(token===sampleRequest){sampleTargetIndex=sampleIndex;sampleLoading=false;paintSampleNavigator();notify(error.message,true);}}
}
function renderAdvanced() {
  return `<details class="advanced-settings" id="advanced-settings" ${advancedOpen?'open':''}><summary>高级设置 <small>导出尺寸、格式、图片质量</small></summary><p class="field-help">默认沿用 PSD 原尺寸。需要统一输出尺寸时，再设置宽度和高度。</p><label class="check-label"><input type="checkbox" data-original-size ${settings.width===0&&settings.height===0?'checked':''} data-edit>沿用 PSD 原始尺寸与比例</label><div class="form-grid export-settings"><label>导出宽度 · px<input type="number" min="1" max="8192" data-setting="width" data-dimension value="${settings.width||customSize.width}" data-edit></label><label>导出高度 · px<input type="number" min="1" max="8192" data-setting="height" data-dimension value="${settings.height||customSize.height}" data-edit></label><label>导出格式<select data-setting="format" data-edit>${options([['jpg','JPG'],['png','PNG']],settings.format)}</select></label><label>图片质量 · 1–100<input type="number" min="1" max="100" data-setting="quality" value="${attr(settings.quality)}" data-edit></label><label>分辨率 · PPI<input type="number" min="1" max="2400" data-setting="ppi" value="${attr(settings.ppi)}" data-edit></label><label class="span-two">本批处理上限 · 0 为全部<input type="number" min="0" data-model="limit" value="${attr(limit)}" data-edit></label></div></details>`;
}
function namingExample() {
  const rules=settings.naming, values={name:'印花示例',index:rules.start,scene:1};
  const fill=pattern=>pattern.replace(/\{(name|index|scene|folder)(?::0([1-6])d?)?\}/g,(_match,key,width)=>String(values[key]??'').padStart(Number(width)||0,'0'));
  values.folder=fill(rules.folder);
  return `${values.folder}/${fill(rules.image)}.${settings.format}`;
}
function renderNaming() {
  const rules=settings.naming;
  const folderCustom=customFolderRule||!['{name}','{index:03}'].includes(rules.folder);
  const imageCustom=customImageRule||!['{scene:02}','{name}_{scene:02}'].includes(rules.image);
  return `<section class="naming-settings" aria-label="导出命名"><h3>导出命名</h3><div class="form-grid"><label>产品文件夹<select data-naming-mode="folder" data-edit>${options([['{name}','使用印花原名'],['{index:03}','顺序编号 · 001、002、003'],['custom','自定义规则']],folderCustom?'custom':rules.folder)}</select></label><label>文件夹里的图片<select data-naming-mode="image" data-edit>${options([['{scene:02}','仅序号 · 01、02、03'],['{name}_{scene:02}','印花原名＋序号'],['custom','自定义规则']],imageCustom?'custom':rules.image)}</select></label>${folderCustom?`<label class="span-two">文件夹规则<input data-naming="folder" value="${attr(rules.folder)}" placeholder="商品_{index:03}" data-edit></label>`:''}${imageCustom?`<label class="span-two">图片规则 · 不含后缀<input data-naming="image" value="${attr(rules.image)}" placeholder="{scene:02}" data-edit></label>`:''}${/\{index/.test(rules.folder+rules.image)?`<label>起始编号<input type="number" min="1" max="999999" data-naming="start" value="${attr(rules.start)}" data-edit></label>`:''}</div><p class="field-help">示例：<strong id="naming-example">${escapeHTML(namingExample())}</strong></p>${folderCustom||imageCustom?'<p class="field-help">{name} 印花原名 · {index:03} 产品编号补零到3位 · {scene:02} 导出图片序号补零到2位。图片规则还可使用 {folder} 表示产品文件夹名。</p>':''}</section>`;
}
function pathPicker(id,model,value,placeholder,action) {
  return `<div class="path-picker">${icon('folder')}<input id="${id}" data-model="${model}" value="${attr(value)}" placeholder="${placeholder}" aria-label="${placeholder}" data-edit><button data-action="${action}" data-edit>选择文件夹</button></div>`;
}
function fitControls() {
  return `<div class="setting-section"><h3>怎样放入印区</h3><div class="fit-options" role="group" aria-label="印花适配">${[['auto','自动适配','满版图铺满，透明图完整放入'],['contain','完整放入','保留全部画面，边缘可能留空'],['cover','等比铺满','铺满印区，超出部分居中裁切'],['cover_bleed','去白边铺满','适合拱形和带白色补边的印花']].map(([value,label,help])=>`<button class="fit-option ${settings.fit===value?'selected':''}" data-fit-choice="${value}" aria-pressed="${settings.fit===value}" data-edit><span class="option-indicator"></span><span><b>${label}</b><small>${help}</small></span></button>`).join('')}</div><p class="field-help">方向和适配以印区自身为准，样机的位置、轮廓和透视固定。</p></div><div id="placement-fields" class="form-grid"><label>印花方向<select data-setting="orientation" data-edit>${options([['auto','自动匹配 · 横竖方向'],['original','保持原方向'],['cw90','顺时针 90°'],['ccw90','逆时针 90°'],['rotate180','旋转 180°']],settings.orientation)}</select></label><label>角度微调 · °<input type="number" min="-180" max="180" step="any" data-setting="rotation" value="${attr(settings.rotation)}" data-edit></label><label>印花适配<select data-setting="fit" data-edit>${options([['auto','自动 · 满版铺满 / 透明图完整'],['contain','完整放入 · 比例不同会留白'],['cover','等比铺满 · 居中裁切'],['cover_bleed','去白边铺满 · 拱形 / 补边图'],['stretch','拉伸填满 · 会改变比例']],settings.fit)}</select></label><label class="check-label"><input type="checkbox" data-setting="trim" ${settings.trim?'checked':''} data-edit>替换前裁去印花透明边缘</label></div>`
    + `<div class="setting-section"><label for="artwork-brightness"><b>印花亮度</b> <output id="artwork-brightness-value" for="artwork-brightness">${attr(settings.brightness)}</output></label><input type="range" id="artwork-brightness" data-setting="brightness" min="-30" max="30" step="1" value="${attr(settings.brightness)}" data-edit><p class="field-help">0 为原亮度。调整印花的中间调，对本批次每款的整套图片生效。</p></div>`;
}
const clampPlacement=(key,value)=>Math.round(Math.max(key==='scale'?10:-100,Math.min(key==='scale'?300:100,value))*100)/100;
function editorScenes(){return currentTemplate()?scenesFor(currentTemplate()).filter(scene=>scene.mode!=='skip'):[];}
function editorKey(){
  const scenes=editorScenes();if(editorSceneIndex>=scenes.length)editorSceneIndex=0;
  return JSON.stringify([templateIdentity(),source.trim()||input.trim(),editorSceneIndex,scenes,['fit','fit_policy','orientation','rotation','trim'].map(key=>settings[key])]);
}
function previewSceneSettings(){return currentTemplate()?scenesFor(currentTemplate()).map(({id,mode,targets,artwork_roles})=>({id,mode,targets,artwork_roles:artwork_roles||{}})):[];}
function templateIdentity(){const template=currentTemplate();return [selectedId,template?.digest,template?.signature,previewSceneSettings()];}
function pixelSettings(value=settings){return Object.fromEntries(Object.keys(value).sort().filter(key=>!['naming','input_layout','input_exclude'].includes(key)).map(key=>[key,value[key]]));}
function currentPreviewKey(allScenes=false){return JSON.stringify([templateIdentity(),source.trim()||input.trim(),allScenes?null:editorSceneIndex,pixelSettings()]);}
function makeEditorDesired(interactive=false,allScenes=false){
  const body={id:selectedId,source:source.trim()||input.trim(),settings:structuredClone(settings),draft:interactive};
  if(!allScenes)body.scene=editorSceneIndex;
  return {key:currentPreviewKey(allScenes),baseKey:editorKey(),quality:interactive?'draft':'full',allScenes,body};
}
function renderPlacementEditor(){
  const scenes=editorScenes();if(editorSceneIndex>=scenes.length)editorSceneIndex=0;
  return `<section class="placement-editor" aria-label="印花位置编辑"><div class="placement-editor-heading"><h3>拖动与缩放印花</h3><button data-action="load-editor" data-edit>${editorEnabled?'重新加载母稿':'加载印花编辑'}</button></div><p class="field-help">母稿正面 · 最终边缘按 PSD 蒙版裁切</p><label>编辑并预览场景<select id="editor-scene" data-edit>${scenes.map((scene,i)=>`<option value="${i}" ${i===editorSceneIndex?'selected':''}>${escapeHTML(scene.name)}${scene.mode==='fixed'?' · 固定场景':''}</option>`).join('')}</select></label><div id="placement-stage" class="placement-stage" tabindex="0" role="group" aria-label="拖动印花调整位置，方向键微调"><canvas id="placement-canvas"></canvas><span class="placement-center" aria-hidden="true"></span><div id="placement-bounds" class="placement-bounds" hidden></div><button type="button" class="placement-resize" data-resize-artwork aria-label="拖动等比缩放印花" title="拖动等比缩放" data-edit hidden></button><p id="placement-placeholder">${editorEnabled?'正在准备母稿…':'加载印花后，可直接拖动调整位置'}</p></div><p id="placement-status" class="field-help" role="status"></p><div class="placement-controls">${[['scale','缩放',10,300],['offset_x','水平位置',-100,100],['offset_y','垂直位置',-100,100]].map(([key,label,min,max])=>`<label>${label} · %<div><input type="range" min="${min}" max="${max}" step="0.1" value="${settings[key]}" data-placement="${key}" aria-label="${label}" data-edit><input type="number" min="${min}" max="${max}" step="0.1" value="${settings[key]}" data-placement="${key}" aria-label="${label}百分比" data-edit></div></label>`).join('')}</div><div class="placement-actions"><button data-action="placement-center" data-edit>居中</button><button data-action="placement-reset" data-edit>重置位置与缩放</button></div><p class="field-help">拖动图案移动，拖动右下角等比缩放；拖动时更新快速预览，停止后自动生成完整效果。位置与缩放对本批次所有印花和所选模板生效。</p></section>`;
}
function paintEditor(activeField=document.activeElement){
  document.querySelectorAll('[data-placement]').forEach(field=>{if(field!==activeField)field.value=settings[field.dataset.placement];});
  const stage=$('#placement-stage'),canvas=$('#placement-canvas');if(!stage||!canvas)return;
  const ready=editorBase&&editorImage?.complete&&editorImage.naturalWidth&&editorBaseKey===editorKey();
  stage.classList.toggle('ready',!!ready);stage.classList.toggle('locked',editorBlocked());
  $('#placement-placeholder').hidden=!!ready;
  $('#placement-placeholder').textContent=editorScenes()[editorSceneIndex]?.mode==='fixed'?'这是固定场景，没有可移动的印花层':!editorEnabled?'加载印花后，可直接拖动调整位置':editorAttemptKey===editorKey()&&!editorFlight&&!ready?'母稿暂不可编辑，请重新加载':'正在准备母稿…';
  const reload=$('[data-action="load-editor"]');if(reload)reload.textContent=editorEnabled?'重新加载母稿':'加载印花编辑';
  $('#placement-bounds').hidden=!ready;
  $('[data-resize-artwork]').hidden=!ready;
  $('#placement-status').textContent=editorScenes()[editorSceneIndex]?.mode==='fixed'?'固定场景 · 没有可移动的印花层':editorPending||editorFlight?'正在更新效果 · 可继续拖动、缩放和切换印花':ready?'全批统一参数 · 拖动时快速预览，停止后自动精修':'';
  if(!ready)return;
  const b=editorBase,w=b.stage_width,h=b.stage_height,s=settings.scale/100;
  stage.style.aspectRatio=`${w} / ${h}`;stage.style.width=`min(100%, ${400*w/h}px)`;
  // Keep canvas memory bounded while preserving native stage coordinates.
  const ratio=Math.min(1,1400/Math.max(w,h));canvas.width=Math.max(1,Math.round(w*ratio));canvas.height=Math.max(1,Math.round(h*ratio));
  const ctx=canvas.getContext('2d');ctx.scale(canvas.width/w,canvas.height/h);
  ctx.translate(w/2+settings.offset_x*w/100,h/2+settings.offset_y*h/100);ctx.scale(s,s);ctx.translate(-w/2,-h/2);
  const artwork=editorArtworkImage();
  if(b.affine){ctx.transform(...b.affine);ctx.drawImage(artwork,0,0);}
  else ctx.drawImage(artwork,b.x,b.y,b.width,b.height);
  const bounds=$('#placement-bounds');bounds.style.left=`${50+(b.x/w*100-50)*s+settings.offset_x}%`;bounds.style.top=`${50+(b.y/h*100-50)*s+settings.offset_y}%`;bounds.style.width=`${b.width/w*100*s}%`;bounds.style.height=`${b.height/h*100*s}%`;
  const handle=$('[data-resize-artwork]');handle.style.left=`clamp(0px, calc(${50+((b.x+b.width)/w*100-50)*s+settings.offset_x}% - 22px), calc(100% - 22px))`;handle.style.top=`clamp(0px, calc(${50+((b.y+b.height)/h*100-50)*s+settings.offset_y}% - 22px), calc(100% - 22px))`;
}
function brightnessLUT(amount){return Uint8ClampedArray.from({length:256},(_,v)=>{const p=Math.min(v,249)/249;return Math.max(0,Math.min(255,Math.round(v+amount*4*p*(1-p))));});}
function editorArtworkImage(){
  if(!settings.brightness)return editorImage;
  if(!editorPixels){
    editorToneCanvas=document.createElement('canvas');editorToneCanvas.width=editorImage.naturalWidth;editorToneCanvas.height=editorImage.naturalHeight;
    const context=editorToneCanvas.getContext('2d',{willReadFrequently:true});context.drawImage(editorImage,0,0);
    editorPixels=context.getImageData(0,0,editorToneCanvas.width,editorToneCanvas.height);editorToneAmount=null;
  }
  if(editorToneAmount!==settings.brightness){
    const context=editorToneCanvas.getContext('2d'),result=context.createImageData(editorPixels.width,editorPixels.height),lut=brightnessLUT(settings.brightness);
    for(let i=0;i<editorPixels.data.length;i+=4){for(let c=0;c<3;c++)result.data[i+c]=lut[editorPixels.data[i+c]];result.data[i+3]=editorPixels.data[i+3];}
    context.putImageData(result,0,0);editorToneAmount=settings.brightness;
  }
  return editorToneCanvas;
}
// A single flight reserves the backend from POST through acknowledgement and completion.
// Changes replace only the desired request; the next flight always uses the latest one.
function scheduleEditor(delay=80){if(editorTimer)return;editorTimer=setTimeout(()=>{editorTimer=null;flushEditor();},delay);}
function queueEditorPreview(interactive=true,allScenes=false){
  if(!editorEnabled)return;
  editorDesired=makeEditorDesired(interactive,allScenes);editorObservedKey=currentPreviewKey();editorPending=true;
  clearTimeout(editorSettleTimer);
  if(interactive)editorSettleTimer=setTimeout(()=>{if(!editorDrag&&editorRangePointer===null)queueEditorPreview(false);},550);
  paintEditor();paintPreviewStatus();scheduleEditor(interactive?80:0);
}
function observeEditor(){
  if(!editorEnabled&&page==='workbench'&&workflowStep===3&&selectedId&&(source.trim()||input.trim()))editorEnabled=true;
  if(!editorEnabled)return;
  const key=currentPreviewKey();if(key!==editorObservedKey)queueEditorPreview(false);
  else if(editorPending&&!editorBlocked()&&!editorFlight)scheduleEditor(0);
}
async function flushEditor(){
  if(!editorEnabled||!editorPending||!editorDesired||editorBlocked()||activeStatuses.includes(state.task?.status)||editorFlight||editorRecovering)return;
  if(!selectedId||!(source.trim()||input.trim())||dirty.has(selectedId)||!editorScenes().length)return;
  let flight=null;
  try{
    validateSettings();const desired=editorDesired,needsBase=!desired.allScenes&&editorScenes()[editorSceneIndex]?.mode!=='fixed'&&editorBaseKey!==desired.baseKey;
    if(needsBase&&editorAttemptKey===desired.baseKey){editorPending=false;return;}
    const body=structuredClone(desired.body);
    if(needsBase){body.settings.brightness=0;delete body.draft;editorAttemptKey=desired.baseKey;}
    flight=editorFlight={...desired,sequence:++previewSequence,kind:needsBase?'editor':'preview'};editorPending=false;
    updateLocks();paintPreviewStatus();scheduleTaskPoll();
    const accepted=await request(needsBase?'/api/editor':'/api/preview',body);
    flight.taskId=accepted.task_id;flight.accepted=true;
    receiveEditor();scheduleTaskPoll(0);
  }catch(error){
    editorFlight=null;
    editorPending=!!flight&&!!editorDesired&&(flight.key!==editorDesired.key||flight.quality!==editorDesired.quality);
    if(flight?.kind==='editor'&&editorAttemptKey===flight.baseKey)editorAttemptKey='';
    // A lost acknowledgement may still have started work. Confirm server idleness
    // before dispatching a newer state; a rejected latest state is not retried forever.
    if(flight){editorRecovering=true;editorRecoveryVersion++;scheduleTaskPoll(0);}
    if(!editorPending)notify(error.message,true);
  }
  updateLocks();paintEditor();paintPreviewStatus();observeEditor();
}
function receiveEditor(){
  if(!editorFlight?.accepted||state.task?.task_id!==editorFlight.taskId||activeStatuses.includes(state.task?.status))return;
  const flight=editorFlight,task=state.task;editorFlight=null;
  if(flight.kind==='editor'){
    const result=task.editor;
    if(task.status==='done'&&task.kind==='editor'&&result?.url&&result.template_id===selectedId&&result.source===(source.trim()||input.trim())&&result.scene===editorSceneIndex&&flight.baseKey===editorKey()){
      editorBase=result;editorBaseKey=flight.baseKey;editorImage=new Image();editorPixels=null;editorToneCanvas=null;editorToneAmount=null;
      const image=editorImage;image.onload=()=>{if(editorImage===image)paintEditor();};image.onerror=()=>{if(editorImage===image){editorBaseKey='';notify('无法读取母稿图片，请重新加载。',true);}};image.src=result.url;
      editorPending=true;
    }else if(flight.baseKey!==editorKey())editorPending=true;
  }else if(previewMatchesFlight(task,flight)){
    if(flight.key!==editorDesired?.key||flight.quality!==editorDesired?.quality)editorPending=true;
    adoptPreview(task,flight);
  }else if(flight.key!==editorDesired?.key||flight.quality!==editorDesired?.quality){
    editorPending=true;
  }
  if(task.status==='failed'&&flight.key===editorDesired?.key){editorPending=false;notify(task.error||'效果预览失败，请重试。',true);}
  paintEditor();paintPreviewStatus();updateLocks();if(editorPending)scheduleEditor(0);
}
function previewMatchesFlight(task,flight){
  const exact=flight.key===editorDesired?.key&&flight.key===currentPreviewKey(flight.allScenes)&&flight.quality===editorDesired?.quality;
  // During uninterrupted motion each completed draft can advance the visible image.
  // It stays explicitly "updating" until its pixels match the current parameters.
  const following=flight.quality==='draft'&&editorDesired?.quality==='draft'&&!flight.allScenes&&flight.baseKey===editorKey()&&flight.body.scene===editorSceneIndex;
  return (exact||following)&&task.status==='done'&&task.kind==='preview'&&task.task_id===flight.taskId&&task.preview_quality===flight.quality
    &&task.preview_template_id===flight.body.id&&task.preview_source===flight.body.source&&JSON.stringify(pixelSettings(task.preview_settings||{}))===JSON.stringify(pixelSettings(flight.body.settings))
    &&JSON.stringify(task.preview_scenes)===JSON.stringify(previewSceneSettings())&&task.preview?.length>0
    &&(flight.allScenes?task.preview.length===editorScenes().length:task.preview.length===1&&task.preview[0].scene_index===flight.body.scene);
}
async function adoptPreview(task,flight){
  const token=++previewLoadToken;
  try{
    await Promise.all(task.preview.map(item=>new Promise((resolve,reject)=>{const image=new Image();image.onload=resolve;image.onerror=reject;image.src=item.url;})));
    if(token!==previewLoadToken||!previewMatchesFlight(task,flight)||(flight.sequence??0)<(displayedPreview?.sequence??0))return;
    displayedPreview={...structuredClone(task),key:flight.key,baseKey:flight.baseKey,sequence:flight.sequence,allScenes:flight.allScenes};
    updatePreviewContent();paintPreviewStatus();updateWorkflow();
  }catch{if(token===previewLoadToken)notify('无法读取场景预览，请重试。',true);}
}
app.addEventListener('pointerdown',event=>{
  if(event.target.matches('input[type="range"][data-placement],#artwork-brightness'))editorRangePointer=event.pointerId;
  const stage=event.target.closest('#placement-stage');if(!stage||editorBlocked()||editorBaseKey!==editorKey()||!editorImage?.naturalWidth||event.button!==0)return;
  event.preventDefault();const rect=stage.getBoundingClientRect();stage.setPointerCapture(event.pointerId);stage.focus({preventScroll:true});
  editorDrag={pointer:event.pointerId,stage,rect,x:event.clientX,y:event.clientY,offsetX:settings.offset_x,offsetY:settings.offset_y,scale:settings.scale,resize:!!event.target.closest('[data-resize-artwork]')};
  stage.classList.add('dragging');
});
app.addEventListener('pointermove',event=>{
  const d=editorDrag;if(!d||event.pointerId!==d.pointer)return;
  if(d.resize){
    const cx=d.rect.left+d.rect.width*(.5+d.offsetX/100),cy=d.rect.top+d.rect.height*(.5+d.offsetY/100),vx=d.x-cx,vy=d.y-cy;
    settings.scale=clampPlacement('scale',d.scale*((event.clientX-cx)*vx+(event.clientY-cy)*vy)/Math.max(1,vx*vx+vy*vy));
  }else{settings.offset_x=clampPlacement('offset_x',d.offsetX+(event.clientX-d.x)/d.rect.width*100);settings.offset_y=clampPlacement('offset_y',d.offsetY+(event.clientY-d.y)/d.rect.height*100);}
  queueEditorPreview();
});
function finishEditorDrag(event){if(editorRangePointer===event.pointerId){editorRangePointer=null;queueEditorPreview(false);}const d=editorDrag;if(!d||event.pointerId!==d.pointer)return;editorDrag=null;d.stage.classList.remove('dragging');if(d.stage.hasPointerCapture(d.pointer))d.stage.releasePointerCapture(d.pointer);savePreferences();queueEditorPreview(false);}
app.addEventListener('pointerup',finishEditorDrag);app.addEventListener('pointercancel',finishEditorDrag);app.addEventListener('lostpointercapture',finishEditorDrag);
app.addEventListener('keydown',event=>{if(event.target.id!=='placement-stage'||editorBlocked()||!editorBase||editorBaseKey!==editorKey()||!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key))return;event.preventDefault();const key=['ArrowLeft','ArrowRight'].includes(event.key)?'offset_x':'offset_y',delta=(['ArrowLeft','ArrowUp'].includes(event.key)?-1:1)*(event.shiftKey?5:.5);settings[key]=clampPlacement(key,settings[key]+delta);paintEditor();savePreferences();queueEditorPreview();});
function renderWorkbench() {
  const template=currentTemplate();
  return `<div class="workbench-page" data-workflow-step="${workflowStep}">${heading('批量套图','选好素材，确认效果，一次完成整批导出。',`<button data-action="import" data-edit>${icon('plus')} 导入 PSD</button>`)}
  <nav class="workflow-steps" aria-label="套图步骤">${[['选择模板','确定商品与场景'],['选择印花','导入待处理的图片'],['确认效果','调整方向与适配'],['批量导出','设置保存与命名']].map(([title,detail],i)=>`<button data-step-target="${i+1}" class="workflow-step ${workflowStep===i+1?'current':''}" ${workflowStep===i+1?'aria-current="step"':''}><span class="step-number">${i+1}</span><span><b>${title}</b><small>${detail}</small></span>${i<3?icon('chevron'):''}</button>`).join('')}</nav>
  <div class="studio-grid"><section class="editor-panel ${workflowStep===1?'template-editor':''}" aria-label="套图设置">
    <div class="step-panel" data-step-panel="1" ${workflowStep!==1?'hidden':''}><div class="panel-heading"><div><h2>选择套图模板</h2><p>可勾选多个模板，一起生成整批商品图。</p></div><button class="text" data-action="go-templates">管理模板</button></div>
    ${state.templates.length?`<div class="template-toolbar"><label class="search-control">${icon('search')}<input id="template-search" value="${attr(templateSearch)}" placeholder="搜索模板名称" aria-label="搜索模板名称"></label><span>${state.templates.length} 个模板</span></div>${renderTemplateCards()}<p id="template-no-match" class="field-help" hidden>没有匹配的模板，换个关键词试试。</p><p class="selection-help">勾选加入批次 · 点击缩略图切换预览</p>${template?`<details class="scene-settings" id="scene-settings" ${sceneSettingsOpen?'open':''}><summary>场景与导出顺序 <small>${escapeHTML(templateName(template))} · ${scenesFor(template).length} 个场景</small></summary>${renderScenes(template)}</details>`:''}`:empty('先导入一个 PSD 模板','导入后会自动识别场景和可替换的印花图层。',`<button class="primary" data-action="import" data-edit>${icon('plus')} 选择 PSD 文件</button>`)}</div>
    <div class="step-panel" data-step-panel="2" ${workflowStep!==2?'hidden':''}><div class="panel-heading"><div><h2>选择印花素材</h2><p>选择一个文件夹，点击其中一张查看套图效果。</p></div></div>${pathPicker('input-path','input',input,'选择印花所在文件夹','choose-input')}<div class="import-rules"><label>文件夹组织<select data-setting="input_layout" data-edit>${options([['folders','按子文件夹分款 · 每张印花独立一套'],['flat','每张图片单独一款']],settings.input_layout||'folders')}</select></label><label>排除文件名关键词 · 逗号分隔<input id="input-exclude" value="${attr((settings.input_exclude||[]).join('，'))}" placeholder="留空则读取所有图片" data-edit></label><small>分款模式：款式文件夹 / 印花名称 / 01、02…；根目录的散图仍各自一款。排除规则同时用于列表和批量导出。</small></div><div id="artwork-browser" aria-busy="${artworkLoading}">${renderArtworks()}</div></div>
    <div class="step-panel" data-step-panel="3" ${workflowStep!==3?'hidden':''}><div class="panel-heading"><div><h2>确认印花效果</h2><p>全批共用一套参数；切换样本检查不同印花，当前场景自动更新。</p></div></div>${renderSampleNavigator()}${renderPlacementEditor()}${fitControls()}<div class="placement-tip">完整放入会保留全部图案；铺满可能裁切。手动角度叠加在所选方向上，正值顺时针、负值逆时针。</div></div>
    <div class="step-panel" data-step-panel="4" ${workflowStep!==4?'hidden':''}><div class="panel-heading"><div><h2>设置批量导出</h2><p>选择保存位置，确认文件夹和图片的命名方式。</p></div></div><label class="field-label" for="output-path">保存到</label>${pathPicker('output-path','output',output,'选择套图结果保存文件夹','choose-output')}<div class="output-hint"><span>每张印花对应一套商品图</span><button data-action="open-output" ${output?'':'disabled'}>打开文件夹 ${icon('arrow')}</button></div>${state.templates.filter(t=>selectedIds.has(t.id)&&compatibilityBlocked(t)).map(t=>`<p class="notice warning">${escapeHTML(templateName(t))} 需要处理兼容问题；请返回第一步查看该模板。</p>`).join('')}${renderNaming()}${renderAdvanced()}</div>
  </section><aside class="preview-panel" aria-label="效果预览"><div class="preview-header"><div><h2>效果预览</h2><small>${template?escapeHTML(templateName(template)):'先选择一个模板'}</small></div><span class="badge">${template?scenesFor(template).filter(s=>s.mode!=='skip').length:0} 个场景</span></div><div class="preview-selected"><span class="field-label">当前印花</span><strong title="${attr(source)}">${source?escapeHTML(source.split(/[\\/]/).pop()):'还没有选择印花'}</strong><button data-action="choose-image" data-edit>${icon('image')} 另选图片</button></div>${template&&!selectedIds.has(template.id)?'<p class="preview-scope">当前预览的模板未加入本批导出，请返回第一步勾选。</p>':selectedIds.size>1?'<p class="preview-scope">这里预览当前模板。已选择多个模板，请分别点击模板检查效果。</p>':''}${renderCompatibility(template)}${renderOverviewControls()}<div id="preview-content">${renderPreviews()}</div><div class="preview-tools"><small>点击图片放大 · 左右方向键切换</small><button data-action="preview" class="primary" data-needs-artworks data-edit>生成整套预览</button></div></aside></div>
  <footer class="workflow-footer"><div class="batch-summary" id="workflow-summary" aria-live="polite"></div><div class="workflow-actions">${workflowStep>1?`<button data-step-target="${workflowStep-1}">上一步</button>`:''}${workflowStep<4?`<button class="primary" data-next-step="${workflowStep+1}">下一步：${['','选择模板','选择印花','确认效果','批量导出'][workflowStep+1]} ${icon('arrow')}</button>`:`<button class="primary export-button" data-action="batch" data-needs-artworks data-edit>${icon('download')} 开始批量导出</button>`}</div></footer></div>`;
}
function filterTemplates() {
  let visible=0;document.querySelectorAll('[data-template-name]').forEach(card=>{card.hidden=!card.dataset.templateName.includes(templateSearch.trim().toLowerCase());if(!card.hidden)visible++;});
  if($('#template-no-match'))$('#template-no-match').hidden=visible>0;
}
function updateWorkflow() {
  if(page!=='workbench')return;
  const totalScenes=state.templates.filter(t=>selectedIds.has(t.id)).reduce((sum,t)=>sum+scenesFor(t).filter(s=>s.mode!=='skip').length,0);
  const count=limit>0?Math.min(artworkTotal,limit):artworkTotal;
  const summary=$('#workflow-summary');
  if(summary)summary.innerHTML=`<b>${selectedIds.size} 个模板 <span>·</span> ${artworkTotal} 张印花</b><small>${totalScenes&&count?`预计导出 ${totalScenes*count} 张图片`:'选择模板和印花后，这里显示导出数量'}${output?' · 已设置保存位置':''}</small>`;
  const complete=[selectedIds.size>0&&![...selectedIds].some(id=>dirty.has(id)),!!input.trim()&&artworkTotal>0,selectedIds.size===1&&selectedIds.has(selectedId)&&!!state.task?.preview?.length&&state.task.preview.length===editorScenes().length&&state.task.preview_template_id===selectedId&&!previewChanged(),false];
  document.querySelectorAll('.workflow-step').forEach((button,i)=>{button.classList.toggle('complete',complete[i]);button.querySelector('.step-number').innerHTML=complete[i]&&workflowStep!==i+1?icon('check'):String(i+1);});
  document.querySelectorAll('[data-action="open-output"]').forEach(button=>button.disabled=!output.trim());
  document.querySelectorAll('[data-fit-choice]').forEach(button=>{const selected=button.dataset.fitChoice===settings.fit;button.classList.toggle('selected',selected);button.setAttribute('aria-pressed',String(selected));});
  if($('#naming-example'))$('#naming-example').textContent=namingExample();
}
function previewChanged() {
  return !displayedPreview||displayedPreview.key!==currentPreviewKey(displayedPreview.allScenes)||displayedPreview.preview_quality!=='full';
}
function options(values, selected) { return values.map(([value,label])=>`<option value="${value}" ${value===selected?'selected':''}>${label}</option>`).join(''); }
function renderScenes(template) {
  const scenes = scenesFor(template);
  const warnings = template.warnings || [];
  return `${template.stale?'<div class="notice warning"><span>PSD 文件已有变化，请重新导入并核对场景设置。</span><button data-action="refresh-template" data-edit>重新导入</button></div>':''}${warnings.length ? `<div class="notice warning"><ul class="warning-list">${warnings.map(w=>`<li>${escapeHTML(typeof w==='string'?w:JSON.stringify(w))}</li>`).join('')}</ul></div>`:''}<p class="field-help">拖动左侧手柄调整导出顺序，保存后生效；原 PSD 不变。</p><div class="scene-list">${scenes.map((scene,index)=>`<article data-scene-id="${attr(scene.id)}" class="scene ${scene.mode==='skip'?'scene-muted':''}"><button class="scene-drag-handle" draggable="true" data-drag-scene="${attr(scene.id)}" data-edit title="拖动调整导出顺序，也可用上下方向键" aria-label="拖动场景 ${attr(scene.name)}">⠿</button><button class="scene-thumb" ${scene.thumbnail?`data-image="${attr(scene.thumbnail)}" data-title="${attr(scene.name)}"`:'disabled'} aria-label="查看 ${attr(scene.name)} 场景">${scene.thumbnail?`<img src="${attr(scene.thumbnail)}" alt="${attr(scene.name)}" loading="lazy">`:'PSD'}</button><div><div class="scene-title"><strong><small class="export-position">${scene.mode==='skip'?'不导出':'导出 '+String(scenes.slice(0,index+1).filter(s=>s.mode!=='skip').length).padStart(2,'0')}</small>场景 ${escapeHTML(scene.name)}</strong><select aria-label="${attr(scene.name)} 导出方式" data-scene-mode="${attr(scene.id)}" data-edit>${options([['replace','换图'],['fixed','固定'],['skip','不导出']],scene.mode||'replace')}</select></div><div class="scene-path">${scene.mode==='replace'?`已选 ${scene.targets?.length||0} 个印花图层`:scene.mode==='fixed'?'保留当前场景原样':'跳过此场景'}</div><details ${scene.mode==='replace'&&!(scene.targets?.length)?'open':''}><summary>印花图层 · 手动调整</summary><div class="target-list">${(scene.candidates||[]).map(candidate=>`<label><input type="checkbox" data-target-scene="${attr(scene.id)}" value="${attr(candidate.id)}" ${(scene.targets||[]).map(String).includes(String(candidate.id))?'checked':''} data-edit><span>${escapeHTML(candidate.path||candidate.name)}${candidate.linked?' · 关联图层':''}</span></label>`).join('')||'<span class="muted">没有可选的智能对象，请检查 PSD。</span>'}</div></details></div></article>`).join('')||empty('没有可用场景','请检查 PSD 或重新导入。')}</div><div class="scene-controls"><small>${dirty.has(template.id)?'<span class="unsaved">● 场景设置尚未保存</span>':'场景设置已与模板同步'}</small><button data-action="save-template" data-edit>保存场景设置</button></div>`;
}
function renderOverviewControls(){
  const scenes=currentTemplate()?scenesFor(currentTemplate()).filter(s=>s.mode!=='skip'):[];
  if(overviewScene>=scenes.length)overviewScene=0;
  return `<div class="overview-controls"><div class="overview-actions"><button data-action="overview-set" data-edit>整套总览</button><button data-action="overview-toggle" aria-expanded="${overviewOpen}" data-edit>多印花总览 · ${overviewSelected.size} 张</button></div>${overviewOpen?`<div class="overview-options"><p>在“选择印花”勾选图片，或直接添加图片。每张印花只生成下方选定的同一个场景。</p><div class="overview-actions"><button data-action="overview-add" data-edit>添加图片</button><button data-action="overview-page" data-edit>选择当前页</button><button data-action="overview-clear" data-edit>清空</button></div><label>要对比的轮播图<select id="overview-scene" data-edit>${scenes.map((scene,index)=>`<option value="${index}" ${index===overviewScene?'selected':''}>导出 ${String(index+1).padStart(2,'0')} · ${escapeHTML(scene.name)}</option>`).join('')}</select></label><div class="overview-selected-list">${[...overviewSelected].map(path=>`<button data-overview-remove="${attr(path)}" title="${attr(path)}" data-edit>${escapeHTML(path.split(/[\\/]/).slice(-2).join('/'))} ×</button>`).join('')||'<small>尚未选择印花，最多 100 张。</small>'}</div><button class="primary" data-action="overview-many" ${overviewSelected.size?'':'disabled'} data-edit>生成总览图 · ${overviewSelected.size} 张</button></div>`:''}</div>`;
}
function renderPreviews() {
  const previews = displayedPreview?.preview||[];
  const partial=previews.length>0&&previews.length<editorScenes().length;
  const placement = [...new Set((displayedPreview?.preview_fit||[]).map(info=>info.reason))].join('；');
  const overview=state.task?.overview;
  const sheet=overview?.template_id===selectedId?`<div class="overview-result"><div><strong>${escapeHTML(overview.title)}</strong><button data-action="overview-save" data-edit>保存总览图</button></div><button class="overview-image" data-image="${attr(overview.url)}" data-title="${attr(overview.title)}"><img src="${attr(overview.url)}" alt="${attr(overview.title)}"></button><small>共 ${overview.count} 张 · 修改选择或参数后请重新生成</small></div>`:'';
  return sheet+`<div id="preview-status" class="preview-live-status" role="status" aria-live="polite"></div>`+(previews.length ? `${partial?'<p class="preview-scope">当前选定场景 · 可生成整套预览检查其他场景。</p>':''}${placement?`<p class="field-help preview-placement">适配方式：${escapeHTML(placement)}</p>`:''}<div class="preview-grid ${previews.length===1?'single-preview':''}" data-preview-source="${attr(displayedPreview.preview_source)}" data-preview-quality="${attr(displayedPreview.preview_quality)}" data-preview-task="${attr(displayedPreview.task_id)}" data-preview-scene="${displayedPreview.allScenes?'all':previews[0].scene_index}" data-preview-current="false">${previews.map(p=>`<button class="preview-tile" data-image="${attr(p.url)}" data-title="${attr(p.name)}"><img src="${attr(p.url)}" alt="${attr(p.name)}"><span>${escapeHTML(p.name)}</span></button>`).join('')}</div>` : `<div class="preview-empty"><span class="preview-symbol" aria-hidden="true">${icon('image')}</span><strong>在这里检查套图效果</strong><p>进入“确认效果”后自动加载，调整时同步更新。</p></div>`);
}
function updatePreviewContent(){
  const container=$('#preview-content');if(!container)return;
  const top=container.scrollTop;container.innerHTML=renderPreviews();container.scrollTop=top;paintPreviewStatus();
}
function paintPreviewStatus(){
  const element=$('#preview-status');if(!element)return;
  const matches=displayedPreview&&displayedPreview.key===currentPreviewKey(displayedPreview.allScenes);
  const current=!!matches&&(!editorDesired||displayedPreview.key===editorDesired.key&&displayedPreview.preview_quality===editorDesired.quality);
  const updating=!!editorFlight||editorPending;
  const oldName=displayedPreview?.preview_source?.split(/[\\/]/).pop()||'';
  const following=displayedPreview?.preview_quality==='draft'&&displayedPreview.baseKey===editorKey()&&editorDesired?.quality==='draft';
  element.classList.toggle('updating',updating||!!displayedPreview&&!current);
  element.textContent=current?(displayedPreview.preview_quality==='draft'?'快速预览 · 停止调整后自动精修':'完整预览 · 与当前设置一致'):displayedPreview?(following?'正在跟随调整 · 显示最近一次快速预览':matches&&displayedPreview.preview_quality==='draft'?'正在精修 · 暂显示快速预览':`${updating?'更新中':'设置已改变'} · 暂显示上一结果：${oldName}`):updating?'正在准备当前场景…':'调整参数会自动更新当前场景';
  const grid=$('.preview-grid');if(grid)grid.dataset.previewCurrent=String(current);
}
function renderTemplates() {
  return heading('模板管理','将 PSD 作为模板导入，逐场景确认印花位置。', '<button class="primary" data-action="import" data-edit>＋ 导入 PSD</button>') + (state.templates.length ? `<div class="catalog-grid">${state.templates.map(t=>`<article class="card catalog-tile"><div class="catalog-cover">${t.scenes?.find(s=>s.thumbnail)?`<img src="${attr(t.scenes.find(s=>s.thumbnail).thumbnail)}" alt="${attr(t.name)} 场景缩略图" loading="lazy">`:'PSD 模板'}</div><h3>${escapeHTML(t.name)}</h3><p title="${attr(t.path)}">${escapeHTML(t.path)}</p><footer><span class="badge">${t.scenes?.length||0} 个场景 · ${t.compatibility?.status==='blocked'?'暂不能套图':t.compatibility?.status==='ready'?'检查通过':t.compatibility?.status==='review'?'需核对效果':'待检查'}</span><div class="actions"><button data-open-template="${attr(t.id)}">配置</button><button class="text error-text" data-remove-template="${attr(t.id)}" data-edit>移除</button></div></footer>${t.warnings?.length?`<p class="field-help error-text">有 ${t.warnings.length} 条扫描提示，打开配置查看。</p>`:''}</article>`).join('')}</div>` : `<section class="card">${empty('导入第一个模板','支持一次选择多个 PSD 文件，导入后会自动扫描场景和候选印花。','<button class="primary" data-action="import" data-edit>选择 PSD 文件</button>')}</section>`);
}
function historyPath(h) { return h.path || h.manifest || h.manifest_path || ''; }
function renderHistory() {
  return heading('批次记录','查看已有批次，继续尚未完成的导出。') + `<section class="card">${state.history.length?`<div class="table-scroll"><table><thead><tr><th>批次 / 保存位置</th><th>状态</th><th>处理结果</th><th>操作</th></tr></thead><tbody>${state.history.map((h,i)=>`<tr><td><strong>${escapeHTML(h.name||h.id||`批次 ${i+1}`)}</strong><div class="path-text muted">${escapeHTML(h.output||historyPath(h))}</div>${h.created||h.created_at?`<small>${formatTime(h.created||h.created_at)}</small>`:''}</td><td><span class="badge ${h.status==='done'?'success':''}">${escapeHTML(statusLabel(h.status||'saved'))}</span></td><td>${h.counts?`成功 ${escapeHTML(h.counts.success||0)} · 失败 ${escapeHTML(h.counts.failed||0)}`:`${escapeHTML(h.completed??h.progress??'—')} / ${escapeHTML(h.total??'—')}`}</td><td><button data-resume="${i}" data-edit ${historyPath(h)?'':'disabled'}>${h.status==='done'?'检查并续跑':'继续批次'}</button></td></tr>`).join('')}</tbody></table></div>`:empty('还没有批次记录','开始批量套图后，记录会显示在这里。中断的批次可以继续处理。')}</section>`;
}
function renderCache() {
  const cache = state.cache || {}, used = Number(cache.bytes)||0, quota = Number(cache.limit_bytes)||0;
  const percent = quota ? Math.min(100, used/quota*100) : 0;
  return heading('缓存管理','复用已处理的模板，加快下一次预览和导出。') + `<section class="card"><header><h2>本地缓存空间</h2><span class="badge">${cache.entries?.length||0} 项</span></header><div class="card-body"><div class="cache-overview"><div><div class="cache-amount">${bytes(used)} <small>/ ${bytes(quota)}</small></div><progress value="${percent}" max="100" aria-label="缓存使用比例"></progress><p>超过容量时，优先清理最近较少使用的缓存。</p></div><div><label for="cache-limit">缓存容量上限 · GB</label><div class="field-row"><input type="number" id="cache-limit" min="0.1" max="1000" step="0.1" value="${quota?(quota/1073741824).toFixed(1):'10'}" data-edit><button data-action="cache-limit" data-edit>保存上限</button></div></div></div><div class="cache-actions"><small>清理会保留正在使用的缓存。下次使用模板时可重新生成。</small><button class="danger-outline" data-action="cache-clean" data-edit>清理未使用缓存</button></div></div></section><section class="card cache-table"><header><h2>缓存项目</h2></header>${cache.entries?.length?`<div class="table-scroll"><table><thead><tr><th>模板</th><th>占用</th><th>最近使用</th><th>状态</th></tr></thead><tbody>${cache.entries.map(e=>`<tr><td>${escapeHTML(e.name||e.key)}</td><td>${bytes(e.bytes)}</td><td>${formatTime(e.last_used)}</td><td><span class="badge ${e.active?'active':''}">${e.active?'使用中':'可清理'}</span></td></tr>`).join('')}</tbody></table></div>`:empty('暂无缓存','预览或批量处理模板后，这里会显示缓存占用。')}</section>`;
}
function formatTime(value) { if (!value) return '—'; const date = new Date(typeof value==='number'?value*1000:value); return Number.isNaN(date.getTime())?escapeHTML(value):escapeHTML(date.toLocaleString('zh-CN',{hour12:false})); }
function statusLabel(status) { return {idle:'准备就绪',running:'处理中',paused:'已暂停',stopping:'正在停止',done:'已完成',failed:'处理失败',saved:'已保存',stopped:'已停止',interrupted:'已中断'}[status]||status; }
function renderTask() {
  const container = $('#task-panel'); if (!container) return;
  const task = state.task || {status:'idle'}, active = activeStatuses.includes(task.status);
  if(editorEnabled&&['editor','preview'].includes(task.kind)){container.innerHTML='';lastTaskSignature='';return;}
  const signature = JSON.stringify(task);
  if(signature===lastTaskSignature && container.innerHTML) return;
  lastTaskSignature=signature;
  if(task.status==='done') {
    const message=task.kind==='editor'?'印花编辑已就绪':task.kind==='preview'?'预览已生成':task.kind==='scan'?'模板已导入':'本次套图已处理完成';
    container.innerHTML=`<div class="completion-bar"><span>✓ ${message}</span>${task.kind==='batch'?'<button class="text" data-action="open-output">打开保存文件夹 ↗</button>':''}</div>`;
    return;
  }
  if (task.status === 'idle') { container.innerHTML=''; return; }
  const progress = Number(task.progress)||0, total = Number(task.total)||0;
  container.innerHTML = `<section class="card task-card" aria-label="任务进度"><header><div class="task-status"><h2>${task.kind==='scan'?'扫描模板':task.kind==='preview'?'生成预览':'当前任务'}</h2><span class="badge ${task.status==='failed'?'failed':task.status==='done'?'success':task.status==='running'?'uploading':''}">${statusLabel(task.status)}</span></div><small>${total?`${Math.round(progress/total*100)}%`:active?'请稍候':''}</small></header><div class="task-body"><p class="task-message ${task.error?'error-text':''}" role="status">${escapeHTML(task.error||task.message||statusLabel(task.status))}</p><div class="task-meter"><progress max="${total||1}" ${total||!active?`value="${total?Math.min(progress,total):task.status==='done'?1:0}"`:''} aria-label="任务进度"></progress><small>${progress} / ${total||'—'}</small></div><div class="task-footer"><small>${task.status==='paused'?'任务已暂停，点击继续可接着处理。':task.status==='stopping'?'正在结束当前处理，请等待。':active?'处理期间，模板和导出参数暂时锁定。':'任务记录保留在本次会话中。'}</small><div class="actions">${task.status==='running'&&task.kind==='batch'?'<button data-control="pause">暂停</button>':''}${task.status==='paused'?'<button data-control="resume" class="primary">继续</button>':''}${['running','paused'].includes(task.status)?'<button data-control="stop" class="danger-outline">停止</button>':''}</div></div>${task.results?.length?`<details class="results-detail"><summary>查看处理结果 · ${task.results.length} 项</summary><ul>${task.results.slice(-100).map(r=>`<li>${escapeHTML(typeof r==='string'?r:r.message||r.path||r.output||r.name||JSON.stringify(r))}</li>`).join('')}</ul></details>`:''}</div></section>`;
}
function updateLocks() {
  $('#placement-stage')?.classList.toggle('locked',editorBlocked());
  document.querySelectorAll('[data-edit]').forEach(element => {
    const live=element.closest('[data-step-panel="3"]')||element.hasAttribute('data-artwork');
    element.disabled = (live?editorBlocked():blocked()) || (['preview','overview-set','overview-many'].includes(element.dataset.action)&&compatibilityBlocked(currentTemplate())) || (element.dataset.action==='batch'&&state.templates.some(t=>selectedIds.has(t.id)&&compatibilityBlocked(t))) || (element.dataset.action==='overview-many'&&!overviewSelected.size) || (element.dataset.action==='overview-set'&&!source) || (element.hasAttribute('data-dimension') && settings.width===0 && settings.height===0) || (element.hasAttribute('data-needs-artworks') && artworkLoading) || (element.dataset.action==='artworks-prev' && (artworkLoading || artworkOffset===0)) || (element.dataset.action==='artworks-next' && (artworkLoading || artworkOffset+40>=artworkTotal)) || ((element.hasAttribute('data-sample-artwork')||element.hasAttribute('data-sample-step'))&&artworkLoading) || (element.hasAttribute('data-sample-step')&&(artworkTotal===0||Number(element.dataset.sampleStep)<0&&sampleTargetIndex<=0||Number(element.dataset.sampleStep)>0&&sampleTargetIndex>=artworkTotal-1)) || (element.hasAttribute('data-resume') && !historyPath(state.history[Number(element.dataset.resume)]||{}));
  });
  document.querySelectorAll('[data-control]').forEach(element => { element.disabled = busyRequest; });
  updateWorkflow();
}
function setDraft(sceneId, mutate) {
  const template = currentTemplate(); if (!template) return;
  if (!sceneDraft.has(template.id)) sceneDraft.set(template.id,structuredClone(template.scenes||[]));
  const scene = sceneDraft.get(template.id).find(s=>String(s.id)===String(sceneId));
  if (scene) { mutate(scene); dirty.add(template.id); }
}
function moveScene(sourceId, targetId, after=false) {
  if(blocked()||sourceId===targetId)return;
  const template=currentTemplate();if(!template)return;
  const scenes=structuredClone(scenesFor(template));
  const from=scenes.findIndex(s=>String(s.id)===String(sourceId));
  if(from<0)return;
  const [scene]=scenes.splice(from,1), to=scenes.findIndex(s=>String(s.id)===String(targetId));
  if(to<0)return;
  scenes.splice(to+(after?1:0),0,scene);sceneDraft.set(template.id,scenes);dirty.add(template.id);
  const scroll=$('.scene-list')?.scrollTop||0;renderPage();if($('.scene-list'))$('.scene-list').scrollTop=scroll;
}
function showImage(index) {
  imageIndex=Math.max(0,Math.min(imageItems.length-1,index));
  const item=imageItems[imageIndex];if(!item)return;
  $('#large-image').src=item.url;$('#large-image').alt=item.title||'套图大图预览';
  $('#image-title').textContent=`${item.title||'图片预览'} · ${imageIndex+1} / ${imageItems.length}`;
  $('#previous-image').disabled=imageIndex===0;$('#next-image').disabled=imageIndex===imageItems.length-1;
}
async function saveTemplate() {
  const template=currentTemplate(); if (!template) return;
  const scenes=scenesFor(template).map(({id,mode,targets,artwork_roles})=>({id,mode,targets:targets||[],artwork_roles:artwork_roles||{}}));
  const invalid=scenes.find(s=>s.mode==='replace'&&!s.targets.length);
  if(invalid) throw new Error('换图场景还未选择印花图层，请展开“印花图层”进行设置。');
  await request('/api/template',{id:template.id,scenes});
  dirty.delete(template.id); sceneDraft.delete(template.id);
}
function validateSettings() {
  const originalSize = settings.width===0 && settings.height===0;
  for (const [key,name,max] of [['width','宽度',8192],['height','高度',8192],['quality','质量',100],['ppi','PPI',2400]]) {
    if(originalSize && (key==='width'||key==='height')) continue;
    if (!Number.isInteger(Number(settings[key])) || Number(settings[key])<1 || Number(settings[key])>max) throw new Error(`${name}请填写 1–${max} 的整数。`);
  }
  if(!originalSize && settings.width*settings.height>32000000) throw new Error('导出总像素不能超过 3200 万，请减小宽度或高度。');
  for(const key of ['scale','offset_x','offset_y'])if(!Number.isFinite(settings[key])||settings[key]!==clampPlacement(key,settings[key]))throw new Error('缩放范围为 10–300%，位置范围为 -100–100%。');
  if (!Number.isInteger(settings.brightness) || Math.abs(settings.brightness) > 30) throw new Error('印花亮度请填写 -30～30 的整数。');
  if (!Number.isFinite(settings.rotation) || settings.rotation < -180 || settings.rotation > 180) throw new Error('角度微调请填写 -180～180 度的数字。');
  if (!Number.isInteger(Number(limit)) || Number(limit)<0) throw new Error('处理上限请填写大于或等于 0 的整数。');
}
async function choose(kind) {
  const data = window.desktop?.choose ? await window.desktop.choose(kind) : await request('/api/choose',{kind});
  return Array.isArray(data) ? data : data?.paths || [];
}
function persistPreferences(snapshot=settings){
  clearTimeout(preferenceTimer);
  const body={input:input.trim(),output:output.trim(),settings:structuredClone(snapshot),limit:Number(limit)};
  preferenceRequest=preferenceRequest.catch(()=>{}).then(()=>request('/api/preferences',body));
  return preferenceRequest;
}
function savePreferences() {
  clearTimeout(preferenceTimer);
  preferenceTimer = setTimeout(async()=>{
    try { validateSettings(); } catch { return; }
    try { await persistPreferences(); }
    catch(error) { notify(`设置未能保存：${error.message}`,true); }
  },500);
}
async function loadArtworks(offset=0, resetSource=false, preservePages=false) {
  const token=++artworkRequest, folder=input.trim();
  artworkOffset=offset; artworkFolder=folder; artworkError=''; artworkLoading=!!folder;
  artworkFiles=[]; artworkExcluded=[]; if(offset===0)artworkTotal=0;
  if(!preservePages||resetSource){artworkPages.clear();artworkIndices.clear();sampleIndex=-1;sampleTargetIndex=-1;sampleRequest++;sampleLoading=false;}
  if(resetSource){source='';overviewSelected.clear();}
  const paint=()=>{if($('#artwork-browser')) { $('#artwork-browser').innerHTML=renderArtworks(); $('#artwork-browser').setAttribute('aria-busy',String(artworkLoading)); } updateLocks();};
  paint();
  if(!folder) { renderPage(); return; }
  try {
    const data=await request('/api/artworks',{folder,offset,limit:40,exclude:settings.input_exclude});
    if(token!==artworkRequest || input.trim()!==folder)return;
    artworkFiles=data.files||[]; artworkExcluded=data.excluded||[]; artworkTotal=data.total||0; artworkOffset=data.offset??offset;
    artworkPages.set(artworkOffset,artworkFiles);artworkFiles.forEach((file,i)=>artworkIndices.set(file.path,artworkOffset+i));
    if(!source && artworkFiles.length)source=artworkFiles[0].path;
    sampleIndex=artworkIndices.get(source)??-1;sampleTargetIndex=sampleIndex;
  } catch(error) { if(token!==artworkRequest)return; artworkError=error.message; }
  finally { if(token===artworkRequest) { artworkLoading=false;paint();updateSampleSelection();observeEditor(); } }
}
async function handleAction(name) {
  if(name==='load-editor'){if(editorBlocked())return;editorEnabled=true;editorAttemptKey='';editorBaseKey='';queueEditorPreview(false);return;}
  if(name==='placement-center'||name==='placement-reset'){if(editorBlocked())return;settings.offset_x=0;settings.offset_y=0;if(name==='placement-reset')settings.scale=100;paintEditor();savePreferences();queueEditorPreview(false);return;}
  if (name==='go-templates') { page='templates'; renderShell(); return; }
  if (name==='open-output') {
    try { if(!output.trim())throw new Error('请先选择保存文件夹。'); clearTimeout(preferenceTimer); await request('/api/preferences',{input:input.trim(),output:output.trim(),settings,limit:Number(limit)}); if(window.desktop?.openOutput)await window.desktop.openOutput(output.trim());else await request('/api/open-output',{path:output.trim()}); }
    catch(error) { notify(error.message,true); }
    return;
  }
  if (blocked()) return;
  try {
    if(name==='overview-toggle'){overviewOpen=!overviewOpen;renderPage();}
    if(name==='overview-add'){const paths=await choose('images');if(overviewSelected.size+paths.length>100)throw new Error('一次最多选择 100 张印花');paths.forEach(p=>overviewSelected.add(p));overviewOpen=true;renderPage();}
    if(name==='overview-clear'){overviewSelected.clear();renderPage();}
    if(name==='overview-page'){if(new Set([...overviewSelected,...artworkFiles.map(f=>f.path)]).size>100)throw new Error('一次最多选择 100 张印花');artworkFiles.forEach(f=>overviewSelected.add(f.path));renderPage();}
    if(name==='overview-set'||name==='overview-many'){
      validateSettings();if(!selectedId)throw new Error('请先选择模板');if(dirty.has(selectedId))throw new Error('请先保存场景设置');
      const sources=name==='overview-set'?[source]:[...overviewSelected];
      if(!sources.length||sources.some(p=>!p))throw new Error('请先选择印花图片');
      await action('/api/overview',{id:selectedId,sources,settings,scene:name==='overview-set'?null:overviewScene},'正在生成总览图');
    }
    if(name==='overview-save'){const paths=await choose('folder');if(paths.length){const result=await request('/api/save-overview',{folder:paths[0]});notify('总览图已保存：'+result.path);}}
    if (name==='import') { const paths=await choose('psd'); if(paths.length) await action('/api/scan',{paths},'开始扫描 PSD 模板'); }
    if (name==='refresh-template') { const template=currentTemplate(); if(template) await action('/api/scan',{paths:[template.path]},'开始重新扫描模板'); }
    if (name==='choose-input'||name==='choose-output') { const paths=await choose('folder'); if(paths.length) { if(name==='choose-input') { input=paths[0]; renderPage(); await loadArtworks(0,true); } else { output=paths[0]; renderPage(); } savePreferences(); } }
    if (name==='choose-image') { const paths=await choose('image'); if(paths.length)selectSample(paths[0]); }
    if (name==='reload-artworks') await loadArtworks(artworkOffset);
    if (name==='artworks-prev') await loadArtworks(Math.max(0,artworkOffset-40),false,true);
    if (name==='artworks-next') await loadArtworks(artworkOffset+40,false,true);
    if (name==='save-template') { busyRequest=true; updateLocks(); await saveTemplate(); await refresh(); renderPage(); notify('场景设置已保存'); }
    if (name==='preview') {
      validateSettings(); if(!selectedId) throw new Error('请先导入并选择模板。');
      if(!source.trim()&&!input.trim()) throw new Error('请选择一张预览印花，或填写印花文件夹。');
      if(dirty.has(selectedId)) throw new Error('请先保存当前模板的场景设置。');
      editorEnabled=true;queueEditorPreview(false,true);
    }
    if (name==='batch') {
      validateSettings(); if(!selectedIds.size) throw new Error('请至少勾选一个套图模板。');
      if(!input.trim()||!output.trim()) throw new Error('请填写印花文件夹和导出文件夹。');
      if([...selectedIds].some(id=>dirty.has(id))) throw new Error('所选模板有未保存的场景设置，请先保存。');
      await action('/api/batch',{ids:[...selectedIds],input:input.trim(),output:output.trim(),settings,limit:Number(limit)},'批量套图已开始');
    }
    if(name==='cache-clean') await action('/api/cache',{action:'clean'},'已清理未使用缓存');
    if(name==='cache-limit') { const amount=Number($('#cache-limit').value); if(!Number.isFinite(amount)||amount<0.1||amount>1000) throw new Error('缓存容量请填写 0.1–1000 GB。'); await action('/api/cache',{action:'limit',limit_bytes:Math.round(amount*1073741824)},'缓存上限已保存'); renderPage(); }
  } catch(error) { notify(error.message,true); }
  finally { busyRequest=false; updateLocks(); }
}
app.addEventListener('click', async (event)=> {
  const button=event.target.closest('button'); if(!button||button.disabled) return;
  if(button.dataset.stepTarget || button.dataset.nextStep) {
    if(button.dataset.nextStep && workflowStep===1 && !selectedIds.size){notify('请先勾选至少一个模板。',true);return;}
    if(button.dataset.nextStep && workflowStep===2 && !source && !artworkTotal){notify('请先选择印花文件夹或预览图片。',true);return;}
    workflowStep=Number(button.dataset.stepTarget||button.dataset.nextStep);renderPage();document.querySelector('.workflow-step.current')?.focus();return;
  }
  if(button.dataset.fitChoice){settings.fit=button.dataset.fitChoice;document.querySelectorAll('[data-setting="fit"]').forEach(field=>field.value=settings.fit);updateWorkflow();savePreferences();queueEditorPreview(false);return;}
  if(button.dataset.page) { page=button.dataset.page; renderShell(); }
  else if(button.dataset.action) await handleAction(button.dataset.action);
  else if(button.dataset.control) await action('/api/control',{action:button.dataset.control});
  else if(button.dataset.configureTemplate) { selectedId=button.dataset.configureTemplate; renderPage(); }
  else if(button.dataset.overviewRemove){overviewSelected.delete(button.dataset.overviewRemove);renderPage();}
  else if(button.dataset.artwork) selectSample(button.dataset.artwork);
  else if(button.dataset.sampleArtwork) selectSample(button.dataset.sampleArtwork,Number(button.dataset.sampleIndex));
  else if(button.dataset.sampleStep) await stepSample(Number(button.dataset.sampleStep));
  else if(button.dataset.openTemplate) { selectedId=button.dataset.openTemplate; workflowStep=1; sceneSettingsOpen=true; page='workbench'; renderShell(); }
  else if(button.dataset.removeTemplate) {
    const template=state.templates.find(t=>t.id===button.dataset.removeTemplate);
    if(confirm(`从模板库移除“${template?.name||'此模板'}”？PSD 原文件会保留。`)) await action('/api/remove-template',{id:button.dataset.removeTemplate},'已从模板库移除');
  }
  else if(button.dataset.resume!==undefined) { const history=state.history[Number(button.dataset.resume)]; await action('/api/resume',{path:historyPath(history)},'正在继续批次'); }
  else if(button.dataset.image) {
    const container=button.closest('.preview-grid')||button.closest('.scene-list')||app;
    imageItems=[...container.querySelectorAll('button[data-image]')].map(b=>({url:b.dataset.image,title:b.dataset.title}));
    showImage(imageItems.findIndex(item=>item.url===button.dataset.image));$('#image-dialog').showModal();
  }
});
app.addEventListener('input',(event)=> {
  const field=event.target;
  if(field.dataset.placement){const key=field.dataset.placement,number=Number(field.value);if(field.value===''||!Number.isFinite(number))return;settings[key]=clampPlacement(key,number);paintEditor(field);savePreferences();queueEditorPreview();return;}
  if(field.id==='template-search'){templateSearch=field.value;filterTemplates();return;}
  if(field.dataset.naming) { settings.naming[field.dataset.naming]=field.type==='number'?Number(field.value):field.value; if($('#naming-example'))$('#naming-example').textContent=namingExample(); savePreferences(); }
  if(field.dataset.model) { const value=field.type==='number'?Number(field.value):field.value; if(field.dataset.model==='input')input=value; if(field.dataset.model==='output')output=value; if(field.dataset.model==='source')source=value; if(field.dataset.model==='limit')limit=value; }
  if(field.dataset.model)paintPreviewStatus();
  if(field.dataset.setting) { settings[field.dataset.setting]=field.type==='checkbox'?field.checked:(field.type==='number'||field.type==='range')?Number(field.value):field.value; if(field.dataset.setting==='brightness')$('#artwork-brightness-value').textContent=field.value; if(!['input_layout','input_exclude'].includes(field.dataset.setting))queueEditorPreview(true);paintPreviewStatus(); }
  if(field.dataset.setting || field.dataset.model==='limit')savePreferences();
  updateWorkflow(); observeEditor();
});
app.addEventListener('change',(event)=> {
  const field=event.target;
  if(field.dataset.placement){field.value=settings[field.dataset.placement];queueEditorPreview(false);return;}
  if(field.id==='editor-scene'){editorSceneIndex=Number(field.value);observeEditor();return;}
  if(field.dataset.setting&&!['input_layout','input_exclude'].includes(field.dataset.setting))queueEditorPreview(false);
  if(field.dataset.namingMode) {
    const key=field.dataset.namingMode,custom=field.value==='custom';
    if(key==='folder')customFolderRule=custom;else customImageRule=custom;
    if(!custom)settings.naming[key]=field.value;
    renderPage();savePreferences();
  }
  if(field.hasAttribute('data-original-size')) { if(field.checked)customSize={width:settings.width,height:settings.height}; settings.width=field.checked?0:customSize.width; settings.height=field.checked?0:customSize.height; renderPage(); savePreferences(); }
  if(field.dataset.overviewArtwork){if(field.checked){if(overviewSelected.size>=100){field.checked=false;notify('一次最多选择 100 张印花',true);return;}overviewSelected.add(field.dataset.overviewArtwork);}else overviewSelected.delete(field.dataset.overviewArtwork);renderPage();}
  if(field.id==='overview-scene')overviewScene=Number(field.value);
  if(field.id==='input-exclude'){settings.input_exclude=field.value.split(/[,，]/).map(s=>s.trim()).filter(Boolean);overviewSelected.clear();loadArtworks(0,true);savePreferences();}
  if(field.id==='template-select') { selectedId=field.value; renderPage(); }
  if(field.dataset.selectTemplate) { if(field.checked)selectedIds.add(field.dataset.selectTemplate);else selectedIds.delete(field.dataset.selectTemplate); renderPage(); }
  if(field.dataset.model==='input' && input.trim()!==artworkFolder)loadArtworks(0,true);
  if(field.dataset.model==='input' || field.dataset.model==='output')savePreferences();
  // Keep the clicked element alive when a field blurs on mouse-down.
  // Rebuilding the page here would swallow the following mouse-up/click.
  if(field.dataset.model==='output' || field.dataset.setting)updateLocks();
  if(field.dataset.sceneMode) { setDraft(field.dataset.sceneMode,s=>{s.mode=field.value;}); renderPage(); }
  if(field.dataset.targetScene) { setDraft(field.dataset.targetScene,s=>{ const candidate=(s.candidates||[]).find(c=>String(c.id)===field.value); const id=candidate?.id??field.value; s.targets=(s.targets||[]).filter(t=>String(t)!==String(id)); if(field.checked)s.targets.push(id); }); renderPage(); }
  if(field.dataset.manualTarget) { setDraft(field.dataset.manualTarget,s=>{s.targets=[...new Set(field.value.split(/[,，\s]+/).filter(Boolean))];}); renderPage(); }
});
app.addEventListener('keydown',event=>{
  if(event.key==='Enter' && event.target.dataset.model==='input') { event.preventDefault(); loadArtworks(0,input.trim()!==artworkFolder); savePreferences(); }
  const handle=event.target.closest('[data-drag-scene]');
  if(handle&&['ArrowUp','ArrowDown'].includes(event.key)) {
    event.preventDefault();const scenes=scenesFor(currentTemplate()),from=scenes.findIndex(s=>s.id===handle.dataset.dragScene),to=from+(event.key==='ArrowUp'?-1:1);
    if(scenes[to]){moveScene(scenes[from].id,scenes[to].id,event.key==='ArrowDown');document.querySelector(`[data-drag-scene="${CSS.escape(handle.dataset.dragScene)}"]`)?.focus();}
  }
});
app.addEventListener('dragstart',event=>{
  const handle=event.target.closest('[data-drag-scene]');
  if(!handle||blocked()){event.preventDefault();return;}
  draggedScene={id:handle.dataset.dragScene,template:selectedId};event.dataTransfer.effectAllowed='move';event.dataTransfer.setData('text/plain',draggedScene.id);
  event.dataTransfer.setDragImage(handle.closest('.scene'),20,20);
});
app.addEventListener('dragover',event=>{
  const row=event.target.closest('[data-scene-id]');
  if(!row||blocked()||draggedScene?.template!==selectedId)return;
  event.preventDefault();event.dataTransfer.dropEffect='move';
  document.querySelectorAll('.scene-drop-before,.scene-drop-after').forEach(el=>el.classList.remove('scene-drop-before','scene-drop-after'));
  row.classList.add(event.clientY>row.getBoundingClientRect().top+row.offsetHeight/2?'scene-drop-after':'scene-drop-before');
  const list=row.closest('.scene-list'),bounds=list.getBoundingClientRect();if(event.clientY<bounds.top+35)list.scrollTop-=18;else if(event.clientY>bounds.bottom-35)list.scrollTop+=18;
});
app.addEventListener('drop',event=>{
  const row=event.target.closest('[data-scene-id]');
  if(row&&draggedScene?.template===selectedId){event.preventDefault();moveScene(draggedScene.id,row.dataset.sceneId,event.clientY>row.getBoundingClientRect().top+row.offsetHeight/2);}
  draggedScene=null;
});
app.addEventListener('dragend',()=>{draggedScene=null;document.querySelectorAll('.scene-drop-before,.scene-drop-after').forEach(el=>el.classList.remove('scene-drop-before','scene-drop-after'));});
app.addEventListener('toggle',event=>{ if(event.target.id==='scene-settings')sceneSettingsOpen=event.target.open; if(event.target.id==='advanced-settings')advancedOpen=event.target.open; },true);
$('#close-image').addEventListener('click',()=>$('#image-dialog').close());
$('#image-dialog').addEventListener('click',event=>{if(event.target===$('#image-dialog'))$('#image-dialog').close();});
$('#previous-image').addEventListener('click',()=>showImage(imageIndex-1));
$('#next-image').addEventListener('click',()=>showImage(imageIndex+1));
$('#image-dialog').addEventListener('keydown',event=>{
  if(['ArrowLeft','ArrowRight'].includes(event.key)&&!event.target.matches('input,textarea,select')){event.preventDefault();showImage(imageIndex+(event.key==='ArrowLeft'?-1:1));}
});
function acceptTaskSnapshot(next){
  const snapshot=Number(next.snapshot_id);
  if(Number.isFinite(snapshot)&&snapshot<latestSnapshot)return false;
  if(Number.isFinite(snapshot))latestSnapshot=snapshot;
  state.task=next.task||{status:'idle'};return true;
}
function scheduleTaskPoll(delay=150){
  if(taskPollTimer||taskPolling)return;
  taskPollTimer=setTimeout(async()=>{
    taskPollTimer=null;taskPolling=true;const recoveryVersion=editorRecoveryVersion;let failed=false;
    try{
      const next=await request('/api/task');
      const accepted=acceptTaskSnapshot(next);if(recoveryVersion===editorRecoveryVersion)editorRecovering=false;
      if(accepted){receiveEditor();renderTask();}updateLocks();paintPreviewStatus();observeEditor();
    }catch(error){failed=true;if(editorFlight)notify(`预览状态读取失败，正在重试：${error.message}`,true);}
    finally{taskPolling=false;if(editorFlight||editorRecovering||activeStatuses.includes(state.task?.status)&&['editor','preview'].includes(state.task?.kind))scheduleTaskPoll(failed?1000:150);}
  },delay);
}
async function refresh() {
  try {
    const next=await request('/api/state');
    const oldStatus=state.task?.status, oldOverview=state.task?.overview?.url, wasConnected=connected;
    if(!acceptTaskSnapshot(next))return;
    state={...next,templates:next.templates||[],task:state.task,cache:next.cache||{},history:next.history||[]};
    connected=true;
    const firstLoad=!initialized;
    if(firstLoad) { const pref=state.preferences||{}; input=pref.input||'';output=pref.output||'';settings={...settings,...pref.settings,fit_policy:2};limit=pref.limit||0; if(settings.width&&settings.height)customSize={width:settings.width,height:settings.height}; initialized=true; }
    const signature=JSON.stringify([state.templates,state.history,state.cache]);
    const previews=JSON.stringify([state.task.overview]);
    const available=new Set(state.templates.map(t=>t.id));
    selectedIds=new Set([...selectedIds].filter(id=>available.has(id)));
    if(!available.has(selectedId)) { selectedId=state.templates[0]?.id||'';if(selectedId)selectedIds.add(selectedId); }
    if(!$('#page-content')||!wasConnected) { renderShell(); catalogSignature=signature; }
    else {
      renderTask();
      const editing=editorDrag||editorRangePointer!==null||editorFlight||draggedScene||document.activeElement?.matches('input,select,summary');
      if(signature!==catalogSignature&&!editing) { renderPage(); catalogSignature=signature; }
      if(previews!==previewSignature&&$('#preview-content')){updatePreviewContent();updateWorkflow();}
      if(state.task.kind==='overview'&&state.task.status==='done'&&state.task.overview?.url!==oldOverview){overviewOpen=false;renderPage();}
      if(oldStatus!==state.task.status)updateLocks();
    }
    receiveEditor(); observeEditor();paintPreviewStatus();
    previewSignature=previews;
    $('#connection').innerHTML='';
    if(firstLoad && input.trim())loadArtworks();
  } catch(error) {
    connected=false;
    if(!$('#page-content')) renderShell();
    $('#connection').innerHTML=`<div class="notice danger connection-error" role="alert">暂时无法读取工作台：${escapeHTML(error.message)}。页面会自动重试。</div>`;
    const service=document.querySelector('.service');if(service)service.innerHTML='<i class="red"></i><span>正在重新连接</span>';
    updateLocks();
  }
}
async function poll() { await refresh(); setTimeout(poll, activeStatuses.includes(state.task?.status)&&!['editor','preview'].includes(state.task?.kind)?1000:5000); }
poll();

window.addEventListener('workbench-update-lock',updateLocks);
window.addEventListener('resize',revealSampleThumbnail);
