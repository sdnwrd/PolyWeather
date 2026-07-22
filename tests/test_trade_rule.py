"""New strategy trades ONLY high model-disagreement (>=MIN_DISAGREEMENT_SPREAD)
tails. _is_vetoed encodes the fire decision: True = do NOT trade."""

import main
from config import MIN_DISAGREEMENT_SPREAD


def test_min_disagreement_threshold_is_5():
    assert MIN_DISAGREEMENT_SPREAD == 5.0


def test_high_disagreement_is_traded():
    assert main._is_vetoed(5.0) is False
    assert main._is_vetoed(8.3) is False


def test_low_or_mid_disagreement_is_vetoed():
    assert main._is_vetoed(4.9) is True   # dead middle
    assert main._is_vetoed(3.0) is True
    assert main._is_vetoed(0.0) is True


def test_missing_spread_is_vetoed():
    # veto model unavailable -> cannot confirm disagreement -> never fire
    assert main._is_vetoed(None) is True
