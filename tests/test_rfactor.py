import math
import pytest
from race_tools import rfactor
from race_tools import track_data


def test_get_track_origin():
    # Test exact match
    origin = rfactor.get_track_origin('Buckmore Park')
    assert origin is rfactor.TRACK_ORIGINS['Buckmore Park']

    # Test normalization and partial match
    origin = rfactor.get_track_origin('buckmore')
    assert origin is rfactor.TRACK_ORIGINS['Buckmore Park']

    # Test mixed case and underscores
    origin = rfactor.get_track_origin('BAYFORD_MEADOWS')
    assert origin is rfactor.TRACK_ORIGINS['Bayford Meadows']

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


def test_camera_projection_from_metadata():
    meta = {
        'Track name': 'Buckmore Park',
        'camera_projection': {
            'vfov_deg': 46.5,
            'resolution_px': [2560, 1440],
        },
    }
    proj = rfactor.RFactorCameraProjection.from_metadata(meta)
    assert proj.vfov_deg == pytest.approx(46.5)
    assert proj.resolution_px == (2560, 1440)
    assert proj.aspect_ratio == pytest.approx(16.0 / 9.0)


def test_unproject_rfactor_gaze_ray():
    proj = rfactor.RFactorCameraProjection(
        vfov_deg=60.0,
        resolution_px=(1920, 1080),
    )
    # Screen center -> straight forward along +x in FRD
    rx, ry, rz = rfactor.unproject_rfactor_gaze_ray(0.5, 0.5, proj)
    assert rx == pytest.approx(1.0)
    assert ry == pytest.approx(0.0)
    assert rz == pytest.approx(0.0)

    # Top-center (u=0.5, v=0.0) -> pitched up (-z in FRD) by vfov/2 = 30 deg
    tx, ty, tz = rfactor.unproject_rfactor_gaze_ray(0.5, 0.0, proj)
    assert tx > 0.0
    assert ty == pytest.approx(0.0)
    assert tz < 0.0
    assert math.degrees(math.atan2(-tz, tx)) == pytest.approx(30.0)

    # Right-center (u=1.0, v=0.5) -> local right (+y in FRD), horizontal half-FOV = atan((16/9)*tan(30 deg))
    expected_half_hfov = math.degrees(math.atan((16.0 / 9.0) * math.tan(math.radians(30.0))))
    gx, gy, gz = rfactor.unproject_rfactor_gaze_ray(1.0, 0.5, proj)
    assert gx > 0.0
    assert gy > 0.0
    assert gz == pytest.approx(0.0)
    assert math.degrees(math.atan2(gy, gx)) == pytest.approx(expected_half_hfov)

    # NaN blink propagation
    nx, ny, nz = rfactor.unproject_rfactor_gaze_ray(math.nan, 0.5, proj)
    assert math.isnan(nx)
    assert math.isnan(ny)
    assert math.isnan(nz)


def test_slerp_quat_shortest_path():
    # Identity and 90-deg Y rotation, with antipodal negated sign on q1
    q0 = (0.0, 0.0, 0.0, 1.0)
    s45 = math.sqrt(0.5)
    q1_neg = (0.0, -s45, 0.0, -s45)  # Same 3D rotation as (0, +s45, 0, +s45)
    q_mid = rfactor.slerp_quat(q0, q1_neg, 0.5)

    # Midpoint must be 45-deg Y rotation: sin(22.5 deg), cos(22.5 deg)
    assert abs(q_mid[1]) == pytest.approx(math.sin(math.radians(22.5)), abs=1e-6)
    assert abs(q_mid[3]) == pytest.approx(math.cos(math.radians(22.5)), abs=1e-6)


def test_reconstruct_gaze_map_yaw_consistency_with_gps():
    origin = rfactor.TRACK_ORIGINS['Buckmore Park']
    proj = rfactor.RFactorCameraProjection(
        vfov_deg=50.0,
        resolution_px=(1920, 1080),
    )

    # Identity quaternion (NED + FRD): faces Game North (+Z_rf).
    # Buckmore Park has angle_deg = -2.0, so Game North maps to 0 - (-2) = 2.0 deg compass bearing!
    q_identity = (0.0, 0.0, 0.0, 1.0)
    kart_hdg = rfactor.reconstruct_kart_heading(q_identity, origin)
    gaze_center = rfactor.reconstruct_gaze_map_yaw(0.5, 0.5, q_identity, proj, origin)
    assert kart_hdg == pytest.approx(2.0)
    assert gaze_center == pytest.approx(kart_hdg)

    # Verify exact match with finite-step GPS bearing from rfactor_to_gps along Game North (0, +10)
    lat0, lon0 = rfactor.rfactor_to_gps(0.0, 0.0, origin)
    lat1, lon1 = rfactor.rfactor_to_gps(0.0, 10.0, origin)
    m_lat, m_lon = track_data.wgs84_meters_per_degree(origin.lat)
    gps_brg = math.degrees(math.atan2((lon1 - lon0) * m_lon, (lat1 - lat0) * m_lat)) % 360.0
    assert gaze_center == pytest.approx(gps_brg, abs=1e-4)

    # Looking right (u = 0.85) rotates compass bearing clockwise; looking left (u = 0.15) rotates CCW
    gaze_right = rfactor.reconstruct_gaze_map_yaw(0.85, 0.5, q_identity, proj, origin)
    gaze_left = rfactor.reconstruct_gaze_map_yaw(0.15, 0.5, q_identity, proj, origin)
    assert (gaze_right - gaze_center) % 360.0 == pytest.approx(
        (gaze_center - gaze_left) % 360.0, abs=1e-6
    )
    assert 20.0 < ((gaze_right - gaze_center) % 360.0) < 40.0

    # v < 0.5 pitches gaze up; v > 0.5 pitches gaze down
    assert rfactor.reconstruct_gaze_pitch_deg(0.5, 0.0, q_identity, proj) == pytest.approx(25.0)
    assert rfactor.reconstruct_gaze_pitch_deg(0.5, 1.0, q_identity, proj) == pytest.approx(-25.0)

    # Clockwise Z_down yaw rotates 2 -> 92 -> 182 -> 272 deg
    for yaw_deg, expected_hdg in [(0.0, 2.0), (90.0, 92.0), (180.0, 182.0), (270.0, 272.0)]:
        yaw_rad = math.radians(yaw_deg)
        q_yaw = (0.0, 0.0, math.sin(0.5 * yaw_rad), math.cos(0.5 * yaw_rad))
        assert rfactor.reconstruct_kart_heading(q_yaw, origin) == pytest.approx(expected_hdg, abs=1e-6)
        assert rfactor.reconstruct_gaze_map_yaw(0.5, 0.5, q_yaw, proj, origin) == pytest.approx(
            expected_hdg, abs=1e-6
        )

    # Camera pitched down by 15 degrees around FRD +y (Right) axis: center gaze still has identical map yaw
    pitch_rad = math.radians(-15.0)
    q_pitched = (0.0, math.sin(0.5 * pitch_rad), 0.0, math.cos(0.5 * pitch_rad))
    gaze_pitched_center = rfactor.reconstruct_gaze_map_yaw(0.5, 0.5, q_pitched, proj, origin)
    assert gaze_pitched_center == pytest.approx(gaze_center, abs=1e-6)
    assert rfactor.reconstruct_gaze_pitch_deg(0.5, 0.5, q_pitched, proj) == pytest.approx(-15.0, abs=1e-6)

    # NaN inputs (blink or trackside camera) return NaN
    assert math.isnan(rfactor.reconstruct_gaze_map_yaw(math.nan, 0.5, q_identity, proj, origin))
    assert math.isnan(
        rfactor.reconstruct_gaze_map_yaw(0.5, 0.5, (math.nan, 0.0, 0.0, 1.0), proj, origin)
    )
    assert math.isnan(rfactor.reconstruct_gaze_pitch_deg(math.nan, 0.5, q_identity, proj))
