const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('overlayAPI', {
  config: () => ipcRenderer.invoke('overlay-config'),
  readJson: (fileName) => ipcRenderer.invoke('read-json', fileName),
  artPath: (cardId) => ipcRenderer.invoke('card-art', cardId),
  setClickThrough: (value) => ipcRenderer.invoke('set-click-through', value),
  onState: (callback) => ipcRenderer.on('overlay-state', (_event, state) => callback(state)),
  onFileChanged: (callback) => ipcRenderer.on('overlay-file-changed', (_event, fileName) => callback(fileName)),
});
