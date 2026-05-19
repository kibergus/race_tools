import pytest
from race_tools.rfactor import (
    get_track_origin,
    rfactor_to_gps,
    TrackOrigin,
    Correction
)


def test_get_track_origin():
    # Test exact match
    origin = get_track_origin('Buckmore Park')
    assert origin.lat == 51.343194

    # Test normalization and partial match
    origin = get_track_origin('buckmore')
    assert origin.lat == 51.343194

    # Test mixed case and underscores
    origin = get_track_origin('BAYFORD_MEADOWS')
    assert origin.lat == 51.34

    # Test missing name
    with pytest.raises(ValueError, match='Track name is missing or empty.'):
        get_track_origin(None)

    with pytest.raises(ValueError, match='Track name is missing or empty.'):
        get_track_origin('')

    # Test unknown track
    with pytest.raises(ValueError, match="Unknown track: 'Unknown Track'"):
        get_track_origin('Unknown Track')


def test_rfactor_to_gps_no_correction():
    # No rotation, no offset
    origin = TrackOrigin(lat=50.0, lon=0.0, correction=Correction(0, 0, 0))

    # At origin
    lat, lon = rfactor_to_gps(0, 0, origin)
    assert lat == pytest.approx(50.0)
    assert lon == pytest.approx(0.0)

    # Test positive Z (Forward/North)
    # 111132.92 meters per degree lat roughly at 50 deg
    lat, lon = rfactor_to_gps(0, 111132.92, origin)
    # It won't be exactly 51.0 because of the higher order terms in the formula, but close
    assert lat > 50.9 and lat < 51.1
    assert lon == pytest.approx(0.0)


def test_rfactor_to_gps_with_offset():
    # Offset only
    origin = TrackOrigin(lat=50.0, lon=0.0, correction=Correction(north_m=100, east_m=50, angle_deg=0))

    # 0,0 in rFactor should be shifted by correction
    lat, lon = rfactor_to_gps(0, 0, origin)

    # Re-calculate expected lat/lon using the same formula (roughly)
    # We can just check that rfactor_to_gps(0, 0) with offset is same as rfactor_to_gps(50, 100) without offset
    lat_off, lon_off = rfactor_to_gps(50, 100, TrackOrigin(lat=50.0, lon=0.0, correction=Correction(0, 0, 0)))
    assert lat == lat_off
    assert lon == lon_off


def test_rfactor_to_gps_with_rotation():
    # 90 degree rotation (CCW)
    # x' = x*cos(90) - z*sin(90) = -z
    # z' = x*sin(90) + z*cos(90) = x
    origin = TrackOrigin(lat=50.0, lon=0.0, correction=Correction(0, 0, 90))

    # rFactor (x=10, z=0) -> Rotated (x'=-0, z'=10)
    lat, lon = rfactor_to_gps(10, 0, origin)

    # Expected: lat should increase (since z' = 10 is North), lon should be 0
    assert lat > 50.0
    assert lon == pytest.approx(0.0)

    # rFactor (x=0, z=10) -> Rotated (x'=-10, z'=0)
    lat, lon = rfactor_to_gps(0, 10, origin)

    # Expected: lat should be 50.0, lon should decrease (since x' = -10 is West)
    assert lat == pytest.approx(50.0)
    assert lon < 0.0
