const { app, BrowserWindow, screen, ipcMain} = require('electron');
const path = require('path');
const {readFileSync, writeFileSync, unlinkSync} = require('fs');
const {homedir} = require("os");
const DBus = require('dbus');

const cfg_file_path = path.join(homedir(), '.unitotem-viewer.conf');



// Create a new service, object and interface
const iface = DBus.registerService('session', 'unitotem.WebView')
					.createObject('/unitotem/WebView')
					.createInterface('unitotem.WebView');


const containers = [null, 'web', 'image', 'video', 'audio'];
const media_fits = ['contain', 'cover', 'fill'];


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
    let file = {};
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

    // TODO will be used later to allow multiple windows
    /*const mainWindow2 = new BrowserWindow({
        autoHideMenuBar: true,
        autoplayPolicy:  'no-user-gesture-required',
        backgroundColor: '#000000',
        frame:           false,
        titleBarStyle:   'hidden',
        webPreferences:  {
            preload: path.join(__dirname, 'preload.js'),
            webviewTag: true
        },
        x:               config.bounds.x+1080,
        y:               config.bounds.y,
        width:           config.bounds.width,
        height:          config.bounds.height
    });
    mainWindow2.loadFile('boot-screen.html');*/

	const session = mainWindow.webContents.session;
	session.on('will-download', e => e.preventDefault());

	iface.addMethod('Show', {
		in: [
			DBus.Define(String,"src"),
			{type: 'y', name: "container"},
			{type: 'y', name: "fit"},
			DBus.Define(String,"bg_color"),
		]
	}, function (src, container, fit, bg_color, callback) {
		container = containers[container];
		fit = media_fits[fit];
		mainWindow.webContents.executeJavaScript(`show("${src}", "${container}", "${fit}", "${bg_color}")`);
		callback(null);
	})

	iface.addMethod('GetAllDisplays', {out: DBus.Define(Array, "displays")},
		function (callback) {callback(null, screen.getAllDisplays())})

	iface.addProperty('bounds', {
		type: {type: 'a{si}'},
		getter: function (callback) {callback(null, config.bounds)},
		setter: function (v, complete) {
            console.log(`Setting new bounds x:${v.x}, y:${v.y}, width:${v.width}, height:${v.height}`);
        	config.bounds = {x:v.x, y:v.y, width:v.width, height:v.height};
			mainWindow.setBounds(config.bounds);
        	writeFileSync(cfg_file_path, JSON.stringify(config));
			complete();
		}
	})

	iface.addProperty('orientation', {
		type: {type: 'i'},
		getter: function (callback) {callback(null, config.orientation)},
		setter: function (orientation, complete) {
			config.orientation = orientation;
			mainWindow.webContents.executeJavaScript(`setOrientation(${orientation})`);
        	writeFileSync(cfg_file_path, JSON.stringify(config));
			complete();
		}
	})

	iface.addProperty('flip', {
		type: {type: 'i'},
		getter: function (callback) {callback(null, config.flip)},
		setter: function (flip, complete) {
			config.flip = flip;
			mainWindow.webContents.executeJavaScript(`setFlip(${flip})`);
        	writeFileSync(cfg_file_path, JSON.stringify(config));
			complete();
		}
	})

	// noinspection JSCheckFunctionSignatures
	iface.addProperty('allowInsecureCerts', {
		type: DBus.Define(Boolean),
		getter: function (callback) {callback(null, config.allowInsecureCerts)},
		setter: function (allowInsecureCerts, complete) {
			config.allowInsecureCerts = allowInsecureCerts;
        	writeFileSync(cfg_file_path, JSON.stringify(config));
			complete();
		}
	})

	iface.addMethod('Reset', {}, function (callback) {
		unlinkSync(cfg_file_path);
        // app.relaunch(); // systemd should handle it
        app.exit();
		callback(null);
	})

	iface.update();


    ipcMain.handle('mainWindow:getOrientation', () => {return config.orientation});
    ipcMain.handle('mainWindow:getFlip', () => {return config.flip});

    app.on('certificate-error', (event, webContents, url, error, certificate, callback) => {
        if (config.allowInsecureCerts || url.match('(?:http|ws)s?://localhost')) {
            event.preventDefault();
            callback(true);
        } else {
            callback(false);
        }
    });
});
