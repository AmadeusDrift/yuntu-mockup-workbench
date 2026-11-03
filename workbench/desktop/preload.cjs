'use strict';
const {contextBridge, ipcRenderer} = require('electron');
contextBridge.exposeInMainWorld('desktop', Object.freeze({
  isDesktop: true,
  choose: (kind) => ipcRenderer.invoke('choose', kind),
  openOutput: (path) => ipcRenderer.invoke('open-output', path)
}));
