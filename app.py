from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import pagesizes
from reportlab.lib.units import inch
from flask import send_file
from flask import Flask, render_template, request, redirect, session
from flask_bcrypt import Bcrypt
import re
import requests
import random
from flask import flash
import sqlite3
import os
from datetime import timedelta

app = Flask(__name__)
app.secret_key = "supersecretkey"
app.permanent_session_lifetime = timedelta(days=7)
bcrypt = Bcrypt(app)

def valid_password(password):
    if len(password) < 5:
        return False
    if not re.search(r"[A-Za-z]", password):
        return False
    if not re.search(r"[0-9]", password):
        return False
    if not re.search(r"[!@#$%^&*]", password):
        return False
    return True


# -----------------------
# Database Connection
# -----------------------
def get_connection():
    conn = sqlite3.connect("agri_erp.db", timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # prevents database lock
    return conn


# -----------------------
# Initialize Database
# -----------------------
def init_db():
    
    conn = get_connection()
    cursor = conn.cursor()

    # Farmers Table
    cursor.execute('''
      CREATE TABLE IF NOT EXISTS farmers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        phone TEXT NOT NULL UNIQUE,
        address TEXT NOT NULL,
        land_area REAL NOT NULL,
        soil_type TEXT,
        irrigation TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
''')

    cursor.execute('''
     CREATE TABLE IF NOT EXISTS crops (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        farmer_id INTEGER,
        crop_name TEXT NOT NULL,
        category TEXT NOT NULL,
        season TEXT,
        quantity REAL NOT NULL,
        image TEXT,
        base_price REAL NOT NULL,
        suggested_price REAL,
        district TEXT,
        state TEXT,
        status TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (farmer_id) REFERENCES farmers(id)
    )
''')
    cursor.execute('''
       CREATE TABLE IF NOT EXISTS market_prices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        commodity TEXT,
        state TEXT,
        district TEXT,
        min_price REAL,
        max_price REAL,
        modal_price REAL,
        last_updated TEXT
    )
''')
    # Sales Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sales(
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         crop_id INTEGER,
         quantity_sold INTEGER,
         price_per_unit REAL,
         total_amount REAL,
         sale_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
""")
   # Users Table (Secure)
    cursor.execute('''
       CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL
       )
    ''')
    # Expense Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        farmer_id INTEGER,
        expense_type TEXT,
        amount REAL,
        description TEXT,
        expense_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (farmer_id) REFERENCES farmers(id)
    )
''')
    cursor.execute("""
        SELECT farmers.name, SUM(sales.total_amount) as total_revenue
        FROM sales
        JOIN crops ON sales.crop_id = crops.id
        JOIN farmers ON crops.farmer_id = farmers.id
        GROUP BY farmers.name
        ORDER BY total_revenue DESC
        LIMIT 1
    """)
    top_farmer = cursor.fetchone()
    cursor.execute("""
        SELECT strftime('%Y-%m', sale_date) as month,
            SUM(total_amount)
        FROM sales
        GROUP BY month
    """)
    monthly_sales = cursor.fetchall()

    try:
        cursor.execute("ALTER TABLE crops ADD COLUMN category TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()
   

# -----------------------
# Signup Route
# -----------------------
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        # Password Validation
        if not valid_password(password):
            flash("Password must contain letter, number & special character (min 5)")
            return redirect("/signup")

        conn = get_connection()
        cursor = conn.cursor()

        # Check duplicate username
        cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
        existing_user = cursor.fetchone()

        if existing_user:
            conn.close()
            flash("Username already registered!")
            return redirect("/signup")

        # Hash Password
        hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')

        cursor.execute(
            "INSERT INTO users (username, password) VALUES (?, ?)",
            (username, hashed_password)
        )

        conn.commit()
        conn.close()

        flash("Account created successfully! Please login.")
        return redirect("/login")

    return render_template("signup.html")
# -----------------------
# Login Route
# -----------------------
@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        username = request.form["username"]
        password = request.form["password"]
        user_captcha = request.form["captcha"]

        # Check CAPTCHA
        if str(session.get("captcha")) != user_captcha:
            flash("Invalid CAPTCHA")
            return redirect("/login")

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
        user = cursor.fetchone()
        conn.close()

        if user and bcrypt.check_password_hash(user["password"], password):
            session["user"] = username

            if request.form.get("remember"):
                session.permanent = True
            else:
                session.permanent = False

            flash("Login Successful!")
            return redirect("/")   # Dashboard
        else:
            flash("Invalid Username or Password")
            return redirect("/login")

    # Generate captcha on GET
    captcha_value = random.randint(1000, 9999)
    session["captcha"] = captcha_value

    return render_template("login.html", captcha=captcha_value)
# -----------------------
# Logout
# -----------------------
@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect("/login")


# -----------------------
# Dashboard
# -----------------------
@app.route("/")
def dashboard():
    if "user" not in session:
        return redirect("/login")

    conn = get_connection()
    cursor = conn.cursor()

    # Basic Counts
    cursor.execute("SELECT COUNT(*) FROM farmers")
    total_farmers = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM crops")
    total_crops = cursor.fetchone()[0]

    # Total Revenue
    cursor.execute("SELECT IFNULL(SUM(total_amount), 0) FROM sales")
    total_revenue = cursor.fetchone()[0]

    # Total Expenses
    cursor.execute("SELECT IFNULL(SUM(amount), 0) FROM expenses")
    total_expenses = cursor.fetchone()[0]

    net_profit = total_revenue - total_expenses

    # 🔥 Monthly Revenue
    cursor.execute("""
        SELECT strftime('%m', sale_date) as month,
               SUM(total_amount)
        FROM sales
        GROUP BY month
        ORDER BY month
    """)
    monthly_data = cursor.fetchall()

    months = []
    revenues = []

    for row in monthly_data:
        months.append(row[0])
        revenues.append(row[1])

    # 🔥 Profit Per Farmer
    cursor.execute("""
        SELECT farmers.name,
               IFNULL(SUM(sales.total_amount), 0) as revenue,
               IFNULL(SUM(expenses.amount), 0) as expense
        FROM farmers
        LEFT JOIN crops ON farmers.id = crops.farmer_id
        LEFT JOIN sales ON crops.id = sales.crop_id
        LEFT JOIN expenses ON farmers.id = expenses.farmer_id
        GROUP BY farmers.id
    """)

    farmer_data = cursor.fetchall()

    farmer_names = []
    farmer_profits = []

    for row in farmer_data:
        name = row[0]
        revenue = row[1]
        expense = row[2]
        profit = revenue - expense

        farmer_names.append(name)
        farmer_profits.append(profit)

    conn.close()

    return render_template(
        "dashboard.html",
        total_farmers=total_farmers,
        total_crops=total_crops,
        total_revenue=total_revenue,
        total_expenses=total_expenses,
        net_profit=net_profit,
        months=months,
        revenues=revenues,
        farmer_names=farmer_names,
        farmer_profits=farmer_profits
    )
# -----------------------
# Add Farmer
# -----------------------
import re

@app.route("/add_farmer", methods=["GET", "POST"])
def add_farmer():

    if request.method == "POST":

        name = request.form["name"]
        phone = request.form["phone"]
        address = request.form["address"]
        land_area = request.form["land_area"]

        # Name validation
        if not re.match("^[A-Za-z ]+$", name):
            return "Farmer name should contain only letters"

        # Phone validation
        if not re.match("^[0-9]{10}$", phone):
            return "Phone number must be exactly 10 digits"

        if not re.match(r"^[A-Za-z0-9\s,./-]+$", address):
            return "Address contains invalid characters"

        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(
            "INSERT INTO farmers (name, phone, address, land_area) VALUES (?, ?, ?, ?)",
            (name, phone, address, land_area)
        )

        conn.commit()
        conn.close()

        return redirect("/farmers")

    return render_template("add_farmer.html")


# -----------------------
# View Farmers
# -----------------------
@app.route("/farmers")
def farmers():
    # Check login
    if "user" not in session:
        return redirect("/login")

    conn = get_connection()
    conn.row_factory = sqlite3.Row   # 👈 Important (to access by column name)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, name, phone, address, land_area, soil_type, irrigation
        FROM farmers
        ORDER BY id DESC
    """)

    farmers = cursor.fetchall()
    conn.close()

    return render_template("farmers.html", farmers=farmers)

# -----------------------
# Add Crop
# -----------------------
@app.route("/add_crop", methods=["GET", "POST"])
def add_crop():
    if "user" not in session:
        return redirect("/login")

    conn = get_connection()
    cursor = conn.cursor()

    # Step 1: Farmer Verification
    if request.method == "POST" and "verify_farmer" in request.form:
        name = request.form["farmer_name"]
        phone = request.form["phone"]

        cursor.execute("SELECT * FROM farmers WHERE name=? AND phone=?", (name, phone))
        farmer = cursor.fetchone()

        if farmer:
            return render_template("add_crop.html", step=2, farmer=farmer)
        else:
            return render_template("add_crop.html", step=1, error="Farmer not found")

    # Step 2: Add Crop
    if request.method == "POST" and "add_crop" in request.form:
        farmer_id = request.form["farmer_id"]
        crop_name = request.form["crop_name"]
        category = request.form["category"]
        season = request.form["season"]
        quantity = request.form["quantity"]
        base_price = request.form["base_price"]
        district = request.form["district"]
        state = request.form["state"]

        suggested_price = get_market_price(crop_name, district, state)

        if suggested_price:
            if float(base_price) > suggested_price:
                status = "High"
            elif float(base_price) < suggested_price:
                status = "Low"
            else:
                status = "Fair"
        else:
            status = "Unavailable"

        cursor.execute("""
            INSERT INTO crops 
            (farmer_id, crop_name, category, season, quantity, base_price, suggested_price, district, state, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (farmer_id, crop_name, category, season, quantity, base_price, suggested_price, district, state, status))

        conn.commit()
        conn.close()

        return redirect("/crops")

    return render_template("add_crop.html", step=1)
# -----------------------
# Sell Crop
# -----------------------
@app.route("/sell_crop/<int:crop_id>", methods=["GET", "POST"])
def sell_crop(crop_id):
    if "user" not in session:
        return redirect("/login")

    conn = get_connection()
    cursor = conn.cursor()

    # Get crop details
    cursor.execute("SELECT * FROM crops WHERE id = ?", (crop_id,))
    crop = cursor.fetchone()

    if not crop:
        conn.close()
        return "Crop not found"

    if request.method == "POST":
        quantity_sold = int(request.form["quantity_sold"])
        price_per_unit = float(request.form["price_per_unit"])

        # Prevent overselling
        if quantity_sold > crop["quantity"]:
            conn.close()
            return "Not enough stock available!"

        total_amount = quantity_sold * price_per_unit

        cursor.execute("""
            INSERT INTO sales (crop_id, quantity_sold, price_per_unit, total_amount, sale_date)
            VALUES (?, ?, ?, ?, datetime('now'))
        """, (crop_id, quantity_sold, price_per_unit, total_amount))

        # Reduce stock
        new_quantity = crop["quantity"] - quantity_sold
        cursor.execute("""
            UPDATE crops SET quantity = ? WHERE id = ?
        """, (new_quantity, crop_id))

        conn.commit()
        conn.close()

        return redirect("/crops")

    conn.close()
    return render_template("sell_crop.html", crop=crop)

# -----------------------
# Sales Page
# -----------------------
# -----------------------
# Sales Page
# -----------------------
@app.route("/sales")
def sales_page():

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT sales.id,
               crops.crop_name,
               sales.quantity_sold,
               sales.price_per_unit,
               sales.total_amount
        FROM sales
        JOIN crops ON sales.crop_id = crops.id
    """)

    sales = cursor.fetchall()

    conn.close()

    return render_template("sales.html", sales=sales)

# -----------------------
# Sales History
# -----------------------
@app.route("/sales")
def sales():

    if "user" not in session:
        return redirect("/login")

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    SELECT 
        sales.id,
        crops.crop_name,
        farmers.name AS farmer_name,
        sales.quantity_sold,
        sales.price_per_unit,
        sales.total_amount,
        sales.sale_date
    FROM sales
    JOIN crops ON sales.crop_id = crops.id
    JOIN farmers ON crops.farmer_id = farmers.id
    ORDER BY sales.sale_date DESC
    """)

    sales = cursor.fetchall()

    conn.close()

    return render_template("sales.html", sales=sales)
# -----------------------
# View Crops
# -----------------------
@app.route("/crops")
def crops():
    if "user" not in session:
        return redirect("/login")

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT crops.*, farmers.name AS farmer_name
        FROM crops
        JOIN farmers ON crops.farmer_id = farmers.id
    ''')

    crops = cursor.fetchall()
    conn.close()

    return render_template("crops.html", crops=crops)
# -----------------------
# Add Expense
# -----------------------
@app.route("/add_expense", methods=["GET", "POST"])
def add_expense():
    if "user" not in session:
        return redirect("/login")

    conn = get_connection()
    cursor = conn.cursor()

    if request.method == "POST":
        farmer_id = request.form["farmer_id"]
        expense_type = request.form["expense_type"]
        amount = float(request.form["amount"])
        description = request.form["description"]

        cursor.execute("""
            INSERT INTO expenses (farmer_id, expense_type, amount, description)
            VALUES (?, ?, ?, ?)
        """, (farmer_id, expense_type, amount, description))

        conn.commit()
        conn.close()

        return redirect("/expenses")

    # Fetch farmers for dropdown
    cursor.execute("SELECT id, name FROM farmers")
    farmers = cursor.fetchall()
    conn.close()

    return render_template("add_expense.html", farmers=farmers)
def get_market_price(crop, district, state):
    try:

        url = "https://api.data.gov.in/resource/9ef84268-d588-465a-a308-a864a43d0070"

        params = {
            "api-key": "579b464db66ec23bdd000001cdd3946e44ce4aad7209ff7b23ac571b",
            "format": "json",
            "filters[commodity]": crop
        }

        response = requests.get(url, params=params)

        data = response.json()

        print("API RESPONSE:", data)   # 👈 ADD THIS

        if "records" in data and len(data["records"]) > 0:
            return float(data["records"][0]["modal_price"])

    except Exception as e:
        print("ERROR:", e)

    return None
# -----------------------
# View Expenses
# -----------------------
@app.route("/expenses")
def expenses():
    if "user" not in session:
        return redirect("/login")

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM expenses ORDER BY expense_date DESC")
    expenses_data = cursor.fetchall()

    conn.close()
    return render_template("expenses.html", expenses=expenses_data)
# -----------------------
# Farmer Report
# -----------------------
@app.route("/farmer_report")
def farmer_report():
    if "user" not in session:
        return redirect("/login")

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT farmers.name,
               COUNT(DISTINCT crops.id) as total_crops,
               IFNULL(SUM(DISTINCT sales.total_amount), 0) as total_revenue,
               IFNULL(SUM(DISTINCT expenses.amount), 0) as total_expenses
        FROM farmers
        LEFT JOIN crops ON farmers.id = crops.farmer_id
        LEFT JOIN sales ON crops.id = sales.crop_id
        LEFT JOIN expenses ON farmers.id = expenses.farmer_id
        GROUP BY farmers.id
    """)

    data = cursor.fetchall()
    conn.close()

    # calculate net profit
    report = []
    for row in data:
        name = row[0]
        crops = row[1]
        revenue = row[2]
        expenses = row[3]
        profit = revenue - expenses
        report.append((name, crops, revenue, expenses, profit))

    return render_template("farmer_report.html", report=report)

# -----------------------
# Export Farmer Report PDF
# -----------------------
@app.route("/export_farmer_report")
def export_farmer_report():
    if "user" not in session:
        return redirect("/login")

    from datetime import datetime

    conn = get_connection()
    cursor = conn.cursor()

    # 🔥 More accurate revenue & expense calculation
    cursor.execute("""
        SELECT f.name,
               IFNULL(SUM(s.total_amount), 0) AS revenue,
               IFNULL((SELECT SUM(e.amount)
                       FROM expenses e
                       WHERE e.farmer_id = f.id), 0) AS expense
        FROM farmers f
        LEFT JOIN crops c ON f.id = c.farmer_id
        LEFT JOIN sales s ON c.id = s.crop_id
        GROUP BY f.id
    """)

    data = cursor.fetchall()
    conn.close()

    # Create PDF file
    file_path = "farmer_report.pdf"
    doc = SimpleDocTemplate(file_path, pagesize=pagesizes.A4)
    elements = []

    styles = getSampleStyleSheet()

    # 🔥 Title
    elements.append(Paragraph("Agricultural Cooperative ERP System", styles['Title']))
    elements.append(Spacer(1, 0.3 * inch))

    elements.append(Paragraph("Farmer Performance Report", styles['Heading2']))
    elements.append(Spacer(1, 0.2 * inch))

    # 🔥 Date
    current_date = datetime.now().strftime("%d-%m-%Y %H:%M")
    elements.append(Paragraph(f"Generated on: {current_date}", styles['Normal']))
    elements.append(Spacer(1, 0.4 * inch))

    # Table Header
    table_data = [["Farmer Name", "Revenue (₹)", "Expenses (₹)", "Net Profit (₹)"]]

    total_revenue = 0
    total_expense = 0
    total_profit = 0

    for row in data:
        name = row[0]
        revenue = row[1]
        expense = row[2]
        profit = revenue - expense

        total_revenue += revenue
        total_expense += expense
        total_profit += profit

        table_data.append([
            name,
            f"{revenue:.2f}",
            f"{expense:.2f}",
            f"{profit:.2f}"
        ])

    # 🔥 Add Total Summary Row
    table_data.append([
        "TOTAL",
        f"{total_revenue:.2f}",
        f"{total_expense:.2f}",
        f"{total_profit:.2f}"
    ])

    table = Table(table_data, repeatRows=1)

    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.darkgreen),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('BACKGROUND', (0, -1), (-1, -1), colors.lightgrey),
        ('GRID', (0, 0), (-1, -1), 0.8, colors.black),
        ('ALIGN', (1, 1), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica')
    ]))

    elements.append(table)
    elements.append(Spacer(1, 0.5 * inch))

    # Footer
    elements.append(Paragraph("Authorized Signature: ____________________", styles['Normal']))

    doc.build(elements)

    return send_file(file_path, as_attachment=True)
@app.route("/edit_crop/<int:crop_id>", methods=["GET", "POST"])
def edit_crop(crop_id):

    conn = get_connection()
    cursor = conn.cursor()

    if request.method == "POST":

        crop_name = request.form["crop_name"]
        quantity = request.form["quantity"]
        base_price = request.form["base_price"]
        status = request.form["status"]

        cursor.execute("""
        UPDATE crops
        SET crop_name=?, quantity=?, base_price=?, status=?
        WHERE id=?
        """, (crop_name, quantity, base_price, status, crop_id))

        conn.commit()
        conn.close()

        return redirect("/crops")

    cursor.execute("SELECT * FROM crops WHERE id=?", (crop_id,))
    crop = cursor.fetchone()

    conn.close()

    return render_template("edit_crop.html", crop=crop)
@app.route("/delete_crop/<int:crop_id>")
def delete_crop(crop_id):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("DELETE FROM crops WHERE id=?", (crop_id,))

    conn.commit()
    conn.close()

    return redirect("/crops") 

from waitress import serve
import os
from app import app  # make sure this imports your Flask app variable

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    serve(app, host="0.0.0.0", port=port)