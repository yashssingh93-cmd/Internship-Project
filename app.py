"""Codic Desk - Asset & IT Ticket Portal (backend).

A small Flask + SQLite REST API that stores hardware assets and IT support
tickets, and also serves the single-page frontend from /static/index.html.
"""
import os
import re
import sqlite3

from flask import Flask, g, jsonify, request, send_from_directory

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

# Vercel serverless functions have a read-only filesystem except for /tmp
if os.environ.get("VERCEL"):
    DB_PATH = "/tmp/codic_desk.db"
else:
    DB_PATH = os.path.join(BASE_DIR, "codic_desk.db")

DEPARTMENTS = ("Engineering", "HR", "Sales", "Design")
CATEGORIES = ("Laptop", "Monitor", "Keyboard", "Mouse")
PRIORITIES = ("Low", "Medium", "High")
TICKET_STATUSES = ("Open", "In Progress", "Resolved")
TAG_PATTERN = re.compile(r"^[A-Z0-9]+(-[A-Z0-9]+)+$")

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")
app.config["DATABASE"] = DB_PATH


# ---------------------------------------------------------------- database
def get_db():
    """One SQLite connection per request, closed automatically afterwards."""
    if "db" not in g:
        g.db = sqlite3.connect(app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    tag       TEXT NOT NULL UNIQUE,
    name      TEXT NOT NULL,
    category  TEXT NOT NULL,
    employee  TEXT NOT NULL DEFAULT '',
    dept      TEXT NOT NULL,
    status    TEXT NOT NULL CHECK (status IN ('Assigned', 'In Storage'))
);
CREATE TABLE IF NOT EXISTS tickets (
    num        INTEGER PRIMARY KEY AUTOINCREMENT,
    summary    TEXT NOT NULL,
    requester  TEXT NOT NULL,
    tag        TEXT NOT NULL DEFAULT '',
    priority   TEXT NOT NULL CHECK (priority IN ('Low', 'Medium', 'High')),
    status     TEXT NOT NULL CHECK (status IN ('Open', 'In Progress', 'Resolved')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_assets_dept ON assets(dept);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
"""

SEED_ASSETS = [
    ("CODIC-LP-101", "ThinkPad T14", "Laptop", "Riya Sharma", "Engineering", "Assigned"),
    ("CODIC-MN-201", 'Dell UltraSharp 27"', "Monitor", "Kabir Mehta", "Design", "Assigned"),
    ("CODIC-KB-301", "Keychron K2", "Keyboard", "", "Engineering", "In Storage"),
    ("CODIC-LP-102", "HP EliteBook 840", "Laptop", "Neha Kapoor", "HR", "Assigned"),
    ("CODIC-MS-401", "Logitech MX Master 3", "Mouse", "Arjun Singh", "Sales", "Assigned"),
    ("CODIC-MN-202", 'LG 24" IPS Monitor', "Monitor", "", "Sales", "In Storage"),
]
SEED_TICKETS = [
    ("Battery drains rapidly", "Riya Sharma", "CODIC-LP-101", "High", "Open"),
    ("External display flickering", "Kabir Mehta", "CODIC-MN-201", "Medium", "In Progress"),
    ("Mouse scroll wheel not responding", "Arjun Singh", "CODIC-MS-401", "Low", "Resolved"),
]


def init_db():
    """Create tables and load the demo records the first time only."""
    db = sqlite3.connect(app.config["DATABASE"])
    db.executescript(SCHEMA)
    if db.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 0:
        db.executemany(
            "INSERT INTO assets (tag, name, category, employee, dept, status) VALUES (?,?,?,?,?,?)",
            SEED_ASSETS,
        )
        # Ticket numbers start at TKT-1001.
        db.execute("INSERT INTO sqlite_sequence (name, seq) VALUES ('tickets', 1000)")
        db.executemany(
            "INSERT INTO tickets (summary, requester, tag, priority, status) VALUES (?,?,?,?,?)",
            SEED_TICKETS,
        )
    db.commit()
    db.close()


# Initialize database automatically when the app is imported/loaded in serverless
init_db()


# ----------------------------------------------------------------- helpers
def asset_json(row):
    return {k: row[k] for k in ("id", "tag", "name", "category", "employee", "dept", "status")}


def ticket_json(row):
    return {
        "id": f"TKT-{row['num']}",
        "summary": row["summary"],
        "requester": row["requester"],
        "tag": row["tag"],
        "priority": row["priority"],
        "status": row["status"],
    }


def error(message, code=400):
    return jsonify({"error": message}), code


def clean(value, limit=60):
    return str(value or "").strip()[:limit]


# ------------------------------------------------------------------ routes
@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/api/assets")
def list_assets():
    rows = get_db().execute("SELECT * FROM assets ORDER BY id DESC").fetchall()
    return jsonify([asset_json(r) for r in rows])


@app.post("/api/assets")
def create_asset():
    data = request.get_json(silent=True) or {}
    name = clean(data.get("name"))
    tag = clean(data.get("tag"), 20).upper()
    employee = clean(data.get("employee"))
    category, dept = data.get("category"), data.get("dept")

    if not name:
        return error("Enter an asset name.")
    if not TAG_PATTERN.match(tag):
        return error("Enter a tag like CODIC-LP-104 (letters, numbers, dashes).")
    if category not in CATEGORIES:
        return error("Choose a valid category.")
    if dept not in DEPARTMENTS:
        return error("Choose a valid department.")

    db = get_db()
    if db.execute("SELECT 1 FROM assets WHERE tag = ?", (tag,)).fetchone():
        return error("That asset tag is already registered.", 409)
    status = "Assigned" if employee else "In Storage"
    cur = db.execute(
        "INSERT INTO assets (tag, name, category, employee, dept, status) VALUES (?,?,?,?,?,?)",
        (tag, name, category, employee, dept, status),
    )
    db.commit()
    row = db.execute("SELECT * FROM assets WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(asset_json(row)), 201


@app.patch("/api/assets/<int:asset_id>/toggle")
def toggle_asset(asset_id):
    """Assigned -> In Storage (clears employee); In Storage -> Assigned (needs employee)."""
    db = get_db()
    row = db.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    if row is None:
        return error("Asset not found.", 404)
    if row["status"] == "Assigned":
        db.execute("UPDATE assets SET status = 'In Storage', employee = '' WHERE id = ?", (asset_id,))
    else:
        employee = clean((request.get_json(silent=True) or {}).get("employee"))
        if not employee:
            return error("Employee name is required to assign this asset.")
        db.execute("UPDATE assets SET status = 'Assigned', employee = ? WHERE id = ?", (employee, asset_id))
    db.commit()
    row = db.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    return jsonify(asset_json(row))


@app.delete("/api/assets/<int:asset_id>")
def delete_asset(asset_id):
    db = get_db()
    cur = db.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
    db.commit()
    if cur.rowcount == 0:
        return error("Asset not found.", 404)
    return jsonify({"deleted": asset_id})


@app.get("/api/tickets")
def list_tickets():
    rows = get_db().execute("SELECT * FROM tickets ORDER BY num DESC").fetchall()
    return jsonify([ticket_json(r) for r in rows])


@app.post("/api/tickets")
def create_ticket():
    data = request.get_json(silent=True) or {}
    requester = clean(data.get("requester"))
    summary = clean(data.get("summary"), 200)
    tag = clean(data.get("tag"), 20).upper()
    priority = data.get("priority")

    if not requester:
        return error("Enter the requester name.")
    if len(summary) < 5:
        return error("Describe the issue in at least 5 characters.")
    if priority not in PRIORITIES:
        return error("Choose a valid priority.")

    db = get_db()
    if tag and not db.execute("SELECT 1 FROM assets WHERE tag = ?", (tag,)).fetchone():
        return error("Linked asset tag does not exist.", 404)
    cur = db.execute(
        "INSERT INTO tickets (summary, requester, tag, priority, status) VALUES (?,?,?,?,'Open')",
        (summary, requester, tag, priority),
    )
    db.commit()
    row = db.execute("SELECT * FROM tickets WHERE num = ?", (cur.lastrowid,)).fetchone()
    return jsonify(ticket_json(row)), 201


@app.patch("/api/tickets/<ticket_id>")
def update_ticket(ticket_id):
    match = re.fullmatch(r"TKT-(\d+)", ticket_id)
    status = (request.get_json(silent=True) or {}).get("status")
    if not match:
        return error("Ticket not found.", 404)
    if status not in TICKET_STATUSES:
        return error("Choose a valid status.")
    db = get_db()
    cur = db.execute("UPDATE tickets SET status = ? WHERE num = ?", (status, int(match.group(1))))
    db.commit()
    if cur.rowcount == 0:
        return error("Ticket not found.", 404)
    row = db.execute("SELECT * FROM tickets WHERE num = ?", (int(match.group(1)),)).fetchone()
    return jsonify(ticket_json(row))


@app.get("/api/stats")
def stats():
    db = get_db()
    one = lambda sql: db.execute(sql).fetchone()[0]
    by_dept = {d: 0 for d in DEPARTMENTS}
    for r in db.execute("SELECT dept, COUNT(*) AS n FROM assets GROUP BY dept"):
        by_dept[r["dept"]] = r["n"]
    return jsonify({
        "total_assets": one("SELECT COUNT(*) FROM assets"),
        "in_storage": one("SELECT COUNT(*) FROM assets WHERE status = 'In Storage'"),
        "open_tickets": one("SELECT COUNT(*) FROM tickets WHERE status != 'Resolved'"),
        "resolved_tickets": one("SELECT COUNT(*) FROM tickets WHERE status = 'Resolved'"),
        "assets_by_department": by_dept,
    })


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
