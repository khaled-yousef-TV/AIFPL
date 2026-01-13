#!/usr/bin/env python3
"""Check entry endpoint for current team state."""

import requests
import json

team_id = 4843814

# Get entry data
entry_url = f'https://fantasy.premierleague.com/api/entry/{team_id}/'
entry_response = requests.get(entry_url, headers={'User-Agent': 'Mozilla/5.0'})

if entry_response.status_code == 200:
    entry_data = entry_response.json()
    print("Entry data:")
    print(f"  Name: {entry_data.get('name')}")
    print(f"  Current event: {entry_data.get('current_event')}")
    print(f"  Last deadline bank: £{entry_data.get('last_deadline_bank', 0) / 10:.1f}m")
    print(f"  Last deadline value: £{entry_data.get('last_deadline_value', 0) / 10:.1f}m")
    print(f"  Overall rank: {entry_data.get('summary_overall_rank')}")
    print()
    
    # Check if there's a "picks" or "team" field
    if 'picks' in entry_data:
        print("Found 'picks' in entry data!")
    if 'team' in entry_data:
        print("Found 'team' in entry_data!")
    
    # Show all keys
    print("Available keys in entry data:")
    for key in sorted(entry_data.keys()):
        if isinstance(entry_data[key], (dict, list)):
            print(f"  {key}: {type(entry_data[key]).__name__} (length: {len(entry_data[key]) if hasattr(entry_data[key], '__len__') else 'N/A'})")
        else:
            print(f"  {key}: {entry_data[key]}")

