"""Grading semantics: Polymarket "be N°C" brackets are half-open [N, N+1).

The bug this guards against: brackets were built as [c_to_f(N), c_to_f(N+1)]
and graded with `low <= v <= high` (inclusive BOTH ends). Adjacent brackets
share their boundary (34°C high == 35°C low == 95.0°F), so a value exactly on
the boundary won BOTH brackets — inflating wins and producing impossible
two-winner city-days. The high endpoint must be EXCLUSIVE so a boundary value
belongs only to the upper (correct) bracket.
"""

from journal import _bracket_contains


def test_boundary_value_belongs_to_upper_bracket_only():
    # 34°C bracket = [93.2, 95.0); 35°C bracket = [95.0, 96.8)
    # Observed 95.0°F == exactly 35.0°C -> Polymarket resolves 35°C.
    assert _bracket_contains(93.2, 95.0, 95.0) is False   # 34°C must LOSE
    assert _bracket_contains(95.0, 96.8, 95.0) is True    # 35°C must WIN


def test_low_endpoint_is_inclusive():
    # 34°C bracket includes its low edge (93.2°F == 34.0°C).
    assert _bracket_contains(93.2, 95.0, 93.2) is True


def test_value_strictly_inside_wins():
    assert _bracket_contains(93.2, 95.0, 94.1) is True


def test_value_below_bracket_loses():
    assert _bracket_contains(93.2, 95.0, 93.1) is False


def test_july17_double_win_regression():
    # actual_high 78.8°F == 26.0°C. Only the 26°C bracket may win.
    assert _bracket_contains(77.0, 78.8, 78.8) is False   # 25°C must LOSE
    assert _bracket_contains(78.8, 80.6, 78.8) is True    # 26°C must WIN
