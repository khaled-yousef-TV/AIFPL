#!/usr/bin/env python3
"""Check team picks from FPL API directly."""

import requests

team_id = 4843814

# Check multiple gameweeks
for gw in [21, 22, 23, 24]:
    url = f'https://fantasy.premierleague.com/api/entry/{team_id}/event/{gw}/picks/'
    response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
    
    if response.status_code == 200:
        data = response.json()
        picks = data.get('picks', [])
        if picks:
            # Get player names
            bootstrap_url = 'https://fantasy.premierleague.com/api/bootstrap-static/'
            bootstrap = requests.get(bootstrap_url).json()
            players_by_id = {p['id']: p for p in bootstrap['elements']}
            
            print(f'\nGameweek {gw}:')
            has_ruben = False
            for pick in picks:
                player = players_by_id.get(pick['element'])
                if player:
                    name = f"{player['first_name']} {player['second_name']}"
                    web_name = player['web_name']
                    if 'dias' in name.lower() or 'dias' in web_name.lower() or pick['element'] == 75:
                        print(f"  *** {name} ({web_name}) - ID {pick['element']} - Position {pick['position']} ***")
                        has_ruben = True
                    else:
                        print(f"  {name} ({web_name}) - ID {pick['element']}")
            
            if has_ruben:
                print(f"  ❌ Ruben Dias found in GW{gw}!")
            else:
                print(f"  ✓ No Ruben Dias in GW{gw}")
    else:
        print(f'\nGameweek {gw}: Not available (status {response.status_code})')

