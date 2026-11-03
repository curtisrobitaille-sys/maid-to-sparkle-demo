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
import io
import os
import random
import sqlite3
from datetime import date, datetime, timedelta
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

from avatar_images import ALYSSA_B64, BRITTANY_B64, KAYLA_B64

# Seed cleaners' photos, keyed by cleaner name. Tiny on purpose — they
# only ever display as small round avatars.
SEED_AVATARS = {
    "Alyssa R.": base64.b64decode(ALYSSA_B64),
    "Kayla M.": base64.b64decode(KAYLA_B64),
    "Brittany S.": base64.b64decode(BRITTANY_B64),
}

# Pillow powers the upload downscale; without it uploads are stored
# as-is (still type-checked and size-capped — see _photo_from_upload).
try:
    from PIL import Image as _PILImage
except ImportError:
    _PILImage = None


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


@app.route("/cleaner-photo/<int:cid>")
def cleaner_photo(cid):
    row = db().execute(
        "SELECT photo, photo_mime FROM cleaners WHERE id = ?",
        (cid,)).fetchone()
    if row is None or not row["photo"]:
        abort(404)
    resp = app.response_class(
        row["photo"], mimetype=row["photo_mime"] or "image/jpeg")
    resp.headers["Cache-Control"] = "public, max-age=300"
    return resp


def _initials(name):
    parts = [p for p in (name or "").split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][0].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _cleaner_avatar(c, cls="avatar"):
    """Round avatar markup for a cleaner row: their stored photo, or
    their initials on a pink gradient when they have none. The ?v= is
    the photo's byte length — a cheap cache-buster so a swapped photo
    shows up right away instead of the browser's cached copy."""
    if c["photo"]:
        return (f'<img class="{cls}" src="/cleaner-photo/{c["id"]}'
                f'?v={len(c["photo"])}" alt="{_e(c["name"])}">')
    return (f'<span class="{cls} avatar-init">'
            f'{_e(_initials(c["name"]))}</span>')


# Generous on purpose: a 4K phone photo runs 3–8 MB and must be
# accepted — it is shrunk to a tiny stored copy before it ever reaches
# the database (see _shrink_photo). Only absurd files are turned away.
MAX_PHOTO_BYTES = 15 * 1024 * 1024


def _photo_mime(data):
    """Sniff an image type from the bytes themselves, never the name."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _shrink_photo(data):
    """Downscale a photo for storage: avatars display at ~60px, so the
    stored copy is capped at 192px on the longest side and re-encoded
    as JPEG quality 75 (a few KB). Transparency flattens onto the app's
    cream background. Without Pillow the original bytes are kept."""
    if _PILImage is None:
        return data, _photo_mime(data)
    try:
        im = _PILImage.open(io.BytesIO(data))
        im.seek(0)  # first frame, if animated
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = _PILImage.new("RGB", im.size, (255, 248, 250))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
        im.thumbnail((192, 192), _PILImage.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=75)
        return buf.getvalue(), "image/jpeg"
    except Exception:
        return data, _photo_mime(data)


def _photo_from_upload(fs):
    """Validate an uploaded photo file.

    Returns (data, mime, error): no file chosen is (None, None, None);
    a bad file is (None, None, message); a good one is shrunk and
    returned as (bytes, mime, None)."""
    if fs is None or not fs.filename:
        return None, None, None
    data = fs.read(MAX_PHOTO_BYTES + 1)
    if len(data) > MAX_PHOTO_BYTES:
        return None, None, ("That photo is too big — please pick one "
                            "under 15 MB.")
    if _photo_mime(data) is None:
        return None, None, ("That file doesn't look like a photo — "
                            "please use a JPG, PNG, WebP or GIF.")
    data, mime = _shrink_photo(data)
    return data, mime, None

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

# A day with this many cleans is FULL — the owner board's calendar rings
# the day cell with dots instead of stacking them under the number.
FULL_DAY = 9

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
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL UNIQUE,
            photo      BLOB,
            photo_mime TEXT
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
            created_at TEXT DEFAULT '',
            on_the_way_at TEXT,
            arrived_at    TEXT,
            done_at       TEXT
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
    # Databases created before cleaner photos existed get the columns.
    cleaner_cols = [r[1] for r in conn.execute("PRAGMA table_info(cleaners)")]
    if "photo" not in cleaner_cols:
        conn.execute("ALTER TABLE cleaners ADD COLUMN photo BLOB")
    if "photo_mime" not in cleaner_cols:
        conn.execute("ALTER TABLE cleaners ADD COLUMN photo_mime TEXT")
    conn.commit()
    # Databases created before status timestamps existed get the columns
    # (existing rows keep NULL stamps — no invented history).
    booking_cols = [r[1] for r in conn.execute("PRAGMA table_info(bookings)")]
    for col in ("on_the_way_at", "arrived_at", "done_at"):
        if col not in booking_cols:
            conn.execute(f"ALTER TABLE bookings ADD COLUMN {col} TEXT")
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
        photo = SEED_AVATARS.get(name)
        cur = conn.execute(
            "INSERT INTO cleaners (name, photo, photo_mime) VALUES (?, ?, ?)",
            (name, photo, "image/jpeg" if photo else None))
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
.book-head{margin-top:-14px}
.book-head h1{margin:0 0 3px}
.book-head .sub{margin:0;line-height:1.35}
.panel.book-form{margin-top:8px}
.no-cleaner{color:var(--mut)}
.jobcard.edge-red{border-left-color:#ef4444}
.jobcard.edge-amber{border-left-color:#f59e0b}
.jobcard.edge-green{border-left-color:#22c55e}
.jobcard.edge-grey{border-left-color:#c4b0ba}
.board-grid{gap:10px}
.jobcard.compact{padding:12px 14px}
.jobcard.compact .when{font-size:16.5px}
.jobcard.compact p{margin:3px 0;font-size:14px;line-height:1.45}
.jobcard.compact .pill{font-size:11px;padding:2px 8px}
.jobcard.compact .tl{width:9px;height:9px;margin-right:5px}
.jobcard.compact .btn.small{padding:6px 11px;font-size:13px}
.assign-corner{position:absolute;top:12px;right:14px;margin:0}
.assign-corner select{width:118px;padding:5px 8px;font-size:13px;
border:1.5px solid var(--line);border-radius:9px;background:#fff;
color:var(--ink)}
.jobcard.has-picker .when{padding-right:126px}
.jobcard.has-picker .client-line{padding-right:126px}
.tile-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}
@media(min-width:760px){.tile-grid{grid-template-columns:repeat(4,1fr)}}
.tile{display:block;background:var(--card);border:1px solid var(--line);
border-radius:14px;padding:12px 13px;text-decoration:none;color:var(--ink);
box-shadow:0 1px 6px rgba(232,67,147,.06)}
.tile-title{display:block;font-weight:800;font-size:15.5px;
color:var(--pink-deep);line-height:1.25}
.tile-line{display:block;font-size:13px;color:var(--mut);line-height:1.35;
margin-top:2px}
.tile-cue{display:block;margin-top:5px;font-size:13px;font-weight:700;
color:var(--pink-dark)}
.tile.attn{background:#fff6df;border-color:#eec987}
.client-search{margin:16px 0 4px}
.client-search label{margin:0 0 6px}
.client-search-row{display:flex;gap:8px}
.client-search-row input{flex:1 1 auto;min-width:0}
.client-search-row .btn{flex:0 0 auto}
.client-card{margin-top:12px;padding:0}
.client-card summary{list-style:none;cursor:pointer;display:flex;flex-wrap:wrap;
align-items:baseline;gap:4px 10px;padding:15px 18px;
-webkit-tap-highlight-color:transparent}
.client-card summary::-webkit-details-marker{display:none}
.client-card summary::after{content:"\\25B8";margin-left:auto;color:var(--pink);
font-weight:800;transition:transform .15s ease}
.client-card[open ] summary::after{transform:rotate(90deg)}
.client-name{font-weight:800;font-size:17px;color:var(--pink-deep)}
.client-phone{color:var(--mut);font-size:14.5px}
.client-detail{padding:0 18px 16px}
.client-detail p{margin:6px 0;color:var(--mut);line-height:1.55}
.client-detail b{color:var(--ink)}
.card.client-card{padding:0}
.card.client-card summary{padding:8px 16px;flex-wrap:nowrap}
.card.client-all{padding:0;margin-top:14px}
.client-all>summary{list-style:none;cursor:pointer;display:flex;
align-items:baseline;gap:4px 10px;padding:13px 18px;
-webkit-tap-highlight-color:transparent}
.client-all>summary::-webkit-details-marker{display:none}
.client-all>summary::after{content:"\\25B8";margin-left:auto;color:var(--pink);
font-weight:800;transition:transform .15s ease}
.client-all[open]>summary::after{transform:rotate(90deg)}
.client-all-title{font-weight:800;font-size:16.5px;color:var(--pink-deep)}
.client-all-body{padding:2px 12px 14px}
.client-all-body .client-card{margin-top:10px}
.client-results{margin-top:2px}
.client-cat{margin-top:12px}
.client-cat label{margin:0 0 6px}
.client-chips{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 8px}
.chip{display:inline-block;padding:3px 10px;border-radius:999px;font-size:12px;
font-weight:700;background:var(--blush2);color:var(--pink-dark)}
.chip.star{background:#fff6df;color:#8a6d1a;box-shadow:inset 0 0 0 1px #eec987}
.client-detail .client-star-line{color:var(--ink);font-weight:700}
.client-card[open] .client-phone{display:none}
.client-name{flex:none}
.client-phone{flex:none}
.client-chips-inline{display:flex;flex-wrap:wrap;gap:4px;flex:0 1 auto;
min-width:0}
.client-chips-inline .chip{font-size:10.5px;padding:2px 7px;line-height:1.3;
text-align:center}
.avatar{border-radius:50%;object-fit:cover;flex:none;background:var(--blush2)}
.avatar-init{display:inline-flex;align-items:center;justify-content:center;
background:linear-gradient(135deg,#f472a5,#c2236a);color:#fff;font-weight:800}
.pick-grid{display:flex;flex-wrap:wrap;gap:16px 14px;margin-top:18px;
max-width:580px}
.pick-tile{display:flex;flex-direction:column;align-items:center;gap:6px;
width:84px;text-decoration:none;color:var(--ink);text-align:center}
.pick-tile .avatar{width:60px;height:60px;font-size:20px;
border:2.5px solid #fff;box-shadow:0 2px 8px rgba(157,28,87,.25)}
.pick-name{font-size:13px;font-weight:700;line-height:1.25}
.team-row{display:flex;gap:13px;align-items:flex-start;margin-top:12px}
.team-row .avatar{width:56px;height:56px;font-size:19px;margin-top:2px}
.team-main{flex:1 1 auto;min-width:0}
.team-name{font-weight:800;font-size:16.5px;color:var(--pink-deep)}
.team-photo-form{display:flex;flex-wrap:wrap;gap:8px;align-items:center;
margin-top:7px}
.team-photo-form input[type=file]{font-size:13px;max-width:225px;min-width:0}
input[type=file]{font:inherit;color:var(--ink)}
.wrap{max-width:1020px;margin:0 auto;padding:22px 18px 40px}
h1{font-size:30px;margin:8px 0 6px;color:var(--pink-deep)}
h2{font-size:21px;margin:26px 0 10px;color:var(--pink-deep)}
.sub{color:var(--mut);font-size:16px;line-height:1.5}
.sub.backline{height:26px;line-height:26px;margin:0 0 8px;text-align:center}
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
.jobcard{border-left:6px solid var(--pink);position:relative}
.jobcard .when{font-size:20px;font-weight:800}
footer.site{border-top:1px solid var(--line);color:var(--mut);font-size:12.5px;
text-align:center;padding:18px 12px 30px;line-height:1.6}
.stamps{color:var(--mut);font-size:13px;margin:6px 0 0}
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
/* Owner board only: booking-count dots. One small black dot per clean in
   a row under the day number (at a full day — FULL_DAY cleans — the dots
   ring the inside edge of the cell instead). The count dots take over the
   under-number spot, so today's pink marker dot steps aside on board days
   that carry count dots. On a filled cell (the selected day — and today
   keeps its pink fill above all) the dots turn white: the cell's own
   styling always wins, the dots just adapt. /book is untouched. */
.board-cal .cal-cell.today.has-dots::after{display:none}
.board-cal .cal-cell.has-dots:not(.full){padding-bottom:11px}
.board-cal .cdots{position:absolute;left:50%;transform:translateX(-50%);
bottom:3px;display:flex;flex-wrap:wrap;justify-content:center;gap:2px;
width:40px;pointer-events:none}
.board-cal .cdot{display:block;width:5px;height:5px;border-radius:50%;
background:#111;flex:none}
.board-cal .cal-cell.sel .cdot{background:#fff}
.board-cal .cdots.ring{position:absolute;inset:0;
transform:none;width:auto;display:block}
.board-cal .cdots.ring .cdot{position:absolute}
.board-cal .cdots.ring .cdot:nth-child(1){top:3px;left:3px}
.board-cal .cdots.ring .cdot:nth-child(2){top:3px;left:50%;margin-left:-3px}
.board-cal .cdots.ring .cdot:nth-child(3){top:3px;right:3px}
.board-cal .cdots.ring .cdot:nth-child(4){right:3px;top:33%}
.board-cal .cdots.ring .cdot:nth-child(5){right:3px;top:66%}
.board-cal .cdots.ring .cdot:nth-child(6){bottom:3px;right:3px}
.board-cal .cdots.ring .cdot:nth-child(7){bottom:3px;left:50%;margin-left:-3px}
.board-cal .cdots.ring .cdot:nth-child(8){bottom:3px;left:3px}
.board-cal .cdots.ring .cdot:nth-child(9){left:3px;top:50%;margin-top:-3px}
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

# Owner pages only: booking-card forms (the corner assign picker and the
# Confirm / Mark done / Cancel buttons) POST and redirect back, and the
# reload used to throw the owner back to the top of the page. Save the
# scroll position as one of those forms is submitted and restore it —
# once, then clear it — when the page it returns to loads. The saved spot
# is keyed to the page it belongs to (path + query) so it can never
# restore on a different page, and the key is distinct from the calendar
# script's (mts-book-scroll) so the two never fight on the board, which
# carries both. Fail-silent: if storage is unavailable the submits work
# exactly as they always did.
OWNER_SCROLL_JS = """
<script>
(function () {
  var PREFIX = "mts-owner-scroll:";
  var here = location.pathname + location.search, y = null;
  try {
    y = sessionStorage.getItem(PREFIX + here);
    if (y !== null) sessionStorage.removeItem(PREFIX + here);
  } catch (e) {}
  if (y !== null) {
    var go = function () { window.scrollTo(0, parseInt(y, 10) || 0); };
    go();
    window.addEventListener("load", go);
  }
  function save(form) {
    var dest = null;
    if (form.classList && form.classList.contains("assign-corner")) {
      var nxt = form.querySelector('input[name="next"]');
      dest = (nxt && nxt.value) || here;
    } else {
      // The action buttons' route always redirects to the bookings list.
      dest = "/owner/bookings";
    }
    try { sessionStorage.setItem(PREFIX + dest, String(window.scrollY)); }
    catch (e) {}
  }
  // The corner picker submits from its select's onchange (form.submit()
  // fires no submit event), so catch its change on the way down, before
  // the inline handler runs.
  document.addEventListener("change", function (ev) {
    var t = ev.target;
    var form = t && t.closest ? t.closest("form.assign-corner") : null;
    if (form) save(form);
  }, true);
  document.addEventListener("submit", function (ev) {
    var form = ev.target;
    if (form && form.closest && form.closest(".jobcard")) save(form);
  }, true);
})();
</script>
"""

# Client book only: the category menu + live search-as-you-type, one
# filter state driving both. Progressive enhancement — every client card
# is already on the page inside the "All clients" drop-down, stamped at
# render with its alphabetical position (data-idx), its searchable
# fields (data-name / data-phone digits / data-email) and its categories
# (data-cats). The category <select> narrows the book first and the
# search then looks inside the category ("All clients" looks
# everywhere): with no query the drop-down holds just that category's
# cards, alphabetical and closed, its summary line naming the category
# and count; with a query, matches move into the results zone under the
# search box, ranked exactly like the server (name starts-with, then
# name contains / phone digits, then email contains; alphabetical
# breaks ties), best match first and already open, while the drop-down
# hides. Clearing the field restores the category's drop-down view. The
# category choice is mirrored into the URL (?cat=) without a reload so
# a copied link reopens on the same category; the server deliberately
# ignores ?cat= — the no-JS fallback is the GET search (unfiltered),
# exactly as before. No fetch, no library; one set of card elements is
# re-parented and re-ordered, never cloned.
CLIENT_SEARCH_JS = """
<script>
(function () {
  var input = document.getElementById("client-q");
  var select = document.getElementById("client-cat");
  var all = document.getElementById("client-all");
  var label = document.getElementById("client-all-label");
  var body = document.getElementById("client-all-body");
  var zone = document.getElementById("client-results");
  if (!input || !all || !body || !zone) return;
  var cards = Array.prototype.slice.call(
    body.querySelectorAll(".client-card"));
  var cat = "";
  function digits(s) { return (s || "").replace(/\\D/g, ""); }
  function idx(c) { return parseInt(c.getAttribute("data-idx"), 10) || 0; }
  function detachAll() {
    cards.forEach(function (c) {
      if (c.parentNode) c.parentNode.removeChild(c);
    });
  }
  function inCat(c) {
    if (!cat) return true;
    var cats = (c.getAttribute("data-cats") || "").split(" ");
    return cats.indexOf(cat) !== -1;
  }
  function rank(c, q, qd) {
    var name = c.getAttribute("data-name") || "";
    var r = null;
    if (name.indexOf(q) === 0) r = 0;
    else if (name.indexOf(q) !== -1) r = 1;
    if (qd.length >= 3 &&
        (c.getAttribute("data-phone") || "").indexOf(qd) !== -1) {
      r = (r === null) ? 1 : Math.min(r, 1);
    }
    if (r === null &&
        (c.getAttribute("data-email") || "").indexOf(q) !== -1) r = 2;
    return r;
  }
  function catLabel() {
    if (!cat) return "All clients";
    if (select) {
      for (var i = 0; i < select.options.length; i++) {
        if (select.options[i].value === cat) return select.options[i].text;
      }
    }
    return cat;
  }
  function showDropdown() {
    detachAll();
    var vis = cards.filter(inCat);
    vis.slice().sort(function (a, b) { return idx(a) - idx(b); })
      .forEach(function (c) { c.open = false; body.appendChild(c); });
    if (label) {
      label.textContent = catLabel() + " · A–Z (" + vis.length + ")";
    }
    zone.innerHTML = "";
    zone.hidden = true;
    all.hidden = false;
    all.open = false;
  }
  function showResults(q) {
    var qd = digits(q), hits = [];
    cards.forEach(function (c) {
      if (!inCat(c)) return;
      var r = rank(c, q, qd);
      if (r !== null) hits.push({ r: r, c: c });
    });
    hits.sort(function (a, b) {
      return (a.r - b.r) || (idx(a.c) - idx(b.c));
    });
    detachAll();
    zone.innerHTML = "";
    if (!hits.length) {
      zone.innerHTML = '<div class="card" style="margin-top:12px">' +
        "<p><b>No client matches that yet.</b></p></div>";
    } else {
      var line = document.createElement("p");
      line.className = "sub";
      line.style.marginTop = "12px";
      line.textContent = hits.length + (hits.length === 1 ?
        " client matches" : " clients match");
      zone.appendChild(line);
      hits.forEach(function (h, i) {
        h.c.open = (i === 0);
        zone.appendChild(h.c);
      });
    }
    zone.hidden = false;
    all.hidden = true;
  }
  function apply() {
    var q = input.value.trim().toLowerCase();
    if (q) showResults(q); else showDropdown();
  }
  var timer = null;
  input.addEventListener("input", function () {
    clearTimeout(timer);
    timer = setTimeout(apply, 150);
  });
  if (select) {
    select.addEventListener("change", function () {
      cat = select.value;
      try {
        var url = new URL(window.location.href);
        if (cat) url.searchParams.set("cat", cat);
        else url.searchParams.delete("cat");
        history.replaceState(null, "", url);
      } catch (e) {}
      apply();
    });
    try {
      var wanted = new URL(window.location.href).searchParams.get("cat");
      if (wanted) {
        for (var i = 0; i < select.options.length; i++) {
          if (select.options[i].value === wanted) {
            cat = wanted;
            select.value = wanted;
            showDropdown();
            break;
          }
        }
      }
    } catch (e) {}
  }
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

# Card left-edge colour by stage — the same hues as the TL_CLASS dots, so
# the dot and the edge always agree. Cancelled gets no edge class and
# keeps the plain pink edge it has today.
EDGE_CLASS = {"on_the_way": "edge-red", "arrived": "edge-amber",
              "done": "edge-green", "requested": "edge-grey",
              "confirmed": "edge-grey"}


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
    <div class="book-head">
    <h1>Book a clean</h1>
    <p class="sub">Pick a service, a day and a start time — real availability,
    no phone tag. Times shown are start times.</p>
    </div>
    <form class="panel book-form" method="post">
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


# Milestone stamps: the moment a booking entered each working status.
# Whoever tapped (cleaner or owner), the transition is what gets recorded.
STAMP_COLUMNS = {"on_the_way": "on_the_way_at", "arrived": "arrived_at",
                 "done": "done_at"}


def _stamp_now():
    """Local wall-clock moment for a milestone — the same clock the
    board's day logic uses, so stamped times read as Halifax time."""
    return datetime.now().isoformat(timespec="seconds")


def _fmt_stamp(raw):
    """'2026-11-03T09:42:07' → '9:42 AM' (no leading zero on the hour)."""
    try:
        t = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return ""
    hour = t.strftime("%I").lstrip("0") or "0"
    return f"{hour}:{t.strftime('%M %p')}"


def stamps_line(b):
    """Compact muted line of the milestones reached so far, in order —
    empty string when none are stamped (nothing invented for old rows)."""
    parts = []
    for status in ("on_the_way", "arrived", "done"):
        raw = b[STAMP_COLUMNS[status]]
        if raw:
            parts.append(f"{STATUS_LABELS[status]} {_fmt_stamp(raw)}")
    if not parts:
        return ""
    return f'<p class="stamps">{" · ".join(parts)}</p>'


def booking_card(b, controls="", compact=False):
    cleaner_name = ""
    if b["cleaner_id"]:
        row = db().execute("SELECT name FROM cleaners WHERE id = ?",
                           (b["cleaner_id"],)).fetchone()
        if row:
            cleaner_name = row["name"]
    active = b["status"] not in ("done", "cancelled")
    if active:
        # The corner picker IS the assignee display on active cards.
        cleaner = ""
    elif cleaner_name:
        cleaner = f' · 👩 {_e(cleaner_name)}'
    else:
        cleaner = ' · <span class="no-cleaner">No cleaner yet</span>'
    notes = f'<p>📝 {_e(b["notes"])}</p>' if b["notes"] else ""
    picker = ""
    if active:
        opts = ['<option value=""{}>Cleaner</option>'.format(
            " selected" if not b["cleaner_id"] else "")]
        for c in db().execute("SELECT * FROM cleaners ORDER BY name"):
            sel = " selected" if c["id"] == b["cleaner_id"] else ""
            opts.append(
                f'<option value="{c["id"]}"{sel}>{_e(c["name"])}</option>')
        nxt = request.path
        if request.query_string:
            nxt += "?" + request.query_string.decode()
        picker = f"""
      <form class="assign-corner" method="post"
        action="/owner/bookings/{b['id']}/assign">
        <input type="hidden" name="next" value="{_e(nxt)}">
        <select name="cleaner_id" aria-label="Assign a cleaner"
          onchange="this.form.submit()">{"".join(opts)}</select>
      </form>"""
    edge = EDGE_CLASS.get(b["status"])
    edge_cls = f" {edge}" if edge else ""
    compact_cls = " compact" if compact else ""
    picker_cls = " has-picker" if active else ""
    return f"""
    <div class="card jobcard{edge_cls}{compact_cls}{picker_cls}">{picker}
      <div class="when">{_e(b['slot'])} — {_e(SERVICE_NAMES.get(b['service'], b['service']))}</div>
      <p class="client-line"><b>{_e(b['name'])}</b> · {_e(b['area'] or b['address'])}</p>
      <p>{status_pill(b['status'])}{cleaner}
      <span class="pill grey">{_e(b['ref'])}</span></p>
      {stamps_line(b)}{notes}{controls}
    </div>"""


def _board_calendar(selected, shown):
    """Month calendar for the owner board — the same markup and classes
    as the /book calendar, but every day is tappable and the months page
    freely (no booking-window limit)."""
    def shift(d, n):
        y, m = d.year, d.month + n
        while m < 1:
            y, m = y - 1, m + 12
        while m > 12:
            y, m = y + 1, m - 12
        return date(y, m, 1)

    prev_m, next_m = shift(shown, -1), shift(shown, 1)
    prev_arrow = (f'<a class="cal-nav" href="/owner?day={selected.isoformat()}'
                  f'&month={prev_m.strftime("%Y-%m")}">‹</a>')
    next_arrow = (f'<a class="cal-nav" href="/owner?day={selected.isoformat()}'
                  f'&month={next_m.strftime("%Y-%m")}">›</a>')
    month_last = shift(shown, 1) - timedelta(days=1)
    # Sunday-start grid, padded with the neighbouring months' days.
    days = []
    d = shown - timedelta(days=(shown.weekday() + 1) % 7)
    while d <= month_last:
        for _ in range(7):
            days.append(d)
            d += timedelta(days=1)
    # One grouped count for the whole rendered grid — non-cancelled cleans
    # per day, padding days included, right for any paged month.
    counts = dict(db().execute(
        """SELECT day, COUNT(*) FROM bookings
           WHERE day >= ? AND day <= ? AND status != 'cancelled'
           GROUP BY day""",
        (days[0].isoformat(), days[-1].isoformat())).fetchall())
    cells = []
    for d in days:
        cls = "cal-cell"
        if d == selected:
            cls += " sel"
        if d == date.today():
            cls += " today"
        if d.month != shown.month:
            cls += " off"
        n = counts.get(d.isoformat(), 0)
        dots = ""
        if n >= FULL_DAY:
            # Full day: ring the inside edge of the cell with FULL_DAY
            # dots (never more) so a packed day reads at a glance.
            cls += " full has-dots"
            dots = ('<span class="cdots ring" aria-hidden="true">'
                    + '<span class="cdot"></span>' * FULL_DAY + '</span>')
        elif n:
            cls += " has-dots"
            dots = ('<span class="cdots" aria-hidden="true">'
                    + '<span class="cdot"></span>' * n + '</span>')
        cells.append(
            f'<a class="{cls}" href="/owner?day={d.isoformat()}'
            f'&month={d.strftime("%Y-%m")}">'
            f'<span class="cal-num">{d.day}</span>{dots}</a>')
    dows = "".join(f'<div class="cal-dow">{n}</div>'
                   for n in ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"))
    return f"""
      <div class="cal board-cal" style="margin:0 auto">
        <div class="cal-head">{prev_arrow}<span class="cal-title">
        {shown.strftime("%B %Y")}</span>{next_arrow}</div>
        <div class="cal-grid">{dows}{"".join(cells)}</div>
      </div>"""


@app.route("/owner")
@owner_required
def owner_home():
    today = date.today()
    today_iso = today.isoformat()
    try:
        selected = date.fromisoformat(
            request.args.get("day", "") or today_iso)
    except ValueError:
        selected = today
    mparam = request.args.get("month", "")
    try:
        shown = (date.fromisoformat(mparam + "-01") if mparam
                 else selected.replace(day=1))
    except ValueError:
        shown = selected.replace(day=1)
    shown = shown.replace(day=1)
    rows = db().execute(
        """SELECT * FROM bookings WHERE day = ? AND status != 'cancelled'
           ORDER BY slot""", (selected.isoformat(),)).fetchall()
    new_quotes = db().execute(
        "SELECT COUNT(*) FROM quotes WHERE status = 'new'").fetchone()[0]
    upcoming = db().execute(
        """SELECT COUNT(*) FROM bookings
           WHERE day > ? AND status NOT IN ('cancelled', 'done')""",
        (today_iso,)).fetchone()[0]
    unassigned = db().execute(
        """SELECT COUNT(*) FROM bookings
           WHERE cleaner_id IS NULL AND day >= ?
             AND status NOT IN ('done', 'cancelled')""",
        (today_iso,)).fetchone()[0]
    team_n = db().execute("SELECT COUNT(*) FROM cleaners").fetchone()[0]
    cards = "".join(booking_card(b, compact=True) for b in rows) or \
        '<div class="card"><p>No cleans booked for this day.</p></div>'
    if selected == today:
        heading = f"Today's board — {pretty_day(today_iso)}"
        back = '<p class="sub backline"></p>'
    else:
        heading = f"Board — {pretty_day(selected.isoformat())}"
        back = ('<p class="sub backline">'
                '<a href="/owner">&larr; Back to today</a></p>')
    if unassigned:
        job_word = "job" if unassigned == 1 else "jobs"
        unassigned_tile = f"""
      <a class="tile attn" href="/owner/bookings?unassigned=1">
      <span class="tile-title">🧹 Unassigned</span>
      <span class="tile-line"><b>{unassigned}</b> {job_word} need a cleaner</span>
      <span class="tile-cue">View unassigned &rarr;</span></a>"""
    else:
        unassigned_tile = """
      <a class="tile" href="/owner/bookings?unassigned=1">
      <span class="tile-title">🧹 Unassigned</span>
      <span class="tile-line">Every job has a cleaner</span>
      <span class="tile-cue">View unassigned &rarr;</span></a>"""
    body = f"""
    <h1>Owner control room</h1>
    <p class="sub">Signed in as owner · <a href="/logout">Sign out</a></p>
    <div class="tile-grid">
      <a class="tile" href="/owner/quotes">
      <span class="tile-title">📩 Quote requests</span>
      <span class="tile-line"><b>{new_quotes}</b> new waiting</span>
      <span class="tile-cue">Open the inbox &rarr;</span></a>
      <a class="tile" href="/owner/bookings">
      <span class="tile-title">🗓️ Coming up</span>
      <span class="tile-line"><b>{upcoming}</b> future bookings after today</span>
      <span class="tile-cue">All bookings &rarr;</span></a>
      <a class="tile" href="/owner/clients">
      <span class="tile-title">👥 Client book</span>
      <span class="tile-line">Every customer in one place</span>
      <span class="tile-cue">View clients &rarr;</span></a>{unassigned_tile}
      <a class="tile" href="/owner/cleaners">
      <span class="tile-title">🧽 Team</span>
      <span class="tile-line"><b>{team_n}</b> cleaners — add or update your team</span>
      <span class="tile-cue">Meet the team &rarr;</span></a>
    </div>
    <h2>{heading}</h2>
    {back}{_board_calendar(selected, shown)}
    <div class="grid board-grid" style="margin-top:14px">{cards}</div>"""
    body += BOOK_SCROLL_JS + OWNER_SCROLL_JS
    return page("Owner Control Room", body)


@app.route("/owner/bookings")
@owner_required
def owner_bookings():
    today_iso = date.today().isoformat()
    unassigned_only = request.args.get("unassigned") == "1"
    if unassigned_only:
        rows = db().execute(
            """SELECT * FROM bookings
               WHERE day >= ? AND cleaner_id IS NULL
                 AND status NOT IN ('done', 'cancelled')
               ORDER BY day, slot""", (today_iso,)).fetchall()
    else:
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
        cards.append(booking_card(b, controls(b), compact=True))
    if unassigned_only:
        job_word = "job" if len(rows) == 1 else "jobs"
        body = f"""
    <h1>Unassigned jobs</h1>
    <p class="sub"><a href="/owner/bookings">&larr; All bookings</a> ·
    {len(rows)} {job_word} still need a cleaner. Pick a cleaner in the
    corner of any card to assign it.</p>
    <div class="grid">{"".join(cards) or '<div class="card"><p>Every job has a cleaner. 🎉</p></div>'}</div>"""
        body += OWNER_SCROLL_JS
        return page("Unassigned Jobs", body)
    body = f"""
    <h1>Bookings</h1>
    <p class="sub"><a href="/owner">&larr; Control room</a> · Upcoming and
    today's bookings, newest requests first in line.</p>
    <div class="grid">{"".join(cards) or '<div class="card"><p>No upcoming bookings.</p></div>'}</div>"""
    body += OWNER_SCROLL_JS
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
    stamp_col = STAMP_COLUMNS.get(new_status)
    if stamp_col:
        db().execute(
            f"UPDATE bookings SET status = ?, {stamp_col} = ? WHERE id = ?",
            (new_status, _stamp_now(), bid))
    else:
        db().execute("UPDATE bookings SET status = ? WHERE id = ?",
                     (new_status, bid))
    db().commit()
    # No success banner — the card's status pill already shows the result.
    return redirect(url_for("owner_bookings"))


@app.route("/owner/bookings/<int:bid>/assign", methods=["POST"])
@owner_required
def owner_booking_assign(bid):
    b = db().execute("SELECT * FROM bookings WHERE id = ?", (bid,)).fetchone()
    if b is None:
        abort(404)
    raw = (request.form.get("cleaner_id") or "").strip()
    row = None
    if raw == "":
        cleaner_id = None
    else:
        try:
            cid = int(raw)
        except ValueError:
            cid = None
        if cid is not None:
            row = db().execute("SELECT * FROM cleaners WHERE id = ?",
                               (cid,)).fetchone()
        if row is None:
            # Unknown cleaner id — ignore the change entirely.
            return redirect(url_for("owner_bookings"))
        cleaner_id = row["id"]
    db().execute("UPDATE bookings SET cleaner_id = ? WHERE id = ?",
                 (cleaner_id, bid))
    db().commit()
    # No success banner — the card already shows the new cleaner (or its
    # absence) in the picker and the cleaner line.
    nxt = request.form.get("next") or ""
    if not (nxt == "/owner" or nxt.startswith("/owner?")
            or nxt == "/owner/bookings"
            or nxt.startswith("/owner/bookings?")):
        nxt = url_for("owner_bookings")
    return redirect(nxt)


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


def _digits(s):
    return "".join(ch for ch in (s or "") if ch.isdigit())


def _client_matches(rows, q):
    """Rank client-book matches for a search query.

    Name matches rank first (starts-with beats contains), then phone
    matches on digits only (so 0107, 5550107 and 902-555-0107 all find
    the same client), then email contains. Rows arrive alphabetical,
    which breaks ties.
    """
    ql = q.lower()
    qd = _digits(q)
    scored = []
    for c in rows:
        name = (c["name"] or "").lower()
        rank = None
        if name.startswith(ql):
            rank = 0
        elif ql in name:
            rank = 1
        if len(qd) >= 3 and qd in _digits(c["phone"]):
            rank = 1 if rank is None else min(rank, 1)
        if rank is None and ql in (c["email"] or "").lower():
            rank = 2
        if rank is not None:
            scored.append((rank, c))
    scored.sort(key=lambda t: t[0])
    return [c for _rank, c in scored]


def _client_categories(rows):
    """Per-client categories for the client book, computed live.

    A client's categories are the distinct services across their
    non-cancelled bookings, plus the services on quote requests matched
    to them. Quotes carry no client_id, so they are attributed by
    contact — email first, then phone digits — the same identity the
    booking intake uses to keep one tidy book. A client with 2+ non-
    cancelled bookings is a Subscriber; when 2+ of those bookings share
    a service + weekday + start slot, that rhythm is named, otherwise
    the card just counts the cleans — never a rhythm the bookings
    don't show.
    """
    info = {c["id"]: {"services": set(), "bookings": []} for c in rows}
    for b in db().execute(
            "SELECT client_id, service, day, slot FROM bookings "
            "WHERE status != 'cancelled'").fetchall():
        entry = info.get(b["client_id"])
        if entry is None:
            continue
        if b["service"] in SERVICE_NAMES:
            entry["services"].add(b["service"])
        entry["bookings"].append(b)
    by_email = {}
    by_phone = {}
    for c in rows:
        if c["email"]:
            by_email.setdefault(c["email"].lower(), c["id"])
        digits = _digits(c["phone"])
        if len(digits) >= 7:
            by_phone.setdefault(digits, c["id"])
    for q in db().execute(
            "SELECT email, phone, service FROM quotes").fetchall():
        svc = (q["service"] or "").strip()
        if svc not in SERVICE_NAMES:
            continue
        cid = None
        if q["email"]:
            cid = by_email.get(q["email"].lower())
        if cid is None:
            qd = _digits(q["phone"])
            if len(qd) >= 7:
                cid = by_phone.get(qd)
        if cid is not None:
            info[cid]["services"].add(svc)
    svc_order = {k: i for i, (k, _n, _d) in enumerate(SERVICES)}
    slot_order = {s: i for i, s in enumerate(SLOTS)}
    out = {}
    for cid, entry in info.items():
        services = sorted(entry["services"], key=lambda k: svc_order[k])
        bookings = entry["bookings"]
        line = None
        if len(bookings) >= 2:
            groups = {}
            for b in bookings:
                try:
                    weekday = date.fromisoformat(b["day"]).strftime("%A")
                except ValueError:
                    continue
                key = (b["service"], weekday, b["slot"])
                groups[key] = groups.get(key, 0) + 1
            best = None
            for (svc, weekday, slot), n in groups.items():
                if n < 2:
                    continue
                rank = (-n, svc_order.get(svc, 99),
                        slot_order.get(slot, 99), weekday)
                if best is None or rank < best[0]:
                    best = (rank, svc, weekday, slot)
            if best is not None:
                _r, svc, weekday, slot = best
                line = (f"⭐ Regular — {SERVICE_NAMES[svc]} · {weekday}s "
                        f"· {slot}")
            else:
                line = f"⭐ Repeat client — {len(bookings)} cleans booked"
        out[cid] = {"services": services,
                    "subscriber": len(bookings) >= 2, "line": line}
    return out


def _client_card(c, expanded=False, idx=None, info=None):
    chips_html = ""
    lines = []
    if info is not None:
        chips = [f'<span class="chip">{_e(SERVICE_NAMES[k])}</span>'
                 for k in info["services"]]
        if info["subscriber"]:
            chips.append('<span class="chip star">⭐ Subscriber</span>')
        if chips:
            chips_html = (f'<span class="client-chips-inline">'
                          f'{"".join(chips)}</span>')
        if info["line"]:
            lines.append(
                f'<p class="client-star-line">{_e(info["line"])}</p>')
    if c["phone"]:
        lines.append(
            f'<p>📞 <a href="tel:{_e(c["phone"])}">{_e(c["phone"])}</a></p>')
    if c["email"]:
        lines.append(f"<p>✉️ {_e(c['email'])}</p>")
    if c["area"]:
        lines.append(f"<p><b>Area:</b> {_e(c['area'])}</p>")
    if c["address"]:
        lines.append(f"<p><b>Address:</b> {_e(c['address'])}</p>")
    if c["notes"]:
        lines.append(f"<p><b>Notes:</b> {_e(c['notes'])}</p>")
    open_attr = " open" if expanded else ""
    attrs = ""
    if idx is not None:
        cats = list(info["services"]) if info else []
        if info and info["subscriber"]:
            cats.append("subscribers")
        cats_attr = " ".join(cats)
        attrs = (f' data-idx="{idx}"'
                 f' data-name="{_e((c["name"] or "").lower())}"'
                 f' data-phone="{_digits(c["phone"])}"'
                 f' data-email="{_e((c["email"] or "").lower())}"'
                 f' data-cats="{_e(cats_attr)}"')
    return f"""
    <details class="card client-card"{open_attr}{attrs}>
      <summary><span class="client-name">{_e(c['name'])}</span>{chips_html}
      <span class="client-phone">{_e(c['phone'])}</span></summary>
      <div class="client-detail">{"".join(lines)}</div>
    </details>"""


@app.route("/owner/clients")
@owner_required
def owner_clients():
    rows = db().execute("SELECT * FROM clients ORDER BY name").fetchall()
    info = _client_categories(rows)
    alpha = {c["id"]: i for i, c in enumerate(rows)}
    q = (request.args.get("q") or "").strip()
    result_line = ""
    script = ""
    if q:
        shown = _client_matches(rows, q)
        if shown:
            noun = "client matches" if len(shown) == 1 else "clients match"
            cards = "".join(
                _client_card(c, expanded=(i == 0), idx=alpha[c["id"]],
                             info=info[c["id"]])
                for i, c in enumerate(shown))
            result_line = f"""
    <p class="sub" style="margin-top:12px">{len(shown)} {noun}
    &ldquo;{_e(q)}&rdquo; · <a href="/owner/clients">Show all clients</a></p>{cards}"""
        else:
            result_line = f"""
    <div class="card" style="margin-top:12px">
      <p><b>No client matches that yet.</b></p>
      <p><a href="/owner/clients">Show all clients</a></p>
    </div>"""
    else:
        cards = "".join(
            _client_card(c, idx=i, info=info[c["id"]])
            for i, c in enumerate(rows))
        cat_counts = {}
        for c in rows:
            for k in info[c["id"]]["services"]:
                cat_counts[k] = cat_counts.get(k, 0) + 1
        cat_opts = ['<option value="">All clients</option>',
                    '<option value="subscribers">⭐ Subscribers</option>']
        for k, name, _d in SERVICES:
            if cat_counts.get(k):
                cat_opts.append(
                    f'<option value="{k}">{_e(name)}</option>')
        result_line = f"""
    <div class="client-cat">
      <label for="client-cat">Category</label>
      <select id="client-cat">{"".join(cat_opts)}</select>
    </div>
    <div class="client-results" id="client-results" hidden></div>
    <details class="card client-all" id="client-all">
      <summary><span class="client-all-title" id="client-all-label">All clients · A&ndash;Z ({len(rows)})</span></summary>
      <div class="client-all-body" id="client-all-body">{cards}</div>
    </details>"""
        script = CLIENT_SEARCH_JS
    body = f"""
    <h1>Client book</h1>
    <p class="sub"><a href="/owner">&larr; Control room</a> · Everyone who has
    booked or asked for a quote, in one place.</p>
    <form class="client-search" method="get" action="/owner/clients">
      <label for="client-q">Search by name or phone number</label>
      <div class="client-search-row">
        <input type="search" id="client-q" name="q" value="{_e(q)}"
          placeholder="e.g. Denise or 0107">
        <button class="btn small" type="submit">Search</button>
      </div>
    </form>{result_line}"""
    if script:
        body += script
    return page("Clients", body)


# ---------------------------------------------------------------- team
@app.route("/owner/cleaners", methods=["GET", "POST"])
@owner_required
def owner_cleaners():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("Please type the new cleaner's name first.")
            return redirect(url_for("owner_cleaners"))
        data, mime, err = _photo_from_upload(request.files.get("photo"))
        if err:
            flash(err)
            return redirect(url_for("owner_cleaners"))
        try:
            db().execute(
                "INSERT INTO cleaners (name, photo, photo_mime) "
                "VALUES (?, ?, ?)", (name, data, mime))
            db().commit()
        except sqlite3.IntegrityError:
            flash(f"{name} is already on the team.")
            return redirect(url_for("owner_cleaners"))
        flash(f"{name} added to the team ✦")
        return redirect(url_for("owner_cleaners"))
    rows = db().execute("SELECT * FROM cleaners ORDER BY name").fetchall()
    cards = []
    for c in rows:
        cards.append(f"""
    <div class="card team-row">
      {_cleaner_avatar(c)}
      <div class="team-main">
        <div class="team-name">{_e(c['name'])}</div>
        <form class="team-photo-form" method="post"
          enctype="multipart/form-data"
          action="/owner/cleaners/{c['id']}/photo">
          <input type="file" name="photo" accept="image/*"
            aria-label="New photo for {_e(c['name'])}">
          <button class="btn small ghost" type="submit">Change photo</button>
        </form>
      </div>
    </div>""")
    body = f"""
    <h1>Your team</h1>
    <p class="sub"><a href="/owner">&larr; Control room</a> · Everyone on
    the crew. New cleaners show up in the picker and the assign lists
    right away.</p>
    {"".join(cards) or '<div class="card"><p>No cleaners yet — add the first one below.</p></div>'}
    <form class="panel" method="post" enctype="multipart/form-data"
      action="/owner/cleaners">
      <label for="new-cleaner-name">Add a cleaner</label>
      <input type="text" id="new-cleaner-name" name="name"
        placeholder="e.g. Sam T." required>
      <label for="new-cleaner-photo">Photo (optional)</label>
      <input type="file" id="new-cleaner-photo" name="photo" accept="image/*">
      <p style="margin-top:18px"><button class="btn" type="submit">Add cleaner</button></p>
    </form>"""
    return page("Your Team", body)


@app.route("/owner/cleaners/<int:cid>/photo", methods=["POST"])
@owner_required
def owner_cleaner_photo(cid):
    c = db().execute("SELECT * FROM cleaners WHERE id = ?",
                     (cid,)).fetchone()
    if c is None:
        abort(404)
    data, mime, err = _photo_from_upload(request.files.get("photo"))
    if err:
        flash(err)
    elif data is None:
        flash("Choose a photo file first, then tap Change photo.")
    else:
        db().execute(
            "UPDATE cleaners SET photo = ?, photo_mime = ? WHERE id = ?",
            (data, mime, cid))
        db().commit()
        flash(f"Photo updated for {c['name']} ✦")
    return redirect(url_for("owner_cleaners"))


# ---------------------------------------------------------------- cleaner
@app.route("/cleaner")
def cleaner_picker():
    rows = db().execute("SELECT * FROM cleaners ORDER BY name").fetchall()
    tiles = "".join(
        f'<a class="pick-tile" href="/cleaner/{c["id"]}">'
        f'{_cleaner_avatar(c)}'
        f'<span class="pick-name">{_e(c["name"])}</span></a>' for c in rows)
    body = f"""
    <h1>Cleaner day view</h1>
    <p class="sub">Pick your name to see <b>only your jobs for today</b> —
    addresses, notes, and your on-the-way buttons.</p>
    <div class="pick-grid">{tiles}</div>"""
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
        edge = EDGE_CLASS.get(b["status"])
        edge_cls = f" {edge}" if edge else ""
        cards.append(f"""
        <div class="card jobcard{edge_cls}">
          <div class="when">{_e(b['slot'])} — {_e(SERVICE_NAMES.get(b['service'], b['service']))}</div>
          <p><b>{_e(b['name'])}</b></p>
          <p>🏠 {_e(b['address'] or b['area'])}</p>
          {phone}{notes}
          <p>{status_pill(b['status'])}</p>
          {stamps_line(b)}
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
    db().execute(
        f"UPDATE bookings SET status = ?, {STAMP_COLUMNS[status]} = ? "
        "WHERE id = ?", (status, _stamp_now(), bid))
    db().commit()
    flash({"on_the_way": "Marked on the way — the office can see you're rolling.",
           "arrived": "Marked arrived. Have a great clean!",
           "done": "Marked done. Nice work!"}[status])
    return redirect(url_for("cleaner_day", cid=cid))


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
