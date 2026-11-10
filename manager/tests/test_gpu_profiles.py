from utils.system import gpu


def _detect(monkeypatch, drm, va=(), v4l2=()):
    monkeypatch.setattr(gpu, '_read_drm_drivers', lambda: list(drm))
    monkeypatch.setattr(gpu, '_installed_va_drivers', lambda: list(va))
    monkeypatch.setattr(gpu, '_v4l2_decoders', lambda: list(v4l2))
    return gpu.detect_gpu()


def test_raspberry_pi_gets_angle_on_gles(monkeypatch):
    profile = _detect(monkeypatch, ['vc4-drm', 'v3d'], v4l2=['/dev/video10 (bcm2835-codec-decode)'])
    assert profile.name == 'pi'
    assert profile.switches['use-gl'] == 'angle'
    assert profile.switches['use-angle'] == 'gles'
    assert 'ignore-gpu-blocklist' in profile.switches
    # MSAA off: halves the WebGL frame rate on the V3D; `implicit` would make every getContext() return null
    assert profile.switches['webgl-antialiasing-mode'] == 'none'
    assert profile.as_dict()['expects_compositing'] is True


def test_other_v4l2_board_keeps_the_v4l2_profile(monkeypatch):
    profile = _detect(monkeypatch, ['rockchip'], v4l2=['/dev/video1 (rkvdec)'])
    assert profile.name == 'v4l2'
    assert 'use-gl' not in profile.switches


def test_software_and_unknown_expect_nothing(monkeypatch):
    assert not _detect(monkeypatch, ['virtio-pci']).as_dict()['expects_compositing']
    assert not _detect(monkeypatch, []).as_dict()['expects_compositing']


def test_raspberry_pi_3_gets_software_profile(monkeypatch, tmp_path):
    model = tmp_path / 'model'
    model.write_bytes(b'Raspberry Pi 3 Model B Rev 1.2\0')
    monkeypatch.setattr(gpu, '_DEVICE_TREE_MODEL', str(model))
    profile = _detect(monkeypatch, ['vc4-drm'])
    assert profile.name == 'pi3'
    assert profile.switches == {'disable-gpu': ''}
    assert 'use-gl' not in profile.switches
    assert profile.as_dict()['expects_compositing'] is False


def test_raspberry_pi_4_keeps_the_pi_profile_whatever_the_model_file(monkeypatch, tmp_path):
    model = tmp_path / 'model'
    model.write_bytes(b'Raspberry Pi 4 Model B Rev 1.5\0')
    monkeypatch.setattr(gpu, '_DEVICE_TREE_MODEL', str(model))
    assert _detect(monkeypatch, ['vc4-drm', 'v3d']).name == 'pi'
    # vc4 alone on a non-Pi-3 board (or without a device tree) is not the Pi 3 profile
    assert _detect(monkeypatch, ['vc4-drm']).name == 'pi'
    monkeypatch.setattr(gpu, '_DEVICE_TREE_MODEL', str(tmp_path / 'absent'))
    assert _detect(monkeypatch, ['vc4-drm']).name == 'pi'
