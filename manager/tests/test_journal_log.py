"""Log lines carry sd-daemon priorities under journald, so MaxLevelStore=warning keeps the manager's warnings and errors."""
import io

from loguru import logger

from utils import _logging


def _emit(level, text):
    out = io.StringIO()
    sink = logger.add(lambda m: _logging.journal_sink(m, out), format='{message}\n{exception}', level='TRACE', colorize=False)
    try:
        logger.log(level, text)
    finally:
        logger.remove(sink)
    return out.getvalue()


def test_each_level_has_its_priority():
    assert _emit('WARNING', 'w').startswith('<4>w')
    assert _emit('ERROR', 'e').startswith('<3>e')
    assert _emit('INFO', 'i').startswith('<6>i')
    assert _emit('DEBUG', 'd').startswith('<7>d')


def test_every_line_of_a_multi_line_record_is_prefixed():
    assert _emit('ERROR', 'a\nb') == '<3>a\n<3>b\n'


def test_the_sink_is_installed_only_under_journald():
    assert _logging.install_journal_sink({}) is False
