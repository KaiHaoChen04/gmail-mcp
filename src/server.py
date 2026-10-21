import base64
from email.message import EmailMessage
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from mcp.server.fastmcp import FastMCP

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]
ROOT = Path(__file__).parent
TOKEN = ROOT / "token.json"
CLIENT = ROOT / "client_secret.json"

mcp = FastMCP("gmail")


def gmail():
    creds = None
    if TOKEN.exists():
        creds = Credentials.from_authorized_user_file(TOKEN, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT, SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds)


@mcp.tool()
def search_emails(query: str = "", max_results: int = 10) -> list[dict]:
    """Search emails using Gmail search syntax (e.g. 'from:bob is:unread')."""
    svc = gmail()
    res = svc.users().messages().list(
        userId="me", q=query, maxResults=max_results
    ).execute()
    out = []
    for m in res.get("messages", []):
        msg = svc.users().messages().get(
            userId="me", id=m["id"], format="metadata",
            metadataHeaders=["From", "Subject", "Date"],
        ).execute()
        headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
        out.append({"id": m["id"], "snippet": msg["snippet"], **headers})
    return out


@mcp.tool()
def read_email(message_id: str) -> str:
    """Read the plain-text body of an email by ID."""
    msg = gmail().users().messages().get(
        userId="me", id=message_id, format="full"
    ).execute()

    def find_text(part):
        if part.get("mimeType") == "text/plain" and part["body"].get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode()
        for p in part.get("parts", []):
            if (t := find_text(p)):
                return t
        return ""

    return find_text(msg["payload"]) or msg["snippet"]


@mcp.tool()
def create_draft(to: str, subject: str, body: str) -> str:
    """Create a draft email (does not send)."""
    m = EmailMessage()
    m["To"], m["Subject"] = to, subject
    m.set_content(body)
    raw = base64.urlsafe_b64encode(m.as_bytes()).decode()
    d = gmail().users().drafts().create(
        userId="me", body={"message": {"raw": raw}}
    ).execute()
    return f"Draft created: {d['id']}"


if __name__ == "__main__":
    mcp.run()  # stdio transport