# Assetto Corsa Evo (ACE) Track Extraction Guide

This document describes how track geometries (centerlines, lateral boundaries, sector splits, and geographic origins) are packaged inside **Assetto Corsa Evo (ACE)**, how they are extracted and transformed into standard GIS/JSON formats, and how telemetry metadata captures track origins.

---

## 1. Overview & Architecture

Assetto Corsa Evo packages its core assets inside monolithic container files (`.kspkg` archives). Official circuits are housed within:
```
C:\Program Files (x86)\Steam\steamapps\common\Assetto Corsa EVO\content.kspkg
```

Within `content.kspkg`, each track directory contains:
* **Centerline Spline:** `content\tracks\<track_name>\containers\layout_<variant>.splinedata.json`
  * High-density 3D nodes (typically 2,000–4,000 points per circuit).
  * Defines exact waypoint positions $(X, Y, Z)$ and vehicle orientation quaternions.
* **Track Width & Control Points:** `content\tracks\<track_name>\layouts\layout_<variant>.trackcontrolpoints`
  * Protobuf-encoded cross-sections around the circuit.
  * Contains legal track edge limits ($w_{\text{left}}$ and $w_{\text{right}}$ in meters) and kerb margins.
* **Sector Timelines & DRS Zones:** `content\tracks\<track_name>\containers\timelines_<variant>.scene`
  * 3D trigger planes for start/finish lines and timing sectors.
* **Track Definition:** `content\tracks\<track_name>\<track_name>.track`
  * Elevation above sea level, dynamic track grip presets, and track metadata.

---

## 2. Package Format (`.kspkg`) Details

* **File Table Location:** Positioned at the very end of the `.kspkg` archive.
  * Version $\ge 0.7$: `0x4_000_000` bytes (64 MB).
  * Version $< 0.7$: `0x2_000_000` bytes (32 MB).
* **Encryption / Obfuscation:**
  * Uses a static 64-bit XOR cipher key: `0x9F9721A97D1135C1`.
  * The file table and files flagged with `Flags & (1 << 8)` are XOR'd with this key.
* **File Table Entry Structure (`0x100` bytes):**
  * `0x000–0x0DF` (140 bytes): `FileNameBuffer` (ASCII string).
  * `0x0E0–0x0E3` (4 bytes): Alignment padding.
  * `0x0E4–0x0E5` (2 bytes): `Flags` (`1 << 0` = Directory, `1 << 8` = XOR Encrypted).
  * `0x0E6–0x0E7` (2 bytes): `FileNameLength` (short).
  * `0x0E8–0x0EF` (8 bytes): `PathHash` (FNV1A-64 of lowercased Unicode path with backslashes).
  * `0x0F0–0x0F7` (8 bytes): `FileSize` (int64).
  * `0x0F8–0x0FF` (8 bytes): `FileOffset` (int64).

---

## 3. Coordinate Systems & Geodetic Alignment

Unlike older simulation engines, Assetto Corsa Evo circuits are laser-scanned and aligned directly to **True North**:
* $+Z$ axis: **North** (meters)
* $+X$ axis: **East** (meters)
* $+Y$ axis: **Elevation / Up** (meters)
* Rotation correction: **$0.0^\circ$** (no rotation matrix required).

### Cartesian to GPS (WGS84) Conversion
Given track geographic origin coordinates $(\text{lat}_0, \text{lon}_0)$ for Cartesian $(0, 0, 0)$:
$$\Delta \text{lat} = \frac{Z}{m_{\text{lat}}}, \quad \Delta \text{lon} = \frac{X}{m_{\text{lon}}}$$
$$\text{lat} = \text{lat}_0 + \Delta \text{lat}, \quad \text{lon} = \text{lon}_0 + \Delta \text{lon}$$

Where degree lengths at latitude $\phi$ are computed via standard WGS84 ellipsoidal expansion:
$$m_{\text{lat}} = 111132.92 - 559.82 \cos(2\phi) + 1.175 \cos(4\phi)$$
$$m_{\text{lon}} = 111412.84 \cos(\phi) - 93.5 \cos(3\phi) + 0.118 \cos(5\phi)$$

---

## 4. Boundary Calculation

For each consecutive node $\vec{P}_i = (x_i, y_i, z_i)$ along the spline:
1. Compute the normalized 2D forward tangent in the horizontal ground plane:
   $$\vec{T}_i = \frac{\vec{P}_{i+1} - \vec{P}_{i-1}}{\|\vec{P}_{i+1} - \vec{P}_{i-1}\|}_{xz} = (t_x, t_z)$$
2. Compute the perpendicular normal pointing toward the left boundary:
   $$\hat{N}_i = (-t_z, t_x)$$
3. Query the nearest control point from `trackcontrolpoints` for lateral widths $w_{\text{left}}$ and $w_{\text{right}}$.
4. Calculate the boundary points:
   $$\vec{B}_{\text{left}} = \vec{P}_i + w_{\text{left}} \hat{N}_i$$
   $$\vec{B}_{\text{right}} = \vec{P}_i - w_{\text{right}} \hat{N}_i$$

---

## 5. Using the Extractor Tool

The repository provides a standalone Python tool [`utils/extract_ace_track.py`](file:///c:/code/race_tools/utils/extract_ace_track.py):

### Basic Usage
```powershell
# Extract Monza to data/tracks/
python utils/extract_ace_track.py --track monza

# Extract another track (e.g. imola, spa)
python utils/extract_ace_track.py --track imola

# Custom path to content.kspkg or custom output directory
python utils/extract_ace_track.py --track monza --kspkg "D:\SteamLibrary\steamapps\common\Assetto Corsa EVO\content.kspkg" --out "data/tracks"
```

### Generated Files
For each extracted circuit, three standard files are generated in `data/tracks/`:
1. **`<slug>.json`**: Track manifest containing lap length, sector splits, origin, aliases, and dense centerline waypoint dictionaries:
   ```json
   {
       "track_name": "Monza",
       "timezone": "Europe/Rome",
       "aliases": ["Monza", "Autodromo Nazionale Monza", "Monza GP", "monza", "MonzaGP"],
       "lap_length": 5751.18,
       "sector_end": [1581.57, 3859.04, 5751.18],
       "origin": {"lat": 45.61389, "lon": 9.28624},
       "center_line": [
           {"lat": 45.61895, "lon": 9.28120, "dist": 0.0, "sector": 0},
           ...
       ],
       "turn_end": [],
       "turns": []
   }
   ```
2. **`<slug>.geojson`**: Standard GIS feature collection containing `Left Boundary`, `Right Boundary`, and `Center Line` LineStrings.
3. **`<slug>.kml`**: Google Earth / GIS compatible KML overlay with styled center and boundary lines.

---

## 6. Telemetry Metadata Transmission (`ace_plugin`)

The C++ companion daemon [`BrBrDbTelemetryAce`](file:///c:/code/race_tools/ace_plugin/) reads AC Evo's Windows Named Shared Memory (`Local\acevo_pmf_static` and `Local\acevo_pmf_physics`) and outputs the following header lines at session start:

```csv
Track name,Monza
Track configuration,GP Time Attack
Origin Latitude,45.61389000
Origin Longitude,9.28624000
Track Origin Latitude,45.61389000
Track Origin Longitude,9.28624000
Origin Heading,0.000000
Track Origin Heading,0.000000
Date,2026-10-22
Time,07:38:08
Driver name,Katia Guseinova
League,race_tools
Class,Ferrari SF-25
Session,Practice
Game version,0.1.0
```

Downstream servers and ingestion workers parse these metadata fields to perform Cartesian-to-GPS conversion server-side.
