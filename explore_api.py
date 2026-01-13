#!/usr/bin/env python3
"""Explore FPL API endpoints to find current team state."""

import requests
import json

team_id = 4843814

endpoints_to_try = [
    f'/entry/{team_id}/',
    f'/entry/{team_id}/picks/',  # Maybe there's a picks endpoint without gameweek?
    f'/entry/{team_id}/transfers/',
    f'/entry/{team_id}/history/',
    f'/entry/{team_id}/event/current/picks/',  # Try "current" as gameweek
    f'/entry/{team_id}/event/latest/picks/',   # Try "latest" as gameweek
]

base_url = 'https://fantasy.premierleague.com/api'

print("Exploring FPL API endpoints for current team state:\n")

for endpoint in endpoints_to_try:
    url = base_url + endpoint
    try:
        response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
        print(f"{endpoint}:")
        print(f"  Status: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, dict):
                print(f"  Keys: {list(data.keys())[:10]}")
                # Check if it has picks or team data
                if 'picks' in data:
                    print(f"  ✓ Has 'picks' field with {len(data['picks'])} items")
                if 'team' in data:
                    print(f"  ✓ Has 'team' field")
                if 'squad' in data:
                    print(f"  ✓ Has 'squad' field")
            elif isinstance(data, list):
                print(f"  List with {len(data)} items")
                if len(data) > 0 and isinstance(data[0], dict):
                    print(f"  First item keys: {list(data[0].keys())[:10]}")
        print()
    except Exception as e:
        print(f"{endpoint}: Error - {e}\n")

# Also check if there's a way to get the highest available gameweek
print("\nChecking for highest available gameweek:")
for gw in range(25, 20, -1):
    url = f'{base_url}/entry/{team_id}/event/{gw}/picks/'
    response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
    if response.status_code == 200:
        print(f"  GW{gw}: Available ✓")
        data = response.json()
        if 'picks' in data:
            print(f"    Has {len(data['picks'])} picks")
        break
    elif response.status_code == 404:
        print(f"  GW{gw}: Not available (404)")
    else:
        print(f"  GW{gw}: Status {response.status_code}")

