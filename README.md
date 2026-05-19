# Race Tools 🏎️📊

[![Python Tests and Linting](https://github.com/kibergus/race_tools/actions/workflows/test.yml/badge.svg)](https://github.com/kibergus/race_tools/actions)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python Version](https://img.shields.io/badge/python-3.8%20%7C%203.9%20%7C%203.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)](https://www.python.org)
[![Code Style: Flake8](https://img.shields.io/badge/code%20style-flake8-orange.svg)](https://flake8.pycqa.org/en/latest/)

`race_tools` is a collections of utilities that I use to understand how my daughter is progressing in karting.

---

## ✨ Key Features

* **🛰️ Coordinate Conversion & Georeferencing (`rfactor.py`):** Converts KartSim simulator local Cartesian coordinates (X/Z in meters) into real-world geographic coordinates (WGS84 Latitude/Longitude).
* **📈 Spline-Based Telemetry Interpolation (`second_monitor.py`):** Utilizes for interpolating Second Monitor telemetry. At the end - motec plugin works better and can provide more data.
* **📸 Interactive Track Render Utility (`utils/render_track.py`):** A service utility I use to align KartSim coordinates with GPS.

---

## 🚀 Quickstart

## 📖 Usage Examples

### 1. Cartesian to GPS Georeferencing

Convert simulator-local meters to high-precision WGS84 geographic coordinates using track origins:

```python
from race_tools.rfactor import get_track_origin, rfactor_to_gps

# 1. Retrieve the origin and correction parameters for the track
origin = get_track_origin('Buckmore Park')

# 2. Convert rFactor Cartesian coordinates (X = right/left, Z = forward/backward)
# (Applying specific track rotations and meter shifts under the hood)
lat, lon = rfactor_to_gps(12.54, -84.32, origin)

print(f"Latitude: {lat:.8f}, Longitude: {lon:.8f}")
```

### 3. Generate GIS Layouts (KML / GeoJSON)

Generate KML and GeoJSON track maps containing Left, Right, and Center paths:

```python
from race_tools.kml_utils import generate_track_limits_kml
from race_tools.geojson_utils import generate_track_limits_geojson

# Coordinates list containing (latitude, longitude) or (latitude, longitude, altitude)
center_path = [[51.343194, 0.501867], [51.343200, 0.501870], ...]
left_boundary = [[51.343210, 0.501850], ...]
right_boundary = [[51.343178, 0.501880], ...]

# Save as KML (for Google Earth)
generate_track_limits_kml(
    center_path=center_path,
    left_path=left_boundary,
    right_path=right_boundary,
    name="Buckmore Park Layout",
    output_path="buckmore_layout.kml"
)

# Save as GeoJSON (for web maps / Leaflet)
generate_track_limits_geojson(
    center_path=center_path,
    left_path=left_boundary,
    right_path=right_boundary,
    name="Buckmore Park GeoJSON",
    output_path="buckmore_layout.geojson"
)
```

---

## 🛠️ Development & Testing

### Running Unit Tests
We use `pytest` as our testing framework. To execute all unit tests, run:
```bash
pytest
```

### Running the Linter
Code style compliance is validated via `flake8`. Configuration preferences (like line length limits) are managed in `.flake8`.
To verify code compliance, run:
```bash
flake8 race_tools tests
```

### Building Distribution Packages (Wheel)
To package the library as a source distribution (`sdist`) and a Python wheel, make sure you have the development dependencies installed, and then run the automated build script:
```bash
./build_wheel.sh
```

Alternatively, you can build and verify the packages manually:
```bash
# Build the source distribution and wheel package
python -m build

# Validate the generated package integrity and metadata (for PyPI compliance)
twine check dist/*
```
The generated `.whl` and `.tar.gz` files will be output to the `dist/` directory.

---

## ⚖️ License

This project is licensed under the Apache License, Version 2.0 - see the [LICENSE](LICENSE) file for details.
