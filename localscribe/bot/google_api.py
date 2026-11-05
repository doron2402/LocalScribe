"""Calendar, Directory and Drive over REST, via a delegated service account."""
from __future__ import annotations

import json
from datetime import datetime, timezone

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events.readonly"
DIRECTORY_SCOPE = "https://www.googleapis.com/auth/admin.directory.user.readonly"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"

_API = "https://www.googleapis.com"
FOLDER_MIME = "application/vnd.google-apps.folder"
DOC_MIME = "application/vnd.google-apps.document"
_MEETING_PROP = "localscribeMeeting"


class GoogleError(RuntimeError):
    def __init__(self, status: int, method: str, url: str):
        super().__init__(f"Google API {method} {url} returned {status}")
        self.status = status


def session(service_account_file: str, subject: str, scope: str):
    """An HTTP session acting as `subject`."""
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(
        service_account_file, scopes=[scope], subject=subject
    )
    return AuthorizedSession(creds)


def _json(resp) -> dict:
    # The status alone: Google's error bodies can quote document names and emails.
    if resp.status_code >= 400:
        raise GoogleError(resp.status_code, resp.request.method, resp.request.url.split("?")[0])
    return resp.json() if resp.content else {}


def list_directory_users(http, domain: str) -> list[str]:
    users, page = [], None
    while True:
        params = {"domain": domain, "maxResults": 200, "query": "isSuspended=false"}
        if page:
            params["pageToken"] = page
        data = _json(http.get(f"{_API}/admin/directory/v1/users", params=params))
        users += [u["primaryEmail"].lower() for u in data.get("users", [])]
        page = data.get("nextPageToken")
        if not page:
            return users


def list_events(http, user: str, time_min: datetime, time_max: datetime) -> list[dict]:
    events, page = [], None
    while True:
        params = {
            "timeMin": time_min.astimezone(timezone.utc).isoformat(),
            "timeMax": time_max.astimezone(timezone.utc).isoformat(),
            "singleEvents": "true", "orderBy": "startTime", "maxResults": 250,
        }
        if page:
            params["pageToken"] = page
        data = _json(http.get(f"{_API}/calendar/v3/calendars/{user}/events", params=params))
        events += data.get("items", [])
        page = data.get("nextPageToken")
        if not page:
            return events


def _quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


_ALL_DRIVES = {"supportsAllDrives": "true", "includeItemsFromAllDrives": "true", "corpora": "allDrives"}


def _search(http, query: str) -> list[dict]:
    params = {"q": query, "fields": "files(id,name,webViewLink)", **_ALL_DRIVES}
    return _json(http.get(f"{_API}/drive/v3/files", params=params)).get("files", [])


def ensure_folder(http, name: str, parent: str) -> str:
    query = (f"name = '{_quote(name)}' and '{_quote(parent)}' in parents "
             f"and mimeType = '{FOLDER_MIME}' and trashed = false")
    found = _search(http, query)
    if found:
        return found[0]["id"]
    body = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent]}
    return _json(http.post(f"{_API}/drive/v3/files", params={"supportsAllDrives": "true"},
                           json=body))["id"]


def find_note(http, meeting_key: str) -> dict | None:
    query = (f"appProperties has {{ key='{_MEETING_PROP}' and value='{_quote(meeting_key)}' }} "
             "and trashed = false")
    found = _search(http, query)
    return found[0] if found else None


def create_doc(http, name: str, parent: str, markdown: str, meeting_key: str) -> dict:
    """Upload markdown and let Drive convert it to a Google Doc."""
    metadata = {
        "name": name, "mimeType": DOC_MIME, "parents": [parent],
        "appProperties": {_MEETING_PROP: meeting_key},
    }
    boundary = "localscribe-note-boundary"
    body = (
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{json.dumps(metadata)}\r\n"
        f"--{boundary}\r\nContent-Type: text/markdown; charset=UTF-8\r\n\r\n"
        f"{markdown}\r\n--{boundary}--\r\n"
    ).encode()
    return _json(http.post(
        f"{_API}/upload/drive/v3/files",
        params={"uploadType": "multipart", "supportsAllDrives": "true", "fields": "id,webViewLink"},
        data=body,
        headers={"Content-Type": f"multipart/related; boundary={boundary}"},
    ))


def share_read(http, file_id: str, email: str) -> None:
    _json(http.post(
        f"{_API}/drive/v3/files/{file_id}/permissions",
        params={"supportsAllDrives": "true", "sendNotificationEmail": "false"},
        json={"type": "user", "role": "reader", "emailAddress": email},
    ))


def file_note(http, root_folder: str, meeting_key: str, title: str, when: datetime,
              markdown: str, readers: list[str]) -> dict:
    """File a meeting's notes under root/YYYY/MM. Safe to call twice for one meeting."""
    existing = find_note(http, meeting_key)
    if existing:
        return existing
    year = ensure_folder(http, f"{when:%Y}", root_folder)
    month = ensure_folder(http, f"{when:%m}", year)
    doc = create_doc(http, f"{when:%Y-%m-%d} {title}", month, markdown, meeting_key)
    for email in readers:
        share_read(http, doc["id"], email)
    return doc
