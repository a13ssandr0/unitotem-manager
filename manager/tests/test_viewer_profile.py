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
