const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('electronAPI', {
    getAllDisplays: () => ipcRenderer.invoke('screen:getAllDisplays'),
    getBounds: () => ipcRenderer.invoke('mainWindow:getBounds'),
    setBounds: (x, y, w, h) => ipcRenderer.invoke('mainWindow:setBounds', x, y, w, h)
})