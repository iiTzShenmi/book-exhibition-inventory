# Setup Guide

## Quick Start

### 1. Environment Configuration

Create a `.env` file in the project root with the following:

```bash
# Production PostgreSQL Database (Render)
DATABASE_URL=postgresql://user:password@host:port/database

# Flask Secret Key (generate a random one for production)
FLASK_SECRET_KEY=your-secret-key-here

# Admin Credentials
ADMIN_USERNAME=admin
ADMIN_PASSWORD=your-secure-password
ADMIN_EMAIL=admin@example.com
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Initialize Database

The database will be automatically initialized when you run the app. For production, ensure `DATABASE_URL` is set correctly.

### 4. Run the Application

**Development (Local SQLite):**
```bash
# Remove DATABASE_URL from .env or leave it empty to use SQLite
flask run
```

**Production (PostgreSQL):**
```bash
# Ensure DATABASE_URL is set in .env
gunicorn app:app
```

## Database Management

### Check Database Health
```bash
python -m database.tools.db_tools check
```

### Sync CSV to Database (Development Only)
```bash
ENABLE_CSV_SYNC=1 python -m database.tools.db_tools sync-csv
```

### Sync Local to Cloud
```bash
# Simple upload
python database/tools/db_sync.py upload

# Full workflow with validation
python database/tools/db_sync.py push --auto-fix
```

## Troubleshooting

### Database Connection Issues

1. **Check DATABASE_URL format:**
   - Must start with `postgresql://` (not `postgres://`)
   - Format: `postgresql://user:password@host:port/database`

2. **Verify connection:**
   ```bash
   python -c "from app import app, db; app.app_context().push(); print('Connected:', db.engine.url)"
   ```

### Common Issues

- **NULL title_id errors:** Run `python -m database.tools.db_tools purge-null`
- **Duplicate titles:** Run `python -m database.tools.db_tools dedupe`
- **Missing metadata:** Use tools in `tools/` directory to fetch covers/authors/topics

## Production Deployment (Render)

1. Set environment variables in Render dashboard:
   - `DATABASE_URL` (automatically provided by Render PostgreSQL)
   - `FLASK_SECRET_KEY`
   - `ADMIN_USERNAME`
   - `ADMIN_PASSWORD`
   - `ADMIN_EMAIL`

2. The `Procfile` is already configured for Gunicorn.

3. Database migrations run automatically on startup.

