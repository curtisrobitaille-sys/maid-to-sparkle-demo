# Maid To Sparkle — Booking Demo

A HalifaxFX sales demo: a booking web app for **Maid To Sparkle**, a Halifax
cleaning business that currently takes all bookings through Facebook
Messenger. All names, phone numbers and addresses in the seed data are
fictional samples.

## Run it

```bash
cd ~/workspace/projects/maid-to-sparkle-demo
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
./venv/bin/python app.py            # http://localhost:5000
# or, the way Render runs it:
./venv/bin/gunicorn app:app
```

Delete `demo.db` and restart to re-seed fresh sample data (seed dates are
computed from the current day, so "today's board" is never empty).

## What's in it

- **Customer side (no login):** landing page with the business's real
  services (residential, deep clean, Airbnb turnover, move-in/out,
  post-construction, commercial), a structured **quote intake** form
  (property type, beds/baths, extras, sq ft band, frequency, pets, eco
  product default), and a **booking flow** — pick a service, a day within
  14 days, and a start time from live availability (taken slots show as
  booked); confirmation screen gives a booking reference (e.g. MTS-12345).
- **Owner control room:** today's board, upcoming bookings with working
  Confirm / Mark done / Cancel buttons, a quote-request inbox in the same
  structured shape, and the client book (seeded clients plus anyone who
  books through the site).
- **Cleaner day view:** pick a cleaner, see only their jobs for today as
  tap-friendly cards (address, tap-to-call phone, notes) with the
  on-the-clock flow: **On my way → I've arrived → Done**, each updating
  the job's status everywhere.

## Demo credentials

- Owner login (shown on the login page): **owner** / **sparkle-demo**
- Cleaners (no password, picker at `/cleaner`): Alyssa R., Kayla M.,
  Brittany S.

Footer on every page labels it plainly: "Demo built by HalifaxFX."
