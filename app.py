from flask import Flask, render_template, request, redirect, url_for, Response
import sqlite3
import pandas as pd
import matplotlib.pyplot as plt
from io import BytesIO
import base64
from datetime import datetime

# Flask Login imports
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "your_secret_key_here"
DB_PATH = "expenses.db"

# ---------- DB helpers ----------
def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    conn.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    conn.commit()
    conn.close()

init_db()

# ---------- Flask-Login setup ----------
login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

class User(UserMixin):
    def __init__(self, id, email, password_hash):
        self.id = id
        self.email = email
        self.password_hash = password_hash

@login_manager.user_loader
def load_user(user_id):
    conn = get_db_connection()
    user_row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    if user_row:
        return User(user_row['id'], user_row['email'], user_row['password_hash'])
    return None

# ---------- Utility: plot to base64 ----------
def plot_to_base64(fig):
    buf = BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", bbox_inches='tight')
    buf.seek(0)
    img_base64 = base64.b64encode(buf.read()).decode('utf-8')
    plt.close(fig)
    return img_base64

# ---------- Auth Routes ----------
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        hashed_pw = generate_password_hash(password, method='pbkdf2:sha256')

        try:
            conn = get_db_connection()
            conn.execute("INSERT INTO users (email, password_hash) VALUES (?, ?)", (email, hashed_pw))
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
        user_row = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        conn.close()

        if user_row and check_password_hash(user_row['password_hash'], password):
            user = User(user_row['id'], user_row['email'], user_row['password_hash'])
            login_user(user)
            return redirect(url_for('index'))
        return "Invalid credentials!"
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# ---------- Expense Routes ----------
@app.route('/')
@login_required
def index():
    conn = get_db_connection()
    rows = conn.execute('SELECT * FROM expenses WHERE user_id = ? ORDER BY date DESC', (current_user.id,)).fetchall()
    conn.close()

    df = pd.DataFrame(rows, columns=rows[0].keys()) if rows else pd.DataFrame(columns=['id','date','category','amount','description','user_id'])
    total = df['amount'].sum() if not df.empty else 0.0
    by_category = df.groupby('category')['amount'].sum() if not df.empty else pd.Series(dtype=float)

    pie_img = None
    if not by_category.empty:
        fig1, ax1 = plt.subplots()
        ax1.pie(by_category.values, labels=by_category.index, autopct='%1.1f%%', startangle=140)
        ax1.axis('equal')
        pie_img = plot_to_base64(fig1)

    line_img = None
    if not df.empty:
        df['date_dt'] = pd.to_datetime(df['date'])
        df['month'] = df['date_dt'].dt.to_period('M').astype(str)
        monthly = df.groupby('month')['amount'].sum().sort_index()
        fig2, ax2 = plt.subplots()
        ax2.plot(monthly.index, monthly.values, marker='o')
        ax2.set_xlabel('Month')
        ax2.set_ylabel('Total Spent')
        ax2.set_title('Monthly Spending')
        for label in ax2.get_xticklabels():
            label.set_rotation(45)
        line_img = plot_to_base64(fig2)

    return render_template('index.html',
                           expenses=rows,
                           total=total,
                           pie_img=pie_img,
                           line_img=line_img)

@app.route('/add', methods=['GET', 'POST'])
@login_required
def add():
    if request.method == 'POST':
        date = request.form.get('date') or datetime.today().strftime('%Y-%m-%d')
        category = request.form.get('category').strip()
        amount = request.form.get('amount')
        description = request.form.get('description', '')

        try:
            amount = float(amount)
        except:
            amount = 0.0

        conn = get_db_connection()
        conn.execute('INSERT INTO expenses (date, category, amount, description, user_id) VALUES (?, ?, ?, ?, ?)',
                     (date, category, amount, description, current_user.id))
        conn.commit()
        conn.close()
        return redirect(url_for('index'))

    return render_template('add.html')

@app.route('/delete/<int:expense_id>', methods=['POST'])
@login_required
def delete(expense_id):
    conn = get_db_connection()
    conn.execute('DELETE FROM expenses WHERE id = ? AND user_id = ?', (expense_id, current_user.id))
    conn.commit()
    conn.close()
    return redirect(url_for('index'))

@app.route('/export')
@login_required
def export_csv():
    conn = get_db_connection()
    df = pd.read_sql_query('SELECT * FROM expenses WHERE user_id = ? ORDER BY date DESC', conn, params=(current_user.id,))
    conn.close()
    csv_data = df.to_csv(index=False)
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-disposition": "attachment; filename=expenses_export.csv"}
    )

if __name__ == '__main__':
    app.run(debug=True)
