import sqlite3
import os
from datetime import datetime, timedelta
from contextlib import contextmanager
from werkzeug.security import generate_password_hash, check_password_hash

_DB_PATH = None

def _get_db_path():
    global _DB_PATH
    if _DB_PATH is None:
        base = os.path.dirname(os.path.abspath(__file__))
        _DB_PATH = os.path.join(base, os.environ.get('DB_PATH', 'nutriscan.db'))
    return _DB_PATH

@contextmanager
def _conn():
    conn = sqlite3.connect(_get_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    with _conn() as c:
        # Step 1: base tables (no columns that may need migration)
        c.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                email              TEXT UNIQUE NOT NULL,
                name               TEXT DEFAULT '',
                daily_calorie_goal INTEGER DEFAULT 2000,
                created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS food_logs (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                date       TEXT NOT NULL,
                food_name  TEXT NOT NULL,
                quantity   REAL DEFAULT 1,
                calories   REAL DEFAULT 0,
                protein    REAL DEFAULT 0,
                fat        REAL DEFAULT 0,
                carbs      REAL DEFAULT 0,
                fiber      REAL DEFAULT 0,
                meal_type  TEXT DEFAULT 'snack',
                notes      TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE INDEX IF NOT EXISTS idx_food_logs_user_date
                ON food_logs(user_id, date);
        ''')

        # Step 2: migrate users columns (ignored silently if already present)
        for sql in [
            "ALTER TABLE users ADD COLUMN username TEXT",
            "ALTER TABLE users ADD COLUMN password_hash TEXT",
        ]:
            try:
                c.execute(sql)
            except Exception:
                pass

        # Step 3: unique index on username — only safe AFTER column exists
        try:
            c.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username "
                "ON users(username) WHERE username IS NOT NULL"
            )
        except Exception:
            pass

        # Step 4: recreate otp_codes with purpose column if old schema detected
        old_cols = {row[1] for row in c.execute("PRAGMA table_info(otp_codes)")}
        if old_cols and 'purpose' not in old_cols:
            c.execute("DROP TABLE otp_codes")

        c.execute('''
            CREATE TABLE IF NOT EXISTS otp_codes (
                email      TEXT NOT NULL,
                purpose    TEXT NOT NULL DEFAULT 'register',
                code       TEXT NOT NULL,
                expires_at REAL NOT NULL,
                PRIMARY KEY (email, purpose)
            )
        ''')


# ── OTP ───────────────────────────────────────────────────────────────────────

def save_otp(email, code, purpose='register', ttl=300):
    expires = (datetime.now() + timedelta(seconds=ttl)).timestamp()
    with _conn() as c:
        c.execute(
            'INSERT OR REPLACE INTO otp_codes (email, purpose, code, expires_at) VALUES (?,?,?,?)',
            (email, purpose, code, expires)
        )

def verify_otp(email, code, purpose='register'):
    with _conn() as c:
        row = c.execute(
            'SELECT code, expires_at FROM otp_codes WHERE email=? AND purpose=?',
            (email, purpose)
        ).fetchone()
        if not row:
            return False
        if row['code'] != code:
            return False
        if datetime.now().timestamp() > row['expires_at']:
            c.execute('DELETE FROM otp_codes WHERE email=? AND purpose=?', (email, purpose))
            return False
        c.execute('DELETE FROM otp_codes WHERE email=? AND purpose=?', (email, purpose))
        return True


# ── User auth ─────────────────────────────────────────────────────────────────

def email_exists(email):
    with _conn() as c:
        row = c.execute('SELECT id FROM users WHERE email=?', (email,)).fetchone()
        return row is not None

def username_exists(username):
    with _conn() as c:
        row = c.execute('SELECT id FROM users WHERE LOWER(username)=LOWER(?)', (username,)).fetchone()
        return row is not None

def create_user(email, name, username, password):
    ph = generate_password_hash(password)
    with _conn() as c:
        c.execute(
            'INSERT INTO users (email, name, username, password_hash) VALUES (?,?,?,?)',
            (email.lower().strip(), name.strip(), username.strip(), ph)
        )
        row = c.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
        return dict(row)

def authenticate(identifier, password):
    """Return user dict if credentials valid, else None."""
    ident = identifier.strip().lower()
    with _conn() as c:
        row = c.execute(
            'SELECT * FROM users WHERE LOWER(email)=? OR LOWER(username)=?',
            (ident, ident)
        ).fetchone()
        if not row:
            return None
        if not row['password_hash']:
            return None
        if not check_password_hash(row['password_hash'], password):
            return None
        return dict(row)

def update_password(email, new_password):
    ph = generate_password_hash(new_password)
    with _conn() as c:
        c.execute('UPDATE users SET password_hash=? WHERE LOWER(email)=?',
                  (ph, email.lower().strip()))


# ── User data ─────────────────────────────────────────────────────────────────

def get_user(user_id):
    with _conn() as c:
        row = c.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
        return dict(row) if row else None

def update_user(user_id, **kwargs):
    allowed = {'name', 'daily_calorie_goal', 'username'}
    with _conn() as c:
        for key, value in kwargs.items():
            if key in allowed:
                c.execute(f'UPDATE users SET {key}=? WHERE id=?', (value, user_id))


# ── Food logs ─────────────────────────────────────────────────────────────────

def add_food_log(user_id, date, food_name, quantity,
                 calories, protein, fat, carbs, fiber=0,
                 meal_type='snack', notes=''):
    with _conn() as c:
        cur = c.execute(
            '''INSERT INTO food_logs
               (user_id, date, food_name, quantity,
                calories, protein, fat, carbs, fiber, meal_type, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
            (user_id, date, food_name, quantity,
             round(calories, 1), round(protein, 1), round(fat, 1),
             round(carbs, 1), round(fiber, 1), meal_type, notes)
        )
        return cur.lastrowid

def get_today_logs(user_id, date=None):
    date = date or datetime.now().strftime('%Y-%m-%d')
    with _conn() as c:
        rows = c.execute(
            'SELECT * FROM food_logs WHERE user_id=? AND date=? ORDER BY created_at',
            (user_id, date)
        ).fetchall()
        return [dict(r) for r in rows]

def delete_food_log(log_id, user_id):
    with _conn() as c:
        c.execute('DELETE FROM food_logs WHERE id=? AND user_id=?', (log_id, user_id))

def get_history(user_id, days=7):
    with _conn() as c:
        rows = c.execute(
            '''SELECT date,
                      ROUND(SUM(calories),1) AS calories,
                      ROUND(SUM(protein),1)  AS protein,
                      ROUND(SUM(fat),1)      AS fat,
                      ROUND(SUM(carbs),1)    AS carbs,
                      ROUND(SUM(fiber),1)    AS fiber
               FROM food_logs WHERE user_id=?
               AND date >= date('now', ?)
               GROUP BY date ORDER BY date''',
            (user_id, f'-{days} days')
        ).fetchall()
        return [dict(r) for r in rows]
