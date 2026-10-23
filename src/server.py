import base64
import re
from email.message import EmailMessage
from pathlib import Path
from html.parser import HTMLParser

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

class _HTMLToText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.out, self._skip = [], False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True
        elif tag in ("br", "p", "div", "tr", "li", "h1", "h2", "h3"):
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            self.out.append(data)


def html_to_text(html: str) -> str:
    p = _HTMLToText()
    p.feed(html)
    return re.sub(r"\n\s*\n+", "\n\n", "".join(p.out)).strip()


def collect_bodies(part, found):
    mime = part.get("mimeType", "")
    data = part.get("body", {}).get("data")
    if data and mime in ("text/plain", "text/html"):
        data += "=" * (-len(data) % 4)  # fix missing base64 padding
        text = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        found.setdefault(mime, []).append(text)
    for child in part.get("parts", []):
        collect_bodies(child, found)

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
def search_emails(
    query: str = "",
    max_results: int = 10,
    primary_only: bool = True,
) -> list[dict]:
    """Search emails using Gmail search syntax (e.g. 'from:bob is:unread').
    By default only searches the Primary tab; set primary_only=False to search everything."""
    svc = gmail()
    q = f"category:primary {query}".strip() if primary_only else query
    res = svc.users().messages().list(
        userId="me", q=q, maxResults=max_results
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
    """Read the body of an email by ID (plain text, falling back to HTML)."""
    msg = gmail().users().messages().get(
        userId="me", id=message_id, format="full"
    ).execute()

    found = {}
    collect_bodies(msg["payload"], found)

    plain = "\n".join(found.get("text/plain", [])).strip()
    html = html_to_text("\n".join(found.get("text/html", [])))

    # Use whichever version has more content
    body = html if len(html) > len(plain) else plain
    return body or msg["snippet"]

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
