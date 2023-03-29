const { app, BrowserWindow, screen, ipcMain} = require('electron');
const path = require('path')

var allowInsecureCerts = false;

app.whenReady().then(() => {
    var screens = screen.getAllDisplays();
    var mainWindow = new BrowserWindow({
        autoHideMenuBar: true,
        autoplayPolicy:  'no-user-gesture-required',
        backgroundColor: '#000000',
        frame:           false,
        titleBarStyle:   'hidden',
        webPreferences:  {
            preload: path.join(__dirname, 'preload.js'),
            webviewTag: true
        },
        x:               screens[0].bounds.x,
        y:               screens[0].bounds.y, 
        width:           screens[0].bounds.width,
        height:          screens[0].bounds.height
    });
    mainWindow.loadFile('../manager/templates/boot-screen.html');
    ipcMain.handle('screen:getAllDisplays', screen.getAllDisplays);
    ipcMain.handle('mainWindow:getBounds', () => mainWindow.getBounds());
    ipcMain.handle('mainWindow:setBounds', (e, x, y, w, h) => 
            mainWindow.setBounds({x:x, y:y, width:w, height:h}));
});

app.on('certificate-error', (event, webContents, url, error, certificate, callback) => {
    if (allowInsecureCerts || url.match('(?:http|ws)s?://localhost')) {
        event.preventDefault();
        callback(true);
    } else {
        callback(false);
    }
});