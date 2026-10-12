from pulsectl import Pulse


def get_audio_devices() -> list[dict[str, str | bool | float]]:
    with Pulse() as pulse:
        return [{'name': sink.name, 'description': sink.description, 'muted': bool(sink.mute),
                 'volume': sink.volume.value_flat} for sink in pulse.sink_list()]


def get_default_audio_device() -> str:
    with Pulse() as pulse:
        return pulse.server_info().default_sink_name


def set_default_audio_device(dev: str):
    with Pulse() as pulse:
        pulse.default_set(pulse.get_sink_by_name(dev))


def set_volume(dev: str | None, volume: float):
    with Pulse() as pulse:
        pulse.volume_set_all_chans(pulse.get_sink_by_name(
            dev or pulse.server_info().default_sink_name), volume)


def set_mute(dev: str | None, mute: bool):
    with Pulse() as pulse:
        pulse.mute(pulse.get_sink_by_name(
            dev or pulse.server_info().default_sink_name), mute)
