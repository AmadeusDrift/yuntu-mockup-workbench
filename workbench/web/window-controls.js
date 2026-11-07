const bridge=window.desktop?.window;
if(bridge){
  document.documentElement.classList.add('is-desktop');
  const svg=path=>`<svg viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.2">${path}</svg>`;
  const maximize=svg('<rect x="3.5" y="3.5" width="9" height="9"/>');
  const restore=svg('<path d="M5.5 3.5v-1h8v8h-1M2.5 5.5h8v8h-8z"/>');
  let state={maximized:false,version:''};
  function mount(){
    const top=document.querySelector('.topbar');
    if(!top||top.querySelector('.window-controls'))return;
    const controls=document.createElement('div');controls.className='window-controls';controls.setAttribute('aria-label','窗口操作');
    for(const [action,label,icon] of [['minimize','最小化',svg('<path d="M3 8h10"/>')],['maximize','最大化',maximize],['close','关闭',svg('<path d="m4 4 8 8M12 4l-8 8"/>')]]){
      const button=document.createElement('button');button.type='button';button.dataset.windowAction=action;button.title=label;button.setAttribute('aria-label',label);button.innerHTML=icon;
      button.addEventListener('click',()=>bridge.action(action).catch(console.error));controls.append(button);
    }
    top.append(controls);
    const version=document.createElement('small');version.className='window-version';top.firstElementChild.append(version);
    update();
  }
  function update(){
    const button=document.querySelector('[data-window-action="maximize"]');
    if(button){button.innerHTML=state.maximized?restore:maximize;button.title=state.maximized?'还原窗口':'最大化';button.setAttribute('aria-label',button.title)}
    const version=document.querySelector('.window-version');if(version)version.textContent=state.version?'v'+state.version:'';
  }
  bridge.onState(value=>{state=value;update()});
  bridge.state().then(value=>{state=value;update()}).catch(console.error);
  new MutationObserver(mount).observe(document.querySelector('#app'),{childList:true,subtree:true});
  mount();
}
