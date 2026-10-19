from alert_service.domain.signal import compute_signal_key, round_frequency


def test_frequencies_within_tolerance_share_a_key():
    assert compute_signal_key("r1", 2437.0, 0.1) == compute_signal_key("r1", 2437.04, 0.1)


def test_frequencies_beyond_tolerance_get_different_keys():
    assert compute_signal_key("r1", 2437.0, 0.1) != compute_signal_key("r1", 2437.2, 0.1)


def test_different_rules_get_different_keys():
    assert compute_signal_key("r1", 2437.0, 0.1) != compute_signal_key("r2", 2437.0, 0.1)


def test_round_frequency_has_no_float_noise():
    assert round_frequency(2437.04, 0.1) == 2437.0
    assert round_frequency(2437.06, 0.1) == 2437.1
