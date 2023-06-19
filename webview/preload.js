const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('electronAPI', {
    getAllDisplays: () => ipcRenderer.invoke('screen:getAllDisplays'),
    getBounds: () => ipcRenderer.invoke('mainWindow:getBounds'),
    setBounds: (x, y, w, h) => ipcRenderer.invoke('mainWindow:setBounds', x, y, w, h),
    saveOrientation: (o) => ipcRenderer.invoke('mainWindow:saveOrientation', o),
    loadOrientation: () => ipcRenderer.invoke('mainWindow:loadOrientation'),
    saveFlip: (f) => ipcRenderer.invoke('mainWindow:saveFlip', f),
    loadFlip: () => ipcRenderer.invoke('mainWindow:loadFlip'),
    getAllowInsecureCerts: () => ipcRenderer.invoke('config:getAllowInsecureCerts'),
    setAllowInsecureCerts: (allow) => ipcRenderer.invoke('config:setAllowInsecureCerts', allow),
    resetDefaults: () => ipcRenderer.invoke('config:reset'),
})