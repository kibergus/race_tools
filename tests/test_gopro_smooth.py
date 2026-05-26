import unittest
from race_tools.smooth_gps import lat_lon_to_enu, enu_to_lat_lon, interpolate_gps_with_imu


class TestGoproSmooth(unittest.TestCase):
    def test_enu_conversion_roundtrip(self) -> None:
        anchor_lat, anchor_lon = 51.5074, -0.1278  # London
        lat, lon = 51.5080, -0.1285

        e, n = lat_lon_to_enu(lat, lon, anchor_lat, anchor_lon)
        lat_back, lon_back = enu_to_lat_lon(e, n, anchor_lat, anchor_lon)

        self.assertAlmostEqual(lat, lat_back, places=7)
        self.assertAlmostEqual(lon, lon_back, places=7)

    def test_interpolation_smoothness(self) -> None:
        # Create GPS points that form a "corner"
        anchor_lat, anchor_lon = 0, 0
        gps_pts = [
            (0.0, 0.0, 0.0, 0.0),
            (1000000.0, 1e-5, 1e-5, 0.0),
            (2000000.0, 2e-5, 0.0, 0.0),
            (3000000.0, 3e-5, 1e-5, 0.0),
            (4000000.0, 4e-5, 0.0, 0.0),
        ]
        # IMU points every 0.1s
        imu_pts = [(t * 100000.0, [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]) for t in range(41)]

        path = interpolate_gps_with_imu(gps_pts, imu_pts, anchor_lat, anchor_lon)

        # Check that we have smoothed high-frequency output
        self.assertTrue(len(path) > 30)
        # Check for any extreme jumps (triangles)

        def dist(p1: tuple[float, float, float], p2: tuple[float, float, float]) -> float:
            return ((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2)**0.5

        for i in range(len(path) - 1):
            d = dist(path[i], path[i + 1])
            self.assertLess(d, 1e-5, f'Jump detected at index {i}')

    def test_overlapping_gps_chunks_merging(self) -> None:
        # Simulate two chunks that overlap in time but have slight noise
        anchor_lat, anchor_lon = 0, 0
        gps_pts = [
            (0.0, 0.0, 0.0, 0.0),
            (1000000.0, 1e-5, 1e-5, 0.0),
            (2000000.0, 2e-5, 0.0, 0.0),
            (1000.0, 0.01e-5, 0.01e-5, 0.0),  # Very close! Should merge.
        ]
        imu_pts = [(t * 100000.0, [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]) for t in range(21)]

        path = interpolate_gps_with_imu(gps_pts, imu_pts, anchor_lat, anchor_lon)
        self.assertTrue(len(path) > 0)

    def test_insufficient_gps_points(self) -> None:
        # Only 1 GPS point after merging
        anchor_lat, anchor_lon = 0, 0
        gps_pts = [(0.0, 0.0, 0.0, 0.0), (100.0, 0.0, 0.0, 0.0)]  # Will merge into 1
        imu_pts = [(50.0, [0.0, 0.0, 0.0], [0.0, 0.0, 0.0])]
        path = interpolate_gps_with_imu(gps_pts, imu_pts, anchor_lat, anchor_lon)
        self.assertEqual(len(path), 0)

    def test_identical_timestamps_filter(self) -> None:
        # Test that identical timestamps are handled by the unique filter
        anchor_lat, anchor_lon = 0, 0
        gps_pts = [
            (0.0, 0.0, 0.0, 0.0),
            (1000000.0, 1e-5, 1e-5, 0.0),
            (1000000.0, 1e-5, 1e-5, 0.0),  # Exact duplicate time
            (2000000.0, 2e-5, 0.0, 0.0),
        ]
        imu_pts = [(500000.0, [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]), (1500000.0, [0.0, 0.0, 0.0], [0.0, 0.0, 0.0])]
        path = interpolate_gps_with_imu(gps_pts, imu_pts, anchor_lat, anchor_lon)
        self.assertEqual(len(path), 2)

    def test_constant_path(self) -> None:
        # Test that a stationary kart remains stationary
        anchor_lat, anchor_lon = 52.0, -1.0
        gps_pts = [(t * 1000000.0, 52.0, -1.0, 100.0) for t in range(10)]
        imu_pts = [(t * 100000.0, [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]) for t in range(100)]
        path = interpolate_gps_with_imu(gps_pts, imu_pts, anchor_lat, anchor_lon)

        for p in path:
            self.assertAlmostEqual(p[0], 52.0, places=6)
            self.assertAlmostEqual(p[1], -1.0, places=6)
            self.assertAlmostEqual(p[2], 100.0, places=1)


if __name__ == '__main__':
    unittest.main()
