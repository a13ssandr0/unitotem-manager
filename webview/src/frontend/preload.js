const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('electronAPI', {
    getOrientation: (window) => ipcRenderer.invoke('mainWindow:getOrientation', window),
    getFlip: (window) => ipcRenderer.invoke('mainWindow:getFlip', window),
})