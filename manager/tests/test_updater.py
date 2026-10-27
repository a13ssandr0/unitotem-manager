from unitotem_node.system.updater import parse_output

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


def test_record_only_changes_on_a_real_difference_and_ignores_failures():
    from unitotem_node.system import updater
    updater.availability.update(available=False, version=None, checked_at=None)
    assert updater.record({'supported': True, 'available': True, 'version': '2026.10.7'}) is True
    assert updater.record({'supported': True, 'available': True, 'version': '2026.10.7'}) is False
    # a failed check (no network) must not clear the hint
    assert updater.record({'supported': True, 'error': 'network'}) is False
    assert updater.availability['available'] is True
    assert updater.record({'supported': False}) is False
    assert updater.record({'supported': True, 'available': False}) is True


def test_slot_names_are_always_capitals():
    from unitotem_node.system.updater import upper_slots
    data = parse_output(0, '{"slot":"a","other_slot":"b","last_result":{"op":"repair","slot":"b"},"armed":{"slot":"b"},'
                           '"default_slot":null,"version":"a"}')
    assert (data['slot'], data['other_slot'], data['default_slot']) == ('A', 'B', None)
    assert data['last_result']['slot'] == 'B' and data['armed']['slot'] == 'B'
    assert data['version'] == 'a'          # only slot names, nothing else that happens to be one letter
    assert upper_slots([{'slot': 'a'}]) == [{'slot': 'A'}]


def test_progress_watcher_helpers():
    import struct
    from unitotem_node.system.update_progress import describe, names_in
    assert describe('repair', 'write', 'B') == ('Rebuilding slot B', 'Writing')
    assert describe('fetch', 'download', None)[0] == 'Downloading the update'
    name = b'unitotem-update.progress\0\0\0\0\0\0\0\0'
    event = struct.pack('iIII', 1, 0x80, 0, len(name)) + name
    assert names_in(event + event) == ['unitotem-update.progress'] * 2
