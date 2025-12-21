# Deployment Checklist

## Pre-Deployment

### 1. Environment Variables
- [ ] Create `.env` file with:
  - `DATABASE_URL` (PostgreSQL connection string)
  - `FLASK_SECRET_KEY` (random secure key)
  - `ADMIN_USERNAME` (admin username)
  - `ADMIN_PASSWORD` (secure password)
  - `ADMIN_EMAIL` (admin email)

### 2. Database Setup
- [ ] Verify PostgreSQL connection string is correct
- [ ] Test database connection:
  ```bash
  python -c "from app import app, db; app.app_context().push(); print('Connected:', db.engine.url)"
  ```
- [ ] Run database health check:
  ```bash
  python -m database.tools.db_tools check
  ```
- [ ] Fix any issues found (duplicates, NULL values, etc.)

### 3. Code Verification
- [ ] All syntax errors fixed ✅
- [ ] All undefined variables removed ✅
- [ ] Database models are correct ✅
- [ ] No linting errors ✅

### 4. Dependencies
- [ ] Install all dependencies:
  ```bash
  pip install -r requirements.txt
  ```
- [ ] Verify all imports work

## Deployment (Render)

### 1. Environment Variables in Render
Set the following in Render dashboard:
- `DATABASE_URL` (automatically provided by Render PostgreSQL)
- `FLASK_SECRET_KEY`
- `ADMIN_USERNAME`
- `ADMIN_PASSWORD`
- `ADMIN_EMAIL`

### 2. Build & Deploy
- [ ] Push code to repository
- [ ] Render will automatically build and deploy
- [ ] Check build logs for errors

### 3. Post-Deployment Verification
- [ ] Website loads correctly
- [ ] Database connection works
- [ ] Admin login works
- [ ] Can search for books
- [ ] Can add/edit books (admin)
- [ ] Can manage cabinets (admin)
- [ ] Audit logs are working

## Local Development

### 1. Setup
- [ ] Create `.env` file (without `DATABASE_URL` for SQLite)
- [ ] Install dependencies
- [ ] Run `flask run` to start development server

### 2. Testing
- [ ] Test all routes
- [ ] Test admin functions
- [ ] Test search functionality
- [ ] Test database operations

## Troubleshooting

### Database Connection Issues
1. Check `DATABASE_URL` format (must start with `postgresql://`)
2. Verify credentials are correct
3. Check network connectivity to database host

### Application Errors
1. Check application logs
2. Run `python -m database.tools.db_tools check` to verify database health
3. Check for NULL values: `python -m database.tools.db_tools purge-null`

### Common Issues
- **NULL title_id errors:** Run `python -m database.tools.db_tools purge-null`
- **Duplicate titles:** Run `python -m database.tools.db_tools dedupe`
- **Missing metadata:** Use tools in `tools/` directory

## Maintenance

### Regular Tasks
- [ ] Monitor database health weekly
- [ ] Check for duplicate titles monthly
- [ ] Review audit logs regularly
- [ ] Backup database regularly (automatic hourly backups on admin pages)

### Database Maintenance Commands
```bash
# Health check
python -m database.tools.db_tools check

# Fix duplicates
python -m database.tools.db_tools dedupe

# Remove NULL rows
python -m database.tools.db_tools purge-null

# Sync local to cloud (if needed)
python -m database.tools.local_db_sync push
```

