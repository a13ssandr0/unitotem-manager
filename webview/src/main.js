const {app, BrowserWindow, screen, ipcMain} = require('electron');
const path = require('path');
const DBus = require('dbus');
const settings = require('electron-settings');
const {registerWindow, onAddWindow, onRemoveWindow} = require('./bus')
const {set} = require("electron-settings");

app.setName("UniTotem");

const windows = {
    0: BrowserWindow
};


ipcMain.handle('mainWindow:getOrientation', (e, window = 0) => {
    return settings.getSync(`windows[${window}].orientation`)
});
ipcMain.handle('mainWindow:getFlip', (e, window = 0) => {
    return settings.getSync(`windows[${window}].flip`)
});

function makeWindow(x, y, width, height) {
    const win = new BrowserWindow({
        autoHideMenuBar: true,
        autoplayPolicy: 'no-user-gesture-required',
        backgroundColor: '#000000',
        frame: false,
        title: app.getName(),
        titleBarStyle: 'hidden',
        webPreferences: {
            preload: path.join(__dirname, 'frontend', 'preload.js'),
            webviewTag: true
        },
        x, y, width, height,
    });
    win.webContents.session.on('will-download', e => e.preventDefault());
    win.loadFile(path.join(__dirname, 'frontend', 'boot-screen.html'));
    // win.loadURL('chrome://gpu')

    return win
}

app.whenReady().then(() => {
    const displays = screen.getAllDisplays();

    if (!settings.hasSync("windows"))
        settings.setSync("windows", {});

    let _windows = settings.getSync("windows");

    if (Object.keys(_windows).length === 0) {
        settings.setSync(`windows[0].bounds`, {
            x: displays[0].bounds.x,
            y: displays[0].bounds.y,
            width: displays[0].bounds.width,
            height: displays[0].bounds.height
        });
        _windows = settings.getSync("windows");
    }

    for (let id in _windows){
        if (!settings.hasSync(`windows[${id}].bounds`))
            settings.setSync(`windows[${id}].bounds`, {
                x: displays[0].bounds.x,
                y: displays[0].bounds.y,
                width: displays[0].bounds.width,
                height: displays[0].bounds.height
            });


        windows[id] = makeWindow(
            settings.getSync(`windows[${id}].bounds.x`),
            settings.getSync(`windows[${id}].bounds.y`),
            settings.getSync(`windows[${id}].bounds.width`),
            settings.getSync(`windows[${id}].bounds.height`));

        registerWindow(windows[id], id)
    }
});

onAddWindow(()=>{
    //find available id
    let id = 0;
    // noinspection JSCheckFunctionSignatures
    for (let i = 0; i <= Math.max(... Object.keys(windows), 0)+1; i++){
        if (!Object.hasOwn(windows, i)){
            id = i;
            break;
        }
    }

    const displays = screen.getAllDisplays();

    //make window
    if (!settings.hasSync("windows"))
        settings.setSync("windows", {});

    if (!settings.hasSync(`windows[${id}].bounds`))
        settings.setSync(`windows[${id}].bounds`, {
            x: displays[0].bounds.x,
            y: displays[0].bounds.y,
            width: displays[0].bounds.width,
            height: displays[0].bounds.height
        });


    windows[id] = makeWindow(
        settings.getSync(`windows[${id}].bounds.x`),
        settings.getSync(`windows[${id}].bounds.y`),
        settings.getSync(`windows[${id}].bounds.width`),
        settings.getSync(`windows[${id}].bounds.height`));

    //return window for registration
    return [windows[id], id];
})

onRemoveWindow((id)=>{if (Object.hasOwn(windows, id)){delete windows[id]}})

app.on('certificate-error', (event, webContents, url, error, certificate, callback) => {
    if (settings.getSync("allowInsecureCerts") || url.match('(?:http|ws)s?://localhost')) {
        event.preventDefault();
        callback(true);
    } else {
        callback(false);
    }
});

// Make the app a single instance app
app.requestSingleInstanceLock()

app.on('second-instance', () => {
    for (let win of windows) {
        if (win.isMinimized()) win.restore()
        win.focus()
    }
})

