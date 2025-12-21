# Saved Squads Not Persisting - Issue Analysis

## Problem
User saved a squad in "My Transfers" tab with a name, but it doesn't appear in incognito window. This suggests the save is not actually reaching the server.

## Root Cause Analysis

### ✅ What's Working:
1. **GitHub Actions workflow** correctly sets `VITE_API_BASE=https://api.fplai.nl` for production
2. **Backend API endpoints** exist and are properly implemented:
   - `POST /api/saved-squads` - Create squad
   - `GET /api/saved-squads` - List all squads
   - `PUT /api/saved-squads/{name}` - Update squad
   - `DELETE /api/saved-squads/{name}` - Delete squad
3. **Database models** are correct (`SavedSquad` table in SQLite)

### ❌ Potential Issues:

1. **API Call Failing Silently**
   - The frontend code catches errors but might not show them clearly
   - Check browser console for network errors
   - Verify `https://api.fplai.nl/api/saved-squads` is accessible

2. **Old localStorage Code Still Present**
   - There's leftover code that references `SAVED_KEY` and `persistSavedSquads`
   - This might be causing confusion, but shouldn't prevent server saves

3. **CORS Issues**
   - If the API call is being blocked by CORS, it would fail silently
   - Check if `https://fplai.nl` is in the CORS allowed origins

4. **Backend Database Not Persisting**
   - SQLite database might not be persisting on Render
   - Render free tier might be clearing the database on restart

## Debugging Steps

### 1. Check Browser Console
Open browser DevTools (F12) → Console tab, then try to save a squad. Look for:
- Network errors (red text)
- Failed fetch requests
- CORS errors

### 2. Check Network Tab
Open DevTools → Network tab, then save a squad. Look for:
- Request to `https://api.fplai.nl/api/saved-squads`
- Response status (should be 200)
- Response body (should show success message)

### 3. Test API Directly
```bash
# Test if API is accessible
curl https://api.fplai.nl/api/saved-squads

# Test creating a squad
curl -X POST https://api.fplai.nl/api/saved-squads \
  -H "Content-Type: application/json" \
  -d '{"name": "Test Squad", "squad": {"squad": [], "bank": 0, "freeTransfers": 1}}'
```

### 4. Check Render Logs
1. Go to Render dashboard
2. Open your backend service
3. Check "Logs" tab for errors when saving

### 5. Verify Database Persistence
The SQLite database file (`fpl_agent.db`) needs to persist on Render. Check:
- Is the database file in a persistent volume?
- Or is it being recreated on each restart?

## Most Likely Issue: Database Not Persisting on Render

**Render Free Tier Issue**: SQLite files are stored in the filesystem, which gets wiped on each deploy/restart on Render's free tier.

### Solution Options:

1. **Use Render's Persistent Disk** (if available on your plan)
2. **Switch to PostgreSQL** (Render provides free PostgreSQL)
3. **Use External Database** (e.g., Supabase, Railway PostgreSQL)

## Quick Fix: Add Better Error Handling

The frontend should show clear error messages when saves fail. Currently it might be failing silently.

