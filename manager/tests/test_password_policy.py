"""The password policy is always applied and matches the PAM-observed vectors in password_vectors.json."""
import json
from pathlib import Path

import pytest

from utils import passwords

VECTORS_FILE = Path(__file__).with_name('password_vectors.json')
DATA = json.loads(VECTORS_FILE.read_text(encoding='utf-8'))
VECTORS = DATA['vectors']


def expected_failed(vector) -> list[str]:
    """The vector's failed list, except the one deliberate difference from PAM: the manager refuses
    more than 128 characters (max_length) where PAM accepts them."""
    if len(vector['password']) > 128 and vector['ok']:
        return ['max_length']
    return vector['failed']


def test_the_vectors_file_is_the_shared_spec():
    assert len(VECTORS) >= 100
    assert DATA['policy']['min_length'] == passwords.POLICY['min_length']
    assert DATA['policy']['max_length'] == passwords.POLICY['max_length']
    assert DATA['policy']['min_classes'] == passwords.POLICY['min_classes']
    assert DATA['policy']['max_repeat'] == passwords.POLICY['max_repeat']
    assert DATA['policy']['username_min_len'] == passwords.POLICY['username_min_len']
    assert DATA['policy']['pam_max_bytes'] == passwords.POLICY['pam_max_bytes']
    assert DATA['policy']['forbidden_words'] == []


@pytest.mark.parametrize('vector', VECTORS, ids=[f'{i:03d}' for i in range(len(VECTORS))])
def test_failed_rule_matches_the_vector(vector):
    assert passwords.failed_ids(vector['password'], vector['username']) == expected_failed(vector)


@pytest.mark.parametrize('vector', VECTORS, ids=[f'{i:03d}' for i in range(len(VECTORS))])
def test_messages_are_empty_iff_the_password_is_accepted(vector):
    messages = passwords.validate_password(vector['password'], vector['username'])
    assert bool(messages) == bool(expected_failed(vector))
    assert len(messages) <= 1


def test_control_characters_are_refused_before_the_length_rule():
    assert passwords.failed_ids('Abc12345\n') == ['control']
    assert passwords.failed_ids('Abc\t1234') == ['control']
    assert passwords.failed_ids('a\x7f') == ['control']
    assert passwords.failed_ids('new\nline') == ['control']
    assert passwords.validate_password('new\nline') == [passwords._MESSAGES['control']]


def test_non_text_is_refused():
    for value in (None, 12345678, b'Abc12345', ['Abc12345']):
        assert passwords.failed_ids(value) == ['type']
        assert passwords.validate_password(value)


def test_lone_surrogates_do_not_raise():
    assert passwords.failed_ids('Ab1\ud800cdefg') == []
    assert passwords.failed_ids('\ud800\ud800') == ['min_length']


def test_the_username_rule_checks_the_username_and_its_reverse():
    assert passwords.failed_ids('xAlicex1', 'alice') == ['username']
    assert passwords.failed_ids('xEcilAx1', 'alice') == ['username']
    assert passwords.failed_ids('bob12345', 'bob') == []


def test_the_rules_spec_is_json_serialisable_and_complete():
    spec = passwords.rules_spec()
    text = json.dumps(spec)
    assert json.loads(text) == spec
    for key in ('min_length', 'max_length', 'min_classes', 'max_repeat', 'username_min_len',
                'pam_max_bytes', 'rules'):
        assert key in spec
    assert 'forbidden_words' not in spec
    assert [rule['id'] for rule in spec['rules']] == [
        'min_length', 'classes', 'repeat', 'username', 'max_length']
    assert all(isinstance(rule['text'], str) and rule['text'] for rule in spec['rules'])


def test_the_old_opt_in_split_is_gone():
    for name in ('BASIC_RULES', 'RULES', 'validate_basic', 'BASIC_MIN_LENGTH', 'MIN_LENGTH', 'MAX_LENGTH',
                 '_COMMON'):
        assert not hasattr(passwords, name), name


def test_usernames_are_those_of_useradd():
    assert not passwords.validate_username('admin')
    assert not passwords.validate_username('_svc-1')
    assert passwords.validate_username('Admin')
    assert passwords.validate_username('1abc')
    assert passwords.validate_username('-x')
    assert passwords.validate_username('a' * 33)
    assert passwords.validate_username('bob\n')
    assert not passwords.validate_username('bob')
