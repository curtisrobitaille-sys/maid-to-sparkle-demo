"""Maid To Sparkle — cleaning-service booking demo. Sales-demo web app.

Built by HalifaxFX as a demonstration for a Halifax cleaning business that
currently runs all bookings through Facebook Messenger.

Self-contained: Flask + sqlite3 only. No email, no push, no third-party APIs.
All names, phone numbers and addresses are fictional sample data.
Seed data is created on first boot (only when the tables are empty).

Run:  python app.py            (uses $PORT or 5000)
  or: gunicorn app:app

Demo owner login:  owner / sparkle-demo
"""
import os
import random
import sqlite3
from datetime import date, timedelta
from functools import wraps

from flask import (Flask, abort, flash, g, get_flashed_messages, redirect,
                   render_template_string, request, session, url_for)

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "demo.db")

app = Flask(__name__)
app.secret_key = "maid-demo-only-not-a-real-secret"

OWNER_USER = "owner"
OWNER_PASS = "sparkle-demo"

SERVICES = [
    ("residential", "Residential Cleaning",
     "Regular home cleans — kitchens, bathrooms, floors, dusting, the works."),
    ("deep", "Deep Clean",
     "Top-to-bottom reset: baseboards, vents, inside appliances available."),
    ("airbnb", "Airbnb Turnover",
     "Fast, reliable turnovers with laundry and restock — guest-ready every time."),
    ("move", "Move-In / Move-Out",
     "Empty-home cleans that get deposits back and new homes move-in ready."),
    ("construction", "Post-Construction",
     "Dust, debris and detail work after the reno crew leaves."),
    ("commercial", "Commercial",
     "Offices and small businesses, scheduled around your hours."),
]
SERVICE_NAMES = {k: v for k, v, _d in SERVICES}

SLOTS = ["8:00 AM", "10:00 AM", "12:30 PM", "2:30 PM", "4:30 PM"]

STATUS_LABELS = {
    "requested": "New request",
    "confirmed": "Confirmed",
    "on_the_way": "On the way",
    "arrived": "Arrived",
    "done": "Done",
    "cancelled": "Cancelled",
}


# ---------------------------------------------------------------- db helpers
def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def _close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS cleaners (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS clients (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            name    TEXT NOT NULL,
            phone   TEXT DEFAULT '',
            email   TEXT DEFAULT '',
            area    TEXT DEFAULT '',
            address TEXT DEFAULT '',
            notes   TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS bookings (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            ref        TEXT NOT NULL UNIQUE,
            client_id  INTEGER REFERENCES clients(id),
            name       TEXT NOT NULL,
            phone      TEXT DEFAULT '',
            email      TEXT DEFAULT '',
            address    TEXT DEFAULT '',
            area       TEXT DEFAULT '',
            service    TEXT NOT NULL,
            day        TEXT NOT NULL,
            slot       TEXT NOT NULL,
            cleaner_id INTEGER REFERENCES cleaners(id),
            status     TEXT NOT NULL DEFAULT 'requested',
            notes      TEXT DEFAULT '',
            created_at TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS quotes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            phone       TEXT DEFAULT '',
            email       TEXT DEFAULT '',
            area        TEXT DEFAULT '',
            prop_type   TEXT DEFAULT '',
            bedrooms    TEXT DEFAULT '',
            bathrooms   TEXT DEFAULT '',
            extras      TEXT DEFAULT '',
            sqft        TEXT DEFAULT '',
            service     TEXT DEFAULT '',
            frequency   TEXT DEFAULT '',
            pets        TEXT DEFAULT '',
            pets_text   TEXT DEFAULT '',
            products    TEXT DEFAULT '',
            pref_dates  TEXT DEFAULT '',
            notes       TEXT DEFAULT '',
            status      TEXT NOT NULL DEFAULT 'new',
            created_at  TEXT DEFAULT ''
        );
        """
    )
    conn.commit()
    if conn.execute("SELECT COUNT(*) FROM cleaners").fetchone()[0] == 0:
        seed(conn)
    conn.close()


# ---------------------------------------------------------------- seed data
CLEANERS = ["Alyssa R.", "Kayla M.", "Brittany S."]

# (name, phone, email, area, address, notes) — all fictional sample data
CLIENTS = [
    ("Jenna P.", "902-555-0101", "jenna.p@example.ca", "Fairview",
     "Fairview — Dutch Village Rd area",
     "Two cats — please keep the front door closed. Eco products only."),
    ("Marcus T.", "902-555-0102", "marcus.t@example.ca", "Clayton Park",
     "Clayton Park — Lacewood Dr area",
     "Gate code in the booking note. Dog is friendly, name is Biscuit."),
    ("Priya K.", "902-555-0103", "priya.k@example.ca", "Bedford",
     "Bedford — Larry Uteck Blvd area",
     "Airbnb host — laundry and towel restock on every turnover."),
    ("Erin C.", "902-555-0104", "erin.c@example.ca", "Dartmouth",
     "Dartmouth — Woodside area",
     "Key in the lockbox by the side door."),
    ("Denise W.", "902-555-0105", "denise.w@example.ca", "Lower Sackville",
     "Lower Sackville — Beaver Bank Rd area",
     "Allergic to strong scents — eco products, no bleach."),
    ("Karim A.", "902-555-0106", "karim.a@example.ca", "Cole Harbour",
     "Cole Harbour — Forest Hills Pkwy area",
     "New build — post-construction dust, be gentle on fresh paint."),
    ("Barb S.", "902-555-0107", "barb.s@example.ca", "Halifax South End",
     "South End — South St area",
     "Biweekly regular. Extra attention to the kitchen, please."),
    ("Tom H.", "902-555-0108", "tom.h@example.ca", "Bedford",
     "Bedford — Hammonds Plains Rd area",
     "Office clean after hours — alarm code provided by owner."),
]


def seed(conn):
    today = date.today()

    def d(offset):
        return (today + timedelta(days=offset)).isoformat()

    cleaner_ids = {}
    for name in CLEANERS:
        cur = conn.execute("INSERT INTO cleaners (name) VALUES (?)", (name,))
        cleaner_ids[name] = cur.lastrowid

    client_ids = {}
    for name, phone, email, area, address, notes in CLIENTS:
        cur = conn.execute(
            """INSERT INTO clients (name, phone, email, area, address, notes)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (name, phone, email, area, address, notes),
        )
        client_ids[name] = cur.lastrowid

    rng = random.Random(2026)
    ref_seq = [1000]

    def booking(client, service, offset, slot, cleaner, status, notes=""):
        name, phone, email, area, address, _n = next(
            c for c in CLIENTS if c[0] == client)
        ref_seq[0] += rng.randint(7, 89)
        conn.execute(
            """INSERT INTO bookings
               (ref, client_id, name, phone, email, address, area, service,
                day, slot, cleaner_id, status, notes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("MTS-%d" % ref_seq[0], client_ids[client], name, phone, email,
             address, area, service, d(offset), slot,
             cleaner_ids.get(cleaner) if cleaner else None, status, notes,
             today.isoformat()),
        )

    # Today — the owner's board is never empty.
    booking("Jenna P.", "residential", 0, "8:00 AM", "Alyssa R.", "confirmed",
            "Biweekly regular. Two cats — door closed, eco products.")
    booking("Marcus T.", "deep", 0, "12:30 PM", "Kayla M.", "on_the_way",
            "First deep clean. Gate code with the office.")
    booking("Priya K.", "airbnb", 0, "10:00 AM", "Brittany S.", "done",
            "Checkout 11 AM — laundry + restock, next guests 3 PM.")
    booking("Erin C.", "residential", 0, "2:30 PM", None, "requested",
            "New customer — booked through the website demo.")
    # Tomorrow.
    booking("Denise W.", "deep", 1, "8:00 AM", "Alyssa R.", "confirmed",
            "No bleach — eco products only.")
    booking("Barb S.", "residential", 1, "10:00 AM", "Brittany S.", "confirmed",
            "Biweekly regular. Kitchen focus.")
    booking("Karim A.", "construction", 1, "12:30 PM", None, "requested",
            "Post-construction — may need a walkthrough call first.")
    # Later this week.
    booking("Priya K.", "airbnb", 2, "10:00 AM", "Kayla M.", "confirmed",
            "Turnover — laundry + restock.")
    booking("Jenna P.", "residential", 4, "8:00 AM", "Alyssa R.", "requested",
            "Asked to add inside-windows extra this visit.")
    booking("Tom H.", "commercial", 6, "4:30 PM", "Kayla M.", "confirmed",
            "Office clean after hours.")

    quotes = [
        ("Sample — Heather D.", "902-555-0111", "heather.d@example.ca",
         "Bedford", "house", "3", "2", "den/office, inside appliances",
         "1,500–2,000 sq ft", "deep", "one-time", "yes",
         "One small dog, very friendly", "eco",
         "Next Tuesday or Thursday morning",
         "Moving in next week — hoping for a deep clean before the furniture arrives."),
        ("Sample — Scott M.", "902-555-0112", "scott.m@example.ca",
         "Dartmouth", "condo", "2", "1.5", "finished basement",
         "1,000–1,500 sq ft", "residential", "biweekly", "no", "",
         "eco", "Weekdays", "Looking for a regular biweekly clean."),
    ]
    for q in quotes:
        conn.execute(
            """INSERT INTO quotes
               (name, phone, email, area, prop_type, bedrooms, bathrooms,
                extras, sqft, service, frequency, pets, pets_text, products,
                pref_dates, notes, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?)""",
            (*q, today.isoformat()),
        )
    conn.commit()


# ---------------------------------------------------------------- page shell
CSS = """
:root{--teal:#0d9488;--teal-dark:#0f766e;--aqua:#e6fffa;--aqua2:#ccfbf1;
--gold:#d97706;--gold-soft:#fef3c7;--ink:#134e4a;--mut:#5b7a76;
--line:#d7efec;--bg:#f7fdfc;--card:#ffffff;--red:#b91c1c}
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
background:var(--bg);color:var(--ink)}
a{color:var(--teal-dark)}
header.site{background:linear-gradient(120deg,#0d9488,#14b8a6);color:#fff;
padding:18px 20px}
header.site .wrap{max-width:1020px;margin:0 auto;display:flex;flex-wrap:wrap;
align-items:center;gap:10px}
.logo{font-size:26px;font-weight:800;letter-spacing:.3px;color:#fff;
text-decoration:none}
.logo .spark{color:#fcd34d}
.tagline{color:#ccfbf1;font-size:14px;margin-left:2px}
nav.main{margin-left:auto;display:flex;gap:8px;flex-wrap:wrap}
nav.main a{color:#fff;text-decoration:none;background:rgba(255,255,255,.16);
padding:8px 14px;border-radius:999px;font-size:14px;font-weight:600}
nav.main a:hover{background:rgba(255,255,255,.3)}
.wrap{max-width:1020px;margin:0 auto;padding:22px 18px 40px}
h1{font-size:30px;margin:8px 0 6px}
h2{font-size:21px;margin:26px 0 10px}
.sub{color:var(--mut);font-size:16px;line-height:1.5}
.hero{background:var(--card);border:1px solid var(--line);border-radius:16px;
padding:28px;margin-top:18px;box-shadow:0 2px 10px rgba(13,148,136,.06)}
.hero h1{margin-top:0}
.cta-row{display:flex;gap:12px;flex-wrap:wrap;margin-top:18px}
.btn{display:inline-block;background:var(--teal);color:#fff;text-decoration:none;
padding:13px 22px;border-radius:10px;font-weight:700;font-size:16px;border:0;
cursor:pointer}
.btn:hover{background:var(--teal-dark)}
.btn.gold{background:var(--gold)}
.btn.ghost{background:#fff;color:var(--teal-dark);border:2px solid var(--teal)}
.btn.small{padding:8px 14px;font-size:14px;border-radius:8px}
.btn.red{background:var(--red)}
.btn.grey{background:#64748b}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));
gap:14px;margin-top:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;
padding:18px;box-shadow:0 1px 6px rgba(13,148,136,.05)}
.card h3{margin:0 0 6px;font-size:18px}
.card p{color:var(--mut);line-height:1.5;margin:4px 0}
.pill{display:inline-block;padding:3px 10px;border-radius:999px;font-size:12px;
font-weight:700;background:var(--aqua2);color:var(--teal-dark)}
.pill.gold{background:var(--gold-soft);color:var(--gold)}
.pill.red{background:#fee2e2;color:var(--red)}
.pill.grey{background:#e2e8f0;color:#475569}
.pill.green{background:#dcfce7;color:#166534}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--line);
border-radius:12px;overflow:hidden}
th,td{padding:10px 12px;text-align:left;border-bottom:1px solid var(--line);
font-size:14px;vertical-align:top}
th{background:var(--aqua);color:var(--teal-dark)}
form.panel{background:#fff;border:1px solid var(--line);border-radius:14px;
padding:22px;margin-top:16px}
label{display:block;font-weight:700;font-size:14px;margin:16px 0 6px}
label:first-child{margin-top:0}
input[type=text],input[type=email],input[type=tel],input[type=password],select,textarea{
width:100%;padding:11px 12px;border:1.5px solid var(--line);border-radius:9px;
font-size:16px;background:#fff;color:var(--ink)}
textarea{min-height:80px}
.checks{display:flex;flex-wrap:wrap;gap:8px}
.checks label{font-weight:500;margin:0;background:var(--bg);border:1px solid var(--line);
border-radius:999px;padding:8px 13px;cursor:pointer;font-size:14px}
.radios{display:flex;flex-wrap:wrap;gap:8px}
.radios label{font-weight:600;margin:0;border:1.5px solid var(--line);
border-radius:10px;padding:10px 14px;cursor:pointer;font-size:14px;background:#fff}
.radios input{margin-right:6px}
.radios label.taken{opacity:.45;cursor:not-allowed;background:#f1f5f9}
.two{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:640px){.two{grid-template-columns:1fr}}
.flash{background:var(--gold-soft);border:1px solid #fcd34d;color:#92400e;
border-radius:10px;padding:12px 16px;margin:14px 0;font-weight:600}
.jobcard{border-left:6px solid var(--teal)}
.jobcard .when{font-size:20px;font-weight:800}
footer.site{border-top:1px solid var(--line);color:var(--mut);font-size:12.5px;
text-align:center;padding:18px 12px 30px;line-height:1.6}
.kv{font-size:14px;line-height:1.7}
.kv b{color:var(--ink)}
.big-tap{display:block;text-align:center;padding:16px;font-size:18px;margin-top:10px}
"""


def page(title, body, **ctx):
    flashes = "".join(
        f'<div class="flash">✦ {_e(m)}</div>' for _c, m in get_flashed_messages(
            with_categories=True))
    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(title)} — Maid To Sparkle</title><style>{CSS}</style></head>
<body>
<header class="site"><div class="wrap">
<a class="logo" href="/">Maid <span class="spark">✦</span> To Sparkle</a>
<span class="tagline">Halifax &amp; HRM cleaning</span>
<nav class="main">
<a href="/">Home</a><a href="/book">Book a Clean</a>
<a href="/quote">Get a Quote</a><a href="/owner">Owner</a>
<a href="/cleaner">Cleaner View</a>
</nav></div></header>
<div class="wrap">{flashes}{body}</div>
<footer class="site">Maid To Sparkle — demonstration website.<br>
Demo built by <b>HalifaxFX</b> · Sample data only — no real bookings are taken here.</footer>
</body></html>"""
    return render_template_string(html, **ctx)


def _e(s):
    return (str(s if s is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def pretty_day(iso):
    d = date.fromisoformat(iso)
    if d == date.today():
        return "Today"
    if d == date.today() + timedelta(days=1):
        return "Tomorrow"
    return d.strftime("%A, %B %d")


def slot_taken(day_iso, slot):
    row = db().execute(
        """SELECT COUNT(*) FROM bookings
           WHERE day = ? AND slot = ? AND status != 'cancelled'""",
        (day_iso, slot),
    ).fetchone()
    return row[0] > 0


def status_pill(status):
    cls = {"requested": "gold", "confirmed": "green", "on_the_way": "gold",
           "arrived": "gold", "done": "", "cancelled": "red"}.get(status, "grey")
    return f'<span class="pill {cls}">{STATUS_LABELS.get(status, status)}</span>'


# ---------------------------------------------------------------- customer
@app.route("/")
def landing():
    cards = "".join(
        f"""<div class="card"><h3>{name}</h3><p>{desc}</p>
        <p><a href="/book?service={key}">Book this &rarr;</a> ·
        <a href="/quote?service={key}">Get a quote</a></p></div>"""
        for key, name, desc in SERVICES)
    body = f"""
    <div class="hero">
      <h1>Your space, <span style="color:var(--gold)">sparkling clean</span> — without the stress.</h1>
      <p class="sub">Reliable, detailed cleaning across <b>Halifax &amp; HRM</b> —
      homes, Airbnbs, move-outs and small businesses. Book in about a minute,
      or send a quick quote form and we'll confirm the details with you.</p>
      <div class="cta-row">
        <a class="btn" href="/book">Book a Clean</a>
        <a class="btn gold" href="/quote">Get a Quote</a>
      </div>
      <p class="sub" style="margin-top:14px">✦ Eco-friendly products by default
      &nbsp;·&nbsp; ✦ Weekly, biweekly, monthly or one-time
      &nbsp;·&nbsp; ✦ Locally owned in Halifax</p>
    </div>
    <h2>Our services</h2>
    <div class="grid">{cards}</div>
    <h2>How it works</h2>
    <div class="grid">
      <div class="card"><h3>1. Tell us about your space</h3>
      <p>Fill the 2-minute quote form — home size, extras, pets, the works —
      so your price is fast and fair.</p></div>
      <div class="card"><h3>2. Pick a time that fits</h3>
      <p>Choose a day and time from real availability. You'll get a booking
      reference right away.</p></div>
      <div class="card"><h3>3. Come home to sparkle</h3>
      <p>Your cleaner shows up on time, works the checklist, and you come
      home to a fresh, spotless space.</p></div>
    </div>"""
    return page("Home", body)


QUOTE_SERVICES = [("residential", "Maintenance / regular clean"),
                  ("deep", "Deep clean"), ("move", "Move-in / move-out"),
                  ("airbnb", "Airbnb turnover"),
                  ("construction", "Post-construction"),
                  ("commercial", "Commercial")]
EXTRAS = ["den/office", "finished basement", "laundry room",
          "inside appliances", "inside windows"]


@app.route("/quote", methods=["GET", "POST"])
def quote():
    if request.method == "POST":
        f = request.form
        name = f.get("name", "").strip()
        if not name:
            flash("Please add your name so we know who to call back.")
            return redirect(url_for("quote"))
        extras = ", ".join(f.getlist("extras"))
        db().execute(
            """INSERT INTO quotes
               (name, phone, email, area, prop_type, bedrooms, bathrooms,
                extras, sqft, service, frequency, pets, pets_text, products,
                pref_dates, notes, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?)""",
            (name, f.get("phone", "").strip(), f.get("email", "").strip(),
             f.get("area", "").strip(), f.get("prop_type", ""),
             f.get("bedrooms", ""), f.get("bathrooms", ""), extras,
             f.get("sqft", ""), f.get("service", ""), f.get("frequency", ""),
             f.get("pets", ""), f.get("pets_text", "").strip(),
             f.get("products", "eco"), f.get("pref_dates", "").strip(),
             f.get("notes", "").strip(), date.today().isoformat()),
        )
        db().commit()
        flash("Thanks, %s! Your quote request is in — we'll confirm the "
              "details and price with you shortly." % name.split()[0])
        return redirect(url_for("landing"))
    preset = request.args.get("service", "residential")
    svc_opts = "".join(
        f'<option value="{k}"{" selected" if k == preset else ""}>{v}</option>'
        for k, v in QUOTE_SERVICES)
    extras_html = "".join(
        f'<label><input type="checkbox" name="extras" value="{x}"> {x}</label>'
        for x in EXTRAS)
    body = f"""
    <h1>Get a quote</h1>
    <p class="sub">Two minutes now saves the whole back-and-forth later —
    every answer lands in one tidy summary we can price from.</p>
    <form class="panel" method="post">
      <div class="two">
        <div><label>Your name *</label><input type="text" name="name" required></div>
        <div><label>Phone</label><input type="tel" name="phone"></div>
      </div>
      <div class="two">
        <div><label>Email</label><input type="email" name="email"></div>
        <div><label>Area (e.g. Bedford, Dartmouth)</label>
        <input type="text" name="area"></div>
      </div>
      <div class="two">
        <div><label>Property type</label><select name="prop_type">
          <option>house</option><option>apartment</option>
          <option>condo</option><option>townhouse</option></select></div>
        <div><label>Square footage</label><select name="sqft">
          <option>under 1,000 sq ft</option><option>1,000–1,500 sq ft</option>
          <option>1,500–2,000 sq ft</option><option>2,000–2,500 sq ft</option>
          <option>2,500+ sq ft</option></select></div>
      </div>
      <div class="two">
        <div><label>Bedrooms</label><select name="bedrooms">
          <option>1</option><option>2</option><option selected>3</option>
          <option>4+</option></select></div>
        <div><label>Bathrooms</label><select name="bathrooms">
          <option>1</option><option>1.5</option><option selected>2</option>
          <option>2.5</option><option>3+</option></select></div>
      </div>
      <label>Extras (check any that apply)</label>
      <div class="checks">{extras_html}</div>
      <div class="two">
        <div><label>Service type</label>
        <select name="service">{svc_opts}</select></div>
        <div><label>How often?</label><select name="frequency">
          <option>one-time</option><option>weekly</option>
          <option>biweekly</option><option>monthly</option></select></div>
      </div>
      <div class="two">
        <div><label>Any pets?</label><select name="pets">
          <option value="no">No</option><option value="yes">Yes</option>
          </select></div>
        <div><label>If yes — tell us about them</label>
        <input type="text" name="pets_text" placeholder="e.g. one friendly dog"></div>
      </div>
      <label>Cleaning products</label>
      <div class="radios">
        <label><input type="radio" name="products" value="eco" checked>
        Eco / low-tox (our default)</label>
        <label><input type="radio" name="products" value="standard">
        Standard products are fine</label>
      </div>
      <label>Preferred dates</label>
      <input type="text" name="pref_dates" placeholder="e.g. next Tue or Thu morning">
      <label>Anything we should know?</label>
      <textarea name="notes" placeholder="Entry instructions, priorities, sensitivities…"></textarea>
      <p style="margin-top:20px"><button class="btn gold" type="submit">
      Send my quote request</button></p>
    </form>"""
    return page("Get a Quote", body)


@app.route("/book", methods=["GET", "POST"])
def book():
    if request.method == "POST":
        f = request.form
        name = f.get("name", "").strip()
        day_iso = f.get("day", "")
        slot = f.get("slot", "")
        service = f.get("service", "residential")
        if service not in SERVICE_NAMES:
            service = "residential"
        if not name or not day_iso or not slot:
            flash("Please pick a day and time, and add your name.")
            return redirect(url_for("book", service=service))
        try:
            chosen = date.fromisoformat(day_iso)
        except ValueError:
            flash("That date didn't look right — please pick again.")
            return redirect(url_for("book", service=service))
        if chosen < date.today() or chosen > date.today() + timedelta(days=14):
            flash("Please pick a date within the next two weeks.")
            return redirect(url_for("book", service=service))
        if slot not in SLOTS or slot_taken(day_iso, slot):
            flash("Sorry — that time was just taken. Please pick another.")
            return redirect(url_for("book", service=service, day=day_iso))
        phone = f.get("phone", "").strip()
        email = f.get("email", "").strip()
        address = f.get("address", "").strip()
        area = f.get("area", "").strip()
        notes = f.get("notes", "").strip()
        # Upsert the client so the owner sees one tidy book.
        client_id = None
        if email:
            row = db().execute("SELECT id FROM clients WHERE email = ?",
                               (email,)).fetchone()
            if row:
                client_id = row["id"]
        if client_id is None:
            cur = db().execute(
                """INSERT INTO clients (name, phone, email, area, address, notes)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (name, phone, email, area, address, notes))
            client_id = cur.lastrowid
        ref = "MTS-%d" % random.Random().randint(10000, 99999)
        while db().execute("SELECT id FROM bookings WHERE ref = ?",
                           (ref,)).fetchone():
            ref = "MTS-%d" % random.Random().randint(10000, 99999)
        db().execute(
            """INSERT INTO bookings
               (ref, client_id, name, phone, email, address, area, service,
                day, slot, cleaner_id, status, notes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'requested', ?, ?)""",
            (ref, client_id, name, phone, email, address, area, service,
             day_iso, slot, notes, date.today().isoformat()))
        db().commit()
        return redirect(url_for("booked", ref=ref))
    preset = request.args.get("service", "residential")
    if preset not in SERVICE_NAMES:
        preset = "residential"
    day_iso = request.args.get("day", "")
    today = date.today()
    try:
        chosen = date.fromisoformat(day_iso) if day_iso else today + timedelta(days=1)
    except ValueError:
        chosen = today + timedelta(days=1)
    if chosen < today or chosen > today + timedelta(days=14):
        chosen = today + timedelta(days=1)
    day_links = []
    for i in range(15):
        d = today + timedelta(days=i)
        label = ("Today" if i == 0 else "Tomorrow" if i == 1
                 else d.strftime("%a %d"))
        active = ' style="background:var(--teal);color:#fff"' if d == chosen else ""
        day_links.append(
            f'<a class="btn ghost small" href="/book?service={preset}'
            f'&day={d.isoformat()}"{active}>{label}</a>')
    slot_radios = []
    for s in SLOTS:
        taken = slot_taken(chosen.isoformat(), s)
        if taken:
            slot_radios.append(
                f'<label class="taken"><input type="radio" disabled> '
                f'{s} — booked</label>')
        else:
            slot_radios.append(
                f'<label><input type="radio" name="slot" value="{s}" required>'
                f' {s}</label>')
    svc_opts = "".join(
        f'<option value="{k}"{" selected" if k == preset else ""}>{v}</option>'
        for k, v, _d in SERVICES)
    body = f"""
    <h1>Book a clean</h1>
    <p class="sub">Pick a service, a day and a start time — real availability,
    no phone tag. Times shown are start times.</p>
    <form class="panel" method="post">
      <label>Service</label>
      <select name="service">{svc_opts}</select>
      <label>Day</label>
      <div style="display:flex;gap:8px;flex-wrap:wrap">{"".join(day_links)}</div>
      <input type="hidden" name="day" value="{chosen.isoformat()}">
      <label>Start time — {pretty_day(chosen.isoformat())}</label>
      <div class="radios">{"".join(slot_radios)}</div>
      <div class="two">
        <div><label>Your name *</label><input type="text" name="name" required></div>
        <div><label>Phone</label><input type="tel" name="phone"></div>
      </div>
      <div class="two">
        <div><label>Email</label><input type="email" name="email"></div>
        <div><label>Area (e.g. Fairview, Bedford)</label>
        <input type="text" name="area"></div>
      </div>
      <label>Address</label>
      <input type="text" name="address" placeholder="Street address or area landmark">
      <label>Anything we should know?</label>
      <textarea name="notes" placeholder="Entry instructions, pets, priorities…"></textarea>
      <p style="margin-top:20px"><button class="btn" type="submit">
      Confirm my booking</button></p>
    </form>"""
    return page("Book a Clean", body)


@app.route("/booked/<ref>")
def booked(ref):
    b = db().execute("SELECT * FROM bookings WHERE ref = ?", (ref,)).fetchone()
    if b is None:
        abort(404)
    body = f"""
    <div class="hero" style="text-align:center">
      <div style="font-size:44px">✦</div>
      <h1>You're booked, {_e(b['name'].split()[0])}!</h1>
      <p class="sub">{_e(SERVICE_NAMES.get(b['service'], b['service']))} —
      <b>{_e(pretty_day(b['day']))}</b> at <b>{_e(b['slot'])}</b></p>
      <p>Your booking reference is</p>
      <p style="font-size:30px;font-weight:800;letter-spacing:2px">{_e(b['ref'])}</p>
      <p class="sub">We'll confirm your cleaner shortly. If anything changes,
      just quote your reference.</p>
      <div class="cta-row" style="justify-content:center">
        <a class="btn ghost" href="/">Back to home</a>
        <a class="btn gold" href="/quote">Get a quote for more</a>
      </div>
    </div>"""
    return page("Booked", body)


# ---------------------------------------------------------------- owner
def owner_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("owner"):
            return redirect(url_for("login"))
        return fn(*a, **kw)
    return wrapper


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if (request.form.get("user") == OWNER_USER
                and request.form.get("password") == OWNER_PASS):
            session["owner"] = True
            return redirect(url_for("owner_home"))
        flash("That login didn't match — try the demo login below.")
    body = f"""
    <h1>Owner sign-in</h1>
    <p class="sub">The control room: today's board, bookings, quote
    requests and the client book.</p>
    <form class="panel" method="post" style="max-width:430px">
      <label>Username</label><input type="text" name="user" required>
      <label>Password</label><input type="password" name="password" required>
      <p style="margin-top:18px"><button class="btn" type="submit">Sign in</button></p>
      <p class="kv" style="background:var(--aqua);border-radius:9px;padding:10px 14px">
      <b>Demo login</b> — user: <b>{OWNER_USER}</b> · password: <b>{OWNER_PASS}</b></p>
    </form>"""
    return page("Owner Sign-In", body)


@app.route("/logout")
def logout():
    session.pop("owner", None)
    return redirect(url_for("landing"))


def booking_card(b, controls=""):
    cleaner = ""
    if b["cleaner_id"]:
        row = db().execute("SELECT name FROM cleaners WHERE id = ?",
                           (b["cleaner_id"],)).fetchone()
        if row:
            cleaner = f' · 👩 {_e(row["name"])}'
    notes = f'<p>📝 {_e(b["notes"])}</p>' if b["notes"] else ""
    return f"""
    <div class="card jobcard">
      <div class="when">{_e(b['slot'])} — {_e(SERVICE_NAMES.get(b['service'], b['service']))}</div>
      <p><b>{_e(b['name'])}</b> · {_e(b['area'] or b['address'])}</p>
      <p>{status_pill(b['status'])}{cleaner}
      <span class="pill grey">{_e(b['ref'])}</span></p>
      {notes}{controls}
    </div>"""


@app.route("/owner")
@owner_required
def owner_home():
    today_iso = date.today().isoformat()
    rows = db().execute(
        """SELECT * FROM bookings WHERE day = ? AND status != 'cancelled'
           ORDER BY slot""", (today_iso,)).fetchall()
    new_quotes = db().execute(
        "SELECT COUNT(*) FROM quotes WHERE status = 'new'").fetchone()[0]
    upcoming = db().execute(
        """SELECT COUNT(*) FROM bookings
           WHERE day > ? AND status NOT IN ('cancelled', 'done')""",
        (today_iso,)).fetchone()[0]
    cards = "".join(booking_card(b) for b in rows) or \
        '<div class="card"><p>No cleans on the board today.</p></div>'
    body = f"""
    <h1>Owner control room</h1>
    <p class="sub">Signed in as owner · <a href="/logout">Sign out</a></p>
    <div class="grid">
      <div class="card"><h3>📩 Quote requests</h3>
      <p><b>{new_quotes}</b> new waiting</p>
      <p><a href="/owner/quotes">Open the inbox &rarr;</a></p></div>
      <div class="card"><h3>🗓️ Coming up</h3>
      <p><b>{upcoming}</b> future bookings after today</p>
      <p><a href="/owner/bookings">All bookings &rarr;</a></p></div>
      <div class="card"><h3>👥 Client book</h3>
      <p>Every customer in one place</p>
      <p><a href="/owner/clients">View clients &rarr;</a></p></div>
    </div>
    <h2>Today's board — {pretty_day(today_iso)}</h2>
    <div class="grid">{cards}</div>"""
    return page("Owner Control Room", body)


@app.route("/owner/bookings")
@owner_required
def owner_bookings():
    today_iso = date.today().isoformat()
    rows = db().execute(
        """SELECT * FROM bookings WHERE day >= ?
           ORDER BY day, slot""", (today_iso,)).fetchall()

    def controls(b):
        btns = []
        if b["status"] == "requested":
            btns.append(("confirm", "✓ Confirm", ""))
        if b["status"] in ("requested", "confirmed", "on_the_way", "arrived"):
            btns.append(("done", "Mark done", "grey"))
            btns.append(("cancel", "Cancel", "red"))
        if not btns:
            return ""
        forms = "".join(
            f'<form method="post" action="/owner/bookings/{b["id"]}/{act}" '
            f'style="display:inline"><button class="btn small {cls}" '
            f'type="submit">{label}</button></form>'
            for act, label, cls in btns)
        return f'<p style="margin-top:10px">{forms}</p>'

    cards = []
    current_day = None
    for b in rows:
        if b["day"] != current_day:
            current_day = b["day"]
            cards.append(f'<h2 style="grid-column:1/-1">{pretty_day(current_day)}</h2>')
        cards.append(booking_card(b, controls(b)))
    body = f"""
    <h1>Bookings</h1>
    <p class="sub"><a href="/owner">&larr; Control room</a> · Upcoming and
    today's bookings, newest requests first in line.</p>
    <div class="grid">{"".join(cards) or '<div class="card"><p>No upcoming bookings.</p></div>'}</div>"""
    return page("Bookings", body)


@app.route("/owner/bookings/<int:bid>/<action>", methods=["POST"])
@owner_required
def owner_booking_action(bid, action):
    b = db().execute("SELECT * FROM bookings WHERE id = ?", (bid,)).fetchone()
    if b is None:
        abort(404)
    new_status = {"confirm": "confirmed", "done": "done",
                  "cancel": "cancelled"}.get(action)
    if new_status is None:
        abort(400)
    db().execute("UPDATE bookings SET status = ? WHERE id = ?",
                 (new_status, bid))
    db().commit()
    flash(f"Booking {b['ref']} is now {STATUS_LABELS[new_status].lower()}.")
    return redirect(url_for("owner_bookings"))


@app.route("/owner/quotes")
@owner_required
def owner_quotes():
    rows = db().execute("SELECT * FROM quotes ORDER BY id DESC").fetchall()
    cards = []
    for q in rows:
        extras = f'<p><b>Extras:</b> {_e(q["extras"])}</p>' if q["extras"] else ""
        pets = q["pets_text"] or ("yes" if q["pets"] == "yes" else "no")
        cards.append(f"""
        <div class="card">
          <h3>{_e(q['name'])} <span class="pill gold">quote request</span></h3>
          <p class="kv">📞 {_e(q['phone'])} · ✉️ {_e(q['email'])}<br>
          <b>Home:</b> {_e(q['prop_type'])}, {_e(q['bedrooms'])} bed /
          {_e(q['bathrooms'])} bath, {_e(q['sqft'])} — {_e(q['area'])}<br>
          <b>Service:</b> {_e(SERVICE_NAMES.get(q['service'], q['service']))}
          · <b>Frequency:</b> {_e(q['frequency'])}<br>
          <b>Pets:</b> {_e(pets)} · <b>Products:</b> {_e(q['products'])}<br>
          <b>Preferred:</b> {_e(q['pref_dates'])}</p>
          {extras}
          <p>{_e(q['notes'])}</p>
        </div>""")
    body = f"""
    <h1>Quote requests</h1>
    <p class="sub"><a href="/owner">&larr; Control room</a> · Every request
    arrives in the same shape — price it fast, nothing gets forgotten.</p>
    <div class="grid">{"".join(cards) or '<div class="card"><p>No quote requests yet.</p></div>'}</div>"""
    return page("Quote Requests", body)


@app.route("/owner/clients")
@owner_required
def owner_clients():
    rows = db().execute("SELECT * FROM clients ORDER BY name").fetchall()
    trs = "".join(
        f"""<tr><td><b>{_e(c['name'])}</b></td><td>{_e(c['phone'])}<br>{_e(c['email'])}</td>
        <td>{_e(c['area'])}<br>{_e(c['address'])}</td><td>{_e(c['notes'])}</td></tr>"""
        for c in rows)
    body = f"""
    <h1>Client book</h1>
    <p class="sub"><a href="/owner">&larr; Control room</a> · Everyone who has
    booked or asked for a quote, in one place.</p>
    <table><tr><th>Client</th><th>Contact</th><th>Area / Address</th>
    <th>Notes</th></tr>{trs}</table>"""
    return page("Clients", body)


# ---------------------------------------------------------------- cleaner
@app.route("/cleaner")
def cleaner_picker():
    rows = db().execute("SELECT * FROM cleaners ORDER BY name").fetchall()
    links = "".join(
        f'<a class="btn big-tap" href="/cleaner/{c["id"]}">'
        f'I\'m {_e(c["name"])}</a>' for c in rows)
    body = f"""
    <h1>Cleaner day view</h1>
    <p class="sub">Pick your name to see <b>only your jobs for today</b> —
    addresses, notes, and your on-the-way buttons.</p>
    <div style="max-width:430px">{links}</div>"""
    return page("Cleaner View", body)


@app.route("/cleaner/<int:cid>")
def cleaner_day(cid):
    c = db().execute("SELECT * FROM cleaners WHERE id = ?", (cid,)).fetchone()
    if c is None:
        abort(404)
    today_iso = date.today().isoformat()
    rows = db().execute(
        """SELECT * FROM bookings
           WHERE cleaner_id = ? AND day = ? AND status != 'cancelled'
           ORDER BY slot""", (cid, today_iso)).fetchall()

    def controls(b):
        nxt = {"confirmed": ("on_the_way", "🚶 On my way"),
               "requested": ("on_the_way", "🚶 On my way"),
               "on_the_way": ("arrived", "📍 I've arrived"),
               "arrived": ("done", "✓ Done — clean complete")}
        step = nxt.get(b["status"])
        if not step:
            return '<p><span class="pill">Shift step complete</span></p>'
        return (f'<form method="post" action="/cleaner/{cid}/booking/'
                f'{b["id"]}/{step[0]}"><button class="btn big-tap" '
                f'type="submit">{step[1]}</button></form>')

    cards = []
    for b in rows:
        phone = (f'<p>📞 <a href="tel:{_e(b["phone"])}">{_e(b["phone"])}</a>'
                 f' (tap to call)</p>') if b["phone"] else ""
        notes = f'<p>📝 {_e(b["notes"])}</p>' if b["notes"] else ""
        cards.append(f"""
        <div class="card jobcard">
          <div class="when">{_e(b['slot'])} — {_e(SERVICE_NAMES.get(b['service'], b['service']))}</div>
          <p><b>{_e(b['name'])}</b></p>
          <p>🏠 {_e(b['address'] or b['area'])}</p>
          {phone}{notes}
          <p>{status_pill(b['status'])}</p>
          {controls(b)}
        </div>""")
    body = f"""
    <h1>{_e(c['name'])} — today</h1>
    <p class="sub"><a href="/cleaner">&larr; Not you? Pick again</a> ·
    {pretty_day(today_iso)} · your jobs only.</p>
    <div class="grid">{"".join(cards) or '<div class="card"><p>No jobs assigned today. Enjoy the quiet!</p></div>'}</div>"""
    return page("My Day", body)


@app.route("/cleaner/<int:cid>/booking/<int:bid>/<status>", methods=["POST"])
def cleaner_action(cid, bid, status):
    allowed = {"on_the_way", "arrived", "done"}
    if status not in allowed:
        abort(400)
    b = db().execute(
        "SELECT * FROM bookings WHERE id = ? AND cleaner_id = ?",
        (bid, cid)).fetchone()
    if b is None:
        abort(404)
    db().execute("UPDATE bookings SET status = ? WHERE id = ?", (status, bid))
    db().commit()
    flash({"on_the_way": "Marked on the way — the office can see you're rolling.",
           "arrived": "Marked arrived. Have a great clean!",
           "done": "Marked done. Nice work!"}[status])
    return redirect(url_for("cleaner_day", cid=cid))


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
