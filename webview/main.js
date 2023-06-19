const { app, BrowserWindow, screen, ipcMain} = require('electron');
const path = require('path');
const {readFileSync, writeFileSync, unlinkSync} = require('fs');
const {homedir} = require("os");

const cfg_file_path = path.join(homedir(), '.unitotem-viewer.conf');

app.whenReady().then(() => {
    const screens = screen.getAllDisplays();

    const _baseConfig = {
        allowInsecureCerts: false,
        bounds: {
            x:      screens[0].bounds.x,
            y:      screens[0].bounds.y, 
            width:  screens[0].bounds.width,
            height: screens[0].bounds.height
        },
        flip: 0,
        orientation: 0
    };
    var file = {};
    try {
        file = JSON.parse(readFileSync(cfg_file_path));
    } catch (err) {
        writeFileSync(cfg_file_path, JSON.stringify(_baseConfig));
    }
    const config = new Proxy(file, {
        get: (target, name)=>{
            return target.hasOwnProperty(name) ? target[name] : _baseConfig[name];
        },
        // save: (filename = cfg_file_path) =>
        //             writeFileSync(filename, JSON.stringify(SELF))
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
    mainWindow.loadFile('boot-screen.html');
    ipcMain.handle('screen:getAllDisplays', screen.getAllDisplays);
    ipcMain.handle('mainWindow:getBounds', () => {return config.bounds});
    ipcMain.handle('mainWindow:setBounds', (e, x, y, w, h) => {
        mainWindow.setBounds({x:x, y:y, width:w, height:h});
        config.bounds = {x:x, y:y, width:w, height:h};
        writeFileSync(cfg_file_path, JSON.stringify(config));
    });
    ipcMain.handle('mainWindow:saveOrientation', (e, orientation) => {
        config.orientation = orientation;
        writeFileSync(cfg_file_path, JSON.stringify(config));
    });
    ipcMain.handle('mainWindow:loadOrientation', () => {return config.orientation});
    ipcMain.handle('mainWindow:saveFlip', (e, flip) => {
        config.flip = flip;
        writeFileSync(cfg_file_path, JSON.stringify(config));
    });
    ipcMain.handle('mainWindow:loadFlip', () => {return config.flip});
    ipcMain.handle('config:getAllowInsecureCerts', () => {return config.allowInsecureCerts});
    ipcMain.handle('config:setAllowInsecureCerts', (e, allow) => {
        config.allowInsecureCerts = allow;
        writeFileSync(cfg_file_path, JSON.stringify(config));
    });

    ipcMain.handle('config:reset', () => {
        unlinkSync(cfg_file_path);
        // app.relaunch(); // systemd should handle it
        app.exit();
    })
    
    app.on('certificate-error', (event, webContents, url, error, certificate, callback) => {
        if (config.allowInsecureCerts || url.match('(?:http|ws)s?://localhost')) {
            event.preventDefault();
            callback(true);
        } else {
            callback(false);
        }
    });
});
