'use strict';
const {contextBridge, ipcRenderer} = require('electron');
contextBridge.exposeInMainWorld('desktop', Object.freeze({
  isDesktop: true,
  choose: (kind) => ipcRenderer.invoke('choose', kind),
  openOutput: (path) => ipcRenderer.invoke('open-output', path),
  updates: Object.freeze({
    state:()=>ipcRenderer.invoke('update-state'),
    check:()=>ipcRenderer.invoke('update-check'),
    download:()=>ipcRenderer.invoke('update-download'),
    cancel:()=>ipcRenderer.invoke('update-cancel'),
    install:()=>ipcRenderer.invoke('update-install'),
    onState:callback=>{const handler=(_event,state)=>callback(state);ipcRenderer.on('update-state',handler);return ()=>ipcRenderer.removeListener('update-state',handler)}
  })
}));
