# Quick Fixes for Critical Issues

## 🔴 Must Fix Before Production

### 1. Remove Hardcoded Default Password
**File:** `app.py:44-45`
```python
# BEFORE (INSECURE):
if not ADMIN_PASSWORD_HASH:
    ADMIN_PASSWORD_HASH = generate_password_hash(_plain_password or "1234")

# AFTER (SECURE):
if not ADMIN_PASSWORD_HASH and not _plain_password:
    raise ValueError("ADMIN_PASSWORD or ADMIN_PASSWORD_HASH must be set in environment variables")
if not ADMIN_PASSWORD_HASH:
    ADMIN_PASSWORD_HASH = generate_password_hash(_plain_password)
```

### 2. Fix Duplicate Commits
**File:** `app.py` (multiple locations)
```python
# BEFORE:
db.session.commit()
log_action(...)
db.session.commit()

# AFTER:
log_action(...)  # log_action doesn't need commit, it just adds to session
db.session.commit()  # Single commit
```

### 3. Add Transaction Rollback
**File:** `app.py` (all database operations)
```python
# BEFORE:
db.session.delete(book)
db.session.commit()

# AFTER:
try:
    db.session.delete(book)
    db.session.commit()
except Exception as e:
    db.session.rollback()
    raise
```

### 4. Remove Credentials from Documentation
**Files:** `SETUP.md`, `CLEANUP_SUMMARY.md`
- Replace actual database URL with placeholder
- Use `DATABASE_URL=postgresql://user:password@host:port/database` format

### 5. Add Rate Limiting
**File:** `app.py:1616`
```python
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)

@app.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute")  # 5 login attempts per minute
def login():
    # ... existing code
```

## 🟡 Should Fix Soon

### 6. Create .env.example
**File:** `.env.example` (new file)
```bash
# Database Configuration
DATABASE_URL=postgresql://user:password@host:port/database

# Flask Configuration
FLASK_SECRET_KEY=your-secret-key-here
APP_SECRET_KEY=your-secret-key-here

# Admin User Configuration
ADMIN_USERNAME=admin
ADMIN_PASSWORD=your-secure-password-here
ADMIN_EMAIL=admin@example.com

# Optional
ENABLE_CSV_SYNC=0
ENABLE_CSV_EXPORT=0
SKIP_INIT=0
LOW_STOCK_THRESHOLD=1
```

### 7. Add Session Timeout
**File:** `app.py:48`
```python
from datetime import timedelta

app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=2)
app.config['SESSION_COOKIE_SECURE'] = True  # HTTPS only in production
app.config['SESSION_COOKIE_HTTPONLY'] = True
```

### 8. Fix Template String Usage
**File:** `app.py:1510, 1568`
- The `render_template_string` usage is actually safe because it uses Jinja2's auto-escaping
- But consider using `render_template` with a template file for better maintainability

### 9. Remove Unused Variable
**File:** `app.py:36`
```python
# Remove this line:
AUTO_GIT_PUSH = 1  # Never used
```

### 10. Add Error Handling
**File:** `app.py` (all routes)
```python
@app.route("/some_route", methods=["POST"])
def some_route():
    try:
        # ... database operations
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
```

## Implementation Priority

1. **Immediate:** Fixes #1, #2, #3 (Security and data integrity)
2. **This Week:** Fixes #4, #5, #6 (Security and configuration)
3. **This Month:** Fixes #7, #8, #9, #10 (Best practices)

