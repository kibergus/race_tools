import pytest
from race_tools import rfactor
from race_tools import track_data


def test_get_track_origin():
    # Test exact match
    origin = rfactor.get_track_origin('Buckmore Park')
    assert origin.lat == 51.343194
    lat, lon = origin.to_gps(0, 0)
    assert lat == pytest.approx(51.343194, abs=0.01)

    # Test normalization and partial match
    origin = rfactor.get_track_origin('buckmore')
    assert origin.lat == 51.343194

    # Test mixed case and underscores
    origin = rfactor.get_track_origin('BAYFORD_MEADOWS')
    assert origin.lat == 51.34

    # Test missing name
    with pytest.raises(ValueError, match='Track name is missing or empty.'):
        rfactor.get_track_origin(None)

    with pytest.raises(ValueError, match='Track name is missing or empty.'):
        rfactor.get_track_origin('')

    # Test unknown track
    with pytest.raises(ValueError, match="Unknown track: 'Unknown Track'"):
        rfactor.get_track_origin('Unknown Track')


def test_rfactor_to_gps_no_correction():
    # No rotation, no offset
    origin = rfactor.RFactorTrackOrigin(lat=50.0, lon=0.0, correction=track_data.Correction(0, 0, 0))

    # At origin
    lat, lon = rfactor.rfactor_to_gps(0, 0, origin)
    assert lat == pytest.approx(50.0)
    assert lon == pytest.approx(0.0)

    # Also test origin.to_gps() method
    lat_method, lon_method = origin.to_gps(0, 0)
    assert lat_method == lat
    assert lon_method == lon

    # Test positive Z (Forward/North)
    # 111132.92 meters per degree lat roughly at 50 deg
    lat, lon = rfactor.rfactor_to_gps(0, 111132.92, origin)
    # It won't be exactly 51.0 because of the higher order terms in the formula, but close
    assert lat > 50.9 and lat < 51.1
    assert lon == pytest.approx(0.0)


def test_rfactor_to_gps_with_offset():
    # Offset only
    origin = rfactor.RFactorTrackOrigin(
        lat=50.0, lon=0.0,
        correction=track_data.Correction(north_m=100, east_m=50, angle_deg=0)
    )

    # 0,0 in rFactor should be shifted by correction
    lat, lon = rfactor.rfactor_to_gps(0, 0, origin)

    # Re-calculate expected lat/lon using the same formula (roughly)
    # We can just check that rfactor_to_gps(0, 0) with offset is same as rfactor_to_gps(50, 100) without offset
    ref_origin = rfactor.RFactorTrackOrigin(lat=50.0, lon=0.0, correction=track_data.Correction(0, 0, 0))
    lat_off, lon_off = rfactor.rfactor_to_gps(50, 100, ref_origin)
    assert lat == lat_off
    assert lon == lon_off


def test_rfactor_to_gps_with_rotation():
    # 90 degree rotation (CCW)
    # x' = x*cos(90) - z*sin(90) = -z
    # z' = x*sin(90) + z*cos(90) = x
    origin = rfactor.RFactorTrackOrigin(lat=50.0, lon=0.0, correction=track_data.Correction(0, 0, 90))

    # rFactor (x=10, z=0) -> Rotated (x'=-0, z'=10)
    lat, lon = rfactor.rfactor_to_gps(10, 0, origin)

    # Expected: lat should increase (since z' = 10 is North), lon should be 0
    assert lat > 50.0
    assert lon == pytest.approx(0.0)

    # rFactor (x=0, z=10) -> Rotated (x'=-10, z'=0)
    lat, lon = rfactor.rfactor_to_gps(0, 10, origin)

    # Expected: lat should be 50.0, lon should decrease (since x' = -10 is West)
    assert lat == pytest.approx(50.0)
    assert lon < 0.0
