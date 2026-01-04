const {app, BrowserWindow, screen, ipcMain} = require('electron');
const path = require('path');
const DBus = require('dbus');
const settings = require('electron-settings');

const containers = [null, 'web', 'image', 'video', 'audio'];
const media_fits = ['contain', 'cover', 'fill'];

let gpu_info_valid = false;
const windows = {
    0: BrowserWindow
};



app.setName("UniTotem");

function setDefaultSettings() {
    if (!settings.hasSync("allowInsecureCerts"))
        settings.setSync("allowInsecureCerts", false);

    if (!settings.hasSync("windows"))
        settings.setSync("windows", {});

    if (!settings.hasSync("windows[0].flip"))
        settings.setSync("windows[0].flip", 0);

    if (!settings.hasSync("windows[0].orientation"))
        settings.setSync("windows[0].orientation", 0);
}
console.log(settings.file());
console.log(settings.getSync());
setDefaultSettings();



// Create a new service, object and interface
const iface = DBus.registerService('session', 'io.github.a13ssandr0.unitotem')
    .createObject('/io/github/a13ssandr0/unitotem/WebView')
    .createInterface('io.github.a13ssandr0.unitotem.WebView');


iface.addMethod('Show', {
    in: [
        DBus.Define(String, "src"),
        {type: 'y', name: "container"},
        {type: 'y', name: "fit"},
        DBus.Define(String, "bg_color"),
    ]
}, async function (src, container, fit, bg_color, callback) {
    container = containers[container];
    fit = media_fits[fit];
    await windows[0].webContents.executeJavaScript(`show("${src}", "${container}", "${fit}", "${bg_color}")`);
    callback(null);
})

iface.addMethod('GetGPUFeatureStats', {out: {type: 'a{ss}'}},
    function (callback) {
        callback(null, gpu_info_valid?app.getGPUFeatureStatus():{})
    })

iface.addMethod('GetAllDisplays', {out: DBus.Define(Array, "displays")},
    function (callback) {
        callback(null, screen.getAllDisplays())
    })

iface.addProperty('Bounds', {
    type: {type: 'a{su}'},
    getter: async function (callback) {
        callback(null, await settings.get("windows[0].bounds"))
    },
    setter: async function (v, complete) {
        console.log(`Setting new bounds x:${v.x}, y:${v.y}, width:${v.width}, height:${v.height}`);
        windows[0].setBounds({x: v.x, y: v.y, width: v.width, height: v.height});
        await settings.set("windows[0].bounds", {x: v.x, y: v.y, width: v.width, height: v.height});
        complete();
    }
})

iface.addProperty('Orientation', {
    type: {type: 'y'},
    getter: async function (callback) {
        callback(null, await settings.get("windows[0].orientation"))
    },
    setter: async function (orientation, complete) {
        await windows[0].webContents.executeJavaScript(`setOrientation(${orientation})`);
        await settings.set("windows[0].orientation", orientation);
        complete();
    }
})

iface.addProperty('Flip', {
    type: {type: 'y'},
    getter: async function (callback) {
        callback(null, await settings.get("windows[0].flip"))
    },
    setter: async function (flip, complete) {
        await windows[0].webContents.executeJavaScript(`setFlip(${flip})`);
        await settings.set("windows[0].flip", flip);
        complete();
    }
})

// noinspection JSCheckFunctionSignatures
iface.addProperty('AllowInsecureCerts', {
    type: DBus.Define(Boolean),
    getter: async function (callback) {
        callback(null, await settings.get("allowInsecureCerts"))
    },
    setter: async function (allowInsecureCerts, complete) {
        await settings.set("allowInsecureCerts", allowInsecureCerts);
        complete();
    }
})

iface.addMethod('Reset', {}, async function (callback) {
    await settings.unset();
    // app.relaunch(); // systemd should handle it
    app.exit();
    callback(null);
})

iface.update();



app.whenReady().then(() => {
    const screens = screen.getAllDisplays();
    console.log(screens);

    if (!settings.hasSync("windows[0].bounds"))
        settings.setSync("windows[0].bounds", {
            x: screens[0].bounds.x,
            y: screens[0].bounds.y,
            width: screens[0].bounds.width,
            height: screens[0].bounds.height
        });


    windows[0] = new BrowserWindow({
        autoHideMenuBar: true,
        autoplayPolicy: 'no-user-gesture-required',
        backgroundColor: '#000000',
        frame: false,
        title: app.getName(),
        titleBarStyle: 'hidden',
        webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            webviewTag: true
        },
        x: settings.getSync("windows[0].bounds.x"),
        y: settings.getSync("windows[0].bounds.y"),
        width: settings.getSync("windows[0].bounds.width"),
        height: settings.getSync("windows[0].bounds.height")
    });
    windows[0].loadFile('boot-screen.html');
    // windows[0].loadURL('chrome://gpu')

    // TODO will be used later to allow multiple windows
    /*windows[1] = new BrowserWindow({
        autoHideMenuBar: true,
        autoplayPolicy:  'no-user-gesture-required',
        backgroundColor: '#000000',
        frame:           false,
        titleBarStyle:   'hidden',
        webPreferences:  {
            preload: path.join(__dirname, 'preload.js'),
            webviewTag: true
        },
        x: settings.getSync("windows[1].bounds.x"),
        y: settings.getSync("windows[1].bounds.y"),
        width: settings.getSync("windows[1].bounds.width"),
        height: settings.getSync("windows[1].bounds.height")
    });
    windows[1].loadFile('boot-screen.html');*/

    const session = windows[0].webContents.session;
    session.on('will-download', e => e.preventDefault());

    iface.update();


    ipcMain.handle('mainWindow:getOrientation', () => {
        return settings.getSync("windows[0].orientation")
    });
    ipcMain.handle('mainWindow:getFlip', () => {
        return settings.getSync("windows[0].flip")
    });

    app.on('certificate-error', (event, webContents, url, error, certificate, callback) => {
        if (settings.getSync("allowInsecureCerts") || url.match('(?:http|ws)s?://localhost')) {
            event.preventDefault();
            callback(true);
        } else {
            callback(false);
        }
    });
});

// Make the app a single instance app
// if (process.mas)
//     app.exit()

app.requestSingleInstanceLock()

app.on('second-instance', () => {
    if (windows[0]) {
        if (windows[0].isMinimized()) windows[0].restore()
        windows[0].focus()
    }
})

app.on("gpu-info-update", ()=>{gpu_info_valid=true;})
