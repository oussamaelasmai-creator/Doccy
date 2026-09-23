from __future__ import annotations

import json
import threading
import uuid
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
UPLOAD_DIR = ROOT / "uploads"
STORE_PATH = DATA_DIR / "store.json"

_LOCK = threading.RLock()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    UPLOAD_DIR.mkdir(exist_ok=True)


def empty_store() -> dict[str, Any]:
    return {
        "users": [
            {
                "id": "user_demo",
                "name": "Utilisateur demo",
                "role": "responsable",
                "allowed_client_ids": ["client_demo_1", "client_demo_2"],
                "allowed_matter_ids": ["matter_demo_1", "matter_demo_2"],
            }
        ],
        "clients": [
            {
                "id": "client_demo_1",
                "name": "Cabinet Martin",
                "contact": "Claire Martin",
                "email": "contact@cabinet-martin.example",
                "created_at": now_iso(),
            },
            {
                "id": "client_demo_2",
                "name": "Entreprise Dumas",
                "contact": "Karim Dumas",
                "email": "juridique@dumas.example",
                "created_at": now_iso(),
            },
        ],
        "matters": [
            {
                "id": "matter_demo_1",
                "client_id": "client_demo_1",
                "name": "Responsabilite civile - morsure de chien",
                "responsible": "S. Bernard",
                "status": "active",
                "created_at": now_iso(),
            },
            {
                "id": "matter_demo_2",
                "client_id": "client_demo_2",
                "name": "Recouvrement fournisseur",
                "responsible": "L. Moreau",
                "status": "active",
                "created_at": now_iso(),
            },
        ],
        "documents": [
            {
                "id": "doc_demo_1",
                "client_id": "client_demo_1",
                "matter_id": "matter_demo_1",
                "filename": "note-analyse.txt",
                "stored_name": "demo_note_analyse.txt",
                "mime_type": "text/plain",
                "type": "Note",
                "responsible": "S. Bernard",
                "document_date": str(date.today()),
                "status": "indexed",
                "has_text_layer": True,
                "ocr_required": False,
                "quality": "good",
                "confidence": 0.96,
                "page_count": 1,
                "created_at": now_iso(),
                "updated_at": now_iso(),
            }
        ],
        "pages": [
            {
                "id": "page_demo_1",
                "document_id": "doc_demo_1",
                "page_number": 1,
                "text": (
                    "Le dossier concerne une attaque de chien ayant provoque une blessure. "
                    "Les pieces utiles portent sur la responsabilite du proprietaire de l'animal."
                ),
                "confidence": 0.96,
                "quality": "good",
                "created_at": now_iso(),
            }
        ],
        "tasks": [
            {
                "id": "task_demo_1",
                "matter_id": "matter_demo_1",
                "title": "Verifier les pieces medicales",
                "assignee": "S. Bernard",
                "due_date": str(date.today()),
                "status": "open",
                "created_at": now_iso(),
            }
        ],
        "alerts": [
            {
                "id": "alert_demo_1",
                "level": "info",
                "message": "Un document demo est deja indexe pour tester la recherche semantique.",
                "created_at": now_iso(),
                "seen": False,
            }
        ],
        "audit_logs": [],
    }


def load_store() -> dict[str, Any]:
    ensure_dirs()
    with _LOCK:
        if not STORE_PATH.exists():
            store = empty_store()
            demo_file = UPLOAD_DIR / "demo_note_analyse.txt"
            demo_file.write_text(store["pages"][0]["text"], encoding="utf-8")
            save_store(store)
            return store
        with STORE_PATH.open("r", encoding="utf-8") as handle:
            return json.load(handle)


def save_store(store: dict[str, Any]) -> None:
    ensure_dirs()
    with _LOCK:
        tmp_path = STORE_PATH.with_suffix(".json.tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(store, handle, ensure_ascii=False, indent=2)
        tmp_path.replace(STORE_PATH)


def mutate(mutator):
    with _LOCK:
        store = load_store()
        result = mutator(store)
        save_store(store)
        return result


def snapshot() -> dict[str, Any]:
    return deepcopy(load_store())


def audit(store: dict[str, Any], actor_id: str, action: str, entity: str, entity_id: str, details: dict[str, Any] | None = None) -> None:
    store["audit_logs"].append(
        {
            "id": new_id("audit"),
            "actor_id": actor_id,
            "action": action,
            "entity": entity,
            "entity_id": entity_id,
            "details": details or {},
            "created_at": now_iso(),
        }
    )
