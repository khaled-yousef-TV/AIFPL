#!/usr/bin/env python3
"""Check transfer history and current gameweek."""

import requests
from datetime import datetime

team_id = 4843814

# Get current gameweek info
bootstrap_url = 'https://fantasy.premierleague.com/api/bootstrap-static/'
bootstrap = requests.get(bootstrap_url).json()

current_gw = None
next_gw = None
for event in bootstrap['events']:
    if event.get('is_current'):
        current_gw = event
    if event.get('is_next'):
        next_gw = event

print(f"Current gameweek: {current_gw['id']} ({current_gw['name']}) - Deadline: {current_gw['deadline_time']}")
if next_gw:
    print(f"Next gameweek: {next_gw['id']} ({next_gw['name']}) - Deadline: {next_gw['deadline_time']}")
print()

# Get transfer history
transfers_url = f'https://fantasy.premierleague.com/api/entry/{team_id}/transfers/'
transfers_response = requests.get(transfers_url, headers={'User-Agent': 'Mozilla/5.0'})

if transfers_response.status_code == 200:
    transfers = transfers_response.json()
    print(f"Transfer history (showing last 5):")
    players_by_id = {p['id']: p for p in bootstrap['elements']}
    
    for i, transfer in enumerate(transfers[:5]):
        time_str = transfer.get('time', 'Unknown')
        event = transfer.get('event', 'Unknown')
        element_in = transfer.get('element_in')
        element_out = transfer.get('element_out')
        
        player_in = players_by_id.get(element_in, {})
        player_out = players_by_id.get(element_out, {})
        
        in_name = f"{player_in.get('first_name', '')} {player_in.get('second_name', '')}".strip() or f"ID {element_in}"
        out_name = f"{player_out.get('first_name', '')} {player_out.get('second_name', '')}".strip() or f"ID {element_out}"
        
        print(f"  {i+1}. GW{event} ({time_str}): {out_name} → {in_name}")
        
        if element_out == 408 or 'dias' in out_name.lower():
            print(f"     *** This transfer removed Ruben Dias! ***")
else:
    print(f"Could not fetch transfers (status {transfers_response.status_code})")

