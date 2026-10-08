from flask import Flask, request, send_file, redirect, url_for, render_template
from fpdf import FPDF
from datetime import datetime, timedelta
from dotenv import load_dotenv
import pandas as pd
import os
import zipfile
import asyncio
import platform
import json
import requests
import threading

load_dotenv()

app = Flask(__name__)

OUTPUT_FOLDER = "generated_files"
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

TELEGRAM_API_ID   = os.environ.get("TELEGRAM_API_ID",   "")
TELEGRAM_API_HASH = os.environ.get("TELEGRAM_API_HASH", "")
TELEGRAM_SESSION  = os.environ.get("TELEGRAM_SESSION",  "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_BOT_USERNAME = os.environ.get("TELEGRAM_BOT_USERNAME", "")

# Windows needs this policy for Telethon
if platform.system() == "Windows":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


# ── Telegram ──────────────────────────────────────────────────

def _fmt_phone(raw):
    digits = "".join(c for c in str(raw) if c.isdigit())
    if len(digits) == 10:
        return f"+91{digits}"
    if len(digits) == 12 and digits.startswith("91"):
        return f"+{digits}"
    return f"+{digits}"


async def _resolve_chat_id(client, phone):
    fmt = _fmt_phone(phone)
    try:
        entity = await client.get_entity(fmt)
    except Exception:
        return None
    return getattr(entity, "id", None)


def _bot_start_hint():
    if TELEGRAM_BOT_USERNAME:
        return f"Ask the candidate to open https://t.me/{TELEGRAM_BOT_USERNAME} and tap Start."
    return "Ask the candidate to start the Telegram bot first."


async def _send_all_via_bot(notifications):
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.errors import FloodWaitError

    async with TelegramClient(
        StringSession(TELEGRAM_SESSION),
        int(TELEGRAM_API_ID),
        TELEGRAM_API_HASH,
    ) as client:
        for name, phone, message in notifications:
            try:
                chat_id = await _resolve_chat_id(client, phone)
                if not chat_id:
                    print(f"  [--] {name} ({_fmt_phone(phone)}) is not on Telegram or has not started the bot. {_bot_start_hint()}")
                    continue

                response = requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    data={
                        "chat_id": chat_id,
                        "text": message,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                    timeout=30,
                )
                if response.ok:
                    print(f"  [OK] Sent via {TELEGRAM_BOT_USERNAME or 'Telegram bot'} to {name} ({_fmt_phone(phone)})")
                else:
                    detail = response.text
                    print(f"  [ERR] Bot send failed for {name} ({_fmt_phone(phone)}): {detail}")

                await asyncio.sleep(2)

            except FloodWaitError as e:
                print(f"  Rate limit - waiting {e.seconds}s")
                await asyncio.sleep(e.seconds + 3)
            except Exception as e:
                print(f"  [ERR] Could not send to {name} ({phone}): {e}")


async def _send_all(notifications):
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.tl.functions.contacts import ImportContactsRequest, DeleteContactsRequest
    from telethon.tl.types import InputPhoneContact
    from telethon.errors import FloodWaitError

    async with TelegramClient(
        StringSession(TELEGRAM_SESSION),
        int(TELEGRAM_API_ID),
        TELEGRAM_API_HASH,
    ) as client:
        for name, phone, message in notifications:
            try:
                fmt = _fmt_phone(phone)

                # Temporarily import as contact so we can get the entity
                result = await client(ImportContactsRequest([
                    InputPhoneContact(client_id=0, phone=fmt, first_name=name, last_name="")
                ]))

                if result.users:
                    user = result.users[0]
                    await client.send_message(user, message, parse_mode="html")
                    await client(DeleteContactsRequest(id=[user]))
                    print(f"  [OK] Sent to {name} ({fmt})")
                else:
                    print(f"  [--] {name} ({fmt}) is not on Telegram")

                await asyncio.sleep(2)

            except FloodWaitError as e:
                print(f"  Rate limit - waiting {e.seconds}s")
                await asyncio.sleep(e.seconds + 3)
            except Exception as e:
                print(f"  [ERR] Could not send to {name} ({phone}): {e}")


def send_notifications(notifications):
    if not notifications:
        return

    def worker():
        if TELEGRAM_BOT_TOKEN and TELEGRAM_API_ID and TELEGRAM_API_HASH and TELEGRAM_SESSION:
            print(f"Sending Telegram notifications via {TELEGRAM_BOT_USERNAME or 'Telegram bot'} to {len(notifications)} candidate(s)...")
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(_send_all_via_bot(notifications))
            finally:
                loop.close()
            print("Notifications done.")
            return

        if not (TELEGRAM_API_ID and TELEGRAM_API_HASH and TELEGRAM_SESSION):
            return
        print(f"Sending Telegram notifications to {len(notifications)} candidate(s)...")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_send_all(notifications))
        finally:
            loop.close()
        print("Notifications done.")

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()


# ── File generators ───────────────────────────────────────────

def sanitize(domain):
    return domain.lower().replace(" ", "_")


def generate_vcf(domain, entries):
    vcf = ""
    for name, phone in entries:
        if pd.isna(phone):
            continue
        vcf += (
            "BEGIN:VCARD\nVERSION:3.0\n"
            f"N:{name};;;\nFN:{name}\n"
            f"TEL;TYPE=CELL:{str(phone)}\n"
            "END:VCARD\n\n"
        )
    with open(os.path.join(OUTPUT_FOLDER, f"{sanitize(domain)}.vcf"), "w", encoding="utf-8") as f:
        f.write(vcf)


def generate_pdf(domain, schedule, duration):
    """schedule: list of (name, phone, time_str)"""
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()
    W = pdf.w

    # ── Header ────────────────────────────────────────────────
    pdf.set_fill_color(55, 20, 120)
    pdf.rect(0, 0, W, 44, "F")
    pdf.set_fill_color(110, 50, 210)
    pdf.rect(0, 42, W, 3, "F")

    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Arial", "B", 21)
    pdf.set_xy(0, 8)
    pdf.cell(W, 13, "INTERVIEW SCHEDULE", align="C")

    pdf.set_font("Arial", "", 11)
    pdf.set_xy(0, 25)
    pdf.cell(W, 9, domain.upper(), align="C")

    # ── Meta ──────────────────────────────────────────────────
    pdf.set_text_color(150, 110, 200)
    pdf.set_font("Arial", "I", 9)
    pdf.set_xy(0, 51)
    pdf.cell(W, 7,
        f"Generated: {datetime.now().strftime('%d %B %Y  |  %I:%M %p')}     "
        f"Slot duration: {duration} min",
        align="C")

    # ── Table header ──────────────────────────────────────────
    col_w = [13, 118, 40]
    pdf.set_xy(14, 64)
    pdf.set_fill_color(55, 20, 120)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Arial", "B", 11)
    for i, (txt, w) in enumerate(zip(["#", "Candidate Name", "Time"], col_w)):
        pdf.cell(w, 10, txt, fill=True, align="L" if i == 1 else "C")
    pdf.ln()

    # ── Rows ──────────────────────────────────────────────────
    for idx, (name, _, time_str) in enumerate(schedule):
        if pdf.get_y() > pdf.h - 30:
            pdf.add_page()
            pdf.set_fill_color(55, 20, 120)
            pdf.set_text_color(255, 255, 255)
            pdf.set_font("Arial", "B", 11)
            for i, (txt, w) in enumerate(zip(["#", "Candidate Name", "Time"], col_w)):
                pdf.cell(w, 10, txt, fill=True, align="L" if i == 1 else "C")
            pdf.ln()

        r, g, b = (245, 238, 255) if idx % 2 == 0 else (255, 255, 255)
        pdf.set_fill_color(r, g, b)
        pdf.set_text_color(28, 20, 50)
        pdf.set_font("Arial", "", 11)
        pdf.set_x(14)
        pdf.cell(col_w[0], 9, str(idx + 1), fill=True, align="C")
        pdf.cell(col_w[1], 9, name[:55], fill=True)
        pdf.set_text_color(88, 28, 135)
        pdf.set_font("Arial", "B", 11)
        pdf.cell(col_w[2], 9, time_str, fill=True, align="C")
        pdf.ln()

    # ── Footer ────────────────────────────────────────────────
    pdf.set_draw_color(200, 180, 240)
    pdf.set_line_width(0.35)
    y_end = pdf.get_y() + 7
    pdf.line(14, y_end, W - 14, y_end)
    pdf.set_xy(14, y_end + 4)
    pdf.set_font("Arial", "", 10)
    pdf.set_text_color(140, 110, 180)
    pdf.cell(0, 8, f"Total Candidates: {len(schedule)}")

    pdf.set_y(-13)
    pdf.set_font("Arial", "I", 8)
    pdf.set_text_color(190, 170, 220)
    pdf.cell(0, 8, "Interview Scheduler", align="C")

    pdf.output(os.path.join(OUTPUT_FOLDER, f"{sanitize(domain)}.pdf"))


# ── Routes ────────────────────────────────────────────────────

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        file          = request.files["excel_file"]
        start_time_str = request.form["start_time"]
        duration      = int(request.form["duration"])
        schedule_type = request.form["schedule_type"]

        df = pd.read_excel(file)

        name_col = domain_col = phone_col = None
        for col in df.columns:
            cl = col.lower()
            if "name" in cl and not name_col:
                name_col = col
            elif "domain" in cl and not domain_col:
                domain_col = col
            elif any(k in cl for k in ("phone", "mobile", "contact")) and not phone_col:
                phone_col = col

        if not name_col or not domain_col:
            return "Error: Could not detect 'Name' or 'Domain' columns.", 400

        df[domain_col] = df[domain_col].astype(str)
        base_time = datetime.strptime(start_time_str, "%H:%M")

        for fn in os.listdir(OUTPUT_FOLDER):
            os.remove(os.path.join(OUTPUT_FOLDER, fn))

        # Organise applicants by domain
        domain_applicants = {}
        for _, row in df.iterrows():
            name  = str(row[name_col]).strip()
            phone = row[phone_col] if phone_col else None
            for dom in [d.strip().lower() for d in row[domain_col].split(",")]:
                domain_applicants.setdefault(dom, []).append((name, phone))

        # Build timed schedules
        current_time     = base_time
        domain_schedules = {}
        for domain, applicants in domain_applicants.items():
            t = current_time if schedule_type == "same_day" else base_time
            schedule = []
            for name, phone in applicants:
                schedule.append((name, phone, t.strftime("%H:%M")))
                t += timedelta(minutes=duration)
            domain_schedules[domain] = schedule
            if schedule_type == "same_day":
                current_time = t

        # Generate files
        for domain, schedule in domain_schedules.items():
            generate_pdf(domain, schedule, duration)
            generate_vcf(domain, [(n, p) for n, p, _ in schedule])

        # Send Telegram notifications in a background thread so the download can start immediately.
        if phone_col:
            notifications = []
            for domain, schedule in domain_schedules.items():
                for name, phone, time_str in schedule:
                    if phone and not pd.isna(phone):
                        msg = (
                            f"🗓 <b>Interview Schedule</b>\n\n"
                            f"Hi <b>{name}</b>! 👋\n\n"
                            f"📌 <b>Domain:</b> {domain.upper()}\n"
                            f"⏰ <b>Time:</b> {time_str}\n\n"
                            f"Please arrive 5 minutes early.\n"
                            f"Best of luck! 🍀"
                        )
                        notifications.append((name, phone, msg))
            send_notifications(notifications)

        return redirect(url_for("download_all"))

    return render_template("index.html")


@app.route("/download_all")
def download_all():
    zip_path = os.path.join(OUTPUT_FOLDER, "all_files.zip")
    with zipfile.ZipFile(zip_path, "w") as zf:
        for fn in os.listdir(OUTPUT_FOLDER):
            if fn.endswith((".pdf", ".vcf")):
                zf.write(os.path.join(OUTPUT_FOLDER, fn), arcname=fn)
    return send_file(zip_path, as_attachment=True)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
