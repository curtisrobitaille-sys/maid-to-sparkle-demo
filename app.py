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

import base64

from brand_images import BANNER_B64, LOGO_B64

BANNER_BYTES = base64.b64decode(BANNER_B64)
LOGO_BYTES = base64.b64decode(LOGO_B64)


def _brand_image(data):
    resp = app.response_class(data, mimetype="image/jpeg")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@app.route("/brand/banner.jpg")
def brand_banner():
    return _brand_image(BANNER_BYTES)


@app.route("/brand/logo.jpg")
def brand_logo():
    return _brand_image(LOGO_BYTES)

OWNER_USER = "owner"
OWNER_PASS = "sparkle-demo"

SERVICES = [
    ("residential", "Regular Cleans",
     "Regular home cleans — kitchens, bathrooms, floors, dusting, the works."),
    ("deep", "Deep Cleans",
     "Top-to-bottom reset: baseboards, vents, inside appliances available."),
    ("airbnb", "Airbnb Turnovers",
     "Fast, reliable turnovers with laundry and restock — guest-ready every time."),
    ("move", "Move-In/Move-Out",
     "Empty-home cleans that get deposits back and new homes move-in ready."),
    ("construction", "Post-Construction",
     "Dust, debris and detail work after the reno crew leaves."),
    ("commercial", "Commercial Cleans",
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
BOLT_SVG = ('<svg class="bolt" viewBox="0 0 24 24" fill="currentColor" '
            'aria-hidden="true"><path d="M11 21h-1l1-7H7.5c-.58 0-.57-.32-.38-.66'
            'l.07-.12C8.48 10.94 10.42 7.54 13 3h1l-1 7h3.5c.49 0 .56.33.47.51'
            'l-.07.15C14.96 15.36 13 18.72 11 21z"/></svg>')

CSS = """
:root{--pink:#e84393;--pink-dark:#c2236a;--pink-deep:#9d1c57;
--rose:#b76e79;--rose-soft:#fbe9ed;--blush:#ffecf3;--blush2:#ffd6e7;
--peri:#6f7bd9;--ink:#42222e;--mut:#8a5f6d;--line:#f3cfdd;
--bg:#fff8fa;--card:#ffffff;--red:#b91c1c}
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
background:var(--bg);color:var(--ink)}
a{color:var(--pink-dark)}
header.site{background:linear-gradient(120deg,#c2236a,#e84393 55%,#f472a5);color:#fff;
padding:10px 20px;box-shadow:0 2px 12px rgba(232,67,147,.25)}
header.site .wrap{max-width:1020px;margin:0 auto;display:flex;flex-wrap:nowrap;
align-items:center;gap:12px;padding:0 18px}
.brandmark{width:54px;height:54px;border-radius:14px;object-fit:cover;
background:#fff;padding:2px;box-shadow:0 1px 6px rgba(157,28,87,.35);
flex:0 0 auto}
.brand-stack{display:flex;flex-direction:column;justify-content:center;
line-height:1.2;min-width:0;flex:1 1 auto}
.logo{font-size:26px;font-weight:800;letter-spacing:.3px;color:#fff;
text-decoration:none;white-space:nowrap}
.logo .spark{color:#ffe066}
.tagline{color:#ffd6e7;font-size:14px;margin-left:2px;white-space:nowrap}
.menu{margin-left:auto;position:relative;flex:0 0 auto}
@media (max-width:600px){.logo{font-size:21px}.tagline{font-size:12.5px}
.menu summary{padding:7px 12px;font-size:13.5px}}
@media (max-width:380px){.logo{font-size:19px;letter-spacing:0}
header.site .wrap{gap:10px}}
.menu summary{list-style:none;cursor:pointer;color:#fff;font-weight:600;
font-size:14px;background:rgba(255,255,255,.18);padding:8px 14px;
border-radius:999px;-webkit-tap-highlight-color:transparent}
.menu summary::-webkit-details-marker{display:none}
.menu summary:hover{background:rgba(255,255,255,.32)}
.menu-list{position:absolute;right:0;top:calc(100% + 10px);background:#fff;
border-radius:12px;box-shadow:0 10px 30px rgba(157,28,87,.28);min-width:210px;
padding:6px;z-index:50}
.menu-list a{display:block;color:var(--ink);text-decoration:none;
padding:11px 14px;font-size:15px;border-radius:8px}
.menu-list a:hover{background:var(--blush)}
.menu-list a.cur{font-weight:800;color:var(--pink-deep)}
.menu-list a.cur::before{content:"✦ ";color:var(--pink)}
.addr-wrap{position:relative}
.addr-list{position:absolute;left:0;right:0;top:calc(100% + 6px);background:#fff;
border:1px solid var(--line);border-radius:12px;
box-shadow:0 10px 30px rgba(157,28,87,.28);padding:6px;z-index:60;
max-height:270px;overflow:auto}
.addr-list button{display:block;width:100%;text-align:left;background:none;
border:0;color:var(--ink);font:inherit;font-size:14.5px;padding:10px 12px;
border-radius:8px;cursor:pointer;-webkit-tap-highlight-color:transparent}
.addr-list button:hover,.addr-list button.sel{background:var(--blush)}
.wrap{max-width:1020px;margin:0 auto;padding:22px 18px 40px}
h1{font-size:30px;margin:8px 0 6px;color:var(--pink-deep)}
h2{font-size:21px;margin:26px 0 10px;color:var(--pink-deep)}
.sub{color:var(--mut);font-size:16px;line-height:1.5}
.hero{background:var(--card);border:1px solid var(--line);border-radius:16px;
padding:22px;margin-top:18px;box-shadow:0 2px 12px rgba(232,67,147,.10)}
.hero h1{margin-top:0}
.hero-img{width:100%;display:block;border-radius:12px;margin-bottom:18px}
.signin-logo{display:block;width:112px;height:112px;object-fit:cover;
border-radius:22px;margin:0 auto 6px}
.cta-row{display:flex;gap:12px;flex-wrap:wrap;margin-top:18px}
.hero-cta{flex-wrap:nowrap}
.hero-cta .btn-book,.hero-cta .btn{min-width:0;white-space:nowrap}
@media(max-width:600px){.hero-cta{gap:10px}
.hero-cta .btn-book{flex:1.55 1 0;font-size:13px;padding:11px 8px;gap:6px}
.hero-cta .btn{flex:1 1 0;font-size:14px;padding:11px 8px;text-align:center}
.hero{padding:18px 14px}}
.btn{display:inline-block;background:var(--pink);color:#fff;text-decoration:none;
padding:13px 22px;border-radius:10px;font-weight:700;font-size:16px;border:0;
cursor:pointer;box-shadow:0 2px 8px rgba(232,67,147,.25)}
.btn:hover{background:var(--pink-dark)}
.btn.rose{background:var(--rose)}
.btn.ghost{background:#fff;color:var(--pink-dark);border:2px solid var(--pink);
box-shadow:none}
.btn.small{padding:8px 14px;font-size:14px;border-radius:8px}
.btn.red{background:var(--red)}
.btn.grey{background:#7a5563}
.btn-book{display:inline-flex;align-items:center;justify-content:center;gap:8px;
background:linear-gradient(90deg,#ec4899,#a855f7,#4f46e5);color:#fff;
text-decoration:none;padding:12px 24px;border-radius:999px;font-weight:700;
font-size:14px;border:0;cursor:pointer;font-family:inherit;line-height:1.2;
box-shadow:0 0 20px rgba(236,72,153,.4);transition:all .3s ease-out}
.btn-book:hover{background:linear-gradient(90deg,#f472b6,#c084fc,#6366f1);
box-shadow:0 0 35px rgba(168,85,247,.7);transform:scale(1.05)}
.btn-book:active{transform:scale(.95)}
.btn-book .bolt{width:16px;height:16px;color:#fbcfe8;flex:none;
transition:transform .3s ease-out}
.btn-book:hover .bolt{transform:rotate(-12deg) scale(1.2)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));
gap:14px;margin-top:14px}
.svc-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;
margin-top:14px}
@media(min-width:760px){.svc-grid{grid-template-columns:repeat(3,1fr);
gap:14px}}
.svc-grid .card{padding:14px;display:flex;flex-direction:column}
.svc-grid .card h3{font-size:15.5px;line-height:1.25;overflow-wrap:break-word}
.svc-grid .card p{font-size:13.5px;line-height:1.45}
.svc-grid .svc-actions{margin-top:auto;padding-top:10px}
.svc-grid .btn-book{font-size:12.5px;padding:8px 11px;gap:6px;
white-space:nowrap;max-width:100%}
.svc-grid .btn-book .bolt{width:13px;height:13px}
.svc-grid .svc-quote{display:block;margin-top:8px;font-size:13.5px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;
padding:18px;box-shadow:0 1px 6px rgba(232,67,147,.06)}
.card h3{margin:0 0 6px;font-size:18px;color:var(--pink-deep)}
.card p{color:var(--mut);line-height:1.5;margin:4px 0}
.pill{display:inline-block;padding:3px 10px;border-radius:999px;font-size:12px;
font-weight:700;background:var(--blush2);color:var(--pink-dark)}
.pill.rose{background:var(--rose-soft);color:var(--rose)}
.pill.red{background:#fee2e2;color:var(--red)}
.pill.grey{background:#f3e2e9;color:#7a5563}
.pill.green{background:#dcfce7;color:#166534}
.tl{display:inline-block;width:11px;height:11px;border-radius:50%;
margin-right:6px;vertical-align:-1px;flex:none}
.tl-red{background:#ef4444;box-shadow:0 0 7px 2px rgba(239,68,68,.55)}
.tl-amber{background:#f59e0b;box-shadow:0 0 7px 2px rgba(245,158,11,.55)}
.tl-green{background:#22c55e;box-shadow:0 0 7px 2px rgba(34,197,94,.55)}
.tl-grey{background:#c4b0ba;box-shadow:0 0 5px 1px rgba(160,120,135,.35)}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--line);
border-radius:12px;overflow:hidden}
th,td{padding:10px 12px;text-align:left;border-bottom:1px solid var(--line);
font-size:14px;vertical-align:top}
th{background:var(--blush);color:var(--pink-dark)}
form.panel{background:#fff;border:1px solid var(--line);border-radius:14px;
padding:22px;margin-top:16px;box-shadow:0 1px 6px rgba(232,67,147,.06)}
label{display:block;font-weight:700;font-size:14px;margin:16px 0 6px}
label:first-child{margin-top:0}
input[type=text],input[type=email],input[type=tel],input[type=password],select,textarea{
width:100%;padding:11px 12px;border:1.5px solid var(--line);border-radius:9px;
font-size:16px;background:#fff;color:var(--ink)}
input:focus,select:focus,textarea:focus{outline:2px solid var(--blush2);
border-color:var(--pink)}
textarea{min-height:80px}
.checks{display:flex;flex-wrap:wrap;gap:8px}
.checks label{font-weight:500;margin:0;background:var(--bg);border:1px solid var(--line);
border-radius:999px;padding:8px 13px;cursor:pointer;font-size:14px}
.radios{display:flex;flex-wrap:wrap;gap:8px}
.radios label{font-weight:600;margin:0;border:1.5px solid var(--line);
border-radius:10px;padding:10px 14px;cursor:pointer;font-size:14px;background:#fff}
.radios input{margin-right:6px}
.radios label.taken{opacity:.45;cursor:not-allowed;background:#f9edf2}
.two{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:640px){.two{grid-template-columns:1fr}}
.flash{background:#ffe4ef;border:1px solid #f78fb8;color:#8f1a52;
border-radius:10px;padding:12px 16px;margin:14px 0;font-weight:600}
.jobcard{border-left:6px solid var(--pink)}
.jobcard .when{font-size:20px;font-weight:800}
footer.site{border-top:1px solid var(--line);color:var(--mut);font-size:12.5px;
text-align:center;padding:18px 12px 30px;line-height:1.6}
.kv{font-size:14px;line-height:1.7}
.kv b{color:var(--ink)}
.big-tap{display:block;text-align:center;padding:16px;font-size:18px;margin-top:10px}
.peri{color:var(--peri)}
.cal{max-width:344px}
.cal-head{display:flex;align-items:center;justify-content:space-between;
margin-bottom:8px}
.cal-title{font-weight:800;font-size:16px;color:var(--pink-deep)}
.cal-nav{display:inline-block;width:34px;height:34px;line-height:32px;
text-align:center;border:1.5px solid var(--line);border-radius:9px;
color:var(--pink-dark);text-decoration:none;font-size:19px;font-weight:700;
background:#fff}
.cal-nav.off{opacity:.35;color:var(--mut)}
.cal-grid{display:grid;grid-template-columns:repeat(7,1fr);gap:4px}
.cal-dow{text-align:center;font-size:12px;font-weight:700;color:var(--mut);
padding:4px 0}
.cal-cell{display:flex;align-items:center;justify-content:center;height:42px;
border-radius:9px;font-weight:600;font-size:15px;color:var(--ink);
text-decoration:none;position:relative;background:#fff;
border:1.5px solid transparent}
a.cal-cell{border-color:var(--line)}
a.cal-cell:hover{border-color:var(--pink)}
.cal-cell.sel{background:var(--pink);color:#fff;border-color:var(--pink)}
.cal-cell.off{color:var(--mut);opacity:.45;background:transparent}
.cal-cell.today::after{content:"";position:absolute;bottom:5px;left:50%;
transform:translateX(-50%);width:5px;height:5px;border-radius:50%;
background:var(--pink)}
.cal-cell.sel.today::after{background:#fff}
#times{scroll-margin-top:96px}
"""

# Booking page only: keep the reader's place across the calendar's page
# reloads. Tapping a day cell or a month arrow saves the scroll position;
# the freshly loaded page restores it (and clears it), so the calendar
# stays exactly where it was on screen while the times below it update.
BOOK_SCROLL_JS = """
<script>
(function () {
  var KEY = "mts-book-scroll", y = null;
  try {
    y = sessionStorage.getItem(KEY);
    if (y !== null) sessionStorage.removeItem(KEY);
  } catch (e) {}
  if (y !== null) {
    var go = function () { window.scrollTo(0, parseInt(y, 10) || 0); };
    go();
    window.addEventListener("load", go);
  }
  document.addEventListener("click", function (ev) {
    var a = ev.target && ev.target.closest ?
      ev.target.closest("a.cal-cell, a.cal-nav") : null;
    if (a) {
      try { sessionStorage.setItem(KEY, String(window.scrollY)); }
      catch (e) {}
    }
  });
})();
</script>
"""

# Booking page only: free address autocomplete via Photon (OpenStreetMap,
# no API key), biased to HRM. Pure enhancement — the Address field stays a
# plain free-text input if the fetch fails, times out, or finds nothing.
ADDRESS_AC_JS = """
<script>
(function () {
  var input = document.getElementById("addr-input");
  var list = document.getElementById("addr-list");
  if (!input || !list || !window.fetch) return;
  var timer = null, seq = 0, items = [], active = -1;
  function compose(p) {
    var head = ((p.housenumber || "") + " " +
      (p.street || p.name || "")).trim();
    var s = head;
    if (p.city) s = s ? s + ", " + p.city : p.city;
    if (p.state === "Nova Scotia") s += ", NS";
    return s;
  }
  function close() {
    list.hidden = true; list.innerHTML = ""; items = []; active = -1;
  }
  function pick(i) {
    if (items[i]) input.value = items[i];
    close();
    input.focus();
  }
  function render() {
    list.innerHTML = "";
    items.forEach(function (txt, i) {
      var b = document.createElement("button");
      b.type = "button";
      b.textContent = txt;
      if (i === active) b.className = "sel";
      b.addEventListener("mousedown", function (ev) {
        ev.preventDefault();
        pick(i);
      });
      list.appendChild(b);
    });
    list.hidden = items.length === 0;
  }
  function fetchNow(q) {
    var my = ++seq;
    var url = "https://photon.komoot.io/api/?q=" + encodeURIComponent(q) +
      "&lat=44.6488&lon=-63.5752" +
      "&bbox=-64.35,44.30,-63.20,45.05&limit=6&lang=en";
    var ctl = window.AbortController ? new AbortController() : null;
    var killer = ctl ? setTimeout(function () { ctl.abort(); }, 4000) : null;
    fetch(url, ctl ? { signal: ctl.signal } : undefined)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (killer) clearTimeout(killer);
        if (my !== seq) return;
        var seen = {}, out = [];
        (data.features || []).forEach(function (f) {
          var p = (f && f.properties) || {};
          if (p.state !== "Nova Scotia") return;
          if (!p.street && !p.name) return;
          var s = compose(p);
          if (s && !seen[s]) { seen[s] = 1; out.push(s); }
        });
        items = out.slice(0, 6);
        active = -1;
        if (items.length) render(); else close();
      })
      .catch(function () {
        if (killer) clearTimeout(killer);
        if (my === seq) close();
      });
  }
  input.addEventListener("input", function () {
    var q = input.value.trim();
    if (timer) clearTimeout(timer);
    if (q.length < 5) { close(); return; }
    timer = setTimeout(function () { fetchNow(q); }, 250);
  });
  input.addEventListener("keydown", function (ev) {
    if (list.hidden || !items.length) return;
    if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
      ev.preventDefault();
      active = ev.key === "ArrowDown" ?
        Math.min(active + 1, items.length - 1) : Math.max(active - 1, 0);
      render();
    } else if (ev.key === "Enter" && active >= 0) {
      ev.preventDefault();
      pick(active);
    } else if (ev.key === "Escape") {
      close();
    }
  });
  document.addEventListener("click", function (ev) {
    if (ev.target !== input && !list.contains(ev.target)) close();
  });
  input.addEventListener("blur", function () { setTimeout(close, 150); });
})();
</script>
"""


def page(title, body, **ctx):
    flashes = "".join(
        f'<div class="flash">✦ {_e(m)}</div>' for _c, m in get_flashed_messages(
            with_categories=True))
    cur = request.path

    def _cur(*paths):
        for p in paths:
            if cur == p or (p != "/" and cur.startswith(p + "/")):
                return ' class="cur"'
        return ""

    nav = f"""<details class="menu"><summary>Menu ☰</summary>
<nav class="menu-list">
<a href="/"{_cur("/")}>Home</a>
<a href="/book"{_cur("/book", "/booked")}>Book a Clean</a>
<a href="/quote"{_cur("/quote")}>Get a Quote</a>
<a href="/owner"{_cur("/owner", "/login")}>Owner</a>
<a href="/cleaner"{_cur("/cleaner")}>Cleaner View</a>
</nav></details>"""
    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(title)} — Maid To Sparkle</title><style>{CSS}</style></head>
<body>
<header class="site"><div class="wrap">
<img class="brandmark" src="/brand/logo.jpg" alt="Maid To Sparkle logo">
<div class="brand-stack">
<a class="logo" href="/">Maid <span class="spark">✦</span> To Sparkle</a>
<span class="tagline">Halifax &amp; HRM cleaning</span>
</div>
{nav}</div></header>
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


# Traffic-light dot shown beside the status label: red while the cleaner
# is on the way, amber once the job has started, green when complete.
# Not-yet-started bookings get a neutral grey; cancelled gets no light.
TL_CLASS = {"on_the_way": "tl-red", "arrived": "tl-amber",
            "done": "tl-green", "requested": "tl-grey",
            "confirmed": "tl-grey"}


def status_pill(status):
    cls = {"requested": "rose", "confirmed": "green", "on_the_way": "rose",
           "arrived": "rose", "done": "", "cancelled": "red"}.get(status, "grey")
    tl = TL_CLASS.get(status)
    dot = f'<span class="tl {tl}"></span>' if tl else ""
    return (f'<span class="pill {cls}">{dot}'
            f'{STATUS_LABELS.get(status, status)}</span>')


# ---------------------------------------------------------------- customer
@app.route("/")
def landing():
    cards = "".join(
        f"""<div class="card"><h3>{name}</h3><p>{desc}</p>
        <p class="svc-actions"><a class="btn-book" href="/book?service={key}">{BOLT_SVG}Book this &rarr;</a>
        <a class="svc-quote" href="/quote?service={key}">Get a quote</a></p></div>"""
        for key, name, desc in SERVICES)
    body = f"""
    <div class="hero">
      <img class="hero-img" src="/brand/banner.jpg" alt="Maid To Sparkle — Halifax &amp; HRM cleaning">
      <h1>Your home called... it wants a <span style="color:var(--pink)">reset</span>.</h1>
      <p class="sub" style="font-size:18px"><b>✦ Now booking new clients</b>
      &nbsp;·&nbsp; Serving <b>Halifax &amp; HRM</b></p>
      <div class="cta-row hero-cta">
        <a class="btn-book" href="/book">{BOLT_SVG}Book Your Clean ✨</a>
        <a class="btn rose" href="/quote">Get a Quote</a>
      </div>
      <p class="sub">Reliable, detailed cleaning — homes, Airbnbs, move-outs
      and small businesses. Book in about a minute, or send a quick quote
      form and we'll confirm the details with you.</p>
      <p class="sub" style="margin-top:14px">✦ Eco-friendly products by default
      &nbsp;·&nbsp; ✦ Weekly, biweekly, monthly or one-time
      &nbsp;·&nbsp; ✦ Locally owned in Halifax</p>
    </div>
    <h2>Our services</h2>
    <div class="svc-grid">{cards}</div>
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


QUOTE_SERVICES = [(k, name) for k, name, _d in SERVICES]
EXTRAS = ["den/office", "finished basement", "laundry room",
          "inside appliances", "inside windows"]
# Service areas offered as a drop-down on the quote and booking forms.
AREAS = ["Bedford", "Sackville", "Beaver Bank", "Halifax", "Dartmouth",
         "Eastern Passage", "Fall River", "Waverley"]
AREA_OPTS = ('<option value="">Choose your area</option>' + "".join(
    f"<option>{a}</option>" for a in AREAS))


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
        <div><label>Area</label>
        <select name="area">{AREA_OPTS}</select></div>
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
      <p style="margin-top:20px"><button class="btn rose" type="submit">
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
    # Plain month calendar for the booking window (today .. today+14),
    # modelled on the Rebuilt attendance calendar's plain look: month title,
    # < > arrows, weekday header, uniform day cells with just the number.
    # Arrows page between the months the window touches and stop there;
    # picking a day uses the same /book?day= link the old pill row used.
    win_start, win_end = today, today + timedelta(days=14)
    first_month = win_start.replace(day=1)
    last_month = win_end.replace(day=1)

    def shift_month(d, n):
        y, m = d.year, d.month + n
        while m < 1:
            y, m = y - 1, m + 12
        while m > 12:
            y, m = y + 1, m - 12
        return date(y, m, 1)

    mparam = request.args.get("month", "")
    try:
        shown = (date.fromisoformat(mparam + "-01") if mparam
                 else chosen.replace(day=1))
    except ValueError:
        shown = chosen.replace(day=1)
    shown = min(max(shown.replace(day=1), first_month), last_month)

    def month_url(d):
        return (f"/book?service={preset}&day={chosen.isoformat()}"
                f"&month={d.strftime('%Y-%m')}")

    prev_m, next_m = shift_month(shown, -1), shift_month(shown, 1)
    prev_arrow = (f'<a class="cal-nav" href="{month_url(prev_m)}">‹</a>'
                  if shown > first_month
                  else '<span class="cal-nav off">‹</span>')
    next_arrow = (f'<a class="cal-nav" href="{month_url(next_m)}">›</a>'
                  if shown < last_month
                  else '<span class="cal-nav off">›</span>')
    month_last = shift_month(shown, 1) - timedelta(days=1)
    # Sunday-start grid, padded with the neighbouring months' days (dimmed).
    d = shown - timedelta(days=(shown.weekday() + 1) % 7)
    cells = []
    while d <= month_last:
        for _ in range(7):
            if d.month != shown.month or not (win_start <= d <= win_end):
                cells.append(f'<span class="cal-cell off">{d.day}</span>')
            else:
                cls = "cal-cell"
                if d == chosen:
                    cls += " sel"
                if d == today:
                    cls += " today"
                cells.append(
                    f'<a class="{cls}" href="/book?service={preset}'
                    f'&day={d.isoformat()}">{d.day}</a>')
            d += timedelta(days=1)
    dows = "".join(f'<div class="cal-dow">{n}</div>'
                   for n in ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"))
    calendar_html = f"""
      <div class="cal">
        <div class="cal-head">{prev_arrow}<span class="cal-title">
        {shown.strftime("%B %Y")}</span>{next_arrow}</div>
        <div class="cal-grid">{dows}{"".join(cells)}</div>
      </div>"""
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
      {calendar_html}
      <input type="hidden" name="day" value="{chosen.isoformat()}">
      <label id="times">Start time — {pretty_day(chosen.isoformat())}</label>
      <div class="radios">{"".join(slot_radios)}</div>
      <div class="two">
        <div><label>Your name *</label><input type="text" name="name" required></div>
        <div><label>Phone</label><input type="tel" name="phone"></div>
      </div>
      <div class="two">
        <div><label>Email</label><input type="email" name="email"></div>
        <div><label>Area</label>
        <select name="area">{AREA_OPTS}</select></div>
      </div>
      <label>Address</label>
      <div class="addr-wrap">
      <input type="text" id="addr-input" name="address" autocomplete="off" placeholder="Street address or area landmark">
      <div class="addr-list" id="addr-list" hidden></div>
      </div>
      <label>Anything we should know?</label>
      <textarea name="notes" placeholder="Entry instructions, pets, priorities…"></textarea>
      <p style="margin-top:20px"><button class="btn-book" type="submit">
      {BOLT_SVG}Confirm my booking</button></p>
    </form>"""
    body += BOOK_SCROLL_JS + ADDRESS_AC_JS
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
        <a class="btn rose" href="/quote">Get a quote for more</a>
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
      <img class="signin-logo" src="/brand/logo.jpg" alt="Maid To Sparkle logo">
      <label>Username</label><input type="text" name="user" required>
      <label>Password</label><input type="password" name="password" required>
      <p style="margin-top:18px"><button class="btn" type="submit">Sign in</button></p>
      <p class="kv" style="background:var(--blush);border-radius:9px;padding:10px 14px">
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
          <h3>{_e(q['name'])} <span class="pill rose">quote request</span></h3>
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
