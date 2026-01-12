import {app, screen} from 'electron';
import DBus from 'dbus';
import settings from 'electron-settings';


// Create a new service, object and interface
const bus = DBus.registerService('session', 'io.github.a13ssandr0.unitotem')
const bus_object = bus.createObject('/io/github/a13ssandr0/unitotem/WebView')
const app_interface = bus_object.createInterface('io.github.a13ssandr0.unitotem.WebView');

let on_add_window = null;
let on_remove_window = null;

let gpu_info_valid = false;
app.on("gpu-info-update", () => {gpu_info_valid = true})

app_interface.addMethod('GetGPUFeatureStats', {out: {type: 'a{ss}'}},
    function (callback) {
        callback(null, gpu_info_valid ? app.getGPUFeatureStatus() : {})
    })


app_interface.addMethod('GetAllDisplays', {out: DBus.Define(Array, "displays")},
    function (callback) {
        callback(null, screen.getAllDisplays())
    })

app_interface.addMethod('AddWindow', {out: {type: 'i', name: 'id'}}, function (callback) {
    if (on_add_window !== null){
        let [window, id] = on_add_window();
        registerWindow(window, id);
        callback(null, id);
    } else {
        callback(null, -1);
    }
})


if (!settings.hasSync("allowInsecureCerts"))
    settings.setSync("allowInsecureCerts", false);

// noinspection JSCheckFunctionSignatures
app_interface.addProperty('AllowInsecureCerts', {
    type: DBus.Define(Boolean),
    getter: async function (callback) {
        callback(null, await settings.get("allowInsecureCerts"))
    },
    setter: async function (allowInsecureCerts, complete) {
        await settings.set("allowInsecureCerts", allowInsecureCerts);
        complete();
    }
})


app_interface.addMethod('Reset', {}, async function (callback) {
    await settings.unset();
    // app.relaunch(); // systemd should handle it
    app.exit();
    callback(null);
})

app_interface.update();


const containers = [null, 'web', 'image', 'video', 'audio'];
const media_fits = ['contain', 'cover', 'fill'];

export function registerWindow(window, id){
    const win_obj = bus.createObject(`/io/github/a13ssandr0/unitotem/WebView/Window/${id}`)
    const win_iface = win_obj.createInterface('io.github.a13ssandr0.unitotem.WebView.Window');

    if (!settings.hasSync("windows"))
        settings.setSync("windows", {});

    if (!settings.hasSync(`windows[${id}].flip`))
        settings.setSync(`windows[${id}].flip`, 0);

    if (!settings.hasSync(`windows[${id}].orientation`))
        settings.setSync(`windows[${id}].orientation`, 0);

    win_iface.addMethod('Destroy', {}, (callback)=>{
        window.destroy();
        bus.removeObject(win_obj);
        settings.unsetSync(`windows[${id}]`);
        if (on_remove_window !== null)
            on_remove_window(id)
        callback(null);
    })

    win_iface.addMethod('Show', {
        in: [
            DBus.Define(String, "src"),
            {type: 'y', name: "container"},
            {type: 'y', name: "fit"},
            DBus.Define(String, "bg_color"),
        ],
        out: {type: 'b', name: 'success'}
    }, async function (src, container, fit, bg_color, callback) {
        container = containers[container];
        fit = media_fits[fit];
        await window.webContents.executeJavaScript(`show("${src}", "${container}", "${fit}", "${bg_color}")`);
        callback(null, true);
    })

    win_iface.addProperty('Bounds', {
        type: {type: 'a{su}'},
        getter: async function (callback) {
            callback(null, await settings.get(`windows[${id}].bounds`))
        },
        setter: async function (v, complete) {
            window.setBounds({x: v.x, y: v.y, width: v.width, height: v.height});
            await settings.set(`windows[${id}].bounds`, {x: v.x, y: v.y, width: v.width, height: v.height});
            complete();
        }
    })

    win_iface.addProperty('Orientation', {
        type: {type: 'y'},
        getter: async function (callback) {
            callback(null, await settings.get(`windows[${id}].orientation`))
        },
        setter: async function (orientation, complete) {
            await window.webContents.executeJavaScript(`setOrientation(${orientation})`);
            await settings.set(`windows[${id}].orientation`, orientation);
            complete();
        }
    })

    win_iface.addProperty('Flip', {
        type: {type: 'y'},
        getter: async function (callback) {
            callback(null, await settings.get(`windows[${id}].flip`))
        },
        setter: async function (flip, complete) {
            await window.webContents.executeJavaScript(`setFlip(${flip})`);
            await settings.set(`windows[${id}].flip`, flip);
            complete();
        }
    })

    win_iface.update();

    return win_obj;
}

// export function unregisterWindow(id){
//     bus.removeObject(`/io/github/a13ssandr0/unitotem/WebView/Window/${id}`);
//     settings.unsetSync(`windows[${id}]`);
// }

export function onAddWindow(callback){on_add_window = callback;}
export function onRemoveWindow(callback){on_remove_window = callback;}