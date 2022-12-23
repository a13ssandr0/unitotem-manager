import sys
from mimetypes import MimeTypes
from os.path import abspath, dirname, join
from threading import Thread

from dasbus.connection import SystemMessageBus
from dasbus.identifier import DBusServiceIdentifier
from dasbus.loop import EventLoop
from dasbus.server.interface import dbus_interface
from dasbus.typing import Bool, Dict, Double, Int, Str, Tuple
from dasbus.xml import XMLGenerator
from jinja2 import BaseLoader, Environment
from PIL import Image
from pulsectl import Pulse
from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtWebEngineWidgets import QWebEngineSettings, QWebEngineView
from PyQt5.QtWidgets import QApplication
from requests import get

BUS = DBusServiceIdentifier(
    namespace=("org", "unitotem", "viewer"),
    message_bus=SystemMessageBus()
)


_fs_template = Environment(loader=BaseLoader).from_string(r"""<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, minimum-scale=0.1">
    <style>
        body{margin: 0px; background: \#{{bg_color|default("000000", true)}}; height: 100%; overflow: hidden;}
        img {content:url("{{src}}");}
        img, video{
            display: block; position: absolute; height: 100%; width: 100%;
            margin: auto; top: 50%; left: 50%; transform: translate(-50%, -50%);
            object-fit: contain; user-select: none;
        }
    </style>
</head>
<body>
    {% if mime.startswith('image') %}<img>
    {% elif mime.startswith('video')%}<video autoplay><source src="{{src}}" type="{{mime}}"></video>
    {% endif %}
</body>
</html>""")


@dbus_interface(BUS.interface_name)
class UniTotemViewer(object):

    def __init__(self, window:QWebEngineView):
        self.window = window

    def Show(self, page: Str):
        if page.startswith('file:'):
            file = page.removeprefix('file://')
            mime = MimeTypes().guess_type(page)[0]
        else:
            remote_file = get(page, stream=True)
            mime = remote_file.headers['Content-Type'].split(';')[0]
            file = remote_file.raw
        
        if mime.startswith('image'):
            with Image.open(file) as img:
                bg_color = self.get_dominant_color(img).removeprefix('0x')
            self.window.setHtml(_fs_template.render(src=page, mime=mime, bg_color=bg_color))
        elif mime.startswith('video'):
            self.window.setHtml(_fs_template.render(src=page, mime=mime))
        else:
            self.window.load(QUrl(page))
    
    def get_dominant_color(self, pil_img, palette_size=16): # https://stackoverflow.com/a/61730849/9655651
        # Resize image to speed up processing
        img = pil_img.copy()
        img.thumbnail((100, 100))
        # Reduce colors (uses k-means internally)
        paletted = img.convert('P', palette=Image.ADAPTIVE, colors=palette_size)
        # Find the color that occurs most often
        palette = paletted.getpalette()
        color_counts = sorted(paletted.getcolors(), reverse=True)
        palette_index = color_counts[0][1]
        dominant_color = palette[palette_index*3:palette_index*3+3]
        return hex((dominant_color[0]<<16) + (dominant_color[1]<<8) + dominant_color[2])

    def GetDisplays(self) -> Dict[Str, Tuple[Int, Int, Int, Int]]:
        return {screen.name(): screen.geometry().getRect() for screen in app.screens()}

    def SetGeometry(self, x:Int, y:Int, w:Int, h:Int):
        self.window.setGeometry(x, y, w, h)

    def GetGeometry(self) -> Tuple[Int, Int, Int, Int]:
        return self.window.geometry().getRect()

    def GetAudioDevices(self) -> Dict[Str, Dict[Str, Str]]:
        with Pulse() as pulse:
            return {sink.name: {'description': sink.description, 'mute': str(bool(sink.mute)),
            'volume': str(sink.volume.value_flat)} for sink in pulse.sink_list()}

    def GetDefaultAudioDevice(self) -> Str:
        with Pulse() as pulse:
            return pulse.server_info().default_sink_name

    def SetDefaultAudioDevice(self, dev: Str):
        with Pulse() as pulse:
            pulse.default_set(pulse.get_sink_by_name(dev))

    def SetVolume(self, dev: Str, volume: Double):
        with Pulse() as pulse:
            pulse.volume_set_all_chans(
                pulse.get_sink_by_name(
                    dev or pulse.server_info().default_sink_name), volume)

    def SetMute(self, dev: Str, mute: Bool):
        with Pulse() as pulse:
            pulse.mute(
                pulse.get_sink_by_name(
                    dev or pulse.server_info().default_sink_name), mute)



if __name__ == "__main__":
    app = QApplication(sys.argv)
    web = QWebEngineView()


    web.load(QUrl('file://' + abspath(join(dirname(sys.argv[0]), 'templates', 'boot-screen.html'))))
    web.settings().setAttribute(QWebEngineSettings.PlaybackRequiresUserGesture, False)
    web.setWindowFlags(Qt.FramelessWindowHint)
    web.setGeometry(*app.screens()[1].geometry().getRect())
    web.show()

    def bus_listener():
        try:
            print(XMLGenerator.prettify_xml(UniTotemViewer.__dbus_xml__))
            BUS._message_bus.publish_object(BUS.object_path, UniTotemViewer(web))
            BUS._message_bus.register_service(BUS.service_name)
            loop = EventLoop()
            loop.run()
        finally:
            BUS._message_bus.disconnect()

    Thread(target=bus_listener, daemon=True).start()
    sys.exit(app.exec_())

    