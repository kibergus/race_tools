from race_tools.sanitize import (
    sanitize_filename,
    strip_race_prefix,
    humanize_track_name,
    parse_time,
    is_final_session,
    clean_slug
)


def test_sanitize_filename():
    assert sanitize_filename(None) == ''
    assert sanitize_filename('') == ''
    assert sanitize_filename('Hello World') == 'hello_world'
    assert sanitize_filename('Hello---World') == 'hello_world'
    assert sanitize_filename('Hello! @World#') == 'hello_world'
    assert sanitize_filename('___abc___') == 'abc'
    assert sanitize_filename('  Leading Trailing  ') == 'leading_trailing'


def test_strip_race_prefix():
    assert strip_race_prefix('Race 1: Final') == 'Final'
    assert strip_race_prefix('Race 10: Heat') == 'Heat'
    assert strip_race_prefix('Race 123: Pre-Final') == 'Pre-Final'
    assert strip_race_prefix('No Prefix') == 'No Prefix'
    assert strip_race_prefix('Race: No Number') == 'Race: No Number'


def test_humanize_track_name():
    assert humanize_track_name('bayford_meadows') == 'Bayford Meadows'
    assert humanize_track_name('buckmore_park_kart_circuit') == 'Buckmore Park'
    assert humanize_track_name('lydd_raceway') == 'Lydd'
    assert humanize_track_name('direct_drive_circuit_shenington') == 'Shenington'
    assert humanize_track_name('pfi') == 'Pfi'


def test_parse_time():
    assert parse_time(None) is None
    assert parse_time('1:05.123') == 65.123
    assert parse_time('0:45.5') == 45.5
    assert parse_time('45.5') == 45.5
    assert parse_time(10.2) == 10.2
    assert parse_time('-') is None
    assert parse_time('') is None
    assert parse_time('invalid') is None


def test_is_final_session():
    assert is_final_session('Final') is True
    assert is_final_session('A-Final') is True
    assert is_final_session('Heat 1') is True
    assert is_final_session('Grand Final') is True
    assert is_final_session('Practice') is False
    assert is_final_session('Qualifying') is False


def test_clean_slug():
    assert clean_slug('Hello World!') == 'hello_world'
    assert clean_slug('  ABC   ') == 'abc'
    assert clean_slug('test@123.com') == 'test_123_com'
    assert clean_slug('___already_clean___') == 'already_clean'
