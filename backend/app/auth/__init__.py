"""Password hashing, JWT issue/verify, and the `current_user` / `get_owned_race`
dependencies (§4 Auth & Tenancy).

- `password.py` — argon2 hashing via argon2-cffi.
- `tokens.py` — PyJWT HS256 issue/verify for access + refresh tokens.
- `deps.py` — the `current_user` FastAPI dependency, cookie helpers, and
  `get_owned_race(db, user, race_id)`, the one place race ownership is checked.
"""
