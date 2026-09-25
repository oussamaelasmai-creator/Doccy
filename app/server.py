from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import sys
from email.parser import BytesParser
from email.policy import default
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .analysis import analysis_status, analyze_document, apply_analysis
from .document_processing import ocr_status, process_document
from .search import search_documents
from .semantic import embed_text, embedding_status
from .storage import ROOT, UPLOAD_DIR, audit, mutate, new_id, now_iso, snapshot


PUBLIC_DIR = ROOT / "public"
CURRENT_USER_ID = "user_demo"


class AppHandler(SimpleHTTPRequestHandler):
    server_version = "DocumentPlatform/0.1"

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in ("", "/"):
            target = PUBLIC_DIR / "index.html"
            body_length = target.stat().st_size
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(body_length))
            self.end_headers()
            return
        self.send_error(HTTPStatus.METHOD_NOT_ALLOWED)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/state":
            self._json(public_state())
            return
        if parsed.path == "/api/search":
            params = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
            store = snapshot()
            user = get_current_user(store)
            self._json({"results": search_documents(store, user, params)})
            return
        if parsed.path.startswith("/api/documents/") and parsed.path.endswith("/download"):
            self._download_document(parsed.path)
            return
        if parsed.path.startswith("/api/documents/") and parsed.path.endswith("/text"):
            self._document_text(parsed.path)
            return
        self._static(parsed.path)

    def do_POST(self) -> None:
        try:
            parsed = urlparse(self.path)
            if parsed.path == "/api/clients":
                payload = self._read_json()
                result = mutate(lambda store: create_client(store, payload))
                self._json(result, HTTPStatus.CREATED)
                return
            if parsed.path == "/api/matters":
                payload = self._read_json()
                result = mutate(lambda store: create_matter(store, payload))
                self._json(result, HTTPStatus.CREATED)
                return
            if parsed.path == "/api/tasks":
                payload = self._read_json()
                result = mutate(lambda store: create_task(store, payload))
                self._json(result, HTTPStatus.CREATED)
                return
            if parsed.path == "/api/documents":
                self._upload_document()
                return
            if parsed.path.startswith("/api/documents/") and parsed.path.endswith("/reprocess"):
                document_id = parsed.path.split("/")[3]
                result = mutate(lambda store: reprocess_document(store, document_id))
                self._json(result)
                return
            self._json({"error": "Route inconnue."}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._json({"error": f"Erreur serveur: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _read_multipart(self) -> tuple[dict[str, str], dict[str, object]]:
        content_type = self.headers.get("Content-Type", "")
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        message = BytesParser(policy=default).parsebytes(
            b"Content-Type: " + content_type.encode("utf-8") + b"\r\nMIME-Version: 1.0\r\n\r\n" + raw
        )
        fields: dict[str, str] = {}
        files: dict[str, object] = {}
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            filename = part.get_filename()
            payload = part.get_payload(decode=True) or b""
            if filename:
                files[name] = {
                    "filename": filename,
                    "content_type": part.get_content_type(),
                    "content": payload,
                }
            elif name:
                fields[name] = payload.decode("utf-8", errors="ignore")
        return fields, files

    def _upload_document(self) -> None:
        fields, files = self._read_multipart()
        uploaded = files.get("file")
        if not isinstance(uploaded, dict):
            self._json({"error": "Aucun fichier recu."}, HTTPStatus.BAD_REQUEST)
            return

        def mutator(store):
            document = create_document_from_upload(store, fields, uploaded)
            return {"document": document, "state": summarize(store)}

        self._json(mutate(mutator), HTTPStatus.CREATED)

    def _download_document(self, path: str) -> None:
        document_id = path.split("/")[3]
        store = snapshot()
        doc = next((item for item in store["documents"] if item["id"] == document_id), None)
        if not doc or not can_access_document(store, doc):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        file_path = UPLOAD_DIR / doc["stored_name"]
        if not file_path.exists():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = doc.get("mime_type") or mimetypes.guess_type(doc["filename"])[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Disposition", f'inline; filename="{doc["filename"]}"')
        self.send_header("Content-Length", str(file_path.stat().st_size))
        self.end_headers()
        with file_path.open("rb") as handle:
            shutil.copyfileobj(handle, self.wfile)

    def _document_text(self, path: str) -> None:
        document_id = path.split("/")[3]
        store = snapshot()
        doc = next((item for item in store["documents"] if item["id"] == document_id), None)
        if not doc or not can_access_document(store, doc):
            self._json({"error": "Document introuvable."}, HTTPStatus.NOT_FOUND)
            return
        pages = [page for page in store["pages"] if page["document_id"] == document_id]
        self._json({"document": doc, "pages": pages})

    def _static(self, path: str) -> None:
        if path in ("", "/"):
            path = "/index.html"
        target = (PUBLIC_DIR / unquote(path.lstrip("/"))).resolve()
        if not str(target).startswith(str(PUBLIC_DIR.resolve())) or not target.exists() or target.is_dir():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        body = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def public_state() -> dict:
    store = snapshot()
    return {
        **summarize(store),
        "clients": store["clients"],
        "matters": store["matters"],
        "documents": sorted(store["documents"], key=lambda item: item["created_at"], reverse=True),
        "tasks": sorted(store["tasks"], key=lambda item: item.get("due_date") or ""),
        "alerts": sorted(store["alerts"], key=lambda item: item["created_at"], reverse=True),
        "audit_logs": sorted(store["audit_logs"], key=lambda item: item["created_at"], reverse=True)[:25],
        "current_user": get_current_user(store),
        "ai_status": {
            "analysis": analysis_status(),
            "embeddings": embedding_status(),
            "ocr": ocr_status(),
        },
    }


def summarize(store: dict) -> dict:
    documents = store["documents"]
    return {
        "stats": {
            "clients": len(store["clients"]),
            "matters": len(store["matters"]),
            "documents": len(documents),
            "indexed": sum(1 for doc in documents if doc["status"] == "indexed"),
            "analyzed": sum(1 for doc in documents if doc.get("analysis_method")),
            "pending": sum(1 for doc in documents if doc["status"] in {"imported", "ocr_pending", "ocr_processing"}),
            "errors": sum(1 for doc in documents if doc["status"] in {"error", "low_quality"}),
            "open_tasks": sum(1 for task in store["tasks"] if task["status"] == "open"),
            "semantic_pages": sum(1 for page in store["pages"] if page.get("embedding")),
        }
    }


def get_current_user(store: dict) -> dict:
    return next(user for user in store["users"] if user["id"] == CURRENT_USER_ID)


def can_access_document(store: dict, doc: dict) -> bool:
    user = get_current_user(store)
    allowed_matter_ids = set(user.get("allowed_matter_ids", []))
    allowed_client_ids = set(user.get("allowed_client_ids", []))
    matter_id = doc.get("matter_id")
    client_id = doc.get("client_id")
    return (
        matter_id in allowed_matter_ids
        or client_id in allowed_client_ids
        or (not matter_id and not client_id)
    )


def create_client(store: dict, payload: dict) -> dict:
    client = {
        "id": new_id("client"),
        "name": payload.get("name", "").strip() or "Nouveau client",
        "contact": payload.get("contact", "").strip(),
        "email": payload.get("email", "").strip(),
        "created_at": now_iso(),
    }
    store["clients"].append(client)
    get_current_user(store)["allowed_client_ids"].append(client["id"])
    audit(store, CURRENT_USER_ID, "create", "client", client["id"])
    return {"client": client}


def create_matter(store: dict, payload: dict) -> dict:
    matter = {
        "id": new_id("matter"),
        "client_id": payload.get("client_id"),
        "name": payload.get("name", "").strip() or "Nouvelle affaire",
        "responsible": payload.get("responsible", "").strip(),
        "status": payload.get("status", "active"),
        "created_at": now_iso(),
    }
    store["matters"].append(matter)
    get_current_user(store)["allowed_matter_ids"].append(matter["id"])
    audit(store, CURRENT_USER_ID, "create", "matter", matter["id"])
    return {"matter": matter}


def create_task(store: dict, payload: dict) -> dict:
    task = {
        "id": new_id("task"),
        "matter_id": payload.get("matter_id"),
        "title": payload.get("title", "").strip() or "Nouvelle tache",
        "assignee": payload.get("assignee", "").strip(),
        "due_date": payload.get("due_date", ""),
        "status": payload.get("status", "open"),
        "created_at": now_iso(),
    }
    store["tasks"].append(task)
    audit(store, CURRENT_USER_ID, "create", "task", task["id"])
    return {"task": task}


def create_document_from_upload(store: dict, fields: dict[str, str], uploaded: dict[str, object]) -> dict:
    original_name = safe_filename(str(uploaded["filename"]))
    document_id = new_id("doc")
    stored_name = f"{document_id}_{original_name}"
    path = UPLOAD_DIR / stored_name
    path.write_bytes(uploaded["content"])

    result = process_document(path, str(uploaded.get("content_type") or ""))
    user_type = fields.get("type") or "Auto"
    keep_matter = bool(fields.get("matter_id"))
    document = {
        "id": document_id,
        "client_id": fields.get("client_id"),
        "matter_id": fields.get("matter_id"),
        "filename": original_name,
        "stored_name": stored_name,
        "mime_type": uploaded.get("content_type") or mimetypes.guess_type(original_name)[0] or "application/octet-stream",
        "title": original_name,
        "type": "" if user_type == "Auto" else user_type,
        "doc_type": "" if user_type == "Auto" else user_type,
        "responsible": fields.get("responsible") or "",
        "document_date": fields.get("document_date") or "",
        "persons": [],
        "summary": "",
        "tags": [],
        "analysis_method": "",
        "analysis_confidence": 0.0,
        "status": result.status,
        "has_text_layer": result.has_text_layer,
        "ocr_required": result.ocr_required,
        "quality": result.quality,
        "confidence": result.confidence,
        "page_count": result.page_count,
        "message": result.message,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    store["documents"].append(document)
    store["pages"] = [page for page in store["pages"] if page["document_id"] != document_id]
    page_rows = []
    for page in result.pages:
        page_rows.append(page_record(document_id, page))
    store["pages"].extend(page_rows)
    if result.pages:
        analysis = analyze_document(store, document, page_rows, keep_matter=keep_matter)
        apply_analysis(document, analysis, keep_matter=keep_matter)
        _sync_document_client_from_matter(store, document)
    if result.status in {"ocr_pending", "low_quality", "error"}:
        store["alerts"].append(
            {
                "id": new_id("alert"),
                "level": "warning" if result.status != "error" else "error",
                "message": f"{original_name}: {result.message or 'controle qualite requis.'}",
                "created_at": now_iso(),
                "seen": False,
            }
        )
    audit(store, CURRENT_USER_ID, "upload", "document", document_id, {"status": result.status})
    return document


def reprocess_document(store: dict, document_id: str) -> dict:
    doc = next((item for item in store["documents"] if item["id"] == document_id), None)
    if not doc:
        return {"error": "Document introuvable."}
    path = UPLOAD_DIR / doc["stored_name"]
    result = process_document(path, doc.get("mime_type", ""))
    keep_matter = bool(doc.get("matter_id"))
    doc.update(
        {
            "status": result.status,
            "has_text_layer": result.has_text_layer,
            "ocr_required": result.ocr_required,
            "quality": result.quality,
            "confidence": result.confidence,
            "page_count": result.page_count,
            "message": result.message,
            "updated_at": now_iso(),
        }
    )
    store["pages"] = [page for page in store["pages"] if page["document_id"] != document_id]
    page_rows = []
    for page in result.pages:
        page_rows.append(page_record(document_id, page))
    store["pages"].extend(page_rows)
    if result.pages:
        analysis = analyze_document(store, doc, page_rows, keep_matter=keep_matter)
        apply_analysis(doc, analysis, keep_matter=keep_matter)
        _sync_document_client_from_matter(store, doc)
    audit(store, CURRENT_USER_ID, "reprocess", "document", document_id, {"status": result.status})
    return {"document": doc}


def _sync_document_client_from_matter(store: dict, document: dict) -> None:
    if document.get("client_id") or not document.get("matter_id"):
        return
    matter = next((item for item in store["matters"] if item["id"] == document["matter_id"]), None)
    if matter:
        document["client_id"] = matter.get("client_id")


def safe_filename(value: str) -> str:
    value = Path(value).name.strip() or "document"
    value = re.sub(r"[^a-zA-Z0-9._ -]+", "_", value)
    return value[:160]


def page_record(document_id: str, page) -> dict:
    return {
        "id": new_id("page"),
        "document_id": document_id,
        "page_number": page.page_number,
        "text": page.text,
        "raw_text": page.raw_text or page.text,
        "confidence": page.confidence,
        "quality": page.quality,
        "embedding": embed_text(page.text),
        "created_at": now_iso(),
    }


def main() -> None:
    host = "127.0.0.1"
    port = int(os.environ.get("PORT", sys.argv[1] if len(sys.argv) > 1 else "8000"))
    try:
        server = ThreadingHTTPServer((host, port), AppHandler)
    except OSError as exc:
        if exc.errno == 48:
            print(
                f"Le port {port} est deja utilise. Ouvre http://{host}:{port} si l'application tourne deja, "
                f"ou lance-la sur un autre port avec: python3 -m app.server {port + 1}",
                file=sys.stderr,
            )
            raise SystemExit(1) from exc
        raise
    print(f"Plateforme documentaire disponible sur http://{host}:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
