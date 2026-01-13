#!/usr/bin/env python3
"""Test script for FPL team import."""

import asyncio
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

from services.dependencies import init_dependencies
from services.fpl_import_service import import_fpl_team

async def test_import():
    """Test importing team 4843814."""
    # Initialize dependencies
    print("Initializing dependencies...")
    init_dependencies()
    print("✓ Dependencies initialized\n")
    
    team_id = 4843814
    print(f"Testing import for team ID: {team_id}\n")
    
    try:
        result = await import_fpl_team(team_id, None)
        
        print(f"✓ Import successful!")
        print(f"  Gameweek used: {result['gameweek']}")
        print(f"  Team name: {result['team_name']}")
        print(f"  Bank: £{result['bank']:.1f}m")
        print(f"  Squad size: {len(result['squad'])}")
        print(f"\n  Players:")
        
        has_dias = False
        for p in sorted(result['squad'], key=lambda x: (x['position'], x['name'])):
            if 'Dias' in p['name'] or 'dias' in p['name'].lower():
                print(f"    *** {p['name']} ({p['position']}) - £{p['price']:.1f}m ***")
                has_dias = True
            else:
                print(f"    {p['name']} ({p['position']}) - £{p['price']:.1f}m")
        
        if has_dias:
            print(f"\n❌ ERROR: Ruben Dias is still in the squad!")
            return False
        else:
            print(f"\n✓ SUCCESS: Ruben Dias is NOT in the squad!")
            return True
            
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = asyncio.run(test_import())
    sys.exit(0 if success else 1)

