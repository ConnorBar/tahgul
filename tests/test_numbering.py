from datetime import date

import pytest

from tahgul import numbering

D = date(2026, 10, 20)


@pytest.mark.parametrize(
    "pattern,seq,code,expected",
    [
        ("{Y}-{seq:3}", 7, None, "2026-007"),
        ("INV-{seq:4}", 42, None, "INV-0042"),
        ("{Y}{M}-{seq:3}", 1, None, "202610-001"),
        ("{CLIENT}-{Y}-{seq:3}", 3, "acme", "ACME-2026-003"),
        ("{y}/{seq}", 1234, None, "26/1234"),
        ("{Y}-{seq:3}", 1000, None, "2026-1000"),  # padding is a minimum, not a cap
    ],
)
def test_format(pattern, seq, code, expected):
    assert numbering.format_number(pattern, on=D, seq=seq, client_code=code) == expected


def test_presets_are_valid():
    for p in numbering.PRESETS:
        assert numbering.validate_pattern(p.pattern) == []


@pytest.mark.parametrize(
    "pattern",
    [
        "",
        "   ",
        "INV-001",
        "{seq}-{seq}",
        "{Y}-{SEQ}",
        "{Y}-{seq:0}",
        "{Q}-{seq}",
        "a{b}{seq}",
        "x" * 60 + "{seq}",
        "{Y}<script>{seq}",
    ],
)
def test_invalid_patterns(pattern):
    assert numbering.validate_pattern(pattern)
    with pytest.raises(ValueError):
        numbering.format_number(pattern, on=D, seq=1)


def test_sequence_resets():
    k = numbering.sequence_key
    # Yearly patterns reset each year, not each month.
    assert k("{Y}-{seq:3}", on=date(2026, 1, 5)) == k("{Y}-{seq:3}", on=date(2026, 12, 5))
    assert k("{Y}-{seq:3}", on=date(2026, 12, 5)) != k("{Y}-{seq:3}", on=date(2027, 1, 5))
    # Monthly.
    assert k("{Y}{M}-{seq}", on=date(2026, 1, 5)) != k("{Y}{M}-{seq}", on=date(2026, 2, 5))
    # Running number never resets.
    assert k("INV-{seq:4}", on=date(2026, 1, 1)) == k("INV-{seq:4}", on=date(2030, 1, 1))
    # Per client.
    assert k("{CLIENT}-{Y}-{seq}", on=D, client_id="a") != k(
        "{CLIENT}-{Y}-{seq}", on=D, client_id="b"
    )
