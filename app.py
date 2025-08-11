from flask import Flask, render_template, request, redirect, url_for, Response, jsonify
import sqlite3
import pandas as pd
from datetime import datetime
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
import os
import random

app = Flask(__name__)
app.secret_key = "your_secret_key_here"
DB_PATH = "expenses.db"

# ---------------- Database ----------------
def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    conn.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL DEFAULT '',
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL
    )
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL,
        category TEXT NOT NULL,
        amount REAL NOT NULL,
        description TEXT,
        user_id INTEGER NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
    """)
    # Auto-add username column if missing
    cols = [c['name'] for c in conn.execute("PRAGMA table_info(users)")]
    if 'username' not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN username TEXT NOT NULL DEFAULT ''")
    conn.commit()
    conn.close()

init_db()

# ---------------- AI Model ----------------
training_data = [
    ("Uber ride to work", "Transport"),
    ("Ola taxi", "Transport"),
    ("Zomato food order", "Food"),
    ("McDonald's lunch", "Food"),
    ("Electricity bill", "Utilities"),
    ("Water bill", "Utilities"),
    ("Movie tickets", "Entertainment"),
    ("Netflix subscription", "Entertainment"),
    ("Grocery shopping", "Groceries"),
    ("Vegetables from market", "Groceries"),
    ("Flight ticket", "Travel"),
    ("Hotel booking", "Travel"),
]

train_texts, train_labels = zip(*training_data)
vectorizer = TfidfVectorizer()
X_train = vectorizer.fit_transform(train_texts)
model = LogisticRegression()
model.fit(X_train, train_labels)

def predict_category(description):
    if not description.strip():
        return None
    X_new = vectorizer.transform([description])
    return model.predict(X_new)[0]

# ---------------- Flask-Login ----------------
login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

class User(UserMixin):
    def __init__(self, id, username, email, password_hash):
        self.id = id
        self.username = username
        self.email = email
        self.password_hash = password_hash

@login_manager.user_loader
def load_user(user_id):
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    if row:
        return User(row['id'], row['username'], row['email'], row['password_hash'])
    return None

# ---------------- Routes ----------------
@app.route('/')
@login_required
def index():
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM expenses WHERE user_id=? ORDER BY date DESC", (current_user.id,)).fetchall()
    conn.close()

    df = pd.DataFrame(rows, columns=rows[0].keys()) if rows else pd.DataFrame(columns=['id','date','category','amount','description','user_id'])
    total = df['amount'].sum() if not df.empty else 0

    by_category = df.groupby('category')['amount'].sum().to_dict() if not df.empty else {}
    if not df.empty:
        df['date_dt'] = pd.to_datetime(df['date'])
        df['month'] = df['date_dt'].dt.to_period('M').astype(str)
    monthly = df.groupby('month')['amount'].sum().to_dict() if not df.empty else {}

    return render_template('index.html', expenses=rows, total=total, by_category=by_category, monthly=monthly)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        hashed_pw = generate_password_hash(password, method='pbkdf2:sha256')
        try:
            conn = get_db_connection()
            conn.execute("INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
                         (username, email, hashed_pw))
            conn.commit()
            conn.close()
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            return "Email already exists!"
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        conn = get_db_connection()
        row = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        conn.close()
        if row and check_password_hash(row['password_hash'], password):
            user = User(row['id'], row['username'], row['email'], row['password_hash'])
            login_user(user)
            return redirect(url_for('index'))
        return "Invalid credentials!"
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/add', methods=['GET', 'POST'])
@login_required
def add():
    if request.method == 'POST':
        date = request.form.get('date') or datetime.today().strftime('%Y-%m-%d')
        category = request.form.get('category', '').strip()
        amount = request.form.get('amount')
        description = request.form.get('description', '')

        if not category and description:
            category = predict_category(description)

        try:
            amount = float(amount)
        except:
            amount = 0.0

        conn = get_db_connection()
        conn.execute("INSERT INTO expenses (date, category, amount, description, user_id) VALUES (?, ?, ?, ?, ?)",
                     (date, category or "Uncategorized", amount, description, current_user.id))
        conn.commit()
        conn.close()
        return redirect(url_for('index'))
    return render_template('add.html')

@app.route('/delete/<int:id>', methods=['POST'])
@login_required
def delete(id):
    conn = get_db_connection()
    conn.execute("DELETE FROM expenses WHERE id=? AND user_id=?", (id, current_user.id))
    conn.commit()
    conn.close()
    return redirect(url_for('index'))

@app.route('/export')
@login_required
def export_csv():
    conn = get_db_connection()
    df = pd.read_sql_query("SELECT * FROM expenses WHERE user_id=? ORDER BY date DESC", conn, params=(current_user.id,))
    conn.close()
    csv_data = df.to_csv(index=False)
    return Response(csv_data, mimetype="text/csv", headers={"Content-disposition": "attachment; filename=expenses.csv"})

# ---------------- AI Coach ----------------
@app.route('/ai_coach')
@login_required
def ai_coach():
    return render_template('ai_coach.html')

@app.route('/ai_coach_chat', methods=['POST'])
@login_required
def ai_coach_chat():
    data = request.get_json()
    user_message = data.get("message", "").lower().strip()
    username = current_user.username

    # --- Response banks ---
    greetings = [
        f"Hey {username}! 👋 How’s your day going?",
        f"Hi {username}, hope you're having a great day! ☀",
        f"Hello {username}! Ready to crush those financial goals? 💪"
    ]

    jokes = [
        f"😂 Hey {username}, why don’t skeletons fight each other? They don’t have the guts!",
        f"🤣 {username}, why was the math book sad? Because it had too many problems!",
        f"😆 Here's one for you {username}: Why did the scarecrow win an award? Because he was outstanding in his field!"
    ]

    saving_tips = [
        f"💡 Tip for you {username}: Try the 50/30/20 rule — 50% needs, 30% wants, 20% savings.",
        f"💡 {username}, avoid impulse purchases by waiting 24 hours before buying non-essentials.",
        f"💡 Track your daily spending, {username}, it makes controlling expenses much easier!"
    ]

    motivation = [
        f"🔥 You're doing awesome, {username}! Keep pushing towards your savings goals.",
        f"🚀 Remember {username}, small savings add up to big wins over time.",
        f"💪 Stay disciplined, {username}! Your future self will thank you."
    ]

    default_responses = [
        f"Hi {username}, I think you should track your spending more closely.",
        f"{username}, categorizing your expenses can really help you see where your money goes.",
        f"Want me to suggest a weekly budget for you, {username}?"
    ]

    # --- Intent recognition ---
    if any(word in user_message for word in ["hello", "hi", "hey"]):
        reply = random.choice(greetings)
    elif "joke" in user_message:
        reply = random.choice(jokes)
    elif any(word in user_message for word in ["save", "control", "spending tips", "reduce expense"]):
        reply = random.choice(saving_tips)
    elif any(word in user_message for word in ["motivate", "encourage", "inspire"]):
        reply = random.choice(motivation)
    elif any(word in user_message for word in ["yes", "ok", "sure"]):
        reply = f"Great, {username}! Let’s make this your most financially smart month yet! 💪"
    else:
        reply = random.choice(default_responses)

    return jsonify({"reply": reply})

if __name__ == '__main__':
    app.run(debug=True)
