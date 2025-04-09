const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('electronAPI', {
    getOrientation: () => ipcRenderer.invoke('mainWindow:getOrientation'),
    getFlip: () => ipcRenderer.invoke('mainWindow:getFlip'),
})