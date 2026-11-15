from utils.extract_ace_track import (
    KNOWN_TRACK_METADATA,
    format_layout_title,
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
    assert get_ace_layout_name('nurburgring', 'nordschleife_btg') == 'Nordschleife BTG'
    assert get_ace_layout_name('nurburgring', 'nordschleife_touristenfahrten') == 'Touristenfahrten'
    assert get_ace_layout_name('nurburgring', 'sprint') == 'Sprint'

    # Single-layout tracks with None layout resolve to 'GP'
    assert get_ace_layout_name('monza', None) == 'GP'
    assert get_ace_layout_name('imola', None) == 'GP'
    assert get_ace_layout_name('spa', None) == 'GP'

    # Multi-layout track with None layout returns None
    assert get_ace_layout_name('fuji', None) is None


def test_alias_construction_and_matching():
    # Fuji shortcut: track_name is 'Fuji Speedway GP Circuit Shortcut', alias is 'Fuji Speedway GP Short'
    track_meta = KNOWN_TRACK_METADATA['fuji']
    layout = 'gp_circuit_shortcut'
    ace_track_name = track_meta.get('ace_track_name', track_meta['track_name'])
    ace_layout = get_ace_layout_name('fuji', layout)
    ace_alias = f'{ace_track_name} {ace_layout}'.strip()
    track_title = f"{track_meta['track_name']} {format_layout_title(layout)}"

    assert ace_alias == 'Fuji Speedway GP Short'
    assert track_title == 'Fuji Speedway GP Circuit Shortcut'

    aliases = []
    if ace_alias.strip().lower() != track_title.strip().lower():
        aliases.append(ace_alias)

    assert aliases == ['Fuji Speedway GP Short']

    # Single-layout track (Monza): track_name is 'Monza', ACE alias is 'Monza GP'
    monza_meta = KNOWN_TRACK_METADATA['monza']
    monza_ace_name = monza_meta.get('ace_track_name', monza_meta['track_name'])
    monza_layout = get_ace_layout_name('monza', None)
    monza_alias = f'{monza_ace_name} {monza_layout}'.strip()
    monza_title = monza_meta['track_name']

    monza_aliases = []
    if monza_alias.strip().lower() != monza_title.strip().lower():
        monza_aliases.append(monza_alias)

    assert monza_aliases == ['Monza GP']

    # Spa: track_name is 'Spa-Francorchamps', ACE alias is 'Circuit de Spa Francorchamps GP'
    spa_meta = KNOWN_TRACK_METADATA['spa']
    spa_ace_name = spa_meta.get('ace_track_name', spa_meta['track_name'])
    spa_layout = get_ace_layout_name('spa', None)
    spa_alias = f'{spa_ace_name} {spa_layout}'.strip()
    assert spa_alias == 'Circuit de Spa Francorchamps GP'

    # GP variant (e.g. Brands Hatch GP or COTA GP): drop GP from track_name, keep it in alias
    bh_meta = KNOWN_TRACK_METADATA['brands_hatch']
    bh_ace_name = bh_meta.get('ace_track_name', bh_meta['track_name'])
    bh_layout = get_ace_layout_name('brands_hatch', 'gp')
    bh_alias = f'{bh_ace_name} {bh_layout}'.strip()
    bh_title = bh_meta['track_name'] if bh_layout == 'GP' else f"{bh_meta['track_name']} {format_layout_title('gp')}"

    bh_aliases = []
    if bh_alias.strip().lower() != bh_title.strip().lower():
        bh_aliases.append(bh_alias)

    assert bh_title == 'Brands Hatch'
    assert bh_aliases == ['Brands Hatch GP']

    # Non-GP variant where alias matches track_name (e.g. Brands Hatch Indy): do not add alias
    indy_layout = get_ace_layout_name('brands_hatch', 'indy')
    indy_alias = f'{bh_ace_name} {indy_layout}'.strip()
    indy_title = f"{bh_meta['track_name']} {format_layout_title('indy')}"

    indy_aliases = []
    if indy_alias.strip().lower() != indy_title.strip().lower():
        indy_aliases.append(indy_alias)

    assert indy_title == 'Brands Hatch Indy'
    assert indy_aliases == []
