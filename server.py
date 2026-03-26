#!/usr/bin/env python3
"""
The Nest Studio — Backend Server
Serves the landing page and powers the AI chat agent via Claude API.
Includes Google Calendar availability checking and booking request notifications.
"""

import json
import os
import smtplib
import sys
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory
import anthropic

# ── CONFIG ──────────────────────────────────────────────────────────────────

CONFIG_PATH = Path(__file__).parent / "config.json"

def load_config():
    """Load config from config.json, with environment variable overrides for production."""
    # Start with defaults
    cfg = {
        "anthropic_api_key": "",
        "google_calendar": {
            "credentials_file": "google_credentials.json",
            "calendar_id": "primary",
            "calendar_ids": {}
        },
        "notification_emails": [],
        "smtp": {
            "host": "smtp.gmail.com",
            "port": 587,
            "username": "",
            "password": ""
        },
        "port": 8888
    }

    # Load config.json if it exists (local dev)
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            cfg.update(json.load(f))

    # Environment variable overrides (production)
    if os.environ.get("ANTHROPIC_API_KEY"):
        cfg["anthropic_api_key"] = os.environ["ANTHROPIC_API_KEY"]
    if os.environ.get("GOOGLE_CALENDAR_ID"):
        cfg["google_calendar"]["calendar_id"] = os.environ["GOOGLE_CALENDAR_ID"]
        cfg["google_calendar"]["calendar_ids"] = {
            "a_room": os.environ["GOOGLE_CALENDAR_ID"],
            "b_room": os.environ["GOOGLE_CALENDAR_ID"],
            "full_studio": os.environ["GOOGLE_CALENDAR_ID"]
        }
    if os.environ.get("SMTP_USERNAME"):
        cfg["smtp"]["username"] = os.environ["SMTP_USERNAME"]
    if os.environ.get("SMTP_PASSWORD"):
        cfg["smtp"]["password"] = os.environ["SMTP_PASSWORD"]
    if os.environ.get("NOTIFICATION_EMAILS"):
        cfg["notification_emails"] = [e.strip() for e in os.environ["NOTIFICATION_EMAILS"].split(",")]
    if os.environ.get("PORT"):
        cfg["port"] = int(os.environ["PORT"])

    return cfg

config = load_config()

# ── FLASK APP ───────────────────────────────────────────────────────────────

app = Flask(__name__, static_folder=".", static_url_path="")

# ── STATIC FILES ────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(".", "index.html")

@app.route("/images/<path:filename>")
def images(filename):
    return send_from_directory("images", filename)

# ── SYSTEM PROMPT ───────────────────────────────────────────────────────────

def get_system_prompt():
    import pytz
    la_tz = pytz.timezone("America/Los_Angeles")
    now = datetime.now(la_tz)
    today = now.strftime("%Y-%m-%d")
    day_of_week = now.strftime("%A")
    # Pre-compute key reference dates so the model doesn't have to do date math
    days_until_friday = (4 - now.weekday()) % 7  # 4 = Friday
    if days_until_friday == 0 and now.hour >= 23:
        days_until_friday = 7  # If it's late Friday, "this Friday" = next Friday
    this_friday = (now + timedelta(days=days_until_friday)).strftime("%Y-%m-%d")
    this_saturday = (now + timedelta(days=(5 - now.weekday()) % 7)).strftime("%Y-%m-%d")
    this_sunday = (now + timedelta(days=(6 - now.weekday()) % 7)).strftime("%Y-%m-%d")

    return f"""You are the AI assistant for The Nest Studio, a premium recording studio. You speak on behalf of the studio in a warm, professional, and knowledgeable tone. You're helpful but not overly formal — think friendly studio manager, not corporate receptionist.

Today is {day_of_week}, {today} (Los Angeles time). ALWAYS use this exact date as your reference when converting relative dates. Here are pre-computed reference dates — use these EXACTLY, do NOT recalculate:
- "this Friday" = {this_friday}
- "this Saturday" = {this_saturday}
- "this Sunday" = {this_sunday}
- For other days, count forward from today ({today}, {day_of_week}). ALWAYS use the current year ({now.year}).

## STUDIO INFO

**The Nest Studio** is a professional recording facility with two rooms and a lounge/common area. It's built for artists, producers, and engineers who take their craft seriously.

## ROOMS & RATES (PUBLIC — share these freely)

### A Room — Flagship Studio
- $200/hour
- $2,200/day (12 hours)
- Full-format analog console
- Large-format monitoring system
- Marble producer's bar with seating
- Tracking-ready live room
- Fully acoustically treated

### B Room — Production Suite
- $100/hour
- $1,000/day (12 hours)
- Nearfield monitors
- Keyboard and production tools
- Comfortable seating (couch, chairs)
- Intimate writing/production environment
- Fully acoustically treated

### Full Studio — Both Rooms
- $300/hour
- $3,500/day (12 hours)
- Full building lockout (A Room + B Room + lounge)
- Ideal for large projects, album tracking, or when maximum flexibility is needed

## IMPORTANT POLICIES

- **Engineers**: Available upon request but NOT guaranteed. If a client needs an engineer, they should mention it when booking and the studio will try to arrange one.
- **Day rate**: A "day" is 12 hours of studio time.
- **No hidden fees**: Rates are straightforward.

## CONFIDENTIAL — DO NOT SHARE
- Friends & Family rates exist but are NOT to be disclosed. If asked about discounts or special rates, say: "We occasionally offer special rates — reach out to us directly at turq@sturdy.co to discuss."
- Do NOT invent or fabricate gear lists, room dimensions, or technical specs you don't know. If asked about specific gear you're not sure about, say you'll need to confirm and suggest they email for details.

## BOOKING FLOW
To submit a booking you need these details: room (a_room/b_room/full_studio), date (YYYY-MM-DD), start_time (HH:MM 24hr), duration_hours (number), client_name, client_contact (email or phone).

CRITICAL RULES:
- CAREFULLY re-read the ENTIRE conversation history before responding. If the client has ALREADY provided any of these details — room, date, time, duration, name, contact — in ANY previous message, you MUST use that information. NEVER re-ask for something already stated.
- If you can calculate duration from a time range (e.g., "8pm-11pm" = 3 hours), do the math yourself.
- If you have ALL required details, call submit_booking_request IMMEDIATELY. Do not ask for confirmation.
- If you are missing details, ask for ONLY the missing ones in a single short message. Do not list room options unless the client hasn't indicated a room preference at all.
- When a client says a room name like "a room" or "A Room", that means room = "a_room". Map it and move on.

When a client asks about availability:
- You MUST ALWAYS use the check_availability tool IMMEDIATELY. NEVER ask clarifying questions first — just check.
- If the client doesn't specify a room, check ALL rooms (call the tool 3 times: a_room, b_room, full_studio) and report what's available across the board.
- If the client specifies a room, check just that room.
- Even if the client gives a vague date like "this week" or "Friday", convert it to a YYYY-MM-DD date and call the tool.
- Report back ONLY the available time windows. Do NOT share who has the room booked or any client/event names — just say which hours are open.
- If multiple days are asked about, call the tool once per day per room.

## SCOPE — STRICTLY ENFORCED
You ONLY discuss topics related to The Nest Studio. This includes:
- Room info, rates, and pricing
- Studio availability and scheduling
- Gear and equipment questions
- Booking sessions
- Engineer availability
- Studio policies
- Directions and parking (direct to email)

If someone asks about ANYTHING else — personal questions, general knowledge, opinions, music advice, other studios, or any topic not directly about The Nest — politely redirect: "I'm here to help with everything related to The Nest Studio — rooms, rates, availability, and booking. What can I help you with?"

Do NOT engage with off-topic conversation even if it seems harmless. Stay focused.

## TONE
- Warm, direct, professional
- Don't oversell — let the studio speak for itself
- Be helpful and efficient
- If you don't know something, say so honestly
- Keep responses concise — this is a chat widget, not an email

## CONTACT
- Email: turq@sturdy.co
- For anything you can't handle, direct them to email
"""

# ── TOOLS (for Claude to call) ──────────────────────────────────────────────

TOOLS = [
    {
        "name": "check_availability",
        "description": "Check studio room availability on a specific date. Returns available time slots for the requested room.",
        "input_schema": {
            "type": "object",
            "properties": {
                "room": {
                    "type": "string",
                    "enum": ["a_room", "b_room", "full_studio"],
                    "description": "Which room to check: a_room, b_room, or full_studio"
                },
                "date": {
                    "type": "string",
                    "description": "Date to check in YYYY-MM-DD format"
                }
            },
            "required": ["room", "date"]
        }
    },
    {
        "name": "submit_booking_request",
        "description": "Submit a booking request. This sends a notification to the studio owner for approval — it does NOT auto-confirm the booking.",
        "input_schema": {
            "type": "object",
            "properties": {
                "room": {
                    "type": "string",
                    "enum": ["a_room", "b_room", "full_studio"],
                    "description": "Which room: a_room, b_room, or full_studio"
                },
                "date": {
                    "type": "string",
                    "description": "Requested date in YYYY-MM-DD format"
                },
                "start_time": {
                    "type": "string",
                    "description": "Start time in HH:MM format (24hr)"
                },
                "duration_hours": {
                    "type": "number",
                    "description": "Number of hours requested"
                },
                "client_name": {
                    "type": "string",
                    "description": "Client's name"
                },
                "client_contact": {
                    "type": "string",
                    "description": "Client's email or phone"
                },
                "notes": {
                    "type": "string",
                    "description": "Any additional notes (e.g., engineer needed, special requirements)"
                }
            },
            "required": ["room", "date", "start_time", "duration_hours", "client_name", "client_contact"]
        }
    }
]

# ── GOOGLE CALENDAR AUTH (runs once at startup) ────────────────────────────

def get_google_creds():
    """Authenticate with Google Calendar. Runs OAuth flow if needed.
    In production, loads token from GOOGLE_TOKEN_PICKLE env var (base64-encoded).
    Locally, uses token.pickle file and runs OAuth browser flow if needed.
    """
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    import pickle
    import base64

    SCOPES = ["https://www.googleapis.com/auth/calendar", "https://www.googleapis.com/auth/gmail.send"]
    creds = None
    token_path = Path(__file__).parent / "token.pickle"
    creds_path = Path(__file__).parent / config["google_calendar"]["credentials_file"]

    # Try loading token from environment variable first (production)
    token_b64 = os.environ.get("GOOGLE_TOKEN_PICKLE")
    if token_b64:
        try:
            creds = pickle.loads(base64.b64decode(token_b64))
            print("   ✓ Google Calendar token loaded from environment", file=sys.stderr)
        except Exception as e:
            print(f"   ⚠️  Failed to load token from env: {e}", file=sys.stderr)

    # Fall back to token.pickle file (local dev)
    if not creds and token_path.exists():
        with open(token_path, "rb") as f:
            creds = pickle.load(f)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # Save refreshed token back to file if running locally
            if not token_b64:
                with open(token_path, "wb") as f:
                    pickle.dump(creds, f)
        elif creds_path.exists() and not os.environ.get("RENDER"):
            # Only run browser OAuth flow locally, never on a cloud server
            print("\n   🔐 Google Calendar authorization required.", file=sys.stderr)
            print("   A browser window will open — sign in and grant access.", file=sys.stderr)
            print("   This only happens once.\n", file=sys.stderr)
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
            creds = flow.run_local_server(port=0)
            with open(token_path, "wb") as f:
                pickle.dump(creds, f)
            print("   ✓ Google Calendar authorized and token saved.\n", file=sys.stderr)
        else:
            return None

    return creds

# Authenticate at import time so the token is ready before any requests
_google_creds = None
_creds_path = Path(__file__).parent / config["google_calendar"]["credentials_file"]
if _creds_path.exists() or os.environ.get("GOOGLE_TOKEN_PICKLE"):
    _google_creds = get_google_creds()


# ── TOOL IMPLEMENTATIONS ────────────────────────────────────────────────────

def check_availability(room: str, date: str) -> dict:
    """Check Google Calendar for room availability."""
    room_names = {"a_room": "A Room", "b_room": "B Room", "full_studio": "Full Studio"}
    room_name = room_names.get(room, room)

    try:
        from googleapiclient.discovery import build
        from google.auth.transport.requests import Request

        global _google_creds
        if not _google_creds:
            return {
                "available": True,
                "room": room_name,
                "date": date,
                "note": "Google Calendar is not configured yet. Showing as available by default — the studio owner will confirm actual availability when they review the booking request.",
                "slots": ["Full day available (pending confirmation)"]
            }

        # Refresh token if expired
        if not _google_creds.valid:
            _google_creds.refresh(Request())
            import pickle
            token_path = Path(__file__).parent / "token.pickle"
            with open(token_path, "wb") as f:
                pickle.dump(_google_creds, f)

        service = build("calendar", "v3", credentials=_google_creds)

        # Always use the shared Nest studio calendar
        calendar_id = config["google_calendar"].get(
            "calendar_id",
            config["google_calendar"]["calendar_ids"].get(room, "primary")
        )

        # Query events for the requested date in LA timezone
        import pytz
        tz = "America/Los_Angeles"
        la_tz = pytz.timezone(tz)
        # Parse the date and localize to get the correct UTC offset (PST vs PDT)
        from datetime import date as date_type
        year, month, day = [int(x) for x in date.split("-")]
        local_dt = la_tz.localize(datetime(year, month, day))
        offset = local_dt.strftime("%z")  # e.g. "-0700" or "-0800"
        offset_formatted = f"{offset[:3]}:{offset[3:]}"  # e.g. "-07:00"

        date_start = f"{date}T00:00:00{offset_formatted}"
        date_end = f"{date}T23:59:59{offset_formatted}"

        print(f"[Calendar] Checking {calendar_id} for {date} (offset: {offset_formatted})", file=sys.stderr)

        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=date_start,
            timeMax=date_end,
            timeZone=tz,
            singleEvents=True,
            orderBy="startTime"
        ).execute()

        raw_events = events_result.get("items", [])
        print(f"[Calendar] Found {len(raw_events)} total events on {date}", file=sys.stderr)

        # Skip all-day events and non-studio events (e.g., Garbage Day, holidays)
        # Only timed events with a dateTime (not just a date) are studio bookings
        all_events = []
        for event in raw_events:
            has_time = "dateTime" in event.get("start", {})
            if has_time:
                all_events.append(event)
            else:
                print(f"[Calendar]   Skipping all-day event: {event.get('summary', 'Unknown')}", file=sys.stderr)

        print(f"[Calendar] {len(all_events)} timed events (studio bookings) on {date}", file=sys.stderr)

        # Filter events by room based on event title
        # Convention: "(A)" = A Room, "(B)" = B Room, "(A+B)" or "(Full)" = Full Studio
        # Events without a room tag are treated as relevant to all rooms
        room_tags = {
            "a_room": ["(a)", "(a room)"],
            "b_room": ["(b)", "(b room)"],
            "full_studio": ["(a+b)", "(full)", "(full studio)", "(lockout)"]
        }

        relevant_events = []
        for event in all_events:
            summary = event.get("summary", "").lower()
            tags_for_room = room_tags.get(room, [])

            # Check if event is specifically for this room
            is_for_this_room = any(tag in summary for tag in tags_for_room)
            # Check if it's a full studio booking (blocks all rooms)
            is_full_lockout = any(tag in summary for tag in room_tags["full_studio"])
            # Check if event has no room tag at all (assume it blocks all rooms)
            has_any_tag = any(tag in summary for tags in room_tags.values() for tag in tags)

            # Full Studio requires BOTH rooms — so ANY room booking blocks it
            is_any_room_booked = False
            if room == "full_studio":
                is_any_room_booked = any(
                    tag in summary
                    for tag in room_tags["a_room"] + room_tags["b_room"] + room_tags["full_studio"]
                )

            if is_for_this_room or is_full_lockout or is_any_room_booked or not has_any_tag:
                relevant_events.append(event)

        print(f"[Calendar] {len(relevant_events)} events relevant to {room_name}", file=sys.stderr)

        if not relevant_events:
            return {
                "available": True,
                "room": room_name,
                "date": date,
                "slots": ["Full day available"],
                "booked_times": []
            }

        booked = []
        for event in relevant_events:
            start = event["start"].get("dateTime", event["start"].get("date"))
            end = event["end"].get("dateTime", event["end"].get("date"))
            summary = event.get("summary", "Booked")
            # Log full details server-side but strip client names from response
            print(f"[Calendar]   - {summary}: {start} to {end}", file=sys.stderr)
            booked.append({"start": start, "end": end, "status": "booked"})

        return {
            "available": False,
            "room": room_name,
            "date": date,
            "booked_times": booked,
            "note": f"{room_name} has bookings on this date. Tell the client which time windows are OPEN, not who has it booked. Do not share any client or event names."
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[Calendar] ERROR: {e}", file=sys.stderr)
        return {
            "available": True,
            "room": room_name,
            "date": date,
            "note": f"Calendar check unavailable right now. Showing as available by default — the studio owner will confirm actual availability when they review the booking request.",
            "slots": ["Full day available (pending confirmation)"]
        }


def submit_booking_request(room: str, date: str, start_time: str,
                           duration_hours: float, client_name: str,
                           client_contact: str, notes: str = "") -> dict:
    """Submit a booking request and notify the studio owner."""
    room_names = {"a_room": "A Room", "b_room": "B Room", "full_studio": "Full Studio"}
    room_name = room_names.get(room, room)

    rates = {
        "a_room": {"hourly": 200, "day": 2200},
        "b_room": {"hourly": 100, "day": 1000},
        "full_studio": {"hourly": 300, "day": 3500}
    }

    rate = rates.get(room, rates["a_room"])
    if duration_hours >= 12:
        estimated_cost = rate["day"]
        duration_label = "Full Day (12 hrs)"
    else:
        estimated_cost = rate["hourly"] * duration_hours
        duration_label = f"{duration_hours} hour{'s' if duration_hours != 1 else ''}"

    # Generate unique booking ID
    import hashlib
    booking_id = hashlib.md5(f"{client_name}{date}{start_time}{datetime.now().isoformat()}".encode()).hexdigest()[:12]

    # Save booking request to file (always works, no external deps)
    booking = {
        "id": booking_id,
        "timestamp": datetime.now().isoformat(),
        "room": room_name,
        "date": date,
        "start_time": start_time,
        "duration": duration_label,
        "estimated_cost": f"${estimated_cost:,.0f}",
        "client_name": client_name,
        "client_contact": client_contact,
        "notes": notes,
        "status": "pending"
    }

    bookings_file = Path(__file__).parent / "bookings.json"
    bookings = []
    if bookings_file.exists():
        with open(bookings_file) as f:
            bookings = json.load(f)
    bookings.append(booking)
    with open(bookings_file, "w") as f:
        json.dump(bookings, f, indent=2)

    # Build confirmation URL (dynamic — works for both localhost and production)
    base_url = os.environ.get("BASE_URL", f"http://localhost:{config.get('port', 8888)}")
    confirm_url = f"{base_url}/api/confirm/{booking_id}"

    # Try to send email notification via Gmail API (using existing Google OAuth)
    email_sent = False
    try:
        import threading
        import base64

        recipients = config.get("notification_emails", [])
        if isinstance(recipients, str):
            recipients = [recipients]

        subject = f"New Booking Request — {room_name} on {date}"

        html_body = f"""
<div style="font-family: -apple-system, sans-serif; max-width: 600px; margin: 0 auto;">
  <h2 style="color: #1A1A1A;">New Booking Request</h2>
  <table style="width: 100%; border-collapse: collapse; margin: 1rem 0;">
    <tr><td style="padding: 8px 0; color: #6B6B6B;">Room</td><td style="padding: 8px 0;"><strong>{room_name}</strong></td></tr>
    <tr><td style="padding: 8px 0; color: #6B6B6B;">Date</td><td style="padding: 8px 0;"><strong>{date}</strong></td></tr>
    <tr><td style="padding: 8px 0; color: #6B6B6B;">Time</td><td style="padding: 8px 0;"><strong>{start_time}</strong></td></tr>
    <tr><td style="padding: 8px 0; color: #6B6B6B;">Duration</td><td style="padding: 8px 0;"><strong>{duration_label}</strong></td></tr>
    <tr><td style="padding: 8px 0; color: #6B6B6B;">Est. Cost</td><td style="padding: 8px 0;"><strong>${estimated_cost:,.0f}</strong></td></tr>
    <tr><td style="padding: 8px 0; color: #6B6B6B;">Client</td><td style="padding: 8px 0;"><strong>{client_name}</strong></td></tr>
    <tr><td style="padding: 8px 0; color: #6B6B6B;">Contact</td><td style="padding: 8px 0;"><strong>{client_contact}</strong></td></tr>
    <tr><td style="padding: 8px 0; color: #6B6B6B;">Notes</td><td style="padding: 8px 0;">{notes or 'None'}</td></tr>
  </table>
  <a href="{confirm_url}" style="display: inline-block; padding: 12px 32px; background: #2D5A3D; color: white; text-decoration: none; border-radius: 6px; font-weight: 600; margin: 1rem 0;">Confirm &amp; Add to Calendar</a>
  <p style="color: #6B6B6B; font-size: 14px; margin-top: 1rem;">Clicking the button will add this booking to THE NEST RECORDING STUDIO SCHEDULE.</p>
</div>"""

        global _google_creds
        if _google_creds and recipients:
            from googleapiclient.discovery import build
            from google.auth.transport.requests import Request

            def _send_gmail(creds, recipients, subject, html_body):
                try:
                    # Refresh token if needed
                    if not creds.valid:
                        creds.refresh(Request())

                    gmail_service = build("gmail", "v1", credentials=creds)

                    # Build the email message
                    msg = MIMEMultipart("alternative")
                    msg["To"] = ", ".join(recipients)
                    msg["Subject"] = subject
                    msg.attach(MIMEText(html_body, "html"))

                    # Encode and send via Gmail API
                    raw_message = base64.urlsafe_b64encode(msg.as_bytes()).decode()
                    gmail_service.users().messages().send(
                        userId="me",
                        body={"raw": raw_message}
                    ).execute()

                    print(f"[Email] Gmail notification sent to {recipients}", file=sys.stderr)
                except Exception as ex:
                    import traceback
                    traceback.print_exc()
                    print(f"[Email] Gmail API failed: {ex}", file=sys.stderr)

            threading.Thread(target=_send_gmail, args=(_google_creds, recipients, subject, html_body), daemon=True).start()
            email_sent = True
        else:
            print("[Email] No Google credentials or recipients configured", file=sys.stderr)

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Email notification failed: {e}", file=sys.stderr)

    return {
        "success": True,
        "booking": booking,
        "email_sent": email_sent,
        "message": f"Booking request submitted for {room_name} on {date} at {start_time} ({duration_label}). Estimated cost: ${estimated_cost:,.0f}. The studio owner has been notified and will confirm your booking shortly."
    }


TOOL_HANDLERS = {
    "check_availability": check_availability,
    "submit_booking_request": submit_booking_request,
}

# ── BOOKING CONFIRMATION (creates calendar event) ──────────────────────────

@app.route("/api/confirm/<booking_id>", methods=["GET"])
def confirm_booking(booking_id):
    """Confirm a booking and add it to Google Calendar."""
    bookings_file = Path(__file__).parent / "bookings.json"
    if not bookings_file.exists():
        return "<h1>No bookings found.</h1>", 404

    with open(bookings_file) as f:
        bookings = json.load(f)

    # Find the booking by ID
    booking = None
    booking_idx = None
    for i, b in enumerate(bookings):
        if b.get("id") == booking_id:
            booking = b
            booking_idx = i
            break

    if not booking:
        return "<h1>Booking not found.</h1>", 404

    if booking.get("status") == "confirmed":
        return f"""
        <html><body style="font-family: -apple-system, sans-serif; max-width: 600px; margin: 50px auto; text-align: center;">
        <h2>Already Confirmed</h2>
        <p>This booking for <strong>{booking['room']}</strong> on <strong>{booking['date']}</strong> at <strong>{booking['start_time']}</strong> has already been added to the calendar.</p>
        </body></html>
        """

    # Create Google Calendar event
    try:
        from googleapiclient.discovery import build
        from google.auth.transport.requests import Request

        global _google_creds
        if not _google_creds:
            return "<h1>Google Calendar not configured.</h1>", 500

        if not _google_creds.valid:
            _google_creds.refresh(Request())
            import pickle
            token_path = Path(__file__).parent / "token.pickle"
            with open(token_path, "wb") as f:
                pickle.dump(_google_creds, f)

        service = build("calendar", "v3", credentials=_google_creds)
        calendar_id = config["google_calendar"].get("calendar_id", "primary")

        # Build room tag for event title
        room_tag_map = {
            "A Room": "(A)",
            "B Room": "(B)",
            "Full Studio": "(A+B)"
        }
        room_tag = room_tag_map.get(booking["room"], "")
        event_title = f"{booking['client_name']} {room_tag}"

        # Parse start time and duration
        start_dt = f"{booking['date']}T{booking['start_time']}:00"
        duration_str = booking.get("duration", "")

        # Calculate end time
        if "12" in duration_str or "day" in duration_str.lower():
            hours = 12
        else:
            # Extract number of hours from duration string
            import re
            match = re.search(r'(\d+\.?\d*)', duration_str)
            hours = float(match.group(1)) if match else 1

        from datetime import timedelta
        start_datetime = datetime.strptime(start_dt, "%Y-%m-%dT%H:%M:%S")
        end_datetime = start_datetime + timedelta(hours=hours)

        event = {
            "summary": event_title,
            "description": f"Booking via The Nest Studio website\nClient: {booking['client_name']}\nContact: {booking['client_contact']}\nNotes: {booking.get('notes', 'None')}",
            "start": {
                "dateTime": start_datetime.strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": "America/Los_Angeles"
            },
            "end": {
                "dateTime": end_datetime.strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": "America/Los_Angeles"
            }
        }

        created_event = service.events().insert(
            calendarId=calendar_id,
            body=event
        ).execute()

        # Update booking status
        bookings[booking_idx]["status"] = "confirmed"
        bookings[booking_idx]["calendar_event_id"] = created_event.get("id")
        with open(bookings_file, "w") as f:
            json.dump(bookings, f, indent=2)

        print(f"[Booking] Confirmed: {event_title} on {booking['date']} at {booking['start_time']}", file=sys.stderr)

        # Send confirmation email to the client
        client_contact = booking.get("client_contact", "")
        if "@" in client_contact and _google_creds:
            try:
                import threading
                import base64

                # Format the start time for display (e.g., "20:00" -> "8:00 PM")
                try:
                    from datetime import datetime as dt_cls
                    t = dt_cls.strptime(booking['start_time'], "%H:%M")
                    display_time = t.strftime("%-I:%M %p")
                except:
                    display_time = booking['start_time']

                client_html = f"""
<div style="font-family: -apple-system, sans-serif; max-width: 600px; margin: 0 auto;">
  <h2 style="color: #2D5A3D;">Your Booking is Confirmed!</h2>
  <p>Hey {booking['client_name'].split()[0]},</p>
  <p>Your session at <strong>The Nest Studio</strong> has been confirmed. Here are the details:</p>
  <table style="width: 100%; border-collapse: collapse; margin: 1rem 0;">
    <tr><td style="padding: 8px 0; color: #6B6B6B;">Room</td><td style="padding: 8px 0;"><strong>{booking['room']}</strong></td></tr>
    <tr><td style="padding: 8px 0; color: #6B6B6B;">Date</td><td style="padding: 8px 0;"><strong>{booking['date']}</strong></td></tr>
    <tr><td style="padding: 8px 0; color: #6B6B6B;">Time</td><td style="padding: 8px 0;"><strong>{display_time}</strong></td></tr>
    <tr><td style="padding: 8px 0; color: #6B6B6B;">Duration</td><td style="padding: 8px 0;"><strong>{booking['duration']}</strong></td></tr>
    <tr><td style="padding: 8px 0; color: #6B6B6B;">Est. Cost</td><td style="padding: 8px 0;"><strong>{booking.get('estimated_cost', 'TBD')}</strong></td></tr>
  </table>
  <p>If you have any questions or need to make changes, reach out to us at <a href="mailto:turq@sturdy.co">turq@sturdy.co</a>.</p>
  <p style="margin-top: 1.5rem;">See you at The Nest!</p>
</div>"""

                client_subject = f"Booking Confirmed — {booking['room']} on {booking['date']}"

                def _send_client_confirmation(creds, client_email, subject, html_body):
                    try:
                        from googleapiclient.discovery import build as gbuild
                        from google.auth.transport.requests import Request as GRequest
                        if not creds.valid:
                            creds.refresh(GRequest())
                        gmail = gbuild("gmail", "v1", credentials=creds)
                        msg = MIMEMultipart("alternative")
                        msg["To"] = client_email
                        msg["Subject"] = subject
                        msg.attach(MIMEText(html_body, "html"))
                        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
                        gmail.users().messages().send(userId="me", body={"raw": raw}).execute()
                        print(f"[Email] Client confirmation sent to {client_email}", file=sys.stderr)
                    except Exception as ex:
                        print(f"[Email] Client confirmation failed: {ex}", file=sys.stderr)

                threading.Thread(
                    target=_send_client_confirmation,
                    args=(_google_creds, client_contact, client_subject, client_html),
                    daemon=True
                ).start()
            except Exception as ex:
                print(f"[Email] Failed to queue client confirmation: {ex}", file=sys.stderr)

        return f"""
        <html><body style="font-family: -apple-system, sans-serif; max-width: 600px; margin: 50px auto; text-align: center;">
        <h2 style="color: #2D5A3D;">Booking Confirmed!</h2>
        <p><strong>{booking['room']}</strong></p>
        <p>{booking['date']} at {booking['start_time']} ({booking['duration']})</p>
        <p>Client: {booking['client_name']} ({booking['client_contact']})</p>
        <p style="margin-top: 1rem; color: #2D5A3D;">A confirmation email has been sent to the client.</p>
        <p style="margin-top: 1rem; color: #6B6B6B;">Added to THE NEST RECORDING STUDIO SCHEDULE calendar.</p>
        </body></html>
        """

    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"<h1>Error creating calendar event: {e}</h1>", 500


# ── CHAT API ────────────────────────────────────────────────────────────────

# In-memory conversation store (per-session, keyed by session ID)
conversations = {}

def content_to_dict(content):
    """Convert SDK content blocks to plain dicts for conversation history."""
    result = []
    for block in content:
        if block.type == "text":
            result.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            result.append({
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": block.input
            })
    return result


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.json
    user_message = data.get("message", "").strip()
    session_id = data.get("session_id", "default")

    if not user_message:
        return jsonify({"error": "No message provided"}), 400

    # Get or create conversation history
    if session_id not in conversations:
        conversations[session_id] = []

    history = conversations[session_id]
    history.append({"role": "user", "content": user_message})

    # Keep conversation history manageable (last 20 messages)
    if len(history) > 20:
        history = history[-20:]
        conversations[session_id] = history

    try:
        client = anthropic.Anthropic(api_key=config["anthropic_api_key"])

        # Initial API call
        print(f"[Chat] Sending message: {user_message[:80]}...", file=sys.stderr)
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            system=get_system_prompt(),
            tools=TOOLS,
            messages=history
        )

        print(f"[Chat] Stop reason: {response.stop_reason}", file=sys.stderr)
        for block in response.content:
            if block.type == "tool_use":
                print(f"[Chat] Tool call: {block.name}({block.input})", file=sys.stderr)
            elif hasattr(block, "text"):
                print(f"[Chat] Text response: {block.text[:100]}...", file=sys.stderr)

        # Handle tool use loop (max 5 iterations to prevent runaway)
        loop_count = 0
        while response.stop_reason == "tool_use" and loop_count < 5:
            loop_count += 1
            tool_results = []
            assistant_content = content_to_dict(response.content)

            for block in response.content:
                if block.type == "tool_use":
                    handler = TOOL_HANDLERS.get(block.name)
                    if handler:
                        result = handler(**block.input)
                    else:
                        result = {"error": f"Unknown tool: {block.name}"}

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result)
                    })

            # Add assistant message and tool results as plain dicts
            history.append({"role": "assistant", "content": assistant_content})
            history.append({"role": "user", "content": tool_results})

            # Continue the conversation with tool results
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1024,
                system=get_system_prompt(),
                tools=TOOLS,
                messages=history
            )

        # Extract the final text response
        assistant_text = ""
        for block in response.content:
            if hasattr(block, "text"):
                assistant_text += block.text

        # Add final assistant response to history
        history.append({"role": "assistant", "content": assistant_text})

        return jsonify({"response": assistant_text})

    except anthropic.AuthenticationError as e:
        print(f"AUTH ERROR: {e}", file=sys.stderr)
        return jsonify({
            "response": "I'm having trouble connecting right now. For immediate help, please email us at turq@sturdy.co and we'll get back to you quickly!"
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"\nChat error: {e}", file=sys.stderr)
        return jsonify({
            "response": "Sorry, I hit a snag. You can reach us directly at turq@sturdy.co — we'd love to help!"
        })


# ── BOOKINGS VIEWER (for studio owner) ──────────────────────────────────────

@app.route("/api/bookings", methods=["GET"])
def get_bookings():
    """View all booking requests (for studio owner)."""
    bookings_file = Path(__file__).parent / "bookings.json"
    if bookings_file.exists():
        with open(bookings_file) as f:
            return jsonify(json.load(f))
    return jsonify([])


# ── RUN ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", config.get("port", 8888)))
    api_key = config.get("anthropic_api_key", "")

    print(f"\n🎙️  The Nest Studio — Server")
    print(f"   http://localhost:{port}\n")

    if not api_key or "YOUR_" in api_key:
        print("   ⚠️  No Anthropic API key configured.")
        print("   Chat will fall back to error messages.")
        print(f"   Add your key to {CONFIG_PATH} or set ANTHROPIC_API_KEY env var\n")
    else:
        print("   ✓ Claude API connected")

    if _google_creds:
        print("   ✓ Google Calendar connected")
    else:
        gcreds = Path(__file__).parent / config["google_calendar"]["credentials_file"]
        if gcreds.exists():
            print("   ✓ Google Calendar credentials found")
        else:
            print("   ⚠️  No Google Calendar credentials.")
            print("   Availability checks will default to 'available (pending confirmation)'.\n")

    smtp = config.get("smtp", {})
    if smtp.get("password") and "YOUR_" not in smtp.get("password", "YOUR_"):
        print("   ✓ Email notifications configured")
    else:
        print("   ⚠️  Email not configured. Bookings will be saved to bookings.json.\n")

    is_production = os.environ.get("RENDER") or os.environ.get("RAILWAY_ENVIRONMENT")
    print(f"   Starting server on port {port}...\n")
    app.run(host="0.0.0.0", port=port, debug=not is_production)
