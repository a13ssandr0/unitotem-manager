const { app, BrowserWindow, screen, ipcMain} = require('electron');
const path = require('path');
const {readFileSync, writeFileSync} = require('fs');
const {homedir} = require("os");


app.whenReady().then(() => {
    const screens = screen.getAllDisplays();

    const _baseConfig = {
        allowInsecureCerts: false,
        bounds: {
            x:      screens[0].bounds.x,
            y:      screens[0].bounds.y, 
            width:  screens[0].bounds.width,
            height: screens[0].bounds.height
        }
    };
    var file = {};
    try {
        file = JSON.parse(readFileSync(path.join(homedir(), '.unitotem-viewer.conf')));
    } catch (err) {
        writeFileSync(path.join(homedir(), '.unitotem-viewer.conf'), JSON.stringify(_baseConfig));
    }
    const config = new Proxy(file, {
        get: (target, name)=>{
            return target.hasOwnProperty(name) ? target[name] : _baseConfig[name];
        }
    });
    


    const mainWindow = new BrowserWindow({
        autoHideMenuBar: true,
        autoplayPolicy:  'no-user-gesture-required',
        backgroundColor: '#000000',
        frame:           false,
        titleBarStyle:   'hidden',
        webPreferences:  {
            preload: path.join(__dirname, 'preload.js'),
            webviewTag: true
        },
        x:               config.bounds.x,
        y:               config.bounds.y,
        width:           config.bounds.width,
        height:          config.bounds.height
    });
    mainWindow.loadFile('../manager/templates/boot-screen.html');
    ipcMain.handle('screen:getAllDisplays', screen.getAllDisplays);
    ipcMain.handle('mainWindow:getBounds', () => {return config.bounds});
    ipcMain.handle('mainWindow:setBounds', (e, x, y, w, h) => {
            mainWindow.setBounds({x:x, y:y, width:w, height:h});
            config.bounds = {x:x, y:y, width:w, height:h};
            writeFileSync(path.join(homedir(), '.unitotem-viewer.conf'), JSON.stringify(config));
        });
    ipcMain.handle('config:getAllowInsecureCerts', () => {return config.allowInsecureCerts});
    ipcMain.handle('config:setAllowInsecureCerts', (allow) => {
        config.allowInsecureCerts = allow;
        writeFileSync(path.join(homedir(), '.unitotem-viewer.conf'), JSON.stringify(config));
    });
});

app.on('certificate-error', (event, webContents, url, error, certificate, callback) => {
    if (config.allowInsecureCerts || url.match('(?:http|ws)s?://localhost')) {
        event.preventDefault();
        callback(true);
    } else {
        callback(false);
    }
});