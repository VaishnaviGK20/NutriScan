import os
import io
import json
import base64
import random
import smtplib
import tempfile
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from difflib import get_close_matches
from functools import wraps

from flask import (Flask, render_template, request, redirect,
                   url_for, session, jsonify)
from PIL import Image
from dotenv import load_dotenv

load_dotenv()

import database as db

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-please-change-in-production')
app.config['MAX_CONTENT_LENGTH'] = 12 * 1024 * 1024  # 12 MB

with open(os.path.join(BASE_DIR, 'calorie_map.json'), 'r') as _f:
    CALORIE_MAP = json.load(_f)

db.init_db()

_detector = None

def _get_detector():
    global _detector
    if _detector is None:
        try:
            import detect as _d
            _detector = _d
        except Exception as e:
            print(f"Detector load error: {e}")
    return _detector


# ---------- helpers ----------

def _login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapper


def _send_otp_email(to_email: str, code: str,
                    subject: str = 'Your NutriScan OTP') -> bool:
    gmail_user = os.environ.get('GMAIL_EMAIL', '').strip()
    gmail_pass = os.environ.get('GMAIL_APP_PASSWORD', '').strip()

    if not gmail_user or not gmail_pass:
        return False

    msg = MIMEMultipart('alternative')
    msg['Subject'] = f'{subject} — {code}'
    msg['From'] = f'NutriScan India <{gmail_user}>'
    msg['To'] = to_email

    html = f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:20px;background:#FFF9F5;
             font-family:Arial,Helvetica,sans-serif;">
  <div style="max-width:420px;margin:0 auto;background:#fff;
              border-radius:20px;padding:36px 28px;
              box-shadow:0 4px 24px rgba(255,107,53,.15);">
    <div style="text-align:center;margin-bottom:28px;">
      <div style="font-size:52px;">&#127869;</div>
      <h2 style="color:#FF6B35;margin:10px 0 4px;font-size:26px;">
        NutriScan India
      </h2>
      <p style="color:#757575;margin:0;font-size:14px;">
        Your smart nutrition companion
      </p>
    </div>
    <p style="color:#1C1C1C;font-size:16px;margin-bottom:6px;">
      Your one-time login code:
    </p>
    <div style="background:#FFF3EE;border:2.5px solid #FF6B35;
                border-radius:14px;padding:22px;text-align:center;
                margin:18px 0;letter-spacing:14px;">
      <span style="font-size:38px;font-weight:800;color:#FF6B35;">
        {code}
      </span>
    </div>
    <p style="color:#9E9E9E;font-size:13px;text-align:center;">
      Valid for <strong>5 minutes</strong>.
      Never share this code with anyone.
    </p>
    <hr style="border:none;border-top:1px solid #F0E8E0;margin:24px 0;">
    <p style="color:#BDBDBD;font-size:12px;text-align:center;">
      If you didn&#39;t request this, you can safely ignore this email.
    </p>
  </div>
</body>
</html>"""

    try:
        msg.attach(MIMEText(html, 'html'))
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=15) as srv:
            srv.login(gmail_user, gmail_pass)
            srv.sendmail(gmail_user, to_email, msg.as_string())
        return True
    except Exception as e:
        print(f"[Email error] {e}")
        return False


def _search_foods(query: str, limit: int = 10) -> list:
    q = query.lower().strip().replace(' ', '_')
    results = []
    seen = set()

    for key, data in CALORIE_MAP.items():
        if q not in key and q.replace('_', ' ') not in key.replace('_', ' '):
            continue
        if key in seen:
            continue
        seen.add(key)
        base = data.get('base', data) if isinstance(data, dict) else data
        if isinstance(base, (int, float)):
            base = {'calories': base, 'protein': 0, 'fat': 0, 'carbs': 0, 'fiber': 0}
        results.append({
            'key': key,
            'name': key.replace('_', ' ').title(),
            'calories': round(base.get('calories', 0), 1),
            'protein':  round(base.get('protein', 0), 1),
            'fat':      round(base.get('fat', 0), 1),
            'carbs':    round(base.get('carbs', 0), 1),
            'fiber':    round(base.get('fiber', 0), 1),
        })

    # fuzzy fallback
    if len(results) < 5:
        close = get_close_matches(q, CALORIE_MAP.keys(), n=6, cutoff=0.45)
        for k in close:
            if k in seen:
                continue
            seen.add(k)
            data = CALORIE_MAP[k]
            base = data.get('base', data) if isinstance(data, dict) else data
            if isinstance(base, (int, float)):
                base = {'calories': base, 'protein': 0, 'fat': 0, 'carbs': 0, 'fiber': 0}
            results.append({
                'key': k,
                'name': k.replace('_', ' ').title(),
                'calories': round(base.get('calories', 0), 1),
                'protein':  round(base.get('protein', 0), 1),
                'fat':      round(base.get('fat', 0), 1),
                'carbs':    round(base.get('carbs', 0), 1),
                'fiber':    round(base.get('fiber', 0), 1),
            })

    return results[:limit]


# ---------- routes ----------

@app.route('/')
def index():
    return redirect(url_for('scan') if 'user_id' in session else url_for('login'))


# ─── LOGIN ───────────────────────────────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('scan'))
    error = None
    if request.method == 'POST':
        identifier = request.form.get('identifier', '').strip()
        password   = request.form.get('password', '')
        if not identifier or not password:
            error = 'Please fill in all fields.'
        else:
            user = db.authenticate(identifier, password)
            if user:
                session['user_id']    = user['id']
                session['user_email'] = user['email']
                session['user_name']  = user.get('name', '')
                return redirect(url_for('scan'))
            else:
                error = 'Incorrect email/username or password.'
    return render_template('login.html', error=error)


# ─── REGISTER ────────────────────────────────────────────────────────────────

@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session:
        return redirect(url_for('scan'))
    error = None
    if request.method == 'POST':
        name  = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        if not name or not email:
            error = 'Please fill in all fields.'
        elif '@' not in email or '.' not in email.split('@')[-1]:
            error = 'Enter a valid email address.'
        elif db.email_exists(email):
            error = 'An account with that email already exists. Please log in.'
        else:
            code = str(random.randint(100000, 999999))
            db.save_otp(email, code, purpose='register')
            sent = _send_otp_email(email, code, subject='Verify your NutriScan account')
            if not sent:
                error = 'Could not send OTP. Check Gmail credentials in .env'
            else:
                session['reg_email'] = email
                session['reg_name']  = name
                return redirect(url_for('register_otp'))
    return render_template('register.html', error=error)


@app.route('/register/otp', methods=['GET', 'POST'])
def register_otp():
    if 'user_id' in session:
        return redirect(url_for('scan'))
    if 'reg_email' not in session:
        return redirect(url_for('register'))
    error = None
    if request.method == 'POST':
        code  = request.form.get('otp', '').strip()
        email = session.get('reg_email', '')
        if db.verify_otp(email, code, purpose='register'):
            session['reg_verified'] = True
            return redirect(url_for('register_setup'))
        error = 'Wrong or expired OTP. Please try again.'
    return render_template('register_otp.html',
                           email=session.get('reg_email', ''),
                           error=error)


@app.route('/register/setup', methods=['GET', 'POST'])
def register_setup():
    if 'user_id' in session:
        return redirect(url_for('scan'))
    if not session.get('reg_verified') or 'reg_email' not in session:
        return redirect(url_for('register'))
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        confirm  = request.form.get('confirm', '')
        if not username or not password:
            error = 'All fields are required.'
        elif not username.replace('_', '').isalnum():
            error = 'Username: only letters, numbers and underscores.'
        elif len(username) < 3:
            error = 'Username must be at least 3 characters.'
        elif db.username_exists(username):
            error = 'That username is taken. Try another.'
        elif len(password) < 8:
            error = 'Password must be at least 8 characters.'
        elif password != confirm:
            error = 'Passwords do not match.'
        else:
            user = db.create_user(
                email=session.pop('reg_email'),
                name=session.pop('reg_name', ''),
                username=username,
                password=password,
            )
            session.pop('reg_verified', None)
            session['user_id']    = user['id']
            session['user_email'] = user['email']
            session['user_name']  = user.get('name', '')
            return redirect(url_for('scan'))
    return render_template('register_setup.html', error=error,
                           username_val=request.form.get('username', ''))


# ─── FORGOT PASSWORD ─────────────────────────────────────────────────────────

@app.route('/forgot', methods=['GET', 'POST'])
def forgot():
    error = None
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        if not email or '@' not in email:
            error = 'Enter a valid email address.'
        elif not db.email_exists(email):
            error = 'No account found with that email.'
        else:
            code = str(random.randint(100000, 999999))
            db.save_otp(email, code, purpose='reset')
            sent = _send_otp_email(email, code, subject='Reset your NutriScan password')
            if not sent:
                error = 'Could not send OTP. Check Gmail credentials in .env'
            else:
                session['reset_email'] = email
                return redirect(url_for('forgot_otp'))
    return render_template('forgot.html', error=error)


@app.route('/forgot/otp', methods=['GET', 'POST'])
def forgot_otp():
    if 'reset_email' not in session:
        return redirect(url_for('forgot'))
    error = None
    if request.method == 'POST':
        code  = request.form.get('otp', '').strip()
        email = session.get('reset_email', '')
        if db.verify_otp(email, code, purpose='reset'):
            session['reset_verified'] = True
            return redirect(url_for('forgot_reset'))
        error = 'Wrong or expired OTP. Please try again.'
    return render_template('forgot_otp.html',
                           email=session.get('reset_email', ''),
                           error=error)


@app.route('/forgot/reset', methods=['GET', 'POST'])
def forgot_reset():
    if not session.get('reset_verified') or 'reset_email' not in session:
        return redirect(url_for('forgot'))
    error = None
    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm  = request.form.get('confirm', '')
        if len(password) < 8:
            error = 'Password must be at least 8 characters.'
        elif password != confirm:
            error = 'Passwords do not match.'
        else:
            db.update_password(session.pop('reset_email'), password)
            session.pop('reset_verified', None)
            return redirect(url_for('login') + '?reset=1')
    return render_template('forgot_reset.html', error=error)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/scan')
@_login_required
def scan():
    user = db.get_user(session['user_id'])
    return render_template('scan.html', user=user, active='scan')


@app.route('/tracker')
@_login_required
def tracker():
    user = db.get_user(session['user_id'])
    today = datetime.now().strftime('%Y-%m-%d')
    logs = db.get_today_logs(session['user_id'], today)

    total = {
        'calories': round(sum(l['calories'] for l in logs), 1),
        'protein':  round(sum(l['protein']  for l in logs), 1),
        'fat':      round(sum(l['fat']      for l in logs), 1),
        'carbs':    round(sum(l['carbs']    for l in logs), 1),
        'fiber':    round(sum(l.get('fiber', 0) for l in logs), 1),
    }

    meal_groups = {}
    for log in logs:
        mt = log['meal_type']
        meal_groups.setdefault(mt, []).append(log)

    return render_template('tracker.html',
                           user=user, logs=logs, total=total,
                           meal_groups=meal_groups, today=today,
                           active='tracker')


@app.route('/history')
@_login_required
def history():
    user = db.get_user(session['user_id'])
    data = db.get_history(session['user_id'], days=14)
    today = datetime.now().strftime('%Y-%m-%d')
    return render_template('history.html', user=user,
                           history_data=data, active='history',
                           now_date=today)


@app.route('/profile', methods=['GET', 'POST'])
@_login_required
def profile():
    user = db.get_user(session['user_id'])
    if request.method == 'POST':
        name = request.form.get('name', '').strip()[:60]
        try:
            goal = max(500, min(int(request.form.get('daily_calorie_goal', 2000)), 6000))
        except ValueError:
            goal = 2000
        db.update_user(session['user_id'], name=name, daily_calorie_goal=goal)
        session['user_name'] = name
        return redirect(url_for('profile'))
    return render_template('profile.html', user=user, active='profile')


# ---------- API ----------

@app.route('/api/scan', methods=['POST'])
@_login_required
def api_scan():
    if 'image' not in request.files:
        return jsonify({'error': 'No image provided'}), 400

    file = request.files['image']
    if not file.filename:
        return jsonify({'error': 'Empty file'}), 400

    allowed = {'image/jpeg', 'image/jpg', 'image/png', 'image/webp'}
    if file.content_type not in allowed:
        return jsonify({'error': 'Upload a JPG or PNG image.'}), 400

    description = request.form.get('description', '').strip()[:500]

    try:
        img = Image.open(file.stream)
        if img.mode != 'RGB':
            img = img.convert('RGB')
        # Resize large images to save memory
        max_dim = 1024
        if max(img.size) > max_dim:
            img.thumbnail((max_dim, max_dim), Image.LANCZOS)

        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
            img.save(tmp.name, 'JPEG', quality=88)
            tmp_path = tmp.name

        try:
            det = _get_detector()
            if det is None:
                return jsonify({'error': 'Detection model not available.'}), 503

            result_img, items, nutrition, explanations = det.detect_and_calculate(
                tmp_path, description
            )

            result_pil = Image.fromarray(result_img)
            buf = io.BytesIO()
            result_pil.save(buf, format='JPEG', quality=80)
            img_b64 = base64.b64encode(buf.getvalue()).decode()

            return jsonify({
                'success': True,
                'image_b64': img_b64,
                'detected_items': items,
                'total_nutrition': {k: round(v, 1) for k, v in nutrition.items()},
                'explanations': explanations,
            })
        finally:
            os.unlink(tmp_path)

    except Exception as e:
        return jsonify({'error': f'Scan failed: {str(e)}'}), 500


@app.route('/api/log', methods=['POST'])
@_login_required
def api_log():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'No data'}), 400

    today = datetime.now().strftime('%Y-%m-%d')
    try:
        qty = max(0.1, float(data.get('quantity', 1)))
        log_id = db.add_food_log(
            user_id=session['user_id'],
            date=today,
            food_name=str(data.get('food_name', 'Unknown food'))[:100],
            quantity=qty,
            calories=max(0, float(data.get('calories', 0))) * qty,
            protein= max(0, float(data.get('protein',  0))) * qty,
            fat=     max(0, float(data.get('fat',     0))) * qty,
            carbs=   max(0, float(data.get('carbs',   0))) * qty,
            fiber=   max(0, float(data.get('fiber',   0))) * qty,
            meal_type=data.get('meal_type', 'snack'),
            notes=str(data.get('notes', ''))[:200],
        )
        return jsonify({'success': True, 'log_id': log_id})
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid data'}), 400


@app.route('/api/log/<int:log_id>', methods=['DELETE'])
@_login_required
def api_delete_log(log_id):
    db.delete_food_log(log_id, session['user_id'])
    return jsonify({'success': True})


@app.route('/api/search')
@_login_required
def api_search():
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify([])
    return jsonify(_search_foods(q))


@app.route('/api/today')
@_login_required
def api_today():
    today = datetime.now().strftime('%Y-%m-%d')
    logs = db.get_today_logs(session['user_id'], today)
    user = db.get_user(session['user_id'])
    total = {
        'calories': round(sum(l['calories'] for l in logs), 1),
        'protein':  round(sum(l['protein']  for l in logs), 1),
        'fat':      round(sum(l['fat']      for l in logs), 1),
        'carbs':    round(sum(l['carbs']    for l in logs), 1),
        'fiber':    round(sum(l.get('fiber', 0) for l in logs), 1),
    }
    return jsonify({
        'logs': logs,
        'total': total,
        'goal': user.get('daily_calorie_goal', 2000),
    })


@app.route('/api/history')
@_login_required
def api_history():
    days = max(1, min(int(request.args.get('days', 7)), 30))
    return jsonify(db.get_history(session['user_id'], days))


if __name__ == '__main__':
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', port=5000, debug=debug)
