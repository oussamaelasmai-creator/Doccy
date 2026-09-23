from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.server import reprocess_document
from app.storage import mutate


def main() -> None:
    def run(store):
        rows = []
        for document in list(store["documents"]):
            result = reprocess_document(store, document["id"])
            updated = result.get("document", {})
            rows.append(
                {
                    "id": updated.get("id"),
                    "filename": updated.get("filename"),
                    "status": updated.get("status"),
                    "quality": updated.get("quality"),
                    "confidence": updated.get("confidence"),
                    "message": updated.get("message", ""),
                }
            )
        return rows

    for row in mutate(run):
        print(
            f"{row['id']} | {row['filename']} | {row['status']} | "
            f"{row['quality']} | {row['confidence']} | {row['message']}"
        )


if __name__ == "__main__":
    main()
