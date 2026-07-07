"""
services/alerts.py — Day 11: SMTP email + Twilio SMS alerts

    send_watch_email(corridor, risk_score, source_count, timestamp)
        -> WATCH transition. Recipients: PROCUREMENT_ANALYST, MINISTRY_USER.

    send_playbook_email(playbook_id, pdf_bytes, pdf_filename, summary)
        -> Playbook generated. Same recipients, PDF attached.

    send_confirmed_sms(corridor, risk_score, playbook_status)
        -> CONFIRMED transition ONLY (never WATCH). Recipients:
           MINISTRY_USER, PROCUREMENT_ANALYST.

Design notes:
- Both smtplib and the Twilio SDK are SYNC/blocking libraries. Every
  send function here runs its actual I/O via asyncio.to_thread — the
  whole session has been full of event-loop-freezing bugs from calling
  blocking libraries directly inside async functions; not repeating
  that here.
- Recipient lookup depends on Person B's get_users_by_roles(), which
  does not exist yet. Every public function here checks for it and
  logs + no-ops if missing, rather than crashing whatever triggered
  the alert (a WATCH detection, a playbook generation) — same
  defensive pattern as routers/auth.py depending on the users table.
- Twilio trial accounts can only SMS numbers verified in the Twilio
  console (Verified Caller IDs) — a TwilioRestException for an
  unverified number is caught and logged, not raised, so a demo SMS
  failure never takes down the pipeline that triggered it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Optional

logger = logging.getLogger(__name__)

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", SMTP_USERNAME)

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "http://localhost:3000")

EMAIL_ALERT_ROLES = ["PROCUREMENT_ANALYST", "MINISTRY_USER"]
SMS_ALERT_ROLES = ["MINISTRY_USER", "PROCUREMENT_ANALYST"]


# ---------------------------------------------------------------------------
# Recipient lookup — defensive against the not-yet-built users table
# ---------------------------------------------------------------------------

def _get_recipients(roles: List[str], contact_field: str) -> List[str]:
    """
    Returns a list of email addresses or phone numbers for the given
    roles. contact_field is "email" or "phone".

    Returns [] (never raises) if get_users_by_roles() doesn't exist yet
    (Person B's users-table helper, not built as of this writing) or if
    it errors for any reason — a recipient-lookup failure must never
    crash the pipeline stage that's trying to send an alert.
    """
    try:
        from db.postgres_queries import get_users_by_roles
    except ImportError:
        logger.warning(
            "alerts: get_users_by_roles() not available yet — "
            "skipping recipient lookup, no alert sent."
        )
        return []

    try:
        users = get_users_by_roles(roles)
        contacts = [u.get(contact_field) for u in users if u.get(contact_field)]
        return contacts
    except Exception as e:
        logger.error(f"alerts: get_users_by_roles failed: {e}")
        return []


# ---------------------------------------------------------------------------
# SMTP (blocking) — always run through asyncio.to_thread
# ---------------------------------------------------------------------------

def _send_email_sync(
    to_addresses: List[str],
    subject: str,
    body_text: str,
    attachment_bytes: Optional[bytes] = None,
    attachment_filename: Optional[str] = None,
) -> None:
    if not SMTP_USERNAME or not SMTP_PASSWORD:
        logger.warning("alerts: SMTP_USERNAME/SMTP_PASSWORD not configured — skipping email")
        return

    msg = MIMEMultipart()
    msg["From"] = SMTP_FROM_EMAIL
    msg["To"] = ", ".join(to_addresses)
    msg["Subject"] = subject
    msg.attach(MIMEText(body_text, "plain"))

    if attachment_bytes and attachment_filename:
        part = MIMEApplication(attachment_bytes, Name=attachment_filename)
        part["Content-Disposition"] = f'attachment; filename="{attachment_filename}"'
        msg.attach(part)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.sendmail(SMTP_FROM_EMAIL, to_addresses, msg.as_string())

    logger.info(f"alerts: email sent to {len(to_addresses)} recipient(s): {subject}")


async def send_watch_email(
    corridor: str,
    risk_score: float,
    source_count: int,
    timestamp: str,
) -> None:
    """WATCH state transition — corridor risk crossed 0.45."""
    recipients = _get_recipients(EMAIL_ALERT_ROLES, "email")
    if not recipients:
        return

    subject = f"[ResiChain WATCH] {corridor} corridor risk elevated"
    body = (
        f"WATCH state triggered for corridor: {corridor}\n\n"
        f"Risk score: {risk_score:.2f}\n"
        f"Confirming sources: {source_count}\n"
        f"Detected at: {timestamp}\n\n"
        f"Dashboard: {DASHBOARD_URL}\n\n"
        f"This is an automated ResiChain alert. No action required at WATCH "
        f"level — CONFIRMED will trigger SMS if risk continues to escalate."
    )

    try:
        await asyncio.to_thread(_send_email_sync, recipients, subject, body)
    except Exception as e:
        logger.error(f"alerts: WATCH email send failed: {e}")


async def send_playbook_email(
    playbook_id: str,
    pdf_bytes: bytes,
    pdf_filename: str,
    summary_text: str,
) -> None:
    """Playbook generated — same recipient group, PDF attached."""
    recipients = _get_recipients(EMAIL_ALERT_ROLES, "email")
    if not recipients:
        return

    subject = f"[ResiChain] Playbook ready — {playbook_id}"
    body = (
        f"A new procurement playbook has been generated.\n\n"
        f"{summary_text}\n\n"
        f"Full details attached (PDF).\n"
        f"Dashboard: {DASHBOARD_URL}/playbooks/{playbook_id}"
    )

    try:
        await asyncio.to_thread(
            _send_email_sync, recipients, subject, body, pdf_bytes, pdf_filename
        )
    except Exception as e:
        logger.error(f"alerts: playbook email send failed: {e}")


# ---------------------------------------------------------------------------
# Twilio SMS (blocking) — always run through asyncio.to_thread
# ---------------------------------------------------------------------------

def _send_sms_sync(to_number: str, message: str) -> None:
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN or not TWILIO_PHONE_NUMBER:
        logger.warning("alerts: Twilio credentials not configured — skipping SMS")
        return

    from twilio.rest import Client
    from twilio.base.exceptions import TwilioRestException

    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    try:
        client.messages.create(body=message, from_=TWILIO_PHONE_NUMBER, to=to_number)
        logger.info(f"alerts: SMS sent to {to_number}")
    except TwilioRestException as e:
        # Trial accounts can only SMS numbers verified in the Twilio
        # console — this is the expected failure mode until a recipient
        # is added under Verified Caller IDs. Log, don't raise: one
        # unreachable phone number must never break the CONFIRMED
        # pipeline for everyone else.
        logger.error(
            f"alerts: SMS to {to_number} failed (Twilio trial accounts require "
            f"the recipient to be a Verified Caller ID): {e}"
        )


async def send_confirmed_sms(
    corridor: str,
    risk_score: float,
    playbook_status: str,
) -> None:
    """
    CONFIRMED state transition ONLY — never call this for WATCH.
    Message deliberately short per spec: corridor, risk score, playbook
    status, dashboard link only.
    """
    recipients = _get_recipients(SMS_ALERT_ROLES, "phone")
    if not recipients:
        return

    message = (
        f"ResiChain CONFIRMED: {corridor} risk {risk_score:.2f}. "
        f"Playbook: {playbook_status}. {DASHBOARD_URL}"
    )

    for phone in recipients:
        try:
            await asyncio.to_thread(_send_sms_sync, phone, message)
        except Exception as e:
            logger.error(f"alerts: CONFIRMED SMS send failed for {phone}: {e}") 