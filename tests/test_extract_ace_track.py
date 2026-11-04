from utils.extract_ace_track import (
    KNOWN_TRACK_METADATA,
    get_ace_layout_name,
)


def test_get_ace_layout_name():
    # Fuji layouts
    assert get_ace_layout_name('fuji', 'gp_circuit_shortcut') == 'GP Short'
    assert get_ace_layout_name('fuji', 'gp_circuit') == 'GP'
    assert get_ace_layout_name('fuji', 'gp') == 'GP'
    assert get_ace_layout_name('fuji', 'gp_short') == 'GP Short'

    # Donington layouts
    assert get_ace_layout_name('donington', 'grand_prix') == 'GP'
    assert get_ace_layout_name('donington', 'national') == 'National'

    # Brands Hatch layouts
    assert get_ace_layout_name('brands_hatch', 'gp') == 'GP'
    assert get_ace_layout_name('brands_hatch', 'indy') == 'Indy'

    # Watkins Glen layouts
    assert get_ace_layout_name('watkins_glen', 'gp') == 'GP'
    assert get_ace_layout_name('watkins_glen', 'gp_inner_loop') == 'GP Inner Loop'
    assert get_ace_layout_name('watkins_glen', 'short') == 'Short'
    assert get_ace_layout_name('watkins_glen', 'short_inner_loop') == 'Short Inner Loop'

    # Nurburgring layouts
    assert get_ace_layout_name('nurburgring', 'gp_strecke') == 'GP Strecke'
    assert get_ace_layout_name('nurburgring', '24h') == '24h'
    assert get_ace_layout_name('nurburgring', 'nordschleife') == 'Nordschleife'
    assert get_ace_layout_name('nurburgring', 'sprint') == 'Sprint'

    # None layout
    assert get_ace_layout_name('fuji', None) is None


def test_alias_construction_and_matching():
    # Fuji shortcut: track_name is 'Fuji Speedway Gp Circuit Shortcut', alias is 'Fuji Speedway GP Short'
    track_meta = KNOWN_TRACK_METADATA['fuji']
    layout = 'gp_circuit_shortcut'
    ace_layout = get_ace_layout_name('fuji', layout)
    ace_alias = f"{track_meta['track_name']} {ace_layout}".strip()
    track_title = f"{track_meta['track_name']} {layout.replace('_', ' ').title()}"

    assert ace_alias == 'Fuji Speedway GP Short'
    assert track_title == 'Fuji Speedway Gp Circuit Shortcut'

    aliases = []
    if ace_alias.strip().lower() != track_title.strip().lower():
        aliases.append(ace_alias)

    assert aliases == ['Fuji Speedway GP Short']

    # When alias matches track_name (e.g. Monza): do not add it
    monza_meta = KNOWN_TRACK_METADATA['monza']
    monza_layout = None
    ace_layout = get_ace_layout_name('monza', monza_layout)
    if ace_layout:
        ace_alias = f"{monza_meta['track_name']} {ace_layout}".strip()
    else:
        ace_alias = monza_meta['track_name'].strip()
    monza_title = monza_meta['track_name']

    monza_aliases = []
    if ace_alias.strip().lower() != monza_title.strip().lower():
        monza_aliases.append(ace_alias)

    assert monza_aliases == []
