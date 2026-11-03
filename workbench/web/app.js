const app = document.querySelector('#app');
const $ = (selector) => document.querySelector(selector);
const escapeHTML = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const attr = escapeHTML;
const bytes = (value) => { const n = Number(value) || 0; return n >= 1073741824 ? `${(n / 1073741824).toFixed(2)} GB` : `${(n / 1048576).toFixed(1)} MB`; };
const activeStatuses = ['running', 'paused', 'stopping'];
let state = {templates:[],task:{status:'idle'},cache:{},history:[],preferences:{}};
let page = 'workbench', selectedId = '', selectedIds = new Set(), sceneDraft = new Map(), dirty = new Set();
let input = '', output = '', source = '', settings = {width:0,height:0,ppi:72,quality:100,fit:'auto',format:'jpg',trim:false}, limit = 0;
let customSize = {width:1254,height:1254}, lastTaskSignature = '';
let initialized = false, busyRequest = false, connected = false, catalogSignature = '', previewSignature = '', toastTimer;
let artworkFiles = [], artworkTotal = 0, artworkOffset = 0, artworkFolder = '', artworkLoading = false, artworkError = '', artworkRequest = 0;
let sceneSettingsOpen = false, advancedOpen = false, preferenceTimer;
const titles = {workbench:'套图工作台', templates:'模板管理', history:'批次记录', cache:'缓存管理'};
const blocked = () => !connected || activeStatuses.includes(state.task?.status) || busyRequest;
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
function renderShell() {
  app.innerHTML = `<div class="app"><aside class="sidebar"><div class="brand"><span class="brand-mark">▧</span><b>套图工作台</b></div><nav aria-label="主导航">${[['workbench','▧'],['templates','▤'],['history','◷'],['cache','▱']].map(([key,icon]) => `<button type="button" data-page="${key}" class="${page===key?'active':''}" title="${titles[key]}" ${page===key?'aria-current="page"':''}><span class="nav-symbol" aria-hidden="true">${icon}</span><span class="nav-text">${titles[key]}</span></button>`).join('')}</nav><div class="service"><i class="${connected?'green':'red'}"></i><span>${connected?'桌面版 · 离线可用':'正在重新连接'}</span></div></aside><div class="workspace"><header class="topbar"><span>套图工具 <em>/</em> <strong>${titles[page]}</strong></span><div class="top-actions"><span class="local-tag"><i></i> 桌面版 · 离线可用</span></div></header><main><div id="connection"></div><div id="task-panel"></div><div id="page-content"></div></main></div></div>`;
  renderPage(); renderTask();
}
function renderPage() {
  if (!$('#page-content')) return;
  $('#page-content').innerHTML = ({workbench:renderWorkbench,templates:renderTemplates,history:renderHistory,cache:renderCache}[page])();
  updateLocks();
}
function templateName(template) {
  const name=template.name.replace(/\.psd$/i,'').replace(/^\d+[_-]/,'');
  const parts=name.split(/[_-]+/).filter(part=>!/(?:七图联动|母版|定稿|修正版|修复版)|^v\d+$/i.test(part)).map(part=>part==='轮廓适配版'?'轮廓版':part);
  return parts.slice(0,3).join(' · ') || name;
}
function renderTemplateCards() {
  return `<div class="template-cards" aria-label="选择套图模板">${state.templates.map(t=>{
    const thumb=t.scenes?.find(scene=>scene.thumbnail)?.thumbnail;
    return `<article class="template-card ${selectedIds.has(t.id)?'selected':''} ${selectedId===t.id?'current':''}"><button class="template-card-cover" data-configure-template="${attr(t.id)}" data-edit aria-label="配置 ${attr(t.name)}" ${selectedId===t.id?'aria-current="true"':''}>${thumb?`<img src="${attr(thumb)}" alt="${attr(t.name)}" loading="lazy">`:'<span>PSD 模板</span>'}${selectedId===t.id?'<span class="configuring">正在配置</span>':''}</button><label title="${attr(t.name)}"><input type="checkbox" data-select-template="${attr(t.id)}" ${selectedIds.has(t.id)?'checked':''} data-edit><strong>${escapeHTML(templateName(t))}</strong></label><small>${t.scenes?.length||0} 个场景${dirty.has(t.id)?' · 未保存':''}</small></article>`;
  }).join('')}</div>`;
}
function renderArtworks() {
  if(artworkLoading) return '<div class="artwork-empty" role="status">正在读取印花缩略图…</div>';
  if(artworkError) return `<div class="notice danger" role="alert">${escapeHTML(artworkError)}<button data-action="reload-artworks" data-edit>重试</button></div>`;
  if(!input.trim()) return '<div class="artwork-empty">选择文件夹后，在这里查看印花并选一张预览。</div>';
  if(!artworkFiles.length) return '<div class="artwork-empty">文件夹中没有可用图片，请选择其他印花文件夹。</div>';
  return `<div class="artwork-summary"><span>共 <strong>${artworkTotal}</strong> 张印花</span><small>点击一张，用于效果预览</small></div><div class="artwork-grid">${artworkFiles.map(file=>`<button class="artwork-tile ${file.path===source?'selected':''}" data-artwork="${attr(file.path)}" data-edit aria-pressed="${file.path===source}" title="${attr(file.name)}"><span class="artwork-image"><img src="${attr(file.url)}" alt="${attr(file.name)}" loading="lazy">${file.path===source?'<span class="artwork-check">✓</span>':''}</span><span class="artwork-name">${escapeHTML(file.name)}</span></button>`).join('')}</div>${artworkTotal>40?`<div class="artwork-pagination"><button data-action="artworks-prev" ${artworkOffset===0?'disabled':''} data-edit>上一页</button><small>${artworkOffset+1}–${Math.min(artworkOffset+40,artworkTotal)} / ${artworkTotal}</small><button data-action="artworks-next" ${artworkOffset+40>=artworkTotal?'disabled':''} data-edit>下一页</button></div>`:''}`;
}
function renderAdvanced() {
  return `<details class="advanced-settings" id="advanced-settings" ${advancedOpen?'open':''}><summary>高级设置 <small>尺寸、适配、图片质量</small></summary><p class="field-help">默认沿用 PSD 原始比例。自动适配会根据印花判断完整放入或铺满印区。</p><label class="check-label"><input type="checkbox" data-original-size ${settings.width===0&&settings.height===0?'checked':''} data-edit>沿用 PSD 原始尺寸与比例</label><div class="form-grid export-settings"><label>导出宽度 · px<input type="number" min="1" max="8192" data-setting="width" data-dimension value="${settings.width||customSize.width}" data-edit></label><label>导出高度 · px<input type="number" min="1" max="8192" data-setting="height" data-dimension value="${settings.height||customSize.height}" data-edit></label><label>印花适配<select data-setting="fit" data-edit>${options([['auto','自动适配 · 推荐'],['contain','完整放入'],['cover','铺满印区 · 裁切'],['stretch','拉伸填满']],settings.fit)}</select></label><label>导出格式<select data-setting="format" data-edit>${options([['jpg','JPG'],['png','PNG']],settings.format)}</select></label><label>图片质量 · 1–100<input type="number" min="1" max="100" data-setting="quality" value="${attr(settings.quality)}" data-edit></label><label>分辨率 · PPI<input type="number" min="1" max="2400" data-setting="ppi" value="${attr(settings.ppi)}" data-edit></label><label class="span-two">本批处理上限 · 0 为全部<input type="number" min="0" data-model="limit" value="${attr(limit)}" data-edit></label></div><label class="check-label"><input type="checkbox" data-setting="trim" ${settings.trim?'checked':''} data-edit>替换前裁去印花透明边缘</label></details>`;
}
function renderWorkbench() {
  const template = currentTemplate();
  return heading('套图工作台','选模板 → 选印花 → 选保存位置，预览满意后开始套图。',`<button data-action="import" data-edit>＋ 导入 PSD</button>`) + `<div class="work-grid"><div class="stack"><section class="card template-section"><header><h2><span class="section-index">1</span>选择模板</h2><button class="text" data-action="go-templates">管理模板</button></header><div class="card-body">${state.templates.length ? `${renderTemplateCards()}<p class="field-help">勾选模板加入批次；点击缩略图配置场景。</p>${template?`<details class="scene-settings" id="scene-settings" ${sceneSettingsOpen?'open':''}><summary>场景设置 <small>${escapeHTML(templateName(template))} · ${scenesFor(template).length} 个场景${dirty.has(template.id)?' · 有未保存的修改':''}</small></summary>${renderScenes(template)}</details>`:''}` : empty('还没有套图模板','导入 PSD 后，即可看到商品缩略图并设置印花位置。', '<button class="primary" data-action="import" data-edit>选择 PSD 文件</button>')}</div></section><section class="card artwork-section"><header><h2><span class="section-index">2</span>选择印花文件夹</h2><small>整批图片一起处理</small></header><div class="card-body"><div class="field-row"><input id="input-path" data-model="input" value="${attr(input)}" placeholder="选择包含印花图片的文件夹" aria-label="印花文件夹" data-edit><button data-action="choose-input" data-edit>选择文件夹</button></div><div id="artwork-browser" aria-busy="${artworkLoading}">${renderArtworks()}</div></div></section><section class="card output-section"><header><h2><span class="section-index">3</span>选择保存文件夹</h2></header><div class="card-body"><div class="field-row"><input id="output-path" data-model="output" value="${attr(output)}" placeholder="套图结果保存位置" aria-label="保存文件夹" data-edit><button data-action="choose-output" data-edit>选择文件夹</button></div><div class="output-hint"><span>${output?'套图完成后可直接打开文件夹。':'请选择一个文件夹保存套图结果。'}</span><button data-action="open-output" ${output?'':'disabled'}>打开文件夹 ↗</button></div>${renderAdvanced()}</div></section></div><div class="stack preview-stack"><section class="card"><header><h2>效果预览</h2><small>点击结果查看大图</small></header><div class="card-body"><div class="preview-selected"><span class="field-label">预览印花</span><strong title="${attr(source)}">${source?escapeHTML(source.split(/[\\/]/).pop()):'从左侧印花中选择一张'}</strong><button data-action="choose-image" data-edit>另选图片</button></div><div id="preview-content">${renderPreviews()}</div><div class="preview-tools"><small>预览模板：${template?escapeHTML(templateName(template)):'尚未选择'}${template&&!selectedIds.has(template.id)?'<br><span class="unsaved">尚未勾选加入批次</span>':''}</small><button data-action="preview" class="primary" data-needs-artworks data-edit>生成预览</button></div><div class="batch-launch"><p>已选 <strong id="selected-count">${selectedIds.size}</strong> 个模板${artworkTotal?` · <strong>${artworkTotal}</strong> 张印花`:''}<br>${settings.width===0&&settings.height===0?'沿用 PSD 原始比例':'自定义导出尺寸'} · ${settings.fit==='auto'?'自动适配印花':settings.fit==='cover'?'铺满印区':settings.fit==='contain'?'完整放入':'拉伸填满'}</p><button class="primary" data-action="batch" data-needs-artworks data-edit>开始批量套图 →</button></div></div></section><div class="simple-tip">透明印花和满版图均可使用自动适配。首次使用模板时，可展开场景设置检查印花位置。</div></div></div>`;
}
function options(values, selected) { return values.map(([value,label])=>`<option value="${value}" ${value===selected?'selected':''}>${label}</option>`).join(''); }
function renderScenes(template) {
  const scenes = scenesFor(template);
  const warnings = template.warnings || [];
  return `${template.stale?'<div class="notice warning"><span>PSD 文件已有变化，请重新导入并核对场景设置。</span><button data-action="refresh-template" data-edit>重新导入</button></div>':''}${warnings.length ? `<div class="notice warning"><ul class="warning-list">${warnings.map(w=>`<li>${escapeHTML(typeof w==='string'?w:JSON.stringify(w))}</li>`).join('')}</ul></div>`:''}<div class="scene-list">${scenes.map(scene=>`<article class="scene ${scene.mode==='skip'?'scene-muted':''}"><button class="scene-thumb" ${scene.thumbnail?`data-image="${attr(scene.thumbnail)}" data-title="${attr(scene.name)}"`:'disabled'} aria-label="查看 ${attr(scene.name)} 场景">${scene.thumbnail?`<img src="${attr(scene.thumbnail)}" alt="${attr(scene.name)}" loading="lazy">`:'PSD'}</button><div><div class="scene-title"><strong>${escapeHTML(scene.name)}</strong><select aria-label="${attr(scene.name)} 导出方式" data-scene-mode="${attr(scene.id)}" data-edit>${options([['replace','换图'],['fixed','固定'],['skip','不导出']],scene.mode||'replace')}</select></div><div class="scene-path">${scene.mode==='replace'?`已选 ${scene.targets?.length||0} 个印花图层`:scene.mode==='fixed'?'保留当前场景原样':'跳过此场景'}</div><details ${scene.mode==='replace'&&!(scene.targets?.length)?'open':''}><summary>印花图层 · 手动调整</summary><div class="target-list">${(scene.candidates||[]).map(candidate=>`<label><input type="checkbox" data-target-scene="${attr(scene.id)}" value="${attr(candidate.id)}" ${(scene.targets||[]).map(String).includes(String(candidate.id))?'checked':''} data-edit><span>${escapeHTML(candidate.path||candidate.name)}${candidate.linked?' · 关联图层':''}</span></label>`).join('')||'<span class="muted">没有可选的智能对象，请检查 PSD。</span>'}</div></details></div></article>`).join('')||empty('没有可用场景','请检查 PSD 或重新导入。')}</div><div class="scene-controls"><small>${dirty.has(template.id)?'<span class="unsaved">● 场景设置尚未保存</span>':'场景设置已与模板同步'}</small><button data-action="save-template" data-edit>保存场景设置</button></div>`;
}
function renderPreviews() {
  const previews = state.task?.preview_template_id === selectedId ? state.task.preview || [] : [];
  const sceneSettings = currentTemplate() ? scenesFor(currentTemplate()).map(({id,mode,targets})=>({id,mode,targets})) : [];
  const changed = (state.task?.preview_settings && Object.entries(settings).some(([key,value])=>state.task.preview_settings[key]!==value)) || (state.task?.preview_source && state.task.preview_source !== (source.trim()||input.trim())) || (state.task?.preview_scenes && JSON.stringify(state.task.preview_scenes)!==JSON.stringify(sceneSettings));
  return previews.length ? `${changed?'<div class="notice warning">预览参数与当前设置不同，请重新生成预览。</div>':''}<div class="preview-grid">${previews.map(p=>`<button class="preview-tile" data-image="${attr(p.url)}" data-title="${attr(p.name)}"><img src="${attr(p.url)}" alt="${attr(p.name)}" loading="lazy"><span>${escapeHTML(p.name)}</span></button>`).join('')}</div>` : `<div class="preview-empty"><span class="preview-symbol" aria-hidden="true">▧</span><strong>在这里检查套图效果</strong><p>选好模板和印花后，点击“生成预览”。</p></div>`;
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
}
function setDraft(sceneId, mutate) {
  const template = currentTemplate(); if (!template) return;
  if (!sceneDraft.has(template.id)) sceneDraft.set(template.id,structuredClone(template.scenes||[]));
  const scene = sceneDraft.get(template.id).find(s=>String(s.id)===String(sceneId));
  if (scene) { mutate(scene); dirty.add(template.id); }
}
async function saveTemplate() {
  const template=currentTemplate(); if (!template) return;
  const scenes=scenesFor(template).map(({id,mode,targets})=>({id,mode,targets:targets||[]}));
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
  if(button.dataset.page) { page=button.dataset.page; renderShell(); }
  else if(button.dataset.action) await handleAction(button.dataset.action);
  else if(button.dataset.control) await action('/api/control',{action:button.dataset.control});
  else if(button.dataset.configureTemplate) { selectedId=button.dataset.configureTemplate; sceneSettingsOpen=true; renderPage(); }
  else if(button.dataset.artwork) { source=button.dataset.artwork; renderPage(); }
  else if(button.dataset.openTemplate) { selectedId=button.dataset.openTemplate; sceneSettingsOpen=true; page='workbench'; renderShell(); }
  else if(button.dataset.removeTemplate) {
    const template=state.templates.find(t=>t.id===button.dataset.removeTemplate);
    if(confirm(`从模板库移除“${template?.name||'此模板'}”？PSD 原文件会保留。`)) await action('/api/remove-template',{id:button.dataset.removeTemplate},'已从模板库移除');
  }
  else if(button.dataset.resume!==undefined) { const history=state.history[Number(button.dataset.resume)]; await action('/api/resume',{path:historyPath(history)},'正在继续批次'); }
  else if(button.dataset.image) { $('#large-image').src=button.dataset.image; $('#image-title').textContent=button.dataset.title||'图片预览'; $('#image-dialog').showModal(); }
});
app.addEventListener('input',(event)=> {
  const field=event.target;
  if(field.dataset.model) { const value=field.type==='number'?Number(field.value):field.value; if(field.dataset.model==='input')input=value; if(field.dataset.model==='output')output=value; if(field.dataset.model==='source')source=value; if(field.dataset.model==='limit')limit=value; }
  if(field.dataset.model && $('#preview-content'))$('#preview-content').innerHTML=renderPreviews();
  if(field.dataset.setting) { settings[field.dataset.setting]=field.type==='checkbox'?field.checked:field.type==='number'?Number(field.value):field.value; if($('#preview-content'))$('#preview-content').innerHTML=renderPreviews(); }
  if(field.dataset.setting || field.dataset.model==='limit')savePreferences();
});
app.addEventListener('change',(event)=> {
  const field=event.target;
  if(field.hasAttribute('data-original-size')) { if(field.checked)customSize={width:settings.width,height:settings.height}; settings.width=field.checked?0:customSize.width; settings.height=field.checked?0:customSize.height; renderPage(); savePreferences(); }
  if(field.id==='template-select') { selectedId=field.value; renderPage(); }
  if(field.dataset.selectTemplate) { if(field.checked)selectedIds.add(field.dataset.selectTemplate);else selectedIds.delete(field.dataset.selectTemplate); renderPage(); }
  if(field.dataset.model==='input' && input.trim()!==artworkFolder)loadArtworks(0,true);
  if(field.dataset.model==='input' || field.dataset.model==='output')savePreferences();
  if(field.dataset.model==='output')renderPage();
  if(field.dataset.setting)renderPage();
  if(field.dataset.sceneMode) { setDraft(field.dataset.sceneMode,s=>{s.mode=field.value;}); renderPage(); }
  if(field.dataset.targetScene) { setDraft(field.dataset.targetScene,s=>{ const candidate=(s.candidates||[]).find(c=>String(c.id)===field.value); const id=candidate?.id??field.value; s.targets=(s.targets||[]).filter(t=>String(t)!==String(id)); if(field.checked)s.targets.push(id); }); renderPage(); }
  if(field.dataset.manualTarget) { setDraft(field.dataset.manualTarget,s=>{s.targets=[...new Set(field.value.split(/[,，\s]+/).filter(Boolean))];}); renderPage(); }
});
app.addEventListener('keydown',event=>{ if(event.key==='Enter' && event.target.dataset.model==='input') { event.preventDefault(); loadArtworks(0,input.trim()!==artworkFolder); savePreferences(); } });
app.addEventListener('toggle',event=>{ if(event.target.id==='scene-settings')sceneSettingsOpen=event.target.open; if(event.target.id==='advanced-settings')advancedOpen=event.target.open; },true);
$('#close-image').addEventListener('click',()=>$('#image-dialog').close());
$('#image-dialog').addEventListener('click',event=>{if(event.target===$('#image-dialog'))$('#image-dialog').close();});
async function refresh() {
  try {
    const next=await request('/api/state');
    const oldStatus=state.task?.status, wasConnected=connected;
    state={...next,templates:next.templates||[],task:next.task||{status:'idle'},cache:next.cache||{},history:next.history||[]};
    connected=true;
    const firstLoad=!initialized;
    if(firstLoad) { const pref=state.preferences||{}; input=pref.input||'';output=pref.output||'';settings={...settings,...pref.settings};limit=pref.limit||0; if(settings.width&&settings.height)customSize={width:settings.width,height:settings.height}; initialized=true; }
    const signature=JSON.stringify([state.templates,state.history,state.cache]);
    const previews=JSON.stringify([state.task.preview,state.task.preview_template_id,state.task.preview_settings]);
    const available=new Set(state.templates.map(t=>t.id));
    selectedIds=new Set([...selectedIds].filter(id=>available.has(id)));
    if(!available.has(selectedId)) { selectedId=state.templates[0]?.id||'';if(selectedId)selectedIds.add(selectedId); }
    if(!$('#page-content')||!wasConnected) { renderShell(); catalogSignature=signature; }
    else {
      renderTask();
      const editing=document.activeElement?.matches('input,select,summary');
      if(signature!==catalogSignature&&!editing) { renderPage(); catalogSignature=signature; }
      if(previews!==previewSignature&&$('#preview-content'))$('#preview-content').innerHTML=renderPreviews();
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
