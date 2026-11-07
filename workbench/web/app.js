const app = document.querySelector('#app');
const $ = (selector) => document.querySelector(selector);
const escapeHTML = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const attr = escapeHTML;
const bytes = (value) => { const n = Number(value) || 0; return n >= 1073741824 ? `${(n / 1073741824).toFixed(2)} GB` : `${(n / 1048576).toFixed(1)} MB`; };
const activeStatuses = ['running', 'paused', 'stopping'];
let state = {templates:[],task:{status:'idle'},cache:{},history:[],preferences:{}};
let workflowStep=1, templateSearch='';
let page = 'workbench', selectedId = '', selectedIds = new Set(), sceneDraft = new Map(), dirty = new Set();
let input = '', output = '', source = '', settings = {width:0,height:0,ppi:72,quality:100,fit:'auto',format:'jpg',trim:false,fit_policy:2,orientation:'auto',rotation:0,naming:{folder:'{name}',image:'{scene:02}',start:1}}, limit = 0;
let customSize = {width:1254,height:1254}, lastTaskSignature = '';
let initialized = false, busyRequest = false, connected = false, catalogSignature = '', previewSignature = '', toastTimer;
let artworkFiles = [], artworkTotal = 0, artworkOffset = 0, artworkFolder = '', artworkLoading = false, artworkError = '', artworkRequest = 0;
let sceneSettingsOpen = false, advancedOpen = false, preferenceTimer;
let customFolderRule = false, customImageRule = false, draggedScene = null, imageItems = [], imageIndex = 0;
const titles = {workbench:'套图工作台', templates:'模板管理', history:'批次记录', cache:'缓存管理'};
const blocked = () => !!window.workbenchUpdateInstalling || !connected || activeStatuses.includes(state.task?.status) || busyRequest;
const currentTemplate = () => state.templates.find((t) => t.id === selectedId);
const scenesFor = (template) => sceneDraft.get(template.id) || template.scenes || [];
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
  try { await request(path, body); if (message) notify(message); await refresh(); }
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
  $('#page-content').innerHTML = ({workbench:renderWorkbench,templates:renderTemplates,history:renderHistory,cache:renderCache}[page])();
  updateLocks(); filterTemplates();
}
function templateName(template) {
  const name=template.name.replace(/\.psd$/i,'').replace(/^\d+[_-]/,'');
  const parts=name.split(/[_-]+/).filter(part=>!/(?:七图联动|母版|定稿|修正版|修复版)|^v\d+$/i.test(part)).map(part=>part==='轮廓适配版'?'轮廓版':part);
  return parts.slice(0,3).join(' · ') || name;
}
function renderTemplateCards() {
  return `<div class="template-cards" aria-label="选择套图模板">${state.templates.map(t=>{
    const thumb=t.scenes?.find(scene=>scene.thumbnail)?.thumbnail;
    return `<article data-template-name="${attr(t.name.toLowerCase())}" class="template-card ${selectedIds.has(t.id)?'selected':''} ${selectedId===t.id?'current':''}"><button class="template-card-cover" data-configure-template="${attr(t.id)}" data-edit aria-label="配置 ${attr(t.name)}" ${selectedId===t.id?'aria-current="true"':''}>${thumb?`<img src="${attr(thumb)}" alt="${attr(t.name)}" loading="lazy">`:'<span>PSD 模板</span>'}${selectedId===t.id?'<span class="configuring">当前预览</span>':''}</button><label title="${attr(t.name)}"><input type="checkbox" data-select-template="${attr(t.id)}" ${selectedIds.has(t.id)?'checked':''} data-edit><strong>${escapeHTML(templateName(t))}</strong></label><small>${t.scenes?.length||0} 个场景${dirty.has(t.id)?' · 未保存':''}</small></article>`;
  }).join('')}</div>`;
}
function renderArtworks() {
  if(artworkLoading) return '<div class="artwork-empty" role="status">正在读取印花缩略图…</div>';
  if(artworkError) return `<div class="notice danger" role="alert">${escapeHTML(artworkError)}<button data-action="reload-artworks" data-edit>重试</button></div>`;
  if(!input.trim()) return '<div class="artwork-empty">选择文件夹后自动读取子目录；左、中、右同目录时作为一套三联画。</div>';
  if(!artworkFiles.length) return '<div class="artwork-empty">文件夹中没有可用图片，请选择其他印花文件夹。</div>';
  return `<div class="artwork-summary"><span>共 <strong>${artworkTotal}</strong> 项印花</span><small>单张图片或三联套组 · 点击预览</small></div><div class="artwork-grid">${artworkFiles.map(file=>`<button class="artwork-tile ${file.path===source?'selected':''}" data-artwork="${attr(file.path)}" data-edit aria-pressed="${file.path===source}" title="${attr(file.name)}"><span class="artwork-image"><img src="${attr(file.url)}" alt="${attr(file.name)}" loading="lazy">${file.path===source?'<span class="artwork-check">✓</span>':''}</span><span class="artwork-name">${escapeHTML(file.name)}${file.kind==='triptych'?'<small>左 · 中 · 右（一套）</small>':''}</span></button>`).join('')}</div>${artworkTotal>40?`<div class="artwork-pagination"><button data-action="artworks-prev" ${artworkOffset===0?'disabled':''} data-edit>上一页</button><small>${artworkOffset+1}–${Math.min(artworkOffset+40,artworkTotal)} / ${artworkTotal}</small><button data-action="artworks-next" ${artworkOffset+40>=artworkTotal?'disabled':''} data-edit>下一页</button></div>`:''}`;
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
  return `<div class="setting-section"><h3>怎样放入印区</h3><div class="fit-options" role="group" aria-label="印花适配">${[['auto','自动适配','满版图铺满，透明图完整放入'],['contain','完整放入','保留全部画面，边缘可能留空'],['cover','等比铺满','铺满印区，超出部分居中裁切'],['cover_bleed','去白边铺满','适合拱形和带白色补边的印花']].map(([value,label,help])=>`<button class="fit-option ${settings.fit===value?'selected':''}" data-fit-choice="${value}" aria-pressed="${settings.fit===value}" data-edit><span class="option-indicator"></span><span><b>${label}</b><small>${help}</small></span></button>`).join('')}</div><p class="field-help">方向和适配以印区自身为准，样机的位置、轮廓和透视固定。</p></div><div id="placement-fields" class="form-grid"><label>印花方向<select data-setting="orientation" data-edit>${options([['auto','自动匹配 · 横竖方向'],['original','保持原方向'],['cw90','顺时针 90°'],['ccw90','逆时针 90°'],['rotate180','旋转 180°']],settings.orientation)}</select></label><label>角度微调 · °<input type="number" min="-180" max="180" step="any" data-setting="rotation" value="${attr(settings.rotation)}" data-edit></label><label>印花适配<select data-setting="fit" data-edit>${options([['auto','自动 · 满版铺满 / 透明图完整'],['contain','完整放入 · 比例不同会留白'],['cover','等比铺满 · 居中裁切'],['cover_bleed','去白边铺满 · 拱形 / 补边图'],['stretch','拉伸填满 · 会改变比例']],settings.fit)}</select></label><label class="check-label"><input type="checkbox" data-setting="trim" ${settings.trim?'checked':''} data-edit>替换前裁去印花透明边缘</label></div>`;
}
function renderWorkbench() {
  const template=currentTemplate();
  return `<div class="workbench-page">${heading('批量套图','选好素材，确认效果，一次完成整批导出。',`<button data-action="import" data-edit>${icon('plus')} 导入 PSD</button>`)}
  <nav class="workflow-steps" aria-label="套图步骤">${[['选择模板','确定商品与场景'],['选择印花','导入待处理的图片'],['确认效果','调整方向与适配'],['批量导出','设置保存与命名']].map(([title,detail],i)=>`<button data-step-target="${i+1}" class="workflow-step ${workflowStep===i+1?'current':''}" ${workflowStep===i+1?'aria-current="step"':''}><span class="step-number">${i+1}</span><span><b>${title}</b><small>${detail}</small></span>${i<3?icon('chevron'):''}</button>`).join('')}</nav>
  <div class="studio-grid"><section class="editor-panel" aria-label="套图设置">
    <div class="step-panel" data-step-panel="1" ${workflowStep!==1?'hidden':''}><div class="panel-heading"><div><h2>选择套图模板</h2><p>可勾选多个模板，一起生成整批商品图。</p></div><button class="text" data-action="go-templates">管理模板</button></div>
    ${state.templates.length?`<div class="template-toolbar"><label class="search-control">${icon('search')}<input id="template-search" value="${attr(templateSearch)}" placeholder="搜索模板名称" aria-label="搜索模板名称"></label><span>${state.templates.length} 个模板</span></div>${renderTemplateCards()}<p id="template-no-match" class="field-help" hidden>没有匹配的模板，换个关键词试试。</p><p class="selection-help">勾选加入批次 · 点击缩略图配置场景</p>${template?`<details class="scene-settings" id="scene-settings" ${sceneSettingsOpen?'open':''}><summary>场景与导出顺序 <small>${escapeHTML(templateName(template))} · ${scenesFor(template).length} 个场景</small></summary>${renderScenes(template)}</details>`:''}`:empty('先导入一个 PSD 模板','导入后会自动识别场景和可替换的印花图层。',`<button class="primary" data-action="import" data-edit>${icon('plus')} 选择 PSD 文件</button>`)}</div>
    <div class="step-panel" data-step-panel="2" ${workflowStep!==2?'hidden':''}><div class="panel-heading"><div><h2>选择印花素材</h2><p>选择一个文件夹，点击其中一张查看套图效果。</p></div></div>${pathPicker('input-path','input',input,'选择印花所在文件夹','choose-input')}<div id="artwork-browser" aria-busy="${artworkLoading}">${renderArtworks()}</div></div>
    <div class="step-panel" data-step-panel="3" ${workflowStep!==3?'hidden':''}><div class="panel-heading"><div><h2>确认印花效果</h2><p>调整后重新生成预览，确认画面方向和印区边缘。</p></div></div>${fitControls()}<div class="placement-tip">完整放入会保留全部图案；铺满可能裁切。手动角度叠加在所选方向上，正值顺时针、负值逆时针。</div></div>
    <div class="step-panel" data-step-panel="4" ${workflowStep!==4?'hidden':''}><div class="panel-heading"><div><h2>设置批量导出</h2><p>选择保存位置，确认文件夹和图片的命名方式。</p></div></div><label class="field-label" for="output-path">保存到</label>${pathPicker('output-path','output',output,'选择套图结果保存文件夹','choose-output')}<div class="output-hint"><span>每张印花对应一套商品图</span><button data-action="open-output" ${output?'':'disabled'}>打开文件夹 ${icon('arrow')}</button></div>${renderNaming()}${renderAdvanced()}</div>
  </section><aside class="preview-panel" aria-label="效果预览"><div class="preview-header"><div><h2>效果预览</h2><small>${template?escapeHTML(templateName(template)):'先选择一个模板'}</small></div><span class="badge">${template?scenesFor(template).filter(s=>s.mode!=='skip').length:0} 个场景</span></div><div class="preview-selected"><span class="field-label">当前印花</span><strong title="${attr(source)}">${source?escapeHTML(source.split(/[\\/]/).pop()):'还没有选择印花'}</strong><button data-action="choose-image" data-edit>${icon('image')} 另选图片</button></div>${template&&!selectedIds.has(template.id)?'<p class="preview-scope">当前预览的模板未加入本批导出，请返回第一步勾选。</p>':selectedIds.size>1?'<p class="preview-scope">这里预览当前模板。已选择多个模板，请分别点击模板检查效果。</p>':''}<div id="preview-content">${renderPreviews()}</div><div class="preview-tools"><small>点击图片放大 · 左右方向键切换</small><button data-action="preview" class="primary" data-needs-artworks data-edit>生成预览</button></div></aside></div>
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
  const complete=[selectedIds.size>0&&![...selectedIds].some(id=>dirty.has(id)),!!input.trim()&&artworkTotal>0,selectedIds.size===1&&selectedIds.has(selectedId)&&!!state.task?.preview?.length&&state.task.preview_template_id===selectedId&&!previewChanged(),false];
  document.querySelectorAll('.workflow-step').forEach((button,i)=>{button.classList.toggle('complete',complete[i]);button.querySelector('.step-number').innerHTML=complete[i]&&workflowStep!==i+1?icon('check'):String(i+1);});
  document.querySelectorAll('[data-action="open-output"]').forEach(button=>button.disabled=!output.trim());
  document.querySelectorAll('[data-fit-choice]').forEach(button=>{const selected=button.dataset.fitChoice===settings.fit;button.classList.toggle('selected',selected);button.setAttribute('aria-pressed',String(selected));});
  if($('#naming-example'))$('#naming-example').textContent=namingExample();
}
function previewChanged() {
  const sceneSettings=currentTemplate()?scenesFor(currentTemplate()).map(({id,mode,targets,artwork_roles})=>({id,mode,targets,artwork_roles:artwork_roles||{}})):[];
  return !state.task?.preview_settings || Object.entries(settings).some(([key,value])=>key!=='naming'&&JSON.stringify(state.task.preview_settings[key])!==JSON.stringify(value)) || state.task.preview_source!==(source.trim()||input.trim()) || JSON.stringify(state.task.preview_scenes)!==JSON.stringify(sceneSettings);
}
function options(values, selected) { return values.map(([value,label])=>`<option value="${value}" ${value===selected?'selected':''}>${label}</option>`).join(''); }
function renderScenes(template) {
  const scenes = scenesFor(template);
  const warnings = template.warnings || [];
  return `${template.stale?'<div class="notice warning"><span>PSD 文件已有变化，请重新导入并核对场景设置。</span><button data-action="refresh-template" data-edit>重新导入</button></div>':''}${warnings.length ? `<div class="notice warning"><ul class="warning-list">${warnings.map(w=>`<li>${escapeHTML(typeof w==='string'?w:JSON.stringify(w))}</li>`).join('')}</ul></div>`:''}<p class="field-help">拖动左侧手柄调整导出顺序，保存后生效；原 PSD 不变。三联画请展开印花图层，给每个已勾选印区指定左图、中图或右图（按产品位置，不按照片里的屏幕位置）。</p><div class="scene-list">${scenes.map((scene,index)=>`<article data-scene-id="${attr(scene.id)}" class="scene ${scene.mode==='skip'?'scene-muted':''}"><button class="scene-drag-handle" draggable="true" data-drag-scene="${attr(scene.id)}" data-edit title="拖动调整导出顺序，也可用上下方向键" aria-label="拖动场景 ${attr(scene.name)}">⠿</button><button class="scene-thumb" ${scene.thumbnail?`data-image="${attr(scene.thumbnail)}" data-title="${attr(scene.name)}"`:'disabled'} aria-label="查看 ${attr(scene.name)} 场景">${scene.thumbnail?`<img src="${attr(scene.thumbnail)}" alt="${attr(scene.name)}" loading="lazy">`:'PSD'}</button><div><div class="scene-title"><strong><small class="export-position">${scene.mode==='skip'?'不导出':'导出 '+String(scenes.slice(0,index+1).filter(s=>s.mode!=='skip').length).padStart(2,'0')}</small>场景 ${escapeHTML(scene.name)}</strong><select aria-label="${attr(scene.name)} 导出方式" data-scene-mode="${attr(scene.id)}" data-edit>${options([['replace','换图'],['fixed','固定'],['skip','不导出']],scene.mode||'replace')}</select></div><div class="scene-path">${scene.mode==='replace'?`已选 ${scene.targets?.length||0} 个印花图层`:scene.mode==='fixed'?'保留当前场景原样':'跳过此场景'}</div><details ${scene.mode==='replace'&&!(scene.targets?.length)?'open':''}><summary>印花图层 · 手动调整</summary><div class="target-list">${(scene.candidates||[]).map(candidate=>`<label><input type="checkbox" data-target-scene="${attr(scene.id)}" value="${attr(candidate.id)}" ${(scene.targets||[]).map(String).includes(String(candidate.id))?'checked':''} data-edit><span>${escapeHTML(candidate.path||candidate.name)}${candidate.linked?' · 关联图层':''}</span></label><select aria-label="${attr(candidate.path||candidate.name)} 对应印花" data-role-scene="${attr(scene.id)}" data-role-target="${attr(candidate.id)}" data-edit>${options([['','单图 / 未指定三联位置'],['left','左图'],['center','中图'],['right','右图']],scene.artwork_roles?.[candidate.id]||'')}</select>`).join('')||'<span class="muted">没有可选的智能对象，请检查 PSD。</span>'}</div></details></div></article>`).join('')||empty('没有可用场景','请检查 PSD 或重新导入。')}</div><div class="scene-controls"><small>${dirty.has(template.id)?'<span class="unsaved">● 场景设置尚未保存</span>':'场景设置已与模板同步'}</small><button data-action="save-template" data-edit>保存场景设置</button></div>`;
}
function renderPreviews() {
  const previews = state.task?.preview_template_id === selectedId ? state.task.preview || [] : [];
  const sceneSettings = currentTemplate() ? scenesFor(currentTemplate()).map(({id,mode,targets,artwork_roles})=>({id,mode,targets,artwork_roles:artwork_roles||{}})) : [];
  const changed = (state.task?.preview_settings && Object.entries(settings).some(([key,value])=>key!=='naming'&&JSON.stringify(state.task.preview_settings[key])!==JSON.stringify(value))) || (state.task?.preview_source && state.task.preview_source !== (source.trim()||input.trim())) || (state.task?.preview_scenes && JSON.stringify(state.task.preview_scenes)!==JSON.stringify(sceneSettings));
  const placement = [...new Set((state.task?.preview_fit||[]).map(info=>info.reason))].join('；');
  return previews.length ? `${changed?'<div class="notice warning">预览参数与当前设置不同，请重新生成预览。</div>':''}${placement?`<p class="field-help preview-placement">本次预览：${escapeHTML(placement)}</p>`:''}<div class="preview-grid">${previews.map(p=>`<button class="preview-tile" data-image="${attr(p.url)}" data-title="${attr(p.name)}"><img src="${attr(p.url)}" alt="${attr(p.name)}" loading="lazy"><span>${escapeHTML(p.name)}</span></button>`).join('')}</div>` : `<div class="preview-empty"><span class="preview-symbol" aria-hidden="true">${icon('image')}</span><strong>在这里检查套图效果</strong><p>选好模板和印花后，点击“生成预览”。</p></div>`;
}
function renderTemplates() {
  return heading('模板管理','将 PSD 作为模板导入，逐场景确认印花位置。', '<button class="primary" data-action="import" data-edit>＋ 导入 PSD</button>') + (state.templates.length ? `<div class="catalog-grid">${state.templates.map(t=>`<article class="card catalog-tile"><div class="catalog-cover">${t.scenes?.find(s=>s.thumbnail)?`<img src="${attr(t.scenes.find(s=>s.thumbnail).thumbnail)}" alt="${attr(t.name)} 场景缩略图" loading="lazy">`:'PSD 模板'}</div><h3>${escapeHTML(t.name)}</h3><p title="${attr(t.path)}">${escapeHTML(t.path)}</p><footer><span class="badge">${t.scenes?.length||0} 个场景</span><div class="actions"><button data-open-template="${attr(t.id)}">配置</button><button class="text error-text" data-remove-template="${attr(t.id)}" data-edit>移除</button></div></footer>${t.warnings?.length?`<p class="field-help error-text">有 ${t.warnings.length} 条扫描提示，打开配置查看。</p>`:''}</article>`).join('')}</div>` : `<section class="card">${empty('导入第一个模板','支持一次选择多个 PSD 文件，导入后会自动扫描场景和候选印花。','<button class="primary" data-action="import" data-edit>选择 PSD 文件</button>')}</section>`);
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
  const signature = JSON.stringify(task);
  if(signature===lastTaskSignature && container.innerHTML) return;
  lastTaskSignature=signature;
  if(task.status==='done') {
    const message=task.kind==='preview'?'预览已生成':task.kind==='scan'?'模板已导入':'本次套图已处理完成';
    container.innerHTML=`<div class="completion-bar"><span>✓ ${message}</span>${task.kind==='batch'?'<button class="text" data-action="open-output">打开保存文件夹 ↗</button>':''}</div>`;
    return;
  }
  if (task.status === 'idle') { container.innerHTML=''; return; }
  const progress = Number(task.progress)||0, total = Number(task.total)||0;
  container.innerHTML = `<section class="card task-card" aria-label="任务进度"><header><div class="task-status"><h2>${task.kind==='scan'?'扫描模板':task.kind==='preview'?'生成预览':'当前任务'}</h2><span class="badge ${task.status==='failed'?'failed':task.status==='done'?'success':task.status==='running'?'uploading':''}">${statusLabel(task.status)}</span></div><small>${total?`${Math.round(progress/total*100)}%`:active?'请稍候':''}</small></header><div class="task-body"><p class="task-message ${task.error?'error-text':''}" role="status">${escapeHTML(task.error||task.message||statusLabel(task.status))}</p><div class="task-meter"><progress max="${total||1}" ${total||!active?`value="${total?Math.min(progress,total):task.status==='done'?1:0}"`:''} aria-label="任务进度"></progress><small>${progress} / ${total||'—'}</small></div><div class="task-footer"><small>${task.status==='paused'?'任务已暂停，点击继续可接着处理。':task.status==='stopping'?'正在结束当前处理，请等待。':active?'处理期间，模板和导出参数暂时锁定。':'任务记录保留在本次会话中。'}</small><div class="actions">${task.status==='running'&&task.kind==='batch'?'<button data-control="pause">暂停</button>':''}${task.status==='paused'?'<button data-control="resume" class="primary">继续</button>':''}${['running','paused'].includes(task.status)?'<button data-control="stop" class="danger-outline">停止</button>':''}</div></div>${task.results?.length?`<details class="results-detail"><summary>查看处理结果 · ${task.results.length} 项</summary><ul>${task.results.slice(-100).map(r=>`<li>${escapeHTML(typeof r==='string'?r:r.message||r.path||r.output||r.name||JSON.stringify(r))}</li>`).join('')}</ul></details>`:''}</div></section>`;
}
function updateLocks() {
  document.querySelectorAll('[data-edit]').forEach(element => { element.disabled = blocked() || (element.hasAttribute('data-dimension') && settings.width===0 && settings.height===0) || (element.hasAttribute('data-needs-artworks') && artworkLoading) || (element.dataset.action==='artworks-prev' && (artworkLoading || artworkOffset===0)) || (element.dataset.action==='artworks-next' && (artworkLoading || artworkOffset+40>=artworkTotal)) || (element.hasAttribute('data-resume') && !historyPath(state.history[Number(element.dataset.resume)]||{})); });
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
  if (!Number.isFinite(settings.rotation) || settings.rotation < -180 || settings.rotation > 180) throw new Error('角度微调请填写 -180～180 度的数字。');
  if (!Number.isInteger(Number(limit)) || Number(limit)<0) throw new Error('处理上限请填写大于或等于 0 的整数。');
}
async function choose(kind) {
  const data = window.desktop?.choose ? await window.desktop.choose(kind) : await request('/api/choose',{kind});
  return Array.isArray(data) ? data : data?.paths || [];
}
function savePreferences() {
  clearTimeout(preferenceTimer);
  preferenceTimer = setTimeout(async()=>{
    try { validateSettings(); } catch { return; }
    try { await request('/api/preferences',{input:input.trim(),output:output.trim(),settings,limit:Number(limit)}); }
    catch(error) { notify(`设置未能保存：${error.message}`,true); }
  },500);
}
async function loadArtworks(offset=0, resetSource=false) {
  const token=++artworkRequest, folder=input.trim();
  artworkOffset=offset; artworkFolder=folder; artworkError=''; artworkLoading=!!folder;
  artworkFiles=[]; if(offset===0)artworkTotal=0;
  if(resetSource)source='';
  const paint=()=>{if($('#artwork-browser')) { $('#artwork-browser').innerHTML=renderArtworks(); $('#artwork-browser').setAttribute('aria-busy',String(artworkLoading)); } updateLocks();};
  paint();
  if(!folder) { renderPage(); return; }
  try {
    const data=await request('/api/artworks',{folder,offset,limit:40});
    if(token!==artworkRequest || input.trim()!==folder)return;
    artworkFiles=data.files||[]; artworkTotal=data.total||0; artworkOffset=data.offset??offset;
    if(!source && artworkFiles.length)source=artworkFiles[0].path;
  } catch(error) { if(token!==artworkRequest)return; artworkError=error.message; }
  finally { if(token===artworkRequest) { artworkLoading=false; renderPage(); } }
}
async function handleAction(name) {
  if (name==='go-templates') { page='templates'; renderShell(); return; }
  if (name==='open-output') {
    try { if(!output.trim())throw new Error('请先选择保存文件夹。'); clearTimeout(preferenceTimer); await request('/api/preferences',{input:input.trim(),output:output.trim(),settings,limit:Number(limit)}); if(window.desktop?.openOutput)await window.desktop.openOutput(output.trim());else await request('/api/open-output',{path:output.trim()}); }
    catch(error) { notify(error.message,true); }
    return;
  }
  if (blocked()) return;
  try {
    if (name==='import') { const paths=await choose('psd'); if(paths.length) await action('/api/scan',{paths},'开始扫描 PSD 模板'); }
    if (name==='refresh-template') { const template=currentTemplate(); if(template) await action('/api/scan',{paths:[template.path]},'开始重新扫描模板'); }
    if (name==='choose-input'||name==='choose-output') { const paths=await choose('folder'); if(paths.length) { if(name==='choose-input') { input=paths[0]; renderPage(); await loadArtworks(0,true); } else { output=paths[0]; renderPage(); } savePreferences(); } }
    if (name==='choose-image') { const paths=await choose('image'); if(paths.length) { source=paths[0]; renderPage(); } }
    if (name==='reload-artworks') await loadArtworks(artworkOffset);
    if (name==='artworks-prev') await loadArtworks(Math.max(0,artworkOffset-40));
    if (name==='artworks-next') await loadArtworks(artworkOffset+40);
    if (name==='save-template') { busyRequest=true; updateLocks(); await saveTemplate(); await refresh(); renderPage(); notify('场景设置已保存'); }
    if (name==='preview') {
      validateSettings(); if(!selectedId) throw new Error('请先导入并选择模板。');
      if(!source.trim()&&!input.trim()) throw new Error('请选择一张预览印花，或填写印花文件夹。');
      if(dirty.has(selectedId)) throw new Error('请先保存当前模板的场景设置。');
      await action('/api/preview',{id:selectedId,source:source.trim()||input.trim(),settings},'正在生成预览');
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
  if(button.dataset.fitChoice){settings.fit=button.dataset.fitChoice;renderPage();savePreferences();return;}
  if(button.dataset.page) { page=button.dataset.page; renderShell(); }
  else if(button.dataset.action) await handleAction(button.dataset.action);
  else if(button.dataset.control) await action('/api/control',{action:button.dataset.control});
  else if(button.dataset.configureTemplate) { selectedId=button.dataset.configureTemplate; sceneSettingsOpen=true; renderPage(); }
  else if(button.dataset.artwork) { source=button.dataset.artwork; renderPage(); }
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
  if(field.id==='template-search'){templateSearch=field.value;filterTemplates();return;}
  if(field.dataset.naming) { settings.naming[field.dataset.naming]=field.type==='number'?Number(field.value):field.value; if($('#naming-example'))$('#naming-example').textContent=namingExample(); savePreferences(); }
  if(field.dataset.model) { const value=field.type==='number'?Number(field.value):field.value; if(field.dataset.model==='input')input=value; if(field.dataset.model==='output')output=value; if(field.dataset.model==='source')source=value; if(field.dataset.model==='limit')limit=value; }
  if(field.dataset.model && $('#preview-content'))$('#preview-content').innerHTML=renderPreviews();
  if(field.dataset.setting) { settings[field.dataset.setting]=field.type==='checkbox'?field.checked:field.type==='number'?Number(field.value):field.value; if($('#preview-content'))$('#preview-content').innerHTML=renderPreviews(); }
  if(field.dataset.setting || field.dataset.model==='limit')savePreferences();
  updateWorkflow();
});
app.addEventListener('change',(event)=> {
  const field=event.target;
  if(field.dataset.namingMode) {
    const key=field.dataset.namingMode,custom=field.value==='custom';
    if(key==='folder')customFolderRule=custom;else customImageRule=custom;
    if(!custom)settings.naming[key]=field.value;
    renderPage();savePreferences();
  }
  if(field.hasAttribute('data-original-size')) { if(field.checked)customSize={width:settings.width,height:settings.height}; settings.width=field.checked?0:customSize.width; settings.height=field.checked?0:customSize.height; renderPage(); savePreferences(); }
  if(field.id==='template-select') { selectedId=field.value; renderPage(); }
  if(field.dataset.selectTemplate) { if(field.checked)selectedIds.add(field.dataset.selectTemplate);else selectedIds.delete(field.dataset.selectTemplate); renderPage(); }
  if(field.dataset.model==='input' && input.trim()!==artworkFolder)loadArtworks(0,true);
  if(field.dataset.model==='input' || field.dataset.model==='output')savePreferences();
  // Keep the clicked element alive when a field blurs on mouse-down.
  // Rebuilding the page here would swallow the following mouse-up/click.
  if(field.dataset.model==='output' || field.dataset.setting)updateLocks();
  if(field.dataset.sceneMode) { setDraft(field.dataset.sceneMode,s=>{s.mode=field.value;}); renderPage(); }
  if(field.dataset.roleScene) { setDraft(field.dataset.roleScene,s=>{s.artwork_roles={...(s.artwork_roles||{})};if(field.value)s.artwork_roles[field.dataset.roleTarget]=field.value;else delete s.artwork_roles[field.dataset.roleTarget];}); renderPage(); }
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
async function refresh() {
  try {
    const next=await request('/api/state');
    const oldStatus=state.task?.status, wasConnected=connected;
    state={...next,templates:next.templates||[],task:next.task||{status:'idle'},cache:next.cache||{},history:next.history||[]};
    connected=true;
    const firstLoad=!initialized;
    if(firstLoad) { const pref=state.preferences||{}; input=pref.input||'';output=pref.output||'';settings={...settings,...pref.settings,fit_policy:2};limit=pref.limit||0; if(settings.width&&settings.height)customSize={width:settings.width,height:settings.height}; initialized=true; }
    const signature=JSON.stringify([state.templates,state.history,state.cache]);
    const previews=JSON.stringify([state.task.preview,state.task.preview_template_id,state.task.preview_settings]);
    const available=new Set(state.templates.map(t=>t.id));
    selectedIds=new Set([...selectedIds].filter(id=>available.has(id)));
    if(!available.has(selectedId)) { selectedId=state.templates[0]?.id||'';if(selectedId)selectedIds.add(selectedId); }
    if(!$('#page-content')||!wasConnected) { renderShell(); catalogSignature=signature; }
    else {
      renderTask();
      const editing=draggedScene||document.activeElement?.matches('input,select,summary');
      if(signature!==catalogSignature&&!editing) { renderPage(); catalogSignature=signature; }
      if(previews!==previewSignature&&$('#preview-content')){$('#preview-content').innerHTML=renderPreviews();updateWorkflow();}
      if(oldStatus!==state.task.status)updateLocks();
    }
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
async function poll() { await refresh(); setTimeout(poll, activeStatuses.includes(state.task?.status)?1000:3000); }
poll();

window.addEventListener('workbench-update-lock',updateLocks);
