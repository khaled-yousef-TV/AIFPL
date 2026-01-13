#!/usr/bin/env python3
"""Check alternative ways to get current team state."""

import requests
import json

team_id = 4843814

# Try different endpoints that might show current/pending state
endpoints = [
    # Maybe there's a way to get "latest" or "current" picks
    f'/entry/{team_id}/picks/',
    f'/entry/{team_id}/event/current/picks/',
    f'/entry/{team_id}/event/latest/picks/',
    f'/entry/{team_id}/event/next/picks/',
    f'/entry/{team_id}/event/0/picks/',  # Sometimes 0 means current
    
    # Check if entry has team info
    f'/entry/{team_id}/',
    
    # Check history for current team
    f'/entry/{team_id}/history/',
]

base_url = 'https://fantasy.premierleague.com/api'

print("Trying alternative endpoints:\n")

for endpoint in endpoints:
    url = base_url + endpoint
    try:
        response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
        print(f"{endpoint}:")
        print(f"  Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, dict):
                # Check for useful fields
                if 'picks' in data:
                    print(f"  ✓ Has picks!")
                    picks = data['picks']
                    print(f"    {len(picks)} players")
                    # Check for Ruben Dias
                    has_ruben = any(p.get('element') == 408 for p in picks)
                    print(f"    Ruben Dias (408): {'YES' if has_ruben else 'NO'}")
                elif 'current' in data:
                    print(f"  Has 'current' field")
                    print(f"  Keys: {list(data.keys())[:10]}")
                else:
                    print(f"  Keys: {list(data.keys())[:15]}")
        print()
    except Exception as e:
        print(f"{endpoint}: Error - {e}\n")

# Also check if we can get transfers with a filter or different endpoint
print("\nChecking transfers endpoint variations:")
transfer_endpoints = [
    f'/entry/{team_id}/transfers/',
    f'/entry/{team_id}/transfers?event=22',
    f'/entry/{team_id}/transfers?future=true',
    f'/entry/{team_id}/transfers?pending=true',
]

for endpoint in transfer_endpoints:
    url = base_url + endpoint
    try:
        response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
        if response.status_code == 200:
            transfers = response.json()
            gw22_transfers = [t for t in transfers if t.get('event') == 22]
            if gw22_transfers:
                print(f"{endpoint}: Found {len(gw22_transfers)} GW22 transfers!")
                for t in gw22_transfers:
                    print(f"  {t.get('time')}: {t.get('element_out')} → {t.get('element_in')}")
            else:
                print(f"{endpoint}: No GW22 transfers")
    except:
        pass

