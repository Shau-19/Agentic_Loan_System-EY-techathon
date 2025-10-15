# src/data/database.py

import sqlite3
import os
import json
import random
import time

# Base directory & database file path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "nbfc_data.db")


class NBFCDatabase:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        first_init = not os.path.exists(db_path)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

        if first_init:
            print("🧱 Setting up new NBFC database...")
            self.setup_database()
            self.seed_synthetic_data()
            print("✅ Database setup complete!")
        else:
            print("📁 Using existing NBFC database")

    def setup_database(self):
        """Create necessary tables"""
        cur = self.conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS customers (
                customer_id TEXT PRIMARY KEY,
                name TEXT,
                age INTEGER,
                city TEXT,
                phone TEXT,
                email TEXT,
                annual_income REAL,
                employment_type TEXT,
                credit_score INTEGER,
                pre_approved_limit REAL
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS loan_applications (
                application_id TEXT PRIMARY KEY,
                customer_id TEXT,
                loan_amount REAL,
                tenure_months INTEGER,
                interest_rate REAL,
                monthly_emi REAL,
                status TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT,
                customer_id TEXT,
                agent_type TEXT,
                message_type TEXT,
                message_content TEXT,
                metadata TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self.conn.commit()

    def seed_synthetic_data(self):
        """Insert 10 mock customers"""
        print("👷‍♂️ Seeding synthetic customer data...")
        customers = []

        names = [
            "Amit Sharma", "Neha Gupta", "Rahul Verma", "Priya Singh",
            "Rohit Mehta", "Sana Ali", "Vikram Rao", "Deepa Iyer",
            "Karan Jain", "Mona Das"
        ]
        cities = ["Delhi", "Mumbai", "Bangalore", "Chennai", "Hyderabad", "Pune", "Kolkata"]

        for i, name in enumerate(names):
            cust_id = f"CUST{2001 + i}"
            annual_income = random.randint(400000, 1800000)
            credit_score = random.randint(600, 900)
            limit = int(annual_income * 0.3 * (credit_score / 800))
            customers.append({
                "customer_id": cust_id,
                "name": name,
                "age": random.randint(25, 60),
                "city": random.choice(cities),
                "phone": f"+91 {random.randint(9000000000, 9999999999)}",
                "email": name.lower().replace(" ", ".") + "@example.com",
                "annual_income": annual_income,
                "employment_type": random.choice(["Salaried", "Self-Employed", "Business Owner"]),
                "credit_score": credit_score,
                "pre_approved_limit": limit
            })

        cur = self.conn.cursor()
        for c in customers:
            cur.execute("""
                INSERT OR REPLACE INTO customers
                (customer_id, name, age, city, phone, email, annual_income, employment_type, credit_score, pre_approved_limit)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                c["customer_id"], c["name"], c["age"], c["city"], c["phone"],
                c["email"], c["annual_income"], c["employment_type"],
                c["credit_score"], c["pre_approved_limit"]
            ))

        self.conn.commit()
        print(f"✅ Inserted {len(customers)} customers into database.")

    def get_customer(self, customer_id: str):
        cur = self.conn.execute("SELECT * FROM customers WHERE customer_id = ?", (customer_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def get_customer_by_phone(self, phone: str):
        if not phone:
            return None
        digits = ''.join(ch for ch in phone if ch.isdigit())[-10:]
        cur = self.conn.execute("SELECT * FROM customers")
        for r in cur.fetchall():
            db_digits = ''.join(ch for ch in r["phone"] if ch.isdigit())[-10:]
            if db_digits == digits:
                return dict(r)
        return None

    def create_loan_application(self, app: dict):
        """
        Create a new loan application record.

        Required keys in `app`:
          - customer_id
          - loan_amount
          - tenure_months
          - interest_rate
          - monthly_emi
          - status

        Optional:
          - application_id : if provided, will be used as the primary key (business ID)
        Returns:
          - application_id (str) if provided OR inserted application_id (str) if provided,
            otherwise returns the sqlite lastrowid as int (legacy behavior).
        """
        cur = self.conn.cursor()

        required_keys = ["customer_id", "loan_amount", "tenure_months", "interest_rate", "monthly_emi", "status"]
        for key in required_keys:
            if key not in app:
                raise ValueError(f"Missing required field '{key}' in loan application")

        provided_app_id = app.get("application_id")

        if provided_app_id:
            cur.execute("""
                INSERT INTO loan_applications
                (application_id, customer_id, loan_amount, tenure_months, interest_rate, monthly_emi, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                provided_app_id,
                app["customer_id"],
                app["loan_amount"],
                app["tenure_months"],
                app["interest_rate"],
                app["monthly_emi"],
                app["status"],
            ))
            self.conn.commit()
            return provided_app_id
        else:
            # generate a basic business-like application id to store as TEXT key so it's human-friendly
            generated_id = f"DBAPP{int(time.time())}{random.randint(100,999)}"
            cur.execute("""
                INSERT INTO loan_applications
                (application_id, customer_id, loan_amount, tenure_months, interest_rate, monthly_emi, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                generated_id,
                app["customer_id"],
                app["loan_amount"],
                app["tenure_months"],
                app["interest_rate"],
                app["monthly_emi"],
                app["status"],
            ))
            self.conn.commit()
            return generated_id

    def get_loan_application(self, application_id: str):
        """Fetch a loan application by application_id (business id or DB-generated id)."""
        cur = self.conn.execute("SELECT * FROM loan_applications WHERE application_id = ?", (application_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def log_conversation(self, conversation_id: str, customer_id: str, agent_type: str, message_type: str, message_content: str, metadata: dict = None):
        cur = self.conn.cursor()
        cur.execute('''
            INSERT INTO conversations
            (conversation_id, customer_id, agent_type, message_type, message_content, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            conversation_id,
            customer_id,
            agent_type,
            message_type,
            message_content,
            json.dumps(metadata or {})
        ))
        self.conn.commit()

    def get_conversation_history(self, conversation_id: str):
        cur = self.conn.execute('''
            SELECT * FROM conversations
            WHERE conversation_id = ?
            ORDER BY timestamp ASC
        ''', (conversation_id,))
        return [dict(r) for r in cur.fetchall()]


# 🧪 Run this file directly to test the DB setup
if __name__ == "__main__":
    db = NBFCDatabase()
    all_customers = db.conn.execute("SELECT * FROM customers").fetchall()

    print("\n📋 Current Customers in DB:")
    for row in all_customers:
        print(f"{row['customer_id']}: {row['name']} ({row['city']}) - Credit Score {row['credit_score']}")
