"""The browser profile: where it is and what a power cut leaves in it."""
import os

from unitotem_viewer import app


def test_stale_process_locks_are_removed_and_the_rest_of_the_profile_is_kept(tmp_path):
    os.symlink('unitotem-12345', tmp_path / 'SingletonLock')      # a dangling link, as Chromium makes it
    (tmp_path / 'SingletonCookie').write_text('x')
    (tmp_path / 'Cookies').write_text('keep')
    app.clear_stale_locks(str(tmp_path))
    assert sorted(p.name for p in tmp_path.iterdir()) == ['Cookies']
    app.clear_stale_locks(str(tmp_path))                          # nothing to remove: no error


def test_profile_is_on_the_data_side_and_the_cache_in_ram():
    assert app.BROWSER_PROFILE.startswith('/var/lib/unitotem/') and app.BROWSER_CACHE.startswith('/var/cache/')


def test_ram_caches_become_links_into_the_cache_and_real_directories_are_replaced(tmp_path):
    profile, cache = tmp_path / 'profile', tmp_path / 'cache'
    (profile / 'Default' / 'GPUCache').mkdir(parents=True)
    (profile / 'Default' / 'GPUCache' / 'data_1').write_text('old')
    (profile / 'GrShaderCache').mkdir()
    (profile / 'Cookies').write_text('keep')
    (profile / 'BrowserMetrics-spare.pma').write_text('x')
    paths = ('BrowserMetrics', 'BrowserMetrics-spare.pma', 'GrShaderCache', 'Default/GPUCache')
    app.link_ram_caches(str(profile), str(cache), paths)
    for rel in paths:
        link = profile / rel
        assert link.is_symlink() and os.readlink(link) == str(cache / 'profile' / rel)
    assert (cache / 'profile' / 'Default' / 'GPUCache').is_dir() and (cache / 'profile' / 'BrowserMetrics').is_dir()
    assert (cache / 'profile').is_dir() and not (cache / 'profile' / 'BrowserMetrics-spare.pma').exists()   # the browser creates the file
    assert (profile / 'Cookies').read_text() == 'keep'
    app.link_ram_caches(str(profile), str(cache), paths)       # second start: unchanged
    assert all((profile / rel).is_symlink() for rel in paths)


def test_ram_caches_are_recreated_after_the_cache_was_emptied_by_a_reboot(tmp_path):
    profile, cache = tmp_path / 'profile', tmp_path / 'cache'
    profile.mkdir()
    app.link_ram_caches(str(profile), str(cache), ('ShaderCache',))
    shutil_rmtree = __import__('shutil').rmtree
    shutil_rmtree(cache)                                         # tmpfs is empty after a boot: the link dangles
    app.link_ram_caches(str(profile), str(cache), ('ShaderCache',))
    assert (profile / 'ShaderCache').is_dir()                    # resolves again


def test_the_browser_log_is_errors_only_and_in_the_ram_cache():
    src = open(app.__file__).read()
    assert "'log_severity'                 : cef.LOGSEVERITY_ERROR" in src
    assert "os.path.join(BROWSER_CACHE, 'chrome_debug.log')" in src
    assert 'link_ram_caches(cache_path, BROWSER_CACHE)' in src
    for rel in ('BrowserMetrics', 'GrShaderCache', 'Default/GPUCache'):
        assert rel in app.RAM_PROFILE_PATHS


def test_metrics_files_left_in_ram_by_earlier_runs_are_removed_at_start(tmp_path):
    profile, cache = tmp_path / 'profile', tmp_path / 'cache'
    profile.mkdir()
    old = cache / 'profile' / 'BrowserMetrics'
    old.mkdir(parents=True)
    (old / 'BrowserMetrics-1.pma').write_text('x')
    (cache / 'profile' / 'ShaderCache').mkdir()
    (cache / 'profile' / 'ShaderCache' / 'data').write_text('kept')
    app.link_ram_caches(str(profile), str(cache), ('BrowserMetrics', 'ShaderCache'))
    assert list(old.iterdir()) == []
    assert (cache / 'profile' / 'ShaderCache' / 'data').read_text() == 'kept'
