#!/usr/bin/env python
"""
itol_upload.py: send a cohort report's tree and annotations to an iTOL account.

The cohort report writes <project>/cohort_report/itol_batch.zip: one tree file
and the annotation files that go with it (see cohort_itol.py). iTOL's batch
upload takes that zip, an API key and the name of a project in the account,
and answers with the id of the new tree. This module makes that one request
and reports the link.

  itol_upload.py --project DIR --itol-project NAME [--tree-name NAME]
                 [--description TEXT] [--no-popup]

What leaves the lab: the tree, the isolate names and the annotations in the
zip. They go to itol.embl.de, into the account the API key belongs to. Nothing
is sent unless this script is run, and it is run only when a person asks.

The API key is read from the ITOL_API_KEY environment variable and from
nowhere else. It is never taken as an argument, printed, or written to a file.
iTOL needs a subscription for batch upload, and the project must already exist
in the account. Each upload makes a new tree; it does not replace an earlier
one.

The connection is verified as usual. If iTOL's certificate cannot be verified
the upload stops before anything is sent.

Standard library only. Prints one JSON object and exits 0 when the tree was
accepted, 1 when it was not.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import secrets
import ssl
import sys
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

UPLOAD_URL = "https://itol.embl.de/batch_uploader.cgi"
TREE_URL = "https://itol.embl.de/tree/{tree_id}"
BATCH_ZIP = "itol_batch.zip"
POPUP_FILE = "popup.txt"
RECORD_NAME = "itol_upload.json"
KEY_VARIABLE = "ITOL_API_KEY"
USER_AGENT = "bdtools-itol-upload"

HttpPost = Callable[[str, bytes, str], str]


@dataclass
class Outcome:
    """What iTOL said about one upload."""

    ok: bool
    tree_id: str = ""
    url: str = ""
    warnings: List[str] = field(default_factory=list)
    message: str = ""
    files: List[str] = field(default_factory=list)


def encode_multipart(fields: Dict[str, str], file_field: str, filename: str,
                     data: bytes) -> Tuple[bytes, str]:
    """A multipart/form-data body and its Content-Type header."""
    boundary = "----bdtools" + secrets.token_hex(16)
    out = io.BytesIO()
    for name, value in fields.items():
        value = re.sub(r"[\r\n]+", " ", str(value))
        out.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        out.write(value.encode("utf-8") + b"\r\n")
    safe_name = re.sub(r'[^A-Za-z0-9._-]', "_", filename) or "upload.zip"
    out.write((f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; '
               f'filename="{safe_name}"\r\nContent-Type: application/zip\r\n\r\n').encode())
    out.write(data + b"\r\n")
    out.write(f"--{boundary}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"


def parse_response(text: str) -> Outcome:
    """Read iTOL's plain-text answer. A line starting SUCCESS carries the new
    tree's id; any other lines beside it are warnings. With no such line the
    upload failed and the text says why."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    for line in reversed(lines):
        found = re.match(r"SUCCESS:?\s+([0-9A-Za-z_]+)\s*$", line)
        if found:
            tree_id = found.group(1)
            return Outcome(True, tree_id, TREE_URL.format(tree_id=tree_id),
                           [other for other in lines if other is not line])
    said = " ".join(lines)
    # An error page is HTML; its tags are noise in a one-line message.
    said = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", said)).strip()
    return Outcome(False, message=(said[:600] or "iTOL sent an empty reply."))


def read_batch(report_dir: Path, include_popup: bool = True) -> Tuple[bytes, List[str]]:
    """The zip to send and the names in it. Raises ValueError when the report
    has no usable zip. With include_popup False the per-isolate details file
    is left out, for when its metadata columns should stay in the lab."""
    path = Path(report_dir) / BATCH_ZIP
    if not path.is_file():
        raise ValueError(f"There is no {BATCH_ZIP} in {report_dir}. Rebuild the cohort report "
                         "for a project that has a tree.")
    try:
        with zipfile.ZipFile(path) as zf:
            members = {name: zf.read(name) for name in zf.namelist()}
    except zipfile.BadZipFile as exc:
        raise ValueError(f"{path} is not a readable zip: {exc}") from exc
    trees = [name for name in members if name.endswith(".tree")]
    if len(trees) != 1:
        raise ValueError(f"{BATCH_ZIP} must hold exactly one .tree file; it holds {len(trees)}.")
    if include_popup:
        return path.read_bytes(), list(members)
    members.pop(POPUP_FILE, None)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buffer.getvalue(), list(members)


def _post(url: str, body: bytes, content_type: str) -> str:
    request = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": content_type, "Content-Length": str(len(body)), "User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read().decode("utf-8", errors="replace")


def upload(zip_bytes: bytes, api_key: str, itol_project: str, tree_name: str,
           description: str = "", http_post: Optional[HttpPost] = None) -> Outcome:
    """Send one zip to iTOL's batch upload and read the answer."""
    if not api_key.strip():
        return Outcome(False, message=f"No iTOL API key: {KEY_VARIABLE} is not set.")
    if not itol_project.strip():
        return Outcome(False, message="Name the iTOL project the tree should go into.")
    fields = {"APIkey": api_key.strip(), "projectName": itol_project.strip(),
              "treeName": tree_name.strip() or "cohort"}
    if description.strip():
        fields["treeDescription"] = description.strip()
    body, content_type = encode_multipart(fields, "zipFile", BATCH_ZIP, zip_bytes)
    try:
        text = (http_post or _post)(UPLOAD_URL, body, content_type)
    except urllib.error.HTTPError as exc:
        return Outcome(False, message=f"iTOL answered HTTP {exc.code}. Nothing was added to the account.")
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, ssl.SSLCertVerificationError):
            return Outcome(False, message="iTOL's certificate could not be verified "
                                          f"({exc.reason.verify_message}), so nothing was sent.")
        return Outcome(False, message=f"Could not reach iTOL: {exc.reason}")
    except (TimeoutError, OSError) as exc:
        return Outcome(False, message=f"Could not reach iTOL: {exc}")
    return parse_response(text)


def send_report(project: Path, api_key: str, itol_project: str, tree_name: str = "",
                description: str = "", include_popup: bool = True,
                http_post: Optional[HttpPost] = None) -> Outcome:
    """Upload a project's cohort tree and, when iTOL accepts it, leave a record
    of where it went beside the report. The record never holds the key."""
    report_dir = Path(project) / "cohort_report"
    try:
        zip_bytes, names = read_batch(report_dir, include_popup)
    except ValueError as exc:
        return Outcome(False, message=str(exc))
    if not description:
        try:
            checks = json.loads((report_dir / "cohort_checks.json").read_text(encoding="utf-8"))
            if checks.get("generated"):
                description = f"Cohort report built {checks['generated']}"
        except (OSError, ValueError):
            pass
    outcome = upload(zip_bytes, api_key, itol_project, tree_name or Path(project).name,
                     description, http_post)
    outcome.files = names
    if outcome.ok:
        record = {"tree_id": outcome.tree_id, "url": outcome.url, "itol_project": itol_project.strip(),
                  "tree_name": tree_name or Path(project).name,
                  "uploaded": time.strftime("%Y-%m-%d %H:%M"), "files": names,
                  "warnings": outcome.warnings}
        try:
            (report_dir / RECORD_NAME).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass        # a read-only project still gets its link in the reply
    return outcome


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Send a cohort report's tree and annotations to iTOL.")
    ap.add_argument("--project", required=True, help="project folder holding cohort_report/")
    ap.add_argument("--itol-project", required=True, help="name of an existing project in the iTOL account")
    ap.add_argument("--tree-name", default="", help="name for the tree in iTOL (default: the project folder's name)")
    ap.add_argument("--description", default="", help="description shown with the tree in iTOL")
    ap.add_argument("--no-popup", action="store_true",
                    help="leave out the per-isolate details file, which holds every metadata column")
    args = ap.parse_args(argv)
    outcome = send_report(Path(args.project), os.environ.get(KEY_VARIABLE, ""), args.itol_project,
                          args.tree_name, args.description, include_popup=not args.no_popup)
    print(json.dumps(asdict(outcome)))
    return 0 if outcome.ok else 1


if __name__ == "__main__":
    sys.exit(main())
