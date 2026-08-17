from app.analytics.vwap import vwap_series


def test_vwap_of_uniform_price_equals_that_price():
    points = [(100.0, 10), (100.0, 20), (100.0, 5)]
    assert vwap_series(points) == [100.0, 100.0, 100.0]


def test_vwap_weights_by_volume():
    # Second point has 4x the volume and a higher price, so cumulative VWAP
    # should be pulled well above the simple average of (100, 200).
    points = [(100.0, 10), (200.0, 40)]
    result = vwap_series(points)
    assert result[0] == 100.0
    simple_average = 150.0
    assert result[1] > simple_average


def test_vwap_zero_volume_point_falls_back_to_price():
    points = [(100.0, 0), (150.0, 0)]
    assert vwap_series(points) == [100.0, 150.0]


def test_vwap_series_length_matches_input():
    points = [(float(i), i + 1) for i in range(50)]
    assert len(vwap_series(points)) == 50
