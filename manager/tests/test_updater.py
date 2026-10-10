from utils.system.updater import parse_output

STATUS = ('{"layout":1,"ab":false,"current_version":"0","slot":null,"other_slot":null,"trial":false,'
          '"pending_confirm":false,"rollback_available":false,"op":null,"last_result":null,"base_url":"x"}')
CHECK = ('{"available":true,"current_version":"0","version":"2026.10.1","channel":"stable","arch":"amd64",'
         '"layout":1,"size":1049135105,"needs_layout_migration":false}')


def test_absent_updater_means_unsupported():
    assert parse_output(127, '') == {'supported': False}


def test_status_is_passed_through():
    data = parse_output(0, STATUS)
    assert data['supported'] and data['ab'] is False and data['layout'] == 1


def test_check_available_and_up_to_date_are_both_ok():
    assert parse_output(0, CHECK)['available'] is True
    assert parse_output(10, CHECK.replace('true', 'false', 1))['available'] is False


def test_failure_keeps_error_code():
    data = parse_output(2, '{"error":"bad","error_code":"bad-manifest-signature"}')
    assert data['error_code'] == 'bad-manifest-signature'


def test_garbage_output_is_reported_not_raised():
    assert parse_output(0, 'not json')['error_code'] == 'bad-output'
    assert parse_output(2, '')['error_code'] == 'failed'
