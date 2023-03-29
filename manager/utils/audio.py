from rpyc import classic as rpyc

def getAudioDevices() -> list[dict[str, str|bool|float]]:
    with rpyc.connect('localhost') as conn, conn.modules.pulsectl.Pulse() as pulse:
        default_dev = pulse.server_info().default_sink_name
        return [{'name': sink.name, 'description': sink.description, 'mute': bool(sink.mute),
            'volume': sink.volume.value_flat, 'default': sink.name==default_dev} for sink in pulse.sink_list()]

def getDefaultAudioDevice() -> str:
    with rpyc.connect('localhost') as conn, conn.modules.pulsectl.Pulse() as pulse:
        return pulse.server_info().default_sink_name

def setDefaultAudioDevice(dev: str):
    with rpyc.connect('localhost') as conn, conn.modules.pulsectl.Pulse() as pulse:
        pulse.default_set(pulse.get_sink_by_name(dev))

def setVolume(dev: str, volume: int):
    with rpyc.connect('localhost') as conn, conn.modules.pulsectl.Pulse() as pulse:
        pulse.volume_set_all_chans(pulse.get_sink_by_name(
            dev or pulse.server_info().default_sink_name), volume)

def setMute(dev: str, mute: bool):
    with rpyc.connect('localhost') as conn, conn.modules.pulsectl.Pulse() as pulse:
        pulse.mute(pulse.get_sink_by_name(
            dev or pulse.server_info().default_sink_name), mute)
