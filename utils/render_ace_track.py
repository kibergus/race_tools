#!/usr/bin/env python3
"""
Utility to render and interactively align Assetto Corsa Evo (ACE) track boundaries
on top of Google Maps satellite imagery.

Features:
- Real-time in-browser alignment tuner: adjust North/East shifts and rotation angle
  with live 60 FPS visual feedback on high-resolution Google Satellite imagery.
- Immediate code output: generates ready-to-paste `track_data.Correction(...)` snippet.
- Keyboard navigation (Arrow keys for N/S/E/W, Q/E for rotation, Shift for coarse, Alt for fine).
- Mouse drag-to-shift mode to quickly pull track boundaries onto satellite roads.
- Optional headless Playwright capture (--screenshot) for 8K static renders.

Instructions for verifying and tuning track alignment:
1. Run this script:
   `PROJECT_ROOT=/home/kibergus/automations ./utils/venv/bin/python3 utils/render_ace_track.py <track_name>`
   The script will automatically:
   - Re-extract track geometry using current coordinates from `race_tools/assetto_corsa.py`.
   - Regenerate the GeoJSON, JSON, and KML files in the track data directory (and $PROJECT_ROOT if set).
   - Generate an interactive HTML tuning page and open it directly in your browser.
2. In the browser:
   - Drag or use sliders / arrow keys / Q/E to align track boundaries with satellite tarmac.
   - Click "Copy Correction" to copy the exact `track_data.Correction(...)` snippet.
   - Paste into `race_tools/assetto_corsa.py`.

Example:
  PROJECT_ROOT=/home/kibergus/automations ./utils/venv/bin/python3 utils/render_ace_track.py spa
"""
import os
import sys
import json
import math
import argparse
import tempfile
import asyncio
import webbrowser

# Add project root to sys.path
PROJECT_ROOT = os.environ.get('PROJECT_ROOT')
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from race_tools import assetto_corsa, track_data  # noqa: E402
from race_tools.sanitize import clean_slug  # noqa: E402
from utils.extract_ace_track import extract_track, get_default_kspkg_path, resolve_path  # noqa: E402

REPO_TRACK_DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data', 'tracks'))
IMAGE_SIZE = 8192


def reprocess_ace_track(
    track_name: str,
    kspkg_path: str | None = None,
    include_meshes: bool = False,
    tarmac_meshes: str | None = None
) -> None:
    """
    Re-extracts track from content.kspkg if available.
    """
    slug = clean_slug(track_name)
    resolved_kspkg = resolve_path(kspkg_path) if kspkg_path else get_default_kspkg_path()
    if not os.path.exists(resolved_kspkg):
        print(f"Note: content.kspkg not found at '{resolved_kspkg}'. Skipping re-extraction.")
        return

    out_dirs = [REPO_TRACK_DATA_DIR]
    if PROJECT_ROOT:
        karting_dir = os.path.join(PROJECT_ROOT, 'karting/data/tracks')
        if os.path.exists(karting_dir) and os.path.realpath(karting_dir) != os.path.realpath(REPO_TRACK_DATA_DIR):
            out_dirs.append(karting_dir)

    for out_dir in out_dirs:
        print(f"Re-extracting ACE track '{slug}' to {out_dir}...")
        try:
            extract_track(
                track_slug=slug,
                kspkg_path=resolved_kspkg,
                output_dir=out_dir,
                include_meshes=include_meshes,
                tarmac_meshes=tarmac_meshes
            )
        except Exception as e:
            print(f"Warning: extraction failed for '{slug}' into {out_dir}: {e}")


def resolve_geojson_by_alias(track_name: str, search_dirs: list[str]) -> str | None:
    """
    Scans all track JSON sidecar files in search_dirs for one whose `aliases` list
    contains track_name (case-insensitive). Returns the corresponding .geojson path, or None.
    """
    norm = track_name.strip().lower()
    for d in search_dirs:
        if not os.path.exists(d):
            continue
        for f in os.listdir(d):
            if not f.endswith('.json'):
                continue
            json_path = os.path.join(d, f)
            try:
                with open(json_path, 'r', encoding='utf-8') as fh:
                    data = json.load(fh)
                aliases = data.get('aliases', [])
                if any(a.strip().lower() == norm for a in aliases) or data.get('track_name', '').strip().lower() == norm:
                    geojson_path = json_path[:-5] + '.geojson'
                    if os.path.exists(geojson_path):
                        return geojson_path
            except Exception:
                continue
    return None


def locate_geojson(track_name: str) -> tuple[str, str]:
    """
    Locates the GeoJSON file corresponding to track_name in repository or PROJECT_ROOT.
    Returns (geojson_path, resolved_slug).
    """
    slug = clean_slug(track_name)
    search_dirs = [REPO_TRACK_DATA_DIR]
    if PROJECT_ROOT:
        karting_dir = os.path.join(PROJECT_ROOT, 'karting/data/tracks')
        if os.path.exists(karting_dir) and karting_dir not in search_dirs:
            search_dirs.append(karting_dir)

    geojson_path = None
    for d in search_dirs:
        candidate = os.path.join(d, f'{slug}.geojson')
        if os.path.exists(candidate):
            geojson_path = candidate
            break

    if not geojson_path:
        resolved = resolve_geojson_by_alias(track_name, search_dirs)
        if resolved:
            geojson_path = resolved
            slug = os.path.basename(resolved).replace('.geojson', '')
        else:
            for d in search_dirs:
                if os.path.exists(d):
                    for f in os.listdir(d):
                        if slug in f and f.endswith('.geojson'):
                            geojson_path = os.path.join(d, f)
                            slug = f.replace('.geojson', '')
                            break
                if geojson_path:
                    break

    if not geojson_path:
        raise FileNotFoundError(f"Could not find GeoJSON for ACE track '{track_name}' in {search_dirs}")
    return geojson_path, slug


def invert_geojson_to_cartesian(
    geojson_data: dict,
    origin: track_data.TrackOrigin
) -> list[dict]:
    """
    Inverts GeoJSON features from GPS (lat, lon) back to local Cartesian coordinates (x, z)
    relative to origin.
    Returns list of {'properties': ..., 'coords': [[x, z], ...]}.
    """
    m_per_deg_lat, m_per_deg_lon = track_data.wgs84_meters_per_degree(origin.lat)
    n0 = origin.correction.north_m
    e0 = origin.correction.east_m
    eff_angle = origin.correction.angle_deg - origin.heading
    rad = math.radians(eff_angle)
    cos_a = math.cos(rad)
    sin_a = math.sin(rad)

    features = []
    for feat in geojson_data.get('features', []):
        props = feat.get('properties', {})
        geom = feat.get('geometry', {})
        gtype = geom.get('type')

        lines = []
        if gtype == 'LineString':
            lines.append(geom.get('coordinates', []))
        elif gtype == 'MultiLineString':
            lines.extend(geom.get('coordinates', []))
        elif gtype == 'Polygon':
            lines.extend(geom.get('coordinates', []))
        elif gtype == 'MultiPolygon':
            for poly in geom.get('coordinates', []):
                lines.extend(poly)

        for line in lines:
            cart_coords = []
            for pt in line:
                lon = pt[0]
                lat = pt[1]

                north_shifted = (lat - origin.lat) * m_per_deg_lat
                east_shifted = (lon - origin.lon) * m_per_deg_lon

                north_rot = north_shifted - n0
                east_rot = east_shifted - e0

                east = east_rot * cos_a + north_rot * sin_a
                north = -east_rot * sin_a + north_rot * cos_a

                x = east
                z = -north
                cart_coords.append([round(x, 3), round(z, 3)])

            if cart_coords:
                features.append({
                    'properties': props,
                    'coords': cart_coords
                })

    return features


def generate_tuner_html(
    track_slug: str,
    origin: assetto_corsa.AssettoCorsaTrackOrigin,
    features: list[dict],
    m_per_deg_lat: float,
    m_per_deg_lon: float
) -> str:
    """
    Builds the interactive Leaflet HTML tuning application.
    """
    has_whitelines = any('whiteline' in (f.get('properties', {}).get('type', '') or '').lower() for f in features)
    has_tarmac = any('tarmac' in (f.get('properties', {}).get('type', '') or '').lower() for f in features)

    template = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>ACE Track Alignment: __TRACK_SLUG__</title>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <style>
        * { box-sizing: border-box; }
        html, body, #map { margin: 0; padding: 0; width: 100%; height: 100%; background: #0b0e14; overflow: hidden; }
        .leaflet-control-attribution { display: none !important; }

        /* Draggable HUD Card */
        #tuner-panel {
            position: absolute;
            top: 20px;
            right: 20px;
            width: 440px;
            max-height: calc(100vh - 40px);
            background: rgba(14, 18, 26, 0.94);
            backdrop-filter: blur(12px);
            border: 1px solid rgba(255, 255, 255, 0.16);
            border-radius: 12px;
            box-shadow: 0 12px 40px rgba(0, 0, 0, 0.75);
            color: #f1f5f9;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            font-size: 13px;
            z-index: 9999;
            display: flex;
            flex-direction: column;
            overflow: hidden;
            transition: max-height 0.25s ease;
        }

        #panel-header {
            padding: 12px 16px;
            background: rgba(255, 255, 255, 0.05);
            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
            cursor: move;
            user-select: none;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        #panel-header h2 {
            margin: 0;
            font-size: 15px;
            font-weight: 700;
            color: #38bdf8;
            letter-spacing: 0.5px;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        #panel-header .subtitle {
            font-size: 11px;
            color: #94a3b8;
            font-weight: 400;
        }

        .header-actions button {
            background: rgba(255, 255, 255, 0.1);
            border: none;
            color: #cbd5e1;
            padding: 4px 8px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 11px;
        }
        .header-actions button:hover { background: rgba(255, 255, 255, 0.2); }

        #panel-body {
            padding: 14px 16px;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 12px;
        }

        /* Mode Selector */
        .mode-row {
            display: flex;
            gap: 8px;
            background: rgba(0, 0, 0, 0.35);
            padding: 4px;
            border-radius: 8px;
        }
        .mode-btn {
            flex: 1;
            padding: 6px 10px;
            background: transparent;
            border: 1px solid transparent;
            color: #94a3b8;
            border-radius: 6px;
            cursor: pointer;
            font-weight: 600;
            font-size: 12px;
            transition: all 0.15s ease;
        }
        .mode-btn.active {
            background: #0284c7;
            color: #ffffff;
            border-color: #38bdf8;
            box-shadow: 0 2px 8px rgba(2, 132, 199, 0.4);
        }

        /* Param Rows */
        .param-group {
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 8px;
            padding: 10px 12px;
            display: flex;
            flex-direction: column;
            gap: 6px;
        }
        .param-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .param-header label {
            font-weight: 600;
            color: #e2e8f0;
            font-size: 12px;
        }
        .param-num-input {
            width: 105px;
            background: #0f172a;
            border: 1px solid #334155;
            color: #38bdf8;
            border-radius: 6px;
            padding: 4px 6px;
            font-family: monospace;
            font-size: 13px;
            text-align: right;
        }
        .param-num-input:focus {
            outline: none;
            border-color: #38bdf8;
        }

        .range-slider {
            width: 100%;
            cursor: pointer;
            accent-color: #38bdf8;
        }

        .nudge-buttons {
            display: flex;
            gap: 3px;
            justify-content: space-between;
        }
        .nudge-btn {
            flex: 1;
            padding: 3px 2px;
            background: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.1);
            color: #cbd5e1;
            border-radius: 4px;
            font-size: 10px;
            cursor: pointer;
            font-family: monospace;
        }
        .nudge-btn:hover {
            background: #0ea5e9;
            color: white;
            border-color: #38bdf8;
        }

        /* Code Snippet Box */
        .code-container {
            background: #090d16;
            border: 1px solid #1e293b;
            border-radius: 8px;
            padding: 10px;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }
        .code-box {
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
            font-size: 11px;
            line-height: 1.4;
            color: #4ade80;
            white-space: pre-wrap;
            word-break: break-all;
            background: #020617;
            padding: 8px;
            border-radius: 6px;
            border: 1px solid #1e293b;
        }
        .btn-copy {
            background: #2563eb;
            color: #ffffff;
            border: none;
            padding: 8px 12px;
            border-radius: 6px;
            font-weight: 700;
            font-size: 12px;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
            transition: all 0.15s ease;
        }
        .btn-copy:hover {
            background: #1d4ed8;
        }
        .btn-reset {
            background: rgba(255, 255, 255, 0.08);
            color: #94a3b8;
            border: 1px solid rgba(255, 255, 255, 0.1);
            padding: 5px 8px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 11px;
        }
        .btn-reset:hover {
            background: #ef4444;
            color: white;
            border-color: #f87171;
        }

        /* Keyboard Hints */
        .kb-hints {
            font-size: 11px;
            color: #94a3b8;
            background: rgba(0, 0, 0, 0.25);
            padding: 8px;
            border-radius: 6px;
            line-height: 1.5;
        }
        .kb-key {
            display: inline-block;
            background: #1e293b;
            color: #e2e8f0;
            padding: 1px 4px;
            border-radius: 3px;
            font-family: monospace;
            font-size: 10px;
            border: 1px solid #334155;
        }

        /* Layer Toggles */
        .layer-controls {
            display: flex;
            flex-wrap: wrap;
            gap: 8px 14px;
            font-size: 11px;
            color: #cbd5e1;
            padding: 6px 2px;
        }
        .layer-item {
            display: flex;
            align-items: center;
            gap: 5px;
            cursor: pointer;
        }
        .swatch {
            width: 12px;
            height: 12px;
            border-radius: 2px;
            display: inline-block;
        }
    </style>
</head>
<body>
    <div id="map"></div>

    <div id="tuner-panel">
        <div id="panel-header">
            <div>
                <h2>🏁 ACE Alignment: __TRACK_SLUG__</h2>
                <div class="subtitle">Origin: __ORIGIN_LAT__, __ORIGIN_LON__ (Heading: __ORIGIN_HEADING__°)</div>
            </div>
            <div class="header-actions">
                <button id="btn-toggle-panel" title="Collapse / Expand Panel">_</button>
            </div>
        </div>

        <div id="panel-body">
            <!-- Mode Switcher -->
            <div class="mode-row">
                <button id="btn-mode-pan" class="mode-btn active" title="Click & drag to navigate satellite map">🖐️ Pan Map (P)</button>
                <button id="btn-mode-drag" class="mode-btn" title="Click & drag map to shift track boundaries">🎯 Drag Track (D)</button>
            </div>

            <!-- North Shift -->
            <div class="param-group">
                <div class="param-header">
                    <label>North Shift (meters)</label>
                    <input type="number" id="input-north" class="param-num-input" step="0.1" value="__INITIAL_NORTH__" />
                </div>
                <input type="range" id="slider-north" class="range-slider" min="-3000" max="3000" step="0.5" value="__INITIAL_NORTH__" />
                <div class="nudge-buttons">
                    <button class="nudge-btn" onclick="nudgeNorth(-100)">-100</button>
                    <button class="nudge-btn" onclick="nudgeNorth(-10)">-10</button>
                    <button class="nudge-btn" onclick="nudgeNorth(-1)">-1</button>
                    <button class="nudge-btn" onclick="nudgeNorth(-0.1)">-0.1</button>
                    <button class="nudge-btn" onclick="nudgeNorth(0.1)">+0.1</button>
                    <button class="nudge-btn" onclick="nudgeNorth(1)">+1</button>
                    <button class="nudge-btn" onclick="nudgeNorth(10)">+10</button>
                    <button class="nudge-btn" onclick="nudgeNorth(100)">+100</button>
                </div>
            </div>

            <!-- East Shift -->
            <div class="param-group">
                <div class="param-header">
                    <label>East Shift (meters)</label>
                    <input type="number" id="input-east" class="param-num-input" step="0.1" value="__INITIAL_EAST__" />
                </div>
                <input type="range" id="slider-east" class="range-slider" min="-3000" max="3000" step="0.5" value="__INITIAL_EAST__" />
                <div class="nudge-buttons">
                    <button class="nudge-btn" onclick="nudgeEast(-100)">-100</button>
                    <button class="nudge-btn" onclick="nudgeEast(-10)">-10</button>
                    <button class="nudge-btn" onclick="nudgeEast(-1)">-1</button>
                    <button class="nudge-btn" onclick="nudgeEast(-0.1)">-0.1</button>
                    <button class="nudge-btn" onclick="nudgeEast(0.1)">+0.1</button>
                    <button class="nudge-btn" onclick="nudgeEast(1)">+1</button>
                    <button class="nudge-btn" onclick="nudgeEast(10)">+10</button>
                    <button class="nudge-btn" onclick="nudgeEast(100)">+100</button>
                </div>
            </div>

            <!-- Rotation Angle -->
            <div class="param-group">
                <div class="param-header">
                    <label>Rotation Angle (degrees CCW)</label>
                    <input type="number" id="input-angle" class="param-num-input" step="0.01" value="__INITIAL_ANGLE__" />
                </div>
                <input type="range" id="slider-angle" class="range-slider" min="-180" max="180" step="0.05" value="__INITIAL_ANGLE__" />
                <div class="nudge-buttons">
                    <button class="nudge-btn" onclick="nudgeAngle(-10)">-10°</button>
                    <button class="nudge-btn" onclick="nudgeAngle(-1)">-1°</button>
                    <button class="nudge-btn" onclick="nudgeAngle(-0.1)">-0.1°</button>
                    <button class="nudge-btn" onclick="nudgeAngle(-0.01)">-0.01°</button>
                    <button class="nudge-btn" onclick="nudgeAngle(0.01)">+0.01°</button>
                    <button class="nudge-btn" onclick="nudgeAngle(0.1)">+0.1°</button>
                    <button class="nudge-btn" onclick="nudgeAngle(1)">+1°</button>
                    <button class="nudge-btn" onclick="nudgeAngle(10)">+10°</button>
                </div>
            </div>

            <!-- Code Snippet & Copy -->
            <div class="code-container">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <span style="font-weight: 700; color: #94a3b8; font-size: 11px;">PYTHON CORRECTION SNIPPET</span>
                    <button class="btn-reset" onclick="resetValues()" title="Reset to initial values">↺ Reset</button>
                </div>
                <div id="code-snippet" class="code-box"></div>
                <button id="btn-copy" class="btn-copy">📋 Copy Correction Snippet</button>
            </div>

            <!-- Layer & Style Toggles -->
            <div class="layer-controls">
                <label class="layer-item"><input type="checkbox" id="chk-left" checked /><span class="swatch" style="background:#ff2200;"></span> Left Boundary</label>
                <label class="layer-item"><input type="checkbox" id="chk-right" checked /><span class="swatch" style="background:#ff7700;"></span> Right Boundary</label>
                <label class="layer-item"><input type="checkbox" id="chk-center" checked /><span class="swatch" style="background:#ffffff; border: 1px dashed #666;"></span> Centerline</label>
                __EXTRA_LAYER_CHECKBOXES__
                <label class="layer-item"><input type="checkbox" id="chk-origin" checked /><span class="swatch" style="background:#00ffff; border-radius:50%;"></span> Origin Pivot</label>
            </div>

            <!-- Keyboard Help -->
            <div class="kb-hints">
                <strong>Keyboard Shortcuts:</strong><br />
                • <span class="kb-key">↑</span> <span class="kb-key">↓</span> <span class="kb-key">←</span> <span class="kb-key">→</span> : Shift ±1m (hold <span class="kb-key">Shift</span> for ±10m, <span class="kb-key">Alt</span> for ±0.1m)<br />
                • <span class="kb-key">Q</span> / <span class="kb-key">E</span> or <span class="kb-key">[</span> / <span class="kb-key">]</span> : Rotate ±0.1° (hold <span class="kb-key">Shift</span> for ±1°, <span class="kb-key">Alt</span> for ±0.01°)<br />
                • Hold <span class="kb-key">Shift</span> while dragging to move track in any mode
            </div>
        </div>
    </div>

    <script>
        const originLat = __ORIGIN_LAT__;
        const originLon = __ORIGIN_LON__;
        const originHeading = __ORIGIN_HEADING__;
        const initialNorth = __INITIAL_NORTH__;
        const initialEast = __INITIAL_EAST__;
        const initialAngle = __INITIAL_ANGLE__;
        const mPerDegLat = __M_PER_DEG_LAT__;
        const mPerDegLon = __M_PER_DEG_LON__;
        const rawFeatures = __RAW_FEATURES_JSON__;

        // Elements
        const inputNorth = document.getElementById('input-north');
        const sliderNorth = document.getElementById('slider-north');
        const inputEast = document.getElementById('input-east');
        const sliderEast = document.getElementById('slider-east');
        const inputAngle = document.getElementById('input-angle');
        const sliderAngle = document.getElementById('slider-angle');
        const codeSnippet = document.getElementById('code-snippet');
        const btnCopy = document.getElementById('btn-copy');

        // Adjust slider ranges dynamically around initial values
        sliderNorth.min = Math.min(-3000, Math.floor(initialNorth - 2000));
        sliderNorth.max = Math.max(3000, Math.ceil(initialNorth + 2000));
        sliderEast.min = Math.min(-3000, Math.floor(initialEast - 2000));
        sliderEast.max = Math.max(3000, Math.ceil(initialEast + 2000));

        // Leaflet Map Init
        const map = L.map('map', {
            zoomControl: true,
            attributionControl: false
        }).setView([originLat, originLon], 16);

        // Google Satellite Tiles (maxZoom 21)
        L.tileLayer('https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}', {
            maxZoom: 21,
            maxNativeZoom: 20
        }).addTo(map);

        // Origin Crosshair Marker
        const originMarker = L.circleMarker([originLat, originLon], {
            radius: 7,
            color: '#00ffff',
            weight: 2,
            fillColor: '#00ffff',
            fillOpacity: 0.8
        }).addTo(map);
        originMarker.bindTooltip(`Origin Pivot: ${originLat.toFixed(6)}, ${originLon.toFixed(6)}`);

        // Setup Layers
        function getFeatureCategory(props) {
            const type = (props.type || "").toLowerCase();
            const name = (props.name || "").toLowerCase();
            if (type === 'legal_limit' || type === 'boundary' || name.includes('boundary')) {
                return (props.side === 'left' || name.includes('left')) ? 'left' : 'right';
            } else if (type === 'whiteline' || name.includes('white line')) {
                return 'whiteline';
            } else if (type === 'physical_tarmac' || name.includes('tarmac')) {
                return 'tarmac';
            } else if (type === 'centerline' || name.includes('center')) {
                return 'center';
            }
            return 'other';
        }

        function getFeatureStyle(category) {
            if (category === 'left') return { color: "#ff2200", weight: 3, opacity: 1.0 };
            if (category === 'right') return { color: "#ff7700", weight: 3, opacity: 1.0 };
            if (category === 'center') return { color: "#ffffff", weight: 1.8, opacity: 0.7, dashArray: "10, 8" };
            if (category === 'whiteline') return { color: "#ffffff", weight: 2.2, opacity: 1.0 };
            if (category === 'tarmac') return { color: "#00f0ff", weight: 3, opacity: 1.0 };
            return { color: "#0064ff", weight: 2, opacity: 0.8 };
        }

        const layerItems = rawFeatures.map(feat => {
            const cat = getFeatureCategory(feat.properties);
            const style = getFeatureStyle(cat);
            const poly = L.polyline([], style).addTo(map);
            return {
                category: cat,
                coords: feat.coords,
                polyline: poly,
                visible: true
            };
        });

        // Fast Coordinate Reprojection (Assetto Corsa -> GPS)
        let updatePending = false;
        function scheduleUpdate() {
            if (!updatePending) {
                updatePending = true;
                requestAnimationFrame(updateTrackLayers);
            }
        }

        function formatNum(v) {
            const s = v.toFixed(2);
            return s.endsWith('.00') ? v.toFixed(1) : s;
        }

        function updateTrackLayers() {
            updatePending = false;
            const N = parseFloat(inputNorth.value) || 0;
            const E = parseFloat(inputEast.value) || 0;
            const A = parseFloat(inputAngle.value) || 0;

            const effAngle = A - originHeading;
            const rad = effAngle * Math.PI / 180.0;
            const cosA = Math.cos(rad);
            const sinA = Math.sin(rad);

            for (let j = 0; j < layerItems.length; j++) {
                const item = layerItems[j];
                if (!item.visible) continue;
                const coords = item.coords;
                const len = coords.length;
                const latLngs = new Array(len);

                for (let i = 0; i < len; i++) {
                    const x = coords[i][0];
                    const z = coords[i][1];
                    let east = x;
                    let north = -z;
                    if (effAngle !== 0) {
                        const east_rot = east * cosA - north * sinA;
                        const north_rot = east * sinA + north * cosA;
                        east = east_rot;
                        north = north_rot;
                    }
                    east += E;
                    north += N;
                    const lat = originLat + (north / mPerDegLat);
                    const lon = originLon + (east / mPerDegLon);
                    latLngs[i] = [lat, lon];
                }
                item.polyline.setLatLngs(latLngs);
            }

            // Update snippet text
            const snippet = `track_data.Correction(north_m=${formatNum(N)}, east_m=${formatNum(E)}, angle_deg=${formatNum(A)})`;
            codeSnippet.textContent = snippet;
        }

        // Sync inputs and sliders
        function syncInputs(val, inputEl, sliderEl) {
            inputEl.value = val;
            if (val < parseFloat(sliderEl.min)) sliderEl.min = Math.floor(val - 500);
            if (val > parseFloat(sliderEl.max)) sliderEl.max = Math.ceil(val + 500);
            sliderEl.value = val;
            scheduleUpdate();
        }

        inputNorth.addEventListener('input', e => syncInputs(parseFloat(e.target.value) || 0, inputNorth, sliderNorth));
        sliderNorth.addEventListener('input', e => syncInputs(parseFloat(e.target.value) || 0, inputNorth, sliderNorth));

        inputEast.addEventListener('input', e => syncInputs(parseFloat(e.target.value) || 0, inputEast, sliderEast));
        sliderEast.addEventListener('input', e => syncInputs(parseFloat(e.target.value) || 0, inputEast, sliderEast));

        inputAngle.addEventListener('input', e => syncInputs(parseFloat(e.target.value) || 0, inputAngle, sliderAngle));
        sliderAngle.addEventListener('input', e => syncInputs(parseFloat(e.target.value) || 0, inputAngle, sliderAngle));

        function nudgeNorth(delta) {
            const cur = parseFloat(inputNorth.value) || 0;
            syncInputs(Math.round((cur + delta) * 10) / 10, inputNorth, sliderNorth);
        }
        function nudgeEast(delta) {
            const cur = parseFloat(inputEast.value) || 0;
            syncInputs(Math.round((cur + delta) * 10) / 10, inputEast, sliderEast);
        }
        function nudgeAngle(delta) {
            const cur = parseFloat(inputAngle.value) || 0;
            syncInputs(Math.round((cur + delta) * 100) / 100, inputAngle, sliderAngle);
        }

        function resetValues() {
            syncInputs(initialNorth, inputNorth, sliderNorth);
            syncInputs(initialEast, inputEast, sliderEast);
            syncInputs(initialAngle, inputAngle, sliderAngle);
        }

        // Mode Switching & Map Dragging of Track
        let dragModeActive = false;
        let isDraggingTrack = false;
        let dragStartLatLng = null;
        let dragStartNorth = 0;
        let dragStartEast = 0;

        const btnPan = document.getElementById('btn-mode-pan');
        const btnDrag = document.getElementById('btn-mode-drag');

        function setDragMode(active) {
            dragModeActive = active;
            if (dragModeActive) {
                btnDrag.classList.add('active');
                btnPan.classList.remove('active');
                map.dragging.disable();
                map.getContainer().style.cursor = 'crosshair';
            } else {
                btnPan.classList.add('active');
                btnDrag.classList.remove('active');
                map.dragging.enable();
                map.getContainer().style.cursor = '';
            }
        }
        btnPan.addEventListener('click', () => setDragMode(false));
        btnDrag.addEventListener('click', () => setDragMode(true));

        map.on('mousedown', function(e) {
            if (dragModeActive || e.originalEvent.shiftKey || e.originalEvent.ctrlKey) {
                isDraggingTrack = true;
                dragStartLatLng = e.latlng;
                dragStartNorth = parseFloat(inputNorth.value) || 0;
                dragStartEast = parseFloat(inputEast.value) || 0;
                map.dragging.disable();
            }
        });

        map.on('mousemove', function(e) {
            if (isDraggingTrack && dragStartLatLng) {
                const dLat = e.latlng.lat - dragStartLatLng.lat;
                const dLon = e.latlng.lng - dragStartLatLng.lng;
                const dNorth = dLat * mPerDegLat;
                const dEast = dLon * mPerDegLon;
                const newN = Math.round((dragStartNorth + dNorth) * 10) / 10;
                const newE = Math.round((dragStartEast + dEast) * 10) / 10;
                syncInputs(newN, inputNorth, sliderNorth);
                syncInputs(newE, inputEast, sliderEast);
            }
        });

        function endTrackDrag() {
            if (isDraggingTrack) {
                isDraggingTrack = false;
                dragStartLatLng = null;
                if (!dragModeActive) {
                    map.dragging.enable();
                }
            }
        }
        map.on('mouseup', endTrackDrag);
        window.addEventListener('mouseup', endTrackDrag);

        // Keyboard Shortcuts
        window.addEventListener('keydown', function(e) {
            if (['INPUT', 'TEXTAREA'].includes(document.activeElement.tagName)) {
                if (e.key === 'Enter') document.activeElement.blur();
                return;
            }

            let mult = 1.0;
            if (e.shiftKey) mult = 10.0;
            if (e.altKey) mult = 0.1;

            let handled = false;
            if (e.key === 'ArrowUp') { nudgeNorth(1.0 * mult); handled = true; }
            else if (e.key === 'ArrowDown') { nudgeNorth(-1.0 * mult); handled = true; }
            else if (e.key === 'ArrowRight') { nudgeEast(1.0 * mult); handled = true; }
            else if (e.key === 'ArrowLeft') { nudgeEast(-1.0 * mult); handled = true; }
            else if (e.key === 'e' || e.key === 'E' || e.key === ']') { nudgeAngle(0.1 * mult); handled = true; }
            else if (e.key === 'q' || e.key === 'Q' || e.key === '[') { nudgeAngle(-0.1 * mult); handled = true; }
            else if (e.key === 'd' || e.key === 'D') { setDragMode(!dragModeActive); handled = true; }
            else if (e.key === 'p' || e.key === 'P') { setDragMode(false); handled = true; }
            else if (e.key === 'r' || e.key === 'R') { resetValues(); handled = true; }

            if (handled) e.preventDefault();
        });

        // Layer Toggles
        function setupLayerToggle(chkId, cat) {
            const el = document.getElementById(chkId);
            if (!el) return;
            el.addEventListener('change', e => {
                const checked = e.target.checked;
                layerItems.forEach(item => {
                    if (item.category === cat) {
                        item.visible = checked;
                        if (!checked && map.hasLayer(item.polyline)) map.removeLayer(item.polyline);
                        else if (checked && !map.hasLayer(item.polyline)) map.addLayer(item.polyline);
                    }
                });
                scheduleUpdate();
            });
        }
        setupLayerToggle('chk-left', 'left');
        setupLayerToggle('chk-right', 'right');
        setupLayerToggle('chk-center', 'center');
        setupLayerToggle('chk-white', 'whiteline');
        setupLayerToggle('chk-tarmac', 'tarmac');

        const chkOrigin = document.getElementById('chk-origin');
        if (chkOrigin) {
            chkOrigin.addEventListener('change', e => {
                if (e.target.checked) originMarker.addTo(map);
                else map.removeLayer(originMarker);
            });
        }

        // Copy Snippet Button
        btnCopy.addEventListener('click', function() {
            const text = codeSnippet.textContent;
            navigator.clipboard.writeText(text).then(() => {
                const prev = btnCopy.textContent;
                btnCopy.textContent = '✓ Copied to Clipboard!';
                btnCopy.style.background = '#16a34a';
                setTimeout(() => {
                    btnCopy.textContent = prev;
                    btnCopy.style.background = '';
                }, 1500);
            }).catch(err => {
                prompt('Copy correction snippet:', text);
            });
        });

        // Make HUD Draggable
        const panel = document.getElementById('tuner-panel');
        const header = document.getElementById('panel-header');
        let pX = 0, pY = 0, pStartX = 0, pStartY = 0;
        header.onmousedown = function(e) {
            if (e.target.tagName === 'BUTTON') return;
            e.preventDefault();
            pStartX = e.clientX;
            pStartY = e.clientY;
            document.onmouseup = function() {
                document.onmouseup = null;
                document.onmousemove = null;
            };
            document.onmousemove = function(e) {
                pX = pStartX - e.clientX;
                pY = pStartY - e.clientY;
                pStartX = e.clientX;
                pStartY = e.clientY;
                panel.style.top = (panel.offsetTop - pY) + "px";
                panel.style.left = (panel.offsetLeft - pX) + "px";
                panel.style.right = "auto";
            };
        };

        // Collapse / Expand
        const btnToggle = document.getElementById('btn-toggle-panel');
        const panelBody = document.getElementById('panel-body');
        btnToggle.addEventListener('click', () => {
            if (panelBody.style.display === 'none') {
                panelBody.style.display = 'flex';
                btnToggle.textContent = '_';
            } else {
                panelBody.style.display = 'none';
                btnToggle.textContent = '□';
            }
        });

        // Initial Layout & Map Fit
        updateTrackLayers();
        const activePolys = layerItems.map(l => l.polyline);
        if (activePolys.length > 0) {
            const group = L.featureGroup(activePolys);
            const bounds = group.getBounds();
            if (bounds.isValid()) {
                map.fitBounds(bounds, { padding: [25, 25] });
            } else {
                map.setView([originLat, originLon], 16);
            }
        } else {
            map.setView([originLat, originLon], 16);
        }
    </script>
</body>
</html>'''

    extra_checkboxes = ""
    if has_whitelines:
        extra_checkboxes += '<label class="layer-item"><input type="checkbox" id="chk-white" checked /><span class="swatch" style="background:#ffffff;"></span> White Lines</label>\n'
    if has_tarmac:
        extra_checkboxes += '<label class="layer-item"><input type="checkbox" id="chk-tarmac" checked /><span class="swatch" style="background:#00f0ff;"></span> Tarmac Meshes</label>\n'

    html = template.replace('__TRACK_SLUG__', track_slug)
    html = html.replace('__ORIGIN_LAT__', f"{origin.lat:.6f}")
    html = html.replace('__ORIGIN_LON__', f"{origin.lon:.6f}")
    html = html.replace('__ORIGIN_HEADING__', f"{origin.heading:.2f}")
    html = html.replace('__INITIAL_NORTH__', f"{origin.correction.north_m:.1f}")
    html = html.replace('__INITIAL_EAST__', f"{origin.correction.east_m:.1f}")
    html = html.replace('__INITIAL_ANGLE__', f"{origin.correction.angle_deg:.2f}")
    html = html.replace('__M_PER_DEG_LAT__', str(m_per_deg_lat))
    html = html.replace('__M_PER_DEG_LON__', str(m_per_deg_lon))
    html = html.replace('__EXTRA_LAYER_CHECKBOXES__', extra_checkboxes)
    html = html.replace('__RAW_FEATURES_JSON__', json.dumps(features))

    return html


def launch_interactive_tuner(
    track_name: str,
    kspkg_path: str | None = None,
    skip_extract: bool = False,
    output_path: str | None = None,
    include_meshes: bool = False,
    tarmac_meshes: str | None = None
) -> str:
    """
    Re-extracts track (unless skipped), loads geometry, and creates an interactive in-browser tuner.
    """
    slug = clean_slug(track_name)

    # 1. Re-extract from kspkg if requested (exact same behavior as current script)
    if not skip_extract:
        reprocess_ace_track(
            slug,
            kspkg_path=kspkg_path,
            include_meshes=include_meshes,
            tarmac_meshes=tarmac_meshes
        )

    # 2. Locate GeoJSON
    geojson_path, resolved_slug = locate_geojson(track_name)
    print(f"Loading track geometry from: {geojson_path}")
    with open(geojson_path, 'r', encoding='utf-8') as f:
        geojson_data = json.load(f)

    # 3. Retrieve track origin
    origin = assetto_corsa.get_track_origin(resolved_slug)

    # 4. Invert coordinates to Cartesian (x, z) relative to origin
    features = invert_geojson_to_cartesian(geojson_data, origin)
    m_per_deg_lat, m_per_deg_lon = track_data.wgs84_meters_per_degree(origin.lat)

    # 5. Generate interactive HTML
    html_content = generate_tuner_html(
        track_slug=resolved_slug,
        origin=origin,
        features=features,
        m_per_deg_lat=m_per_deg_lat,
        m_per_deg_lon=m_per_deg_lon
    )

    if not output_path or not output_path.endswith('.html'):
        output_html = os.path.abspath(f'{resolved_slug}_tune.html')
    else:
        output_html = os.path.abspath(output_path)

    with open(output_html, 'w', encoding='utf-8') as f:
        f.write(html_content)

    print("\n" + "=" * 72)
    print(f"🏁 ACE Interactive Alignment Tuner")
    print(f"Track:  {resolved_slug}")
    print(f"Origin: ({origin.lat:.6f}, {origin.lon:.6f}), heading: {origin.heading}°")
    print(f"Current: track_data.Correction(north_m={origin.correction.north_m}, east_m={origin.correction.east_m}, angle_deg={origin.correction.angle_deg})")
    print("-" * 72)
    print(f"Interactive tuner HTML generated and opened in browser:")
    print(f"file://{output_html}")
    print("=" * 72 + "\n")

    try:
        webbrowser.open(f'file://{output_html}')
    except Exception as e:
        print(f"Notice: Could not automatically open browser ({e}). Please open the file URL above.")

    return output_html


async def render_ace_track_async(
    track_name: str,
    kspkg_path: str | None = None,
    skip_extract: bool = False,
    output_path: str | None = None,
    image_size: int = IMAGE_SIZE,
    include_meshes: bool = False,
    tarmac_meshes: str | None = None
) -> str:
    """
    Renders ACE track boundaries using Playwright (for static screenshot capture).
    """
    # Lazy import playwright only when screenshot is requested
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("Playwright is required for headless screenshots. Run in project venv or use interactive mode.")
        raise

    slug = clean_slug(track_name)

    # 1. Re-extract from kspkg if requested
    if not skip_extract:
        reprocess_ace_track(
            slug,
            kspkg_path=kspkg_path,
            include_meshes=include_meshes,
            tarmac_meshes=tarmac_meshes
        )

    # 2. Locate GeoJSON
    geojson_path, slug = locate_geojson(track_name)
    print(f"Loading track geometry from: {geojson_path}")
    with open(geojson_path, 'r', encoding='utf-8') as f:
        geojson_data = json.load(f)

    # Calculate track bounds and aspect ratio
    all_coords = []
    has_extra_layers = False
    for feat in geojson_data.get('features', []):
        props = feat.get('properties', {})
        ftype = (props.get('type') or '').lower()
        if ftype in ('whiteline', 'physical_tarmac'):
            has_extra_layers = True
        geom = feat.get('geometry', {})
        gtype = geom.get('type')
        if gtype == 'LineString':
            all_coords.extend(geom.get('coordinates', []))
        elif gtype == 'Polygon':
            for ring in geom.get('coordinates', []):
                all_coords.extend(ring)

    if all_coords:
        lats = [c[1] for c in all_coords]
        lons = [c[0] for c in all_coords]
        min_lat, max_lat = min(lats), max(lats)
        min_lon, max_lon = min(lons), max(lons)
        lat_mid = (min_lat + max_lat) / 2
        d_lat_m = max((max_lat - min_lat) * 111132.0, 1.0)
        d_lon_m = max((max_lon - min_lon) * (111412.0 * math.cos(math.radians(lat_mid))), 1.0)

        if d_lon_m >= d_lat_m:
            vp_width = image_size
            vp_height = max(1024, int(round(image_size * (d_lat_m / d_lon_m))))
        else:
            vp_height = image_size
            vp_width = max(1024, int(round(image_size * (d_lon_m / d_lat_m))))
    else:
        vp_width, vp_height = image_size, image_size

    print(f"Rendering image at {vp_width}x{vp_height} viewport resolution with minimal margins...")

    legend_extra_html = ""
    if has_extra_layers:
        legend_extra_html = '''
            <div class="legend-item"><span class="legend-swatch" style="background: #ffffff; border: 1px solid #888;"></span> White Lines (whiteline_*.mesh)</div>
            <div class="legend-item"><span class="legend-swatch" style="background: #00f0ff; height: 10px; border: 1px solid #00c8ff;"></span> Physical Tarmac Meshes</div>
        '''

    html_template = '''<!DOCTYPE html>
    <html>
    <head>
        <title>ACE Track Render</title>
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
        <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
        <style>
            body, html, #map { margin: 0; padding: 0; width: 100%; height: 100%; background: black; }
            .leaflet-control-attribution { display: none !important; }
            .legend-box {
                position: absolute;
                top: 25px;
                right: 25px;
                z-index: 9999;
                background: rgba(10, 14, 20, 0.88);
                color: #ffffff;
                padding: 18px 24px;
                border-radius: 10px;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
                font-size: 22px;
                line-height: 1.6;
                box-shadow: 0 4px 20px rgba(0,0,0,0.8);
            }
            .legend-title {
                font-weight: 700;
                font-size: 26px;
                margin-bottom: 12px;
                border-bottom: 1px solid rgba(255, 255, 255, 0.25);
                padding-bottom: 6px;
                letter-spacing: 0.5px;
            }
            .legend-item {
                margin-bottom: 6px;
                display: flex;
                align-items: center;
            }
            .legend-swatch {
                display: inline-block;
                width: 32px;
                height: 6px;
                margin-right: 14px;
                border-radius: 2px;
            }
        </style>
    </head>
    <body>
        <div id="map"></div>
        <div class="legend-box">
            <div class="legend-title">ACE Track Layers</div>
            <div class="legend-item"><span class="legend-swatch" style="background: #ff2200;"></span> Left Boundary (AI Corridor)</div>
            <div class="legend-item"><span class="legend-swatch" style="background: #ff7700;"></span> Right Boundary (AI Corridor)</div>
            ''' + legend_extra_html + '''
            <div class="legend-item"><span class="legend-swatch" style="background: transparent; border-top: 3px dashed #ffffff; height: 0;"></span> Centerline Spline</div>
        </div>
        <script>
            const geojsonData = {geojson_data};

            const map = L.map('map', {
                zoomControl: false,
                attributionControl: false
            });

            L.tileLayer('https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}', {
                maxZoom: 20
            }).addTo(map);

            const trackLayer = L.geoJSON(geojsonData, {
                style: function(feature) {
                    const props = feature.properties || {};
                    const type = (props.type || "").toLowerCase();
                    const name = (props.name || "").toLowerCase();
                    let color = "#0064ff";
                    let weight = 2;
                    let opacity = 1.0;
                    let dashArray = null;

                    if (type === 'legal_limit' || type === 'boundary' || name.includes('boundary')) {
                        color = (props.side === 'left' || name.includes('left')) ? "#ff2200" : "#ff7700";
                        weight = 3;
                        opacity = 1.0;
                    } else if (type === 'whiteline' || name.includes('white line')) {
                        color = "#ffffff";
                        weight = 2;
                        opacity = 1.0;
                    } else if (type === 'physical_tarmac' || name.includes('tarmac')) {
                        color = "#00f0ff";
                        weight = 3;
                        opacity = 1.0;
                    } else if (type === 'centerline' || name.includes('center')) {
                        color = "#ffffff";
                        weight = 1.5;
                        opacity = 0.6;
                        dashArray = "10, 10";
                    }

                    return {
                        color: color,
                        weight: weight,
                        opacity: opacity,
                        dashArray: dashArray,
                        fillOpacity: 0
                    };
                }
            }).addTo(map);

            map.fitBounds(trackLayer.getBounds(), { padding: [12, 12] });
        </script>
    </body>
    </html>
    '''

    html_content = html_template.replace('{geojson_data}', json.dumps(geojson_data))

    with tempfile.NamedTemporaryFile('w', suffix='.html', delete=False, encoding='utf-8') as f:
        f.write(html_content)
        temp_html_path = f.name

    if not output_path:
        output_path = os.path.abspath(f'{slug}.jpeg')

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={'width': vp_width, 'height': vp_height})

        await page.goto(f'file://{temp_html_path}')
        await page.wait_for_load_state('networkidle')
        await asyncio.sleep(6)

        await page.screenshot(path=output_path, type='jpeg', quality=95)
        await browser.close()

    os.unlink(temp_html_path)
    print(f"Saved track render to {output_path}")
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Render or interactively align ACE track on Google Satellite imagery")
    parser.add_argument("track", help="Track name / slug (e.g. monza, imola, spa)")
    parser.add_argument("--kspkg", default=None, help="Path to content.kspkg (optional)")
    parser.add_argument("--no-extract", action="store_true", help="Skip re-extracting geometry from kspkg")
    parser.add_argument("--out", default=None, help="Output file path (.html for interactive tuner, .jpeg/.png for screenshot)")
    parser.add_argument("--screenshot", action="store_true", help="Capture headless Playwright screenshot instead of opening interactive tuner")
    parser.add_argument("--size", type=int, default=IMAGE_SIZE, help="Image width and height in pixels (for screenshot)")
    parser.add_argument("--include-meshes", action="store_true", help="Include white line and tarmac meshes in GeoJSON")
    parser.add_argument("--tarmac-meshes", default=None, help="Tarmac meshes to extract (e.g. 1-5, 1,2,3 or all)")
    args = parser.parse_args()

    is_screenshot = args.screenshot or (args.out and any(args.out.lower().endswith(ext) for ext in ('.jpg', '.jpeg', '.png')))

    if is_screenshot:
        try:
            asyncio.run(render_ace_track_async(
                track_name=args.track,
                kspkg_path=args.kspkg,
                skip_extract=args.no_extract,
                output_path=args.out,
                image_size=args.size,
                include_meshes=args.include_meshes,
                tarmac_meshes=args.tarmac_meshes
            ))
        except Exception as e:
            print(f"Error rendering screenshot: {e}")
            sys.exit(1)
    else:
        try:
            launch_interactive_tuner(
                track_name=args.track,
                kspkg_path=args.kspkg,
                skip_extract=args.no_extract,
                output_path=args.out,
                include_meshes=args.include_meshes,
                tarmac_meshes=args.tarmac_meshes
            )
        except Exception as e:
            print(f"Error launching interactive tuner: {e}")
            sys.exit(1)


if __name__ == '__main__':
    main()
