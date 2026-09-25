from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.analysis import analyze_document, apply_analysis
from app.server import _sync_document_client_from_matter
from app.storage import mutate


def main() -> None:
    def mutator(store):
        for document in store["documents"]:
            pages = [page for page in store["pages"] if page["document_id"] == document["id"]]
            if not pages:
                continue
            keep_matter = bool(document.get("matter_locked"))
            analysis = analyze_document(store, document, pages, keep_matter=keep_matter)
            apply_analysis(document, analysis, keep_matter=keep_matter)
            _sync_document_client_from_matter(store, document)
            print(f"{document['id']} | {document['filename']} | {document.get('type')} | {document.get('title')}")

    mutate(mutator)


if __name__ == "__main__":
    main()
