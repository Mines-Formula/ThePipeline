from csv_to_influxdb.line_protocol import esc_measure, esc_tag


def test_esc_measure_commas():
    assert esc_measure("a,b") == r"a\,b"


def test_esc_measure_spaces():
    assert esc_measure("wheel speed") == r"wheel\ speed"


def test_esc_measure_equals():
    assert esc_measure("a=b") == r"a\=b"


def test_esc_measure_multiple_special_chars():
    assert esc_measure("a,b=c d") == r"a\,b\=c\ d"


def test_esc_measure_no_special_chars():
    assert esc_measure("RPM") == "RPM"


def test_esc_tag_matches_esc_measure():
    for s in ["a,b", "wheel speed", "a=b", "clean"]:
        assert esc_tag(s) == esc_measure(s)
