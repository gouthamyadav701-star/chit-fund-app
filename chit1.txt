import tkinter as tk
from tkinter import messagebox, simpledialog
import sqlite3
import hashlib
from datetime import datetime

# ---------- SECURITY ----------
def hash_pass(p):
    return hashlib.sha256(p.encode()).hexdigest()

def strong_pass(p):
    return len(p) >= 8 and any(c.isupper() for c in p) and any(c.isdigit() for c in p)

# ---------- DATABASE ----------
def db():
    return sqlite3.connect("chit_ultra.db")

def init_db():
    con = db()
    cur = con.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        password TEXT,
        role TEXT,
        approved INTEGER
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS members(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        total REAL,
        paid REAL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS payments(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        member_id INTEGER,
        amount REAL,
        date TEXT
    )
    """)

    # default admin
    cur.execute("SELECT * FROM users WHERE username='admin'")
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO users(username,password,role,approved) VALUES(?,?,?,?)",
            ("admin", hash_pass("Admin123"), "admin", 1)
        )

    con.commit()
    con.close()

# ---------- LOGIN ----------
def login():
    u = user_entry.get()
    p = pass_entry.get()

    con = db()
    cur = con.cursor()

    cur.execute("""
    SELECT * FROM users 
    WHERE username=? AND password=? AND approved=1
    """, (u, hash_pass(p)))

    user = cur.fetchone()
    con.close()

    if user:
        root.destroy()
        home(user[3])  # role
    else:
        messagebox.showerror("Error", "Invalid / Not approved")

# ---------- REGISTER ----------
def register():
    def submit():
        u = ru.get()
        p = rp.get()
        c = rc.get()
        role = role_var.get()

        if p != c:
            messagebox.showerror("Error", "Password mismatch")
            return

        if not strong_pass(p):
            messagebox.showerror("Error", "Weak password")
            return

        con = db()
        con.execute(
            "INSERT INTO users(username,password,role,approved) VALUES(?,?,?,0)",
            (u, hash_pass(p), role)
        )
        con.commit()
        con.close()

        messagebox.showinfo("Success", "Wait for admin approval")
        reg.destroy()

    reg = tk.Toplevel()
    reg.title("Register")

    ru = tk.Entry(reg)
    rp = tk.Entry(reg, show="*")
    rc = tk.Entry(reg, show="*")
    role_var = tk.StringVar(value="staff")

    tk.Label(reg, text="Username").pack()
    ru.pack()

    tk.Label(reg, text="Password").pack()
    rp.pack()

    tk.Label(reg, text="Confirm Password").pack()
    rc.pack()

    tk.OptionMenu(reg, role_var, "admin", "staff").pack()

    tk.Button(reg, text="Register", command=submit).pack()

# ---------- HOME ----------
def home(role):
    win = tk.Tk()
    win.title("ChitFund Ultra")

    name = tk.Entry(win)
    amount = tk.Entry(win)

    name.pack()
    amount.pack()

    listbox = tk.Listbox(win, width=60)
    listbox.pack()

    def load():
        listbox.delete(0, tk.END)
        con = db()
        cur = con.cursor()

        cur.execute("SELECT * FROM members")
        for m in cur.fetchall():
            due = m[2] - m[3]
            listbox.insert(tk.END, f"{m[1]} | Paid {m[3]} | Due {due}")

        con.close()

    def add_member():
        try:
            con = db()
            con.execute(
                "INSERT INTO members(name,total,paid) VALUES(?,?,0)",
                (name.get(), float(amount.get()))
            )
            con.commit()
            con.close()
            load()
        except:
            messagebox.showerror("Error", "Invalid input")

    def pay():
        idx = listbox.curselection()
        if not idx:
            return

        amt = simpledialog.askfloat("Payment", "Enter amount")
        if not amt:
            return

        con = db()
        cur = con.cursor()

        cur.execute("SELECT id FROM members")
        member_id = cur.fetchall()[idx[0]][0]

        cur.execute(
            "UPDATE members SET paid = paid + ? WHERE id=?",
            (amt, member_id)
        )

        cur.execute(
            "INSERT INTO payments(member_id,amount,date) VALUES(?,?,?)",
            (member_id, amt, datetime.now().isoformat())
        )

        con.commit()
        con.close()
        load()

    def view_history():
        idx = listbox.curselection()
        if not idx:
            return

        con = db()
        cur = con.cursor()

        cur.execute("SELECT id FROM members")
        member_id = cur.fetchall()[idx[0]][0]

        cur.execute(
            "SELECT amount,date FROM payments WHERE member_id=?",
            (member_id,)
        )

        data = cur.fetchall()
        con.close()

        msg = "\n".join([f"₹{a} on {d}" for a, d in data])
        messagebox.showinfo("Payment History", msg)

    tk.Button(win, text="Add Member", command=add_member).pack()
    tk.Button(win, text="Pay", command=pay).pack()
    tk.Button(win, text="History", command=view_history).pack()

    load()
    win.mainloop()

# ---------- START ----------
init_db()

root = tk.Tk()
root.title("Login")

user_entry = tk.Entry(root)
pass_entry = tk.Entry(root, show="*")

user_entry.pack()
pass_entry.pack()

tk.Button(root, text="Login", command=login).pack()
tk.Button(root, text="Register", command=register).pack()

root.mainloop()