# Multi-User Support Plan for CONNECT Manager

## Status: Phase 1 Implementation Complete

The foundational multi-user authentication system has been implemented. See "Implementation Summary" at the end of this document.

---

## Executive Summary

This plan outlines how to update the CONNECT Manager web app to support multiple concurrent users (1-3 engineers demo-ing simultaneously from a team of 9). The current architecture is **single-user with global state** - all credentials and selections are shared across the entire application.

## Current State Analysis

| Aspect | Current State | Problem for Multi-User |
|--------|---------------|------------------------|
| **Authentication** | None | Anyone can access/overwrite data |
| **User Model** | No users table | Cannot distinguish between users |
| **Session Handling** | Global `AppState` singleton | All users share same state |
| **Credential Storage** | Single LucidLink token, per-DataStore S3 creds | Token overwritten by last user |
| **SSE Logs** | Global broadcast | All users see all activity |
| **Selected Filespace/DataStore** | Global variables | User A's selection affects User B |

## Recommended Approach: JWT + Per-User Sessions

For a team of 9 with 1-3 concurrent demo users, I recommend a **lightweight JWT-based session system** rather than a full OAuth2 identity provider. This balances security with implementation simplicity.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        Browser (User A)                         │
│  ┌───────────────┐    ┌───────────────┐    ┌───────────────┐   │
│  │   Login Form  │───▶│  JWT Cookie   │───▶│  HTMX + SSE   │   │
│  └───────────────┘    └───────────────┘    └───────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      FastAPI Backend                            │
│  ┌───────────────┐    ┌───────────────┐    ┌───────────────┐   │
│  │ Auth Middleware│───▶│ User Context  │───▶│ Route Handler │   │
│  │ (JWT verify)  │    │ (Depends)     │    │               │   │
│  └───────────────┘    └───────────────┘    └───────────────┘   │
│                              │                                  │
│                              ▼                                  │
│  ┌───────────────────────────────────────────────────────────┐ │
│  │                    User-Scoped State                       │ │
│  │  user_sessions[user_id] = {                               │ │
│  │    token, api_host, filespaces, datastores,               │ │
│  │    selected_filespace, selected_datastore, logs           │ │
│  │  }                                                         │ │
│  └───────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      SQLite Database                            │
│  ┌───────────┐  ┌────────────────────┐  ┌──────────────────┐   │
│  │   users   │  │ user_credentials   │  │  user_settings   │   │
│  │ (id,email)│  │ (user_id, type,    │  │ (user_id, key,   │   │
│  │           │  │  encrypted_value)  │  │  value)          │   │
│  └───────────┘  └────────────────────┘  └──────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Implementation Phases

### Phase 1: User Model & Authentication (Foundation)

**Goal:** Add login/logout with user accounts

#### 1.1 Database Schema Changes

```sql
-- New tables
CREATE TABLE users (
    id TEXT PRIMARY KEY,           -- UUID
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    display_name TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login TIMESTAMP
);

CREATE TABLE user_sessions (
    id TEXT PRIMARY KEY,           -- JWT jti claim
    user_id TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    revoked BOOLEAN DEFAULT FALSE,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

-- Modify existing tables to add user_id FK
ALTER TABLE profiles ADD COLUMN user_id TEXT REFERENCES users(id);
ALTER TABLE datastore_credentials ADD COLUMN user_id TEXT REFERENCES users(id);
ALTER TABLE sqs_credentials ADD COLUMN user_id TEXT REFERENCES users(id);
ALTER TABLE sqs_queues ADD COLUMN user_id TEXT REFERENCES users(id);
ALTER TABLE import_jobs ADD COLUMN user_id TEXT REFERENCES users(id);
```

#### 1.2 New Files to Create

| File | Purpose |
|------|---------|
| `app/services/auth.py` | JWT creation/verification, password hashing |
| `app/models/user.py` | User Pydantic models |
| `app/routes/auth.py` | Login/logout/register endpoints |
| `app/middleware/auth.py` | Request authentication middleware |
| `app/templates/login.html` | Login page template |

#### 1.3 Authentication Flow

```python
# app/services/auth.py
from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

SECRET_KEY = os.getenv("JWT_SECRET_KEY")  # Generate with: openssl rand -hex 32
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 480  # 8 hours for demo sessions

def create_access_token(user_id: str) -> str:
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode(
        {"sub": user_id, "exp": expire, "jti": str(uuid4())},
        SECRET_KEY, algorithm=ALGORITHM
    )

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

def hash_password(password: str) -> str:
    return pwd_context.hash(password)
```

#### 1.4 Dependencies

Add to `requirements.txt`:
```
python-jose[cryptography]>=3.3.0
passlib[bcrypt]>=1.7.4
```

---

### Phase 2: Per-User State Management

**Goal:** Isolate each user's session data

#### 2.1 Replace Global AppState

```python
# app/services/user_state.py
from typing import Dict
from dataclasses import dataclass, field

@dataclass
class UserSession:
    """Per-user session state"""
    user_id: str
    token: str = ""                          # LucidLink API token
    api_host: str = ""
    filespaces: Dict[str, str] = field(default_factory=dict)
    datastores: Dict[str, dict] = field(default_factory=dict)
    selected_filespace: str = ""
    selected_datastore: str = ""
    datastore_credentials: Dict[str, dict] = field(default_factory=dict)
    s3_services: Dict[str, Any] = field(default_factory=dict)
    logs: list = field(default_factory=list)

class UserStateManager:
    """Manages per-user sessions"""
    def __init__(self):
        self._sessions: Dict[str, UserSession] = {}

    def get_session(self, user_id: str) -> UserSession:
        if user_id not in self._sessions:
            self._sessions[user_id] = UserSession(user_id=user_id)
            self._sessions[user_id].load_from_db()  # Restore saved state
        return self._sessions[user_id]

    def clear_session(self, user_id: str):
        if user_id in self._sessions:
            del self._sessions[user_id]

# Global manager (replaces global AppState)
user_state_manager = UserStateManager()
```

#### 2.2 Update Route Handlers

```python
# app/main.py - Example transformation

# BEFORE (global state):
@app.post("/api/load-filespaces")
async def load_filespaces(token: str = Form(...)):
    state.token = token  # Global!
    state.filespaces = {...}

# AFTER (per-user state):
@app.post("/api/load-filespaces")
async def load_filespaces(
    token: str = Form(...),
    current_user: User = Depends(get_current_user)  # Injected
):
    session = user_state_manager.get_session(current_user.id)
    session.token = token  # User-scoped
    session.filespaces = {...}
```

#### 2.3 SSE Per-User Filtering

```python
# Filter log broadcasts to specific user
@app.get("/api/logs/stream")
async def log_stream(current_user: User = Depends(get_current_user)):
    async def event_generator():
        session = user_state_manager.get_session(current_user.id)
        last_index = 0
        while True:
            if len(session.logs) > last_index:
                for log in session.logs[last_index:]:
                    yield f"data: {log}\n\n"
                last_index = len(session.logs)
            await asyncio.sleep(0.5)
    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

---

### Phase 3: Credential Isolation

**Goal:** Each user's LucidLink/S3/SQS credentials are private

#### 3.1 User-Scoped Credential Storage

```python
# app/services/user_credentials.py

class UserCredentialStore:
    """Store credentials namespaced by user_id"""

    def save_ll_token(self, user_id: str, token: str):
        # Store in keyring with user-specific key
        keyring.set_password(
            "connect-manager",
            f"ll_token_{user_id}",
            token
        )
        # Also save reference in DB
        db.execute(
            "INSERT OR REPLACE INTO user_credentials (user_id, type, key_ref) VALUES (?, ?, ?)",
            (user_id, "ll_token", f"ll_token_{user_id}")
        )

    def get_ll_token(self, user_id: str) -> Optional[str]:
        return keyring.get_password("connect-manager", f"ll_token_{user_id}")
```

#### 3.2 Database Credential Queries

All existing queries need user_id filtering:

```python
# BEFORE:
def get_datastore_credentials():
    return db.execute("SELECT * FROM datastore_credentials").fetchall()

# AFTER:
def get_datastore_credentials(user_id: str):
    return db.execute(
        "SELECT * FROM datastore_credentials WHERE user_id = ?",
        (user_id,)
    ).fetchall()
```

---

### Phase 4: Frontend Updates

**Goal:** Add login UI, handle auth state in browser

#### 4.1 Login Page Template

```html
<!-- app/templates/login.html -->
{% extends "base.html" %}
{% block content %}
<div class="login-container">
    <h1>CONNECT Manager</h1>
    <form hx-post="/api/auth/login" hx-target="#login-error">
        <input type="email" name="email" placeholder="Email" required>
        <input type="password" name="password" placeholder="Password" required>
        <button type="submit" class="btn btn-neon">Sign In</button>
    </form>
    <div id="login-error"></div>
</div>
{% endblock %}
```

#### 4.2 Auth State in Base Template

```html
<!-- app/templates/base.html additions -->
<script>
    // Check auth state on page load
    document.addEventListener('DOMContentLoaded', async () => {
        const response = await fetch('/api/auth/me');
        if (response.status === 401) {
            window.location.href = '/login';
        }
    });
</script>

<!-- User info in header -->
<div class="user-info" x-data="{ user: null }" x-init="user = await (await fetch('/api/auth/me')).json()">
    <span x-text="user?.display_name || user?.email"></span>
    <button @click="logout()" class="btn-text">Logout</button>
</div>
```

#### 4.3 Protected Route Middleware

```python
# app/middleware/auth.py
from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse

async def require_auth(request: Request, call_next):
    # Public routes
    public_paths = ["/login", "/api/auth/login", "/static", "/favicon.ico"]
    if any(request.url.path.startswith(p) for p in public_paths):
        return await call_next(request)

    # Check for JWT cookie
    token = request.cookies.get("access_token")
    if not token:
        if request.url.path.startswith("/api/"):
            raise HTTPException(status_code=401, detail="Not authenticated")
        return RedirectResponse(url="/login")

    # Verify token and inject user
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        request.state.user_id = payload["sub"]
    except JWTError:
        return RedirectResponse(url="/login")

    return await call_next(request)
```

---

### Phase 5: Security Hardening

**Goal:** Follow OWASP best practices

#### 5.1 Cookie Security

```python
# Set secure cookie attributes
response.set_cookie(
    key="access_token",
    value=token,
    httponly=True,      # Prevent XSS access
    secure=True,        # HTTPS only (set False for local dev)
    samesite="lax",     # CSRF protection
    max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60
)
```

#### 5.2 Session Security Checklist

| Control | Implementation |
|---------|----------------|
| **Session ID Length** | JWT with 256-bit secret |
| **HTTPS Only** | `secure=True` cookie flag |
| **HttpOnly** | Prevent JS access to token |
| **Session Timeout** | 8 hours (configurable) |
| **Regenerate on Login** | New JWT on each login |
| **Logout Invalidation** | Clear cookie + optional JWT blacklist |
| **CSRF Protection** | SameSite cookie + HTMX headers |

#### 5.3 Password Requirements

```python
def validate_password(password: str) -> bool:
    """Enforce password policy"""
    if len(password) < 12:
        return False
    if not re.search(r"[A-Z]", password):
        return False
    if not re.search(r"[a-z]", password):
        return False
    if not re.search(r"\d", password):
        return False
    return True
```

---

## Database Migration Strategy

### Migration Script

```python
# migrations/001_add_multiuser.py

def upgrade(db):
    # 1. Create new tables
    db.execute("""
        CREATE TABLE users (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            display_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP
        )
    """)

    # 2. Add user_id to existing tables
    for table in ['profiles', 'datastore_credentials', 'sqs_credentials',
                  'sqs_queues', 'import_jobs']:
        db.execute(f"ALTER TABLE {table} ADD COLUMN user_id TEXT")

    # 3. Create default admin user for existing data
    admin_id = str(uuid4())
    db.execute(
        "INSERT INTO users (id, email, password_hash, display_name) VALUES (?, ?, ?, ?)",
        (admin_id, "admin@localhost", hash_password("changeme"), "Admin")
    )

    # 4. Assign existing data to admin
    for table in ['profiles', 'datastore_credentials', 'sqs_credentials',
                  'sqs_queues', 'import_jobs']:
        db.execute(f"UPDATE {table} SET user_id = ? WHERE user_id IS NULL", (admin_id,))

    # 5. Add foreign key constraints (SQLite requires table recreation)
    # ... (omitted for brevity)

def downgrade(db):
    # Remove user_id columns, drop users table
    pass
```

---

## User Management Options

### Option A: Self-Registration (Simplest)

- Users create their own accounts
- No admin approval required
- Good for trusted team environments

```python
@app.post("/api/auth/register")
async def register(email: str = Form(...), password: str = Form(...)):
    # Validate email domain (optional)
    if not email.endswith("@lucidlink.com"):
        raise HTTPException(400, "Only @lucidlink.com emails allowed")
    # Create user
    ...
```

### Option B: Invite-Only (Recommended for Your Use Case)

- Admin creates user accounts
- Users receive email with temporary password
- More control over who has access

```python
@app.post("/api/admin/invite")
async def invite_user(
    email: str = Form(...),
    current_user: User = Depends(require_admin)  # Admin only
):
    temp_password = generate_temp_password()
    create_user(email, temp_password)
    # Send email with temp password (or just display it)
    return {"temp_password": temp_password}
```

### Option C: SSO/OAuth (Enterprise)

- Integrate with Google Workspace, Okta, etc.
- Users authenticate with existing corporate credentials
- More complex but best UX for larger teams

---

## File Changes Summary

| File | Action | Description |
|------|--------|-------------|
| `app/main.py` | Modify | Add auth middleware, update all routes with `Depends(get_current_user)` |
| `app/services/auth.py` | Create | JWT creation/verification, password hashing |
| `app/services/user_state.py` | Create | Per-user session state manager |
| `app/services/user_credentials.py` | Create | User-scoped credential storage |
| `app/services/database.py` | Modify | Add users table, user_id to existing tables |
| `app/models/user.py` | Create | User Pydantic models |
| `app/routes/auth.py` | Create | Login/logout/register endpoints |
| `app/middleware/auth.py` | Create | Request authentication middleware |
| `app/templates/login.html` | Create | Login page |
| `app/templates/base.html` | Modify | Add user info, logout button, auth checks |
| `app/templates/partials/*.html` | Modify | Minor updates for user context |
| `requirements.txt` | Modify | Add python-jose, passlib |
| `docker-compose.yml` | Modify | Add JWT_SECRET_KEY env var |
| `.env.example` | Modify | Add JWT_SECRET_KEY |
| `migrations/001_add_multiuser.py` | Create | Database migration script |

---

## Implementation Order

```
Week 1: Foundation
├── Day 1-2: Database schema changes + migration script
├── Day 3-4: Auth service (JWT, password hashing)
└── Day 5: Login/logout endpoints + login page

Week 2: State Management
├── Day 1-2: UserStateManager + replace global AppState
├── Day 3-4: Update all route handlers with user context
└── Day 5: User-scoped credential storage

Week 3: Frontend + Testing
├── Day 1-2: Frontend auth state, protected routes
├── Day 3: SSE per-user filtering
├── Day 4: Security hardening (cookies, CSRF)
└── Day 5: Testing, documentation, team onboarding
```

---

## Environment Variables

Add to `.env`:

```bash
# Authentication
JWT_SECRET_KEY=<generate with: openssl rand -hex 32>
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=480

# Optional: Restrict registration to specific domain
ALLOWED_EMAIL_DOMAIN=lucidlink.com
```

---

## Testing Strategy

### Unit Tests

```python
# tests/test_auth.py
def test_create_access_token():
    token = create_access_token("user123")
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    assert payload["sub"] == "user123"

def test_password_hashing():
    hashed = hash_password("secure123")
    assert verify_password("secure123", hashed)
    assert not verify_password("wrong", hashed)
```

### Integration Tests

```python
# tests/test_multiuser.py
async def test_user_isolation():
    """Verify User A cannot see User B's data"""
    # Login as User A
    token_a = await login("user_a@test.com", "pass")
    # Create a profile
    await create_profile(token_a, "Profile A")

    # Login as User B
    token_b = await login("user_b@test.com", "pass")
    profiles = await list_profiles(token_b)

    # User B should NOT see User A's profile
    assert "Profile A" not in [p["name"] for p in profiles]
```

---

## Rollback Plan

If issues arise during deployment:

1. **Database**: Keep backup before migration, migration has `downgrade()` function
2. **Code**: Git revert to pre-multi-user commit
3. **Data**: User data is additive; existing single-user data preserved as "admin"

---

## Sources

- [FastAPI OAuth2 + JWT Tutorial](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/)
- [OWASP Session Management Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)
- [OWASP Authentication Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html)
- [FastAPI Security Best Practices](https://betterstack.com/community/guides/scaling-python/authentication-fastapi/)
- [Session Management Best Practices - WorkOS](https://workos.com/blog/session-management-best-practices)

---

## Implementation Summary (Phase 1 Complete)

### Files Created

| File | Purpose |
|------|---------|
| `app/services/auth.py` | JWT creation/verification, password hashing, user authentication |
| `app/services/user_state.py` | Per-user session state manager (replaces global AppState) |
| `app/models/__init__.py` | Models package |
| `app/models/user.py` | Pydantic models for User, TokenData, etc. |
| `app/routes/__init__.py` | Routes package |
| `app/routes/auth.py` | Login/logout/register/user management endpoints |
| `app/middleware/__init__.py` | Middleware package |
| `app/middleware/auth.py` | Auth middleware for protected routes |
| `app/templates/login.html` | Login page with LucidLink branding |

### Files Modified

| File | Changes |
|------|---------|
| `app/main.py` | Added auth middleware, updated all 40+ routes with user context |
| `app/services/database.py` | Added users/sessions tables, user CRUD, migration helpers |
| `app/services/secrets.py` | Added user-specific token storage functions |
| `app/templates/base.html` | Added user menu dropdown with logout |
| `app/static/css/style.css` | Added user menu and semantic color CSS variables |
| `app/pyproject.toml` | Added python-jose and passlib dependencies |
| `Dockerfile` | Added auth dependencies and env vars |
| `docker-compose.yml` | Added JWT_SECRET_KEY and auth env vars |

### Default Credentials

- **Email:** `admin@localhost`
- **Password:** `admin`

### Environment Variables

```bash
JWT_SECRET_KEY=dev-secret-change-in-production  # MUST change in production
ACCESS_TOKEN_EXPIRE_MINUTES=480                  # 8 hours
COOKIE_SECURE=false                              # Set true for HTTPS
ALLOWED_EMAIL_DOMAIN=                            # Optional domain restriction
DISABLE_REGISTRATION=                            # Set true for invite-only
```

### Testing the Implementation

1. Rebuild Docker: `./run.sh build`
2. Start: `./run.sh`
3. Navigate to http://localhost:8000
4. Login with `admin@localhost` / `admin`
5. Test multi-user by opening in another browser (incognito)

### Remaining Work (Future Phases)

- [ ] User management UI for admins
- [ ] Password change flow
- [ ] Email notifications for invites
- [ ] User-scoped filtering for DataStore credentials in database
- [ ] Integration tests for multi-user isolation
- [ ] Production deployment guide with secure JWT_SECRET_KEY
