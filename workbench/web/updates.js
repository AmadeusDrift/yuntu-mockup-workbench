const api=window.desktop?.updates;
if(api){
  const dialog=document.querySelector('#update-dialog');let state={},announced='';
  const labels={idle:'检查更新',checking:'检查更新中…',available:'发现新版本',downloading:'正在下载更新',ready:'安装更新',installing:'正在安装更新',offline:'检查更新',error:'更新未完成',current:'检查更新'};
  function render(next){
    state=next;window.workbenchUpdateInstalling=state.status==='installing';
    window.dispatchEvent(new Event('workbench-update-lock'));
    const button=document.querySelector('#check-update');if(button)button.textContent=labels[state.status]||'检查更新';
    document.querySelector('#update-title').textContent=state.version&&['available','downloading','ready','installing','error'].includes(state.status)?`更新到 v${state.version}`:'软件更新';
    document.querySelector('#update-current').textContent=`当前版本 v${state.current||''}`;
    document.querySelector('#update-message').textContent=state.message||'每天首次启动自动检查，有新版时提醒。';
    document.querySelector('#update-notes').textContent=state.notes||'';
    const progress=document.querySelector('#update-progress');progress.hidden=state.status!=='downloading';progress.value=state.progress||0;
    const download=document.querySelector('#update-download');download.hidden=!(['available','error'].includes(state.status)&&state.version);download.textContent=state.status==='error'?'重新下载并更新':'立即更新';
    document.querySelector('#update-install').hidden=state.status!=='ready';
    document.querySelector('#update-recheck').hidden=['downloading','installing','checking'].includes(state.status);
    document.querySelector('#update-cancel').hidden=state.status!=='downloading';
    document.querySelector('#update-close').disabled=state.status==='installing';
    if(state.status==='available'&&!state.manual&&announced!==state.version){announced=state.version;if(!dialog.open)dialog.showModal()}
  }
  function attach(){const parent=document.querySelector('.top-actions');if(parent&&!document.querySelector('#check-update')){const button=document.createElement('button');button.id='check-update';button.className='text';button.textContent=labels[state.status]||'检查更新';button.onclick=()=>{dialog.showModal();if(!['available','downloading','ready','installing'].includes(state.status))api.check()};parent.prepend(button)}}
  new MutationObserver(attach).observe(document.querySelector('#app'),{subtree:true,childList:true});attach();
  api.onState(render);api.state().then(render);
  document.querySelector('#update-close').onclick=()=>dialog.close();
  document.querySelector('#update-download').onclick=()=>api.download();
  document.querySelector('#update-install').onclick=()=>api.install();
  document.querySelector('#update-recheck').onclick=()=>api.check();
  document.querySelector('#update-cancel').onclick=()=>api.cancel();
  dialog.addEventListener('cancel',e=>{if(state.status==='installing')e.preventDefault()});
}
