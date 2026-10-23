#!/usr/bin/env python3
"""
Utility to render Assetto Corsa Evo (ACE) track boundaries on top of Google Maps satellite imagery.
Uses Playwright to screenshot a Leaflet map with Google Satellite tiles.

Instructions for verifying track alignment:
1. Identify track alignment issues (offset or rotation) in the rendered JPEG.
2. Update the track's geographic origin coordinates in KNOWN_TRACK_METADATA within
   `utils/extract_ace_track.py`.
3. Run this script: `python3 utils/render_ace_track.py <track_name>`.
   The script will automatically:
   - Re-extract the track geometry from content.kspkg if available.
   - Regenerate the GeoJSON, JSON, and KML files in the track data directory.
   - Capture a new satellite render to verify the fix.

Example:
  python3 utils/render_ace_track.py "monza"
"""
import os
import sys

# Auto re-exec with venv python if current python lacks playwright driver
_VENV_PY = os.path.abspath(os.path.join(os.path.dirname(__file__), 'venv', 'bin', 'python3'))
if (
    not os.path.exists('/usr/share/nodejs/playwright/cli.js')
    and os.path.exists(_VENV_PY)
    and sys.prefix != os.path.dirname(os.path.dirname(_VENV_PY))
):
    os.execv(_VENV_PY, [_VENV_PY] + sys.argv)

import json
import math
import argparse
import tempfile
import asyncio
from playwright.async_api import async_playwright

# Add project root to sys.path
PROJECT_ROOT = os.environ.get('PROJECT_ROOT')
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from race_tools.sanitize import clean_slug  # noqa: E402
from utils.extract_ace_track import extract_track, get_default_kspkg_path, resolve_path  # noqa: E402

REPO_TRACK_DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data', 'tracks'))
IMAGE_SIZE = 8192


def reprocess_ace_track(track_name: str, kspkg_path: str | None = None) -> None:
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
            extract_track(slug, resolved_kspkg, out_dir)
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
                if any(a.strip().lower() == norm for a in aliases):
                    geojson_path = json_path[:-5] + '.geojson'
                    if os.path.exists(geojson_path):
                        return geojson_path
            except Exception:
                continue
    return None


async def render_ace_track_async(
    track_name: str,
    kspkg_path: str | None = None,
    skip_extract: bool = False,
    output_path: str | None = None,
    image_size: int = IMAGE_SIZE
) -> str:
    """
    Renders ACE track boundaries using Playwright.
    """
    slug = clean_slug(track_name)

    # 1. Re-extract from kspkg if requested
    if not skip_extract:
        reprocess_ace_track(slug, kspkg_path)

    # 2. Locate GeoJSON
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

    print(f"Loading track geometry from: {geojson_path}")
    with open(geojson_path, 'r', encoding='utf-8') as f:
        geojson_data = json.load(f)

    # Calculate track bounds and aspect ratio to minimize margins
    all_coords = []
    for feat in geojson_data.get('features', []):
        geom = feat.get('geometry', {})
        if geom.get('type') == 'LineString':
            all_coords.extend(geom.get('coordinates', []))

    if all_coords:
        lats = [c[1] for c in all_coords]
        lons = [c[0] for c in all_coords]
        min_lat, max_lat = min(lats), max(lats)
        min_lon, max_lon = min(lons), max(lons)
        lat_mid = (min_lat + max_lat) / 2
        d_lat_m = max((max_lat - min_lat) * 111132.0, 1.0)
        d_lon_m = max((max_lon - min_lon) * (111412.0 * math.cos(math.radians(lat_mid))), 1.0)

        # Proportional viewport matching track geometry aspect ratio
        if d_lon_m >= d_lat_m:
            vp_width = image_size
            vp_height = max(1024, int(round(image_size * (d_lat_m / d_lon_m))))
        else:
            vp_height = image_size
            vp_width = max(1024, int(round(image_size * (d_lon_m / d_lat_m))))
    else:
        vp_width, vp_height = image_size, image_size

    print(f"Rendering image at {vp_width}x{vp_height} viewport resolution with minimal margins...")

    # HTML Template for Leaflet
    html_template = '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>ACE Track Render</title>
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
        <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
        <style>
            body, html, #map {{ margin: 0; padding: 0; width: 100%; height: 100%; background: black; }}
            .leaflet-control-attribution {{ display: none !important; }}
        </style>
    </head>
    <body>
        <div id="map"></div>
        <script>
            const geojsonData = {geojson_data};

            const map = L.map('map', {{
                zoomControl: false,
                attributionControl: false
            }});

            // Google Satellite Tiles
            L.tileLayer('https://mt1.google.com/vt/lyrs=s&x={{x}}&y={{y}}&z={{z}}', {{
                maxZoom: 20
            }}).addTo(map);

            const trackLayer = L.geoJSON(geojsonData, {{
                style: function(feature) {{
                    const name = (feature.properties.name || "").toLowerCase();
                    let color = "#0064ff";
                    let weight = 3;
                    let opacity = 1;

                    if (name.includes('left')) color = "#0096ff";
                    if (name.includes('right')) color = "#00c8ff";
                    if (name.includes('center')) {{
                        color = "#ffffff";
                        weight = 1;
                        opacity = 0.6;
                    }}

                    return {{
                        color: color,
                        weight: weight,
                        opacity: opacity,
                        fillOpacity: 0
                    }};
                }}
            }}).addTo(map);

            map.fitBounds(trackLayer.getBounds(), {{ padding: [10, 10] }});
        </script>
    </body>
    </html>
    '''

    html_content = html_template.format(geojson_data=json.dumps(geojson_data))

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
        await asyncio.sleep(6)  # Buffer for high-resolution tile rendering

        await page.screenshot(path=output_path, type='jpeg', quality=95)
        await browser.close()

    os.unlink(temp_html_path)
    print(f"Saved track render to {output_path}")
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Render ACE track on Google Satellite imagery")
    parser.add_argument("track", help="Track name / slug (e.g. monza, imola, spa)")
    parser.add_argument("--kspkg", default=None, help="Path to content.kspkg (optional)")
    parser.add_argument("--no-extract", action="store_true", help="Skip re-extracting geometry from kspkg")
    parser.add_argument("--out", default=None, help="Output image file path (defaults to <slug>.jpeg)")
    parser.add_argument("--size", type=int, default=IMAGE_SIZE, help="Image width and height in pixels")
    args = parser.parse_args()

    try:
        asyncio.run(render_ace_track_async(
            track_name=args.track,
            kspkg_path=args.kspkg,
            skip_extract=args.no_extract,
            output_path=args.out,
            image_size=args.size
        ))
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
