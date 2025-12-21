# Update Render Environment Variables

## ⚠️ Important: The `.env` file is NOT pushed to GitHub (by design for security)

The `.env` file is in `.gitignore` and should never be committed. You need to manually update the environment variables in Render.

## Steps to Update Render:

1. **Go to your Render Dashboard**: https://dashboard.render.com

2. **Navigate to your AIFPL backend service**

3. **Go to Environment Variables** (in the left sidebar)

4. **Update/Add these variables:**
   - `THE_ODDS_API_KEY` = `5cf47fc2b01e4a1b9253a3db59b55679`
   - `BETTING_ODDS_ENABLED` = `true`
   - `BETTING_ODDS_WEIGHT` = `0.25`

5. **Click "Save Changes"**

6. **Restart the service** (Render will usually auto-restart, but you can manually restart from the dashboard)

## Verify it's working:

After updating, you can check the betting odds status:
```bash
curl https://api.fplai.nl/api/betting-odds-status
```

You should see:
```json
{
  "enabled": true,
  "has_api_key": true,
  "weight": 0.25,
  "api_key_set": true,
  "enabled_env": "true"
}
```

## Local Development:

For local development, the `.env` file is already created at:
- `backend/.env`

This file is ignored by git and will not be pushed to GitHub.

