# Saving Mechanisms Summary - AIFPL Repository

## Overview

The AIFPL application uses **two storage mechanisms**:
1. **localStorage** (client-side, browser-only)
2. **Server-side database** (SQLite, stored on backend server)

---

## 📦 **localStorage (Client-Side Only)**

### 1. **Draft Squad Auto-Save**
- **Storage Key**: `fpl_squad_draft_v1`
- **Location**: Browser localStorage
- **What's Saved**:
  - Current squad (player list)
  - Bank (money remaining)
  - Free transfers count
  - Last updated timestamp
- **When**: Auto-saves whenever squad/bank/freeTransfers change
- **Purpose**: Quick draft recovery if page refreshes
- **Scope**: Local to each browser/device
- **API Endpoint**: None (purely local)

**Code Location**: `frontend/src/App.tsx` (lines 180, 218-229, 262-268)

---

## 🗄️ **Server-Side Database (SQLite)**

All server-side storage uses SQLite database (`fpl_agent.db`) with the following tables:

### 1. **Saved Squads (User-Created with Custom Names)**
- **Table**: `saved_squads`
- **Storage**: Server-side database
- **What's Saved**:
  - Custom squad name (user-provided)
  - Full squad data (formation, starting_xi, bench, captain, vice_captain)
  - Bank and free transfers
  - Timestamps (saved_at, updated_at)
- **When**: User manually saves/updates via "My Transfers" tab
- **Purpose**: Named squads that persist across sessions and devices
- **API Endpoints**:
  - `GET /api/saved-squads` - Get all saved squads
  - `GET /api/saved-squads/{name}` - Get specific squad
  - `POST /api/saved-squads` - Create new squad
  - `PUT /api/saved-squads/{name}` - Update existing squad
  - `DELETE /api/saved-squads/{name}` - Delete squad
- **Frontend Location**: "My Transfers" tab → Saved Squads section

**Code Locations**:
- Backend: `backend/database/crud.py` (lines 481-592)
- Backend API: `backend/api/main.py` (lines 1916-2052)
- Frontend: `frontend/src/App.tsx` (lines 186-212, 270-370)

---

### 2. **Selected Teams (Auto-Saved "Team of the Week")**
- **Table**: `selected_teams`
- **Storage**: Server-side database
- **What's Saved**:
  - Gameweek number
  - Full suggested squad data (from AI suggester)
  - Saved timestamp
- **When**: 
  - **Automatically** saved 30 minutes before each gameweek deadline
  - Scheduled job runs via APScheduler
- **Purpose**: Historical record of AI-suggested squads per gameweek
- **API Endpoints**:
  - `GET /api/selected-teams` - Get all selected teams (all gameweeks)
  - `GET /api/selected-teams/{gameweek}` - Get team for specific gameweek
  - `POST /api/selected-teams` - Manually trigger save (usually automatic)
- **Frontend Location**: "Selected Teams" tab
- **Scheduler**: Runs automatically via `save_selected_team_job()` at deadline - 30 minutes

**Code Locations**:
- Backend: `backend/database/crud.py` (lines 366-432)
- Backend API: `backend/api/main.py` (lines 145-364, 1781-1913)
- Scheduler: `backend/api/main.py` (lines 214-270, 329-358)

---

### 3. **Daily Snapshots**
- **Table**: `daily_snapshots`
- **Storage**: Server-side database
- **What's Saved**:
  - Gameweek number
  - Full suggested squad data (current AI suggestion)
  - Saved timestamp
- **When**: 
  - **Automatically** saved every day at midnight (00:00)
  - Scheduled job runs via APScheduler
- **Purpose**: Track how AI suggestions change over time for the same gameweek
- **API Endpoints**: 
  - Used internally by `GET /api/selected-teams/{gameweek}` (returns latest snapshot for current gameweek)
- **Note**: Creates new entry each day (keeps history, but only latest is used)

**Code Locations**:
- Backend: `backend/database/crud.py` (lines 436-477)
- Backend API: `backend/api/main.py` (lines 183-212, 350-358)

---

## 📊 **Summary Table**

| Feature | Storage Type | Table/Key | Auto-Save? | User-Initiated? | Scope |
|---------|-------------|-----------|------------|-----------------|-------|
| **Draft Squad** | localStorage | `fpl_squad_draft_v1` | ✅ Yes | ❌ No | Browser only |
| **Saved Squads** | Server DB | `saved_squads` | ❌ No | ✅ Yes | All devices |
| **Selected Teams** | Server DB | `selected_teams` | ✅ Yes (30min before deadline) | ❌ No | All devices |
| **Daily Snapshots** | Server DB | `daily_snapshots` | ✅ Yes (midnight) | ❌ No | All devices |

---

## 🔄 **Data Flow**

### Draft Squad (localStorage):
```
User edits squad → Auto-saves to localStorage → Persists in browser
```

### Saved Squads (Server):
```
User clicks "Save" → POST /api/saved-squads → SQLite database → Available on all devices
```

### Selected Teams (Server):
```
Scheduler triggers (30min before deadline) → GET suggested squad → POST /api/selected-teams → SQLite database
```

### Daily Snapshots (Server):
```
Scheduler triggers (midnight) → GET suggested squad → save_daily_snapshot() → SQLite database
```

---

## 🗂️ **Database Schema**

### `saved_squads` Table:
- `id` (Integer, Primary Key)
- `name` (String, Unique, Indexed) - Custom name
- `squad_data` (JSON) - Full squad data
- `saved_at` (DateTime)
- `updated_at` (DateTime)

### `selected_teams` Table:
- `id` (Integer, Primary Key)
- `gameweek` (Integer, Unique, Indexed)
- `squad_data` (JSON) - Full squad data
- `saved_at` (DateTime)

### `daily_snapshots` Table:
- `id` (Integer, Primary Key)
- `gameweek` (Integer, Indexed)
- `squad_data` (JSON) - Full squad data
- `saved_at` (DateTime, Indexed)

---

## 🎯 **Key Differences**

1. **localStorage (Draft)**:
   - ✅ Fast, no network calls
   - ✅ Works offline
   - ❌ Browser/device specific
   - ❌ Lost if browser data cleared
   - ❌ Not synced across devices

2. **Server-Side (All Others)**:
   - ✅ Synced across all devices
   - ✅ Persistent (survives browser clears)
   - ✅ Historical tracking
   - ❌ Requires network connection
   - ❌ Slightly slower (database queries)

---

## 📝 **Notes**

- The **draft squad** in localStorage is a **temporary work-in-progress** that auto-saves as you type
- **Saved squads** are **permanent named squads** you create manually
- **Selected teams** are **historical records** of AI suggestions (auto-saved)
- **Daily snapshots** track **how suggestions evolve** over time for the same gameweek

