"""Guard: the test session never points the user store at the real /etc/unitotem."""
from utils.models.command_line import cmdargs


def test_users_file_is_redirected_to_tmp(tmp_path):
    assert cmdargs.users_file.parent == tmp_path
    assert not str(cmdargs.users_file).startswith('/etc/unitotem')
