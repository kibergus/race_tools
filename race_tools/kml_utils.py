"""
Utilities for generating KML files for visualization in Google Earth and other GIS tools.
"""
from typing import Sequence


def generate_track_limits_kml(
    center_path: Sequence[Sequence[float]],
    left_path: Sequence[Sequence[float]],
    right_path: Sequence[Sequence[float]],
    name: str = 'Track Layout',
    output_path: str = 'track_limits.kml',
    include_centerline: bool = True
) -> str:
    """
    Generate a KML file with center line and track boundaries.

    Args:
        center_path: List of (lat, lon, alt) or (lat, lon) for the centerline.
        left_path: List of (lat, lon, alt) or (lat, lon) for the left boundary.
        right_path: List of (lat, lon, alt) or (lat, lon) for the right boundary.
        name: Name of the KML document.
        output_path: Path where the KML file will be saved.
        include_centerline: Whether to include the centerline in the KML.

    Returns:
        The path to the generated KML file.
    """

    def get_coords_string(path: Sequence[Sequence[float]]) -> str:
        coord_strings = []
        for p in path:
            lat, lon = p[0], p[1]
            alt = p[2] if len(p) > 2 else 0
            coord_strings.append(f'{lon},{lat},{alt}')
        return '\n'.join(coord_strings)

    center_line_placemark = ''
    if include_centerline:
        center_line_placemark = f'''
    <Placemark>
      <name>Center Line</name>
      <styleUrl>#centerLine</styleUrl>
      <LineString>
        <tessellate>1</tessellate>
        <coordinates>
{get_coords_string(center_path)}
        </coordinates>
      </LineString>
    </Placemark>'''

    kml_content = f'''<?xml version='1.0' encoding='UTF-8'?>
<kml xmlns='http://www.opengis.net/kml/2.2'>
  <Document>
    <name>{name}</name>
    <Style id='centerLine'>
      <LineStyle><color>7f00ffff</color><width>2</width></LineStyle>
    </Style>
    <Style id='leftBoundary'>
      <LineStyle><color>ff0000ff</color><width>3</width></LineStyle>
    </Style>
    <Style id='rightBoundary'>
      <LineStyle><color>ff00ff00</color><width>3</width></LineStyle>
    </Style>
{center_line_placemark}

    <Placemark>
      <name>Left Boundary</name>
      <styleUrl>#leftBoundary</styleUrl>
      <LineString>
        <tessellate>1</tessellate>
        <coordinates>
{get_coords_string(left_path)}
        </coordinates>
      </LineString>
    </Placemark>

    <Placemark>
      <name>Right Boundary</name>
      <styleUrl>#rightBoundary</styleUrl>
      <LineString>
        <tessellate>1</tessellate>
        <coordinates>
{get_coords_string(right_path)}
        </coordinates>
      </LineString>
    </Placemark>
  </Document>
</kml>
'''
    with open(output_path, 'w') as f:
        f.write(kml_content)

    return output_path


def generate_kml(points: Sequence[Sequence[float]], name: str = 'Track Layout', output_path: str = 'track.kml') -> str:
    """
    Generate a simple KML file from a list of points.

    Args:
        points: List of (lat, lon, alt) or (lat, lon).
        name: Name of the KML document.
        output_path: Path where the KML file will be saved.

    Returns:
        The path to the generated KML file.
    """
    kml_header = '''<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>{name}</name>
    <Style id='yellowLineGreenPoly'>
      <LineStyle>
        <color>7f00ffff</color>
        <width>4</width>
      </LineStyle>
      <PolyStyle>
        <color>7f00ff00</color>
      </PolyStyle>
    </Style>
    <Placemark>
      <name>Track Path</name>
      <styleUrl>#yellowLineGreenPoly</styleUrl>
      <LineString>
        <extrude>1</extrude>
        <tessellate>1</tessellate>
        <altitudeMode>clampToGround</altitudeMode>
        <coordinates>
'''.format(name=name)

    kml_footer = '''
        </coordinates>
      </LineString>
    </Placemark>
  </Document>
</kml>
'''

    coord_strings = []
    for p in points:
        lat = p[0]
        lon = p[1]
        alt = p[2] if len(p) > 2 else 0
        coord_strings.append(f'{lon},{lat},{alt}')

    coords_text = '\n'.join(coord_strings)

    with open(output_path, 'w') as f:
        f.write(kml_header)
        f.write(coords_text)
        f.write(kml_footer)

    return output_path
