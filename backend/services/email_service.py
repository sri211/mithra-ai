import os
import smtplib
import asyncio
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from loguru import logger

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", "") or SMTP_USER
FROM_NAME = os.getenv("FROM_NAME", "Mithra AI")
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://mithraai.in")


def _send_sync(to_email: str, subject: str, html: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{FROM_NAME} <{FROM_EMAIL}>"
    msg["To"] = to_email
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
        server.ehlo()
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(FROM_EMAIL, to_email, msg.as_string())


async def send_email(to_email: str, subject: str, html: str) -> None:
    if not SMTP_USER or not SMTP_PASS:
        logger.warning("SMTP not configured (SMTP_USER/SMTP_PASS missing) — email not sent")
        return
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _send_sync, to_email, subject, html)


async def send_password_reset_email(to_email: str, reset_token: str) -> None:
    reset_url = f"{FRONTEND_URL}/reset-password?token={reset_token}"
    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#0a0614;font-family:Inter,system-ui,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0a0614;padding:40px 20px;">
    <tr><td align="center">
      <table width="100%" style="max-width:480px;background:rgba(20,13,40,0.95);border:1px solid rgba(124,58,237,0.25);border-radius:20px;overflow:hidden;">
        <!-- Header -->
        <tr><td style="background:linear-gradient(135deg,#7c3aed,#5b21b6);padding:28px 36px;text-align:center;">
          <div style="font-size:28px;font-weight:900;color:#fff;letter-spacing:-0.5px;">Mithra AI</div>
          <div style="font-size:13px;color:rgba(255,255,255,0.7);margin-top:4px;">Your AI Career Companion</div>
        </td></tr>
        <!-- Body -->
        <tr><td style="padding:36px;">
          <h2 style="margin:0 0 12px;font-size:20px;font-weight:800;color:#f1f5f9;">Reset your password</h2>
          <p style="margin:0 0 24px;font-size:14px;color:#94a3b8;line-height:1.7;">
            We received a request to reset the password for your Mithra AI account associated with <strong style="color:#a78bfa;">{to_email}</strong>.
          </p>
          <p style="margin:0 0 24px;font-size:14px;color:#94a3b8;line-height:1.7;">
            Click the button below to set a new password. This link expires in <strong style="color:#f1f5f9;">1 hour</strong>.
          </p>
          <div style="text-align:center;margin:32px 0;">
            <a href="{reset_url}" style="display:inline-block;padding:14px 36px;background:linear-gradient(135deg,#7c3aed,#6d28d9);color:#fff;font-size:15px;font-weight:700;text-decoration:none;border-radius:12px;box-shadow:0 4px 16px rgba(124,58,237,0.4);">
              Reset Password
            </a>
          </div>
          <p style="margin:0 0 8px;font-size:12px;color:#475569;line-height:1.6;">
            If the button doesn't work, copy and paste this URL into your browser:
          </p>
          <p style="margin:0 0 24px;font-size:11px;color:#7c3aed;word-break:break-all;">{reset_url}</p>
          <hr style="border:none;border-top:1px solid rgba(124,58,237,0.15);margin:24px 0;">
          <p style="margin:0;font-size:12px;color:#475569;line-height:1.6;">
            If you didn't request this, you can safely ignore this email — your password won't change.
          </p>
        </td></tr>
        <!-- Footer -->
        <tr><td style="padding:16px 36px;background:rgba(0,0,0,0.3);text-align:center;">
          <p style="margin:0;font-size:11px;color:#334155;">© 2026 Mithra AI · <a href="https://mithraai.in" style="color:#7c3aed;text-decoration:none;">mithraai.in</a></p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""
    try:
        await send_email(to_email, "Reset your Mithra AI password", html)
        logger.info(f"Password reset email sent to {to_email}")
    except Exception as e:
        logger.error(f"Failed to send password reset email to {to_email}: {e}")
        raise
