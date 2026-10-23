import pytest
from race_tools import assetto_corsa
from race_tools import track_data


def test_get_track_origin():
    # Known track origin
    origin = assetto_corsa.get_track_origin('monza')
    assert origin.lat == pytest.approx(45.613890)
    assert origin.lon == pytest.approx(9.286240)
    assert origin.correction.north_m == pytest.approx(820.0)
    assert origin.correction.east_m == pytest.approx(-30.0)
    lat, lon = origin.to_gps(0, 0)
    assert lat > 45.0

    # Imola
    origin_imola = assetto_corsa.get_track_origin('Imola')
    assert origin_imola.lat == pytest.approx(44.3412)
    assert origin_imola.correction == track_data.Correction()

    # Missing track name
    with pytest.raises(ValueError, match='Track name is missing or empty.'):
        assetto_corsa.get_track_origin(None)

    with pytest.raises(ValueError, match='Track name is missing or empty.'):
        assetto_corsa.get_track_origin('')

    # Unknown track
    with pytest.raises(ValueError, match="Unknown track: 'NonExistent'"):
        assetto_corsa.get_track_origin('NonExistent')


def test_assetto_corsa_to_gps():
    origin = assetto_corsa.AssettoCorsaTrackOrigin(
        lat=45.0, lon=10.0,
        correction=track_data.Correction(north_m=0.0, east_m=0.0, angle_deg=0.0)
    )

    # At Cartesian origin (0, 0)
    lat, lon = assetto_corsa.assetto_corsa_to_gps(0, 0, origin)
    assert lat == pytest.approx(45.0)
    assert lon == pytest.approx(10.0)

    # Method origin.to_gps
    lat_m, lon_m = origin.to_gps(0, 0)
    assert lat_m == lat
    assert lon_m == lon

    # Negative Z (North) in ACE
    lat_z, lon_z = origin.to_gps(0, -1000)
    assert lat_z > 45.0
    assert lon_z == pytest.approx(10.0)

    # Positive X (East) shift
    lat_x, lon_x = origin.to_gps(1000, 0)
    assert lat_x == pytest.approx(45.0)
    assert lon_x > 10.0


def test_assetto_corsa_to_gps_with_correction():
    origin = assetto_corsa.AssettoCorsaTrackOrigin(
        lat=45.0, lon=10.0,
        correction=track_data.Correction(north_m=100.0, east_m=50.0, angle_deg=0.0)
    )
    lat, lon = origin.to_gps(0, 0)

    ref = assetto_corsa.AssettoCorsaTrackOrigin(
        lat=45.0, lon=10.0,
        correction=track_data.Correction(0, 0, 0)
    )
    lat_ref, lon_ref = ref.to_gps(50.0, -100.0)
    assert lat == lat_ref
    assert lon == lon_ref
