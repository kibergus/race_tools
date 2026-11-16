import math
import pytest
from race_tools import assetto_corsa
from race_tools import rfactor
from race_tools import track_data


def test_correction_defaults():
    corr = track_data.Correction()
    assert corr.north_m == 0.0
    assert corr.east_m == 0.0
    assert corr.angle_deg == 0.0


def test_correction_custom():
    corr = track_data.Correction(north_m=10.5, east_m=-5.0, angle_deg=45.0)
    assert corr.north_m == 10.5
    assert corr.east_m == -5.0
    assert corr.angle_deg == 45.0


def test_track_origin_is_abstract():
    with pytest.raises(TypeError):
        track_data.TrackOrigin(lat=51.0, lon=0.0)


def test_track_origin_subclass():
    class DummyOrigin(track_data.TrackOrigin):
        def to_gps(self, x: float, z: float) -> tuple[float, float]:
            return self.lat + z, self.lon + x

    origin = DummyOrigin(lat=50.0, lon=1.0)
    assert origin.lat == 50.0
    assert origin.lon == 1.0
    assert origin.correction == track_data.Correction()
    assert origin.heading == 0.0
    lat, lon = origin.to_gps(2.0, 3.0)
    assert lat == 53.0
    assert lon == 3.0


def test_wgs84_meters_per_degree():
    m_lat, m_lon = track_data.wgs84_meters_per_degree(0.0)
    # At equator: 1 deg lat is ~110574m, 1 deg lon is ~111319m
    assert 110000 < m_lat < 111000
    assert 111000 < m_lon < 112000

    m_lat_pole, m_lon_pole = track_data.wgs84_meters_per_degree(90.0)
    # At poles, longitude degree length approaches 0
    assert m_lon_pole == pytest.approx(0.0, abs=1.0)
    assert m_lat_pole > 111000


def test_find_track_origin():
    class DummyOrigin(track_data.TrackOrigin):
        def to_gps(self, x: float, z: float) -> tuple[float, float]:
            return self.lat, self.lon

    origins = {
        'Silverstone GP': DummyOrigin(52.0786, -1.0169),
        'Spa-Francorchamps': DummyOrigin(50.4372, 5.9714),
    }

    # Match exact
    assert track_data.find_track_origin('Silverstone GP', origins, 'test.py').lat == 52.0786
    # Match substring / normalized
    assert track_data.find_track_origin('silverstone', origins, 'test.py').lat == 52.0786
    assert track_data.find_track_origin('spa', origins, 'test.py').lat == 50.4372

    # Empty / None
    with pytest.raises(ValueError, match='Track name is missing or empty.'):
        track_data.find_track_origin(None, origins, 'test.py')
    with pytest.raises(ValueError, match='Track name is missing or empty.'):
        track_data.find_track_origin('', origins, 'test.py')

    # Unknown
    expected_err = r"Unknown track: 'Monza'\. Please add its coordinates to TRACK_ORIGINS in test\.py"
    with pytest.raises(ValueError, match=expected_err):
        track_data.find_track_origin('Monza', origins, 'test.py')


def _finite_diff_bearing(origin: track_data.TrackOrigin, dx: float, dz: float) -> float:
    lat0, lon0 = origin.to_gps(0.0, 0.0)
    lat1, lon1 = origin.to_gps(dx, dz)
    m_lat, m_lon = track_data.wgs84_meters_per_degree(origin.lat)
    return math.degrees(math.atan2((lon1 - lon0) * m_lon, (lat1 - lat0) * m_lat)) % 360.0


def test_local_ned_quat_to_geodetic_ned_rfactor_and_ac():
    q_north = (0.0, 0.0, 0.0, 1.0)
    # rFactor: Game North is (0, +10)
    rf_origin = rfactor.TRACK_ORIGINS['Buckmore Park']
    geo_rf = track_data.local_ned_quat_to_geodetic_ned(q_north, rf_origin)
    assert track_data.ned_quat_to_heading_deg(geo_rf) == pytest.approx(
        _finite_diff_bearing(rf_origin, 0.0, 10.0), abs=1e-4
    )

    # Assetto Corsa: Game North is (0, -10)
    for track_name in ('Imola', 'Suzuka'):
        ac_origin = assetto_corsa.get_track_origin(track_name)
        geo_ac = track_data.local_ned_quat_to_geodetic_ned(q_north, ac_origin)
        assert track_data.ned_quat_to_heading_deg(geo_ac) == pytest.approx(
            _finite_diff_bearing(ac_origin, 0.0, -10.0), abs=1e-4
        )
