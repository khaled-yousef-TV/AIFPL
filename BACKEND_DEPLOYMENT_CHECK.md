# Backend Deployment Issue - 404 Errors

## Problem
Getting 404 errors when trying to save squads:
- `https://api.fplai.nl/api/saved-squads` → 404 Not Found
- `https://api.fplai.nl/api/players/search` → 500 Internal Server Error

## Root Cause
The backend code has the endpoints, but **Render hasn't redeployed** with the latest code that includes the saved-squads endpoints.

## Solution

### Option 1: Trigger Manual Redeploy on Render (Recommended)
1. Go to https://dashboard.render.com
2. Open your AIFPL backend service
3. Click **"Manual Deploy"** → **"Deploy latest commit"**
4. Wait for deployment to complete (2-3 minutes)

### Option 2: Push a Small Change to Trigger Auto-Deploy
If Render is set to auto-deploy on git push, you can:
1. Make a small change (like updating a comment)
2. Commit and push
3. This will trigger Render to redeploy

### Option 3: Check Render Logs
1. Go to Render dashboard → Your service → **Logs**
2. Check for errors during startup
3. Look for database initialization errors

## Verify Endpoints Exist in Code
✅ `/api/saved-squads` (GET) - Line 1939
✅ `/api/saved-squads/{name}` (GET) - Line 1953  
✅ `/api/saved-squads` (POST) - Line 1970
✅ `/api/saved-squads/{name}` (PUT) - Line 1999
✅ `/api/saved-squads/{name}` (DELETE) - Line 2033

All endpoints are correctly defined in the code.

## Quick Test After Redeploy
```bash
# Test health endpoint
curl https://api.fplai.nl/api/health

# Test saved squads endpoint
curl https://api.fplai.nl/api/saved-squads

# Should return: {"squads": []} (empty array if no squads saved yet)
```

## Common Issues on Render Starter Package

1. **Database not initialized**: SQLite database might not exist yet
   - First API call will create it automatically
   - Check Render logs for database creation

2. **Port binding**: Make sure start command uses `$PORT`:
   ```bash
   uvicorn api.main:app --host 0.0.0.0 --port $PORT
   ```

3. **Python version**: Check `runtime.txt` matches Render's Python version

