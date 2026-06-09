"""
Utility to render track boundaries on top of Google Maps satellite imagery.
Uses Playwright to screenshot a Leaflet map with Google Satellite tiles.

Instructions for devising track corrections:
1. Identify the track alignment issues (offset or rotation) in the rendered JPEG.
2. Update the track's entry in `race_tools/race_tools/rfactor.py` (lat, lon, or Correction values).
3. Run this script: `python3 render_track.py <track_name>`.
   The script will automatically:
   - Find the matching .AIW file in `track_extractor/unpacked/`.
   - Re-run `parse_aiw.py` to regenerate the GeoJSON with your updated corrections.
   - Capture a new satellite render to verify the fix.

./venv/bin/python3 render_track.py "fulbeck"
"""
import os
import sys
import json
import tempfile
import asyncio
import subprocess
from playwright.async_api import async_playwright

# Add project root to sys.path
PROJECT_ROOT = os.environ.get('PROJECT_ROOT')
if not PROJECT_ROOT:
    raise KeyError('PROJECT_ROOT environment variable is not set')
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from race_tools.sanitize import clean_slug  # noqa: E402

# Constants
TRACK_DATA_DIR = os.path.join(PROJECT_ROOT, 'karting/data/tracks')
IMAGE_SIZE = 1280
AIW_UNPACKED_DIR = os.path.join(PROJECT_ROOT, 'karting/track_extractor/unpacked')
PARSE_AIW_SCRIPT = os.path.join(PROJECT_ROOT, 'karting/track_extractor/parse_aiw.py')


def reprocess_track(track_name: str):
    """
    Finds the corresponding .AIW file and re-runs parse_aiw.py to update GeoJSON.
    """
    def normalize(s: str) -> str:
        import re
        return re.sub(r'[^a-z0-9]', '', s.lower())

    norm_input = normalize(track_name)

    aiw_file = None
    if os.path.exists(AIW_UNPACKED_DIR):
        for f in os.listdir(AIW_UNPACKED_DIR):
            if not f.endswith('.AIW'):
                continue
            norm_f = normalize(f.replace('.AIW', '').replace('KSP_', ''))
            if norm_input in norm_f or norm_f in norm_input:
                aiw_file = os.path.join(AIW_UNPACKED_DIR, f)
                break

    if not aiw_file:
        print(f"Note: No matching AIW file found for '{track_name}' in {AIW_UNPACKED_DIR}. Skipping re-processing.")
        return

    print(f'Re-processing track data from {os.path.basename(aiw_file)}...')

    try:
        # We assume the current python has the necessary dependencies (pandas, etc.)
        subprocess.run([sys.executable, PARSE_AIW_SCRIPT, aiw_file], check=True)
    except subprocess.CalledProcessError as e:
        print(f'Error during re-processing: {e}')
        sys.exit(1)


async def render_track_async(track_name: str) -> str:
    """
    Renders the track boundaries using Playwright.
    """
    # 1. Re-process AIW if possible
    reprocess_track(track_name)

    # 2. Find and load GeoJSON
    slug = clean_slug(track_name)
    geojson_path = os.path.join(TRACK_DATA_DIR, f'{slug}.geojson')

    if not os.path.exists(geojson_path):
        found = False
        for f in os.listdir(TRACK_DATA_DIR):
            if slug in f and f.endswith('.geojson'):
                geojson_path = os.path.join(TRACK_DATA_DIR, f)
                slug = f.replace('.geojson', '')
                found = True
                break
        if not found:
            raise FileNotFoundError(f'Could not find GeoJSON for track: {track_name}')

    with open(geojson_path, 'r') as f:
        geojson_data = json.load(f)

    # HTML Template for Leaflet
    html_template = '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Track Render</title>
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
                        opacity = 0.5;
                    }}

                    return {{
                        color: color,
                        weight: weight,
                        opacity: opacity,
                        fillOpacity: 0
                    }};
                }}
            }}).addTo(map);

            map.fitBounds(trackLayer.getBounds(), {{ padding: [50, 50] }});
        </script>
    </body>
    </html>
    '''

    # Create temporary HTML
    html_content = html_template.format(geojson_data=json.dumps(geojson_data))

    with tempfile.NamedTemporaryFile('w', suffix='.html', delete=False) as f:
        f.write(html_content)
        temp_html_path = f.name

    output_path = os.path.abspath(f'{slug}.jpeg')

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={'width': IMAGE_SIZE, 'height': IMAGE_SIZE})

        # Load the local HTML file
        await page.goto(f'file://{temp_html_path}')

        # Wait for tiles to load
        await page.wait_for_load_state('networkidle')
        await asyncio.sleep(2)  # Extra buffer for tile rendering

        await page.screenshot(path=output_path, type='jpeg', quality=95)
        await browser.close()

    os.unlink(temp_html_path)
    print(f'Saved track render to {output_path}')
    return output_path


def main():
    if len(sys.argv) < 2:
        print('Usage: python3 render_track.py <track_name>')
        sys.exit(1)

    track_input = sys.argv[1]
    try:
        asyncio.run(render_track_async(track_input))
    except Exception as e:
        print(f'Error: {e}')
        raise


if __name__ == '__main__':
    main()
