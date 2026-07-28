"""Idempotent migration: import existing product-*.json into the SQLite products table.

Safe to run multiple times — uses INSERT OR IGNORE on product_id.
Does NOT delete original JSON files.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import Settings
from app.services.db import init_db, upsert_product


def migrate(storage_root: Path | None = None) -> int:
    settings = Settings()
    if storage_root:
        settings = Settings(storage_root=storage_root)

    init_db(settings.db_path)

    metadata_dir = settings.storage_root / "metadata"
    if not metadata_dir.exists():
        print("No metadata directory found, nothing to migrate.")
        return 0

    count = 0
    for json_path in sorted(metadata_dir.glob("product-*.json")):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"  SKIP {json_path.name}: {exc}")
            continue

        product_id = data.get("product_id", "")
        if not product_id:
            print(f"  SKIP {json_path.name}: no product_id")
            continue

        fact_card = data.get("fact_card", {})
        name = fact_card.get("商品名称", "")
        image_path = data.get("original_image_path", "")
        fact_card_path = f"metadata/{json_path.name}"
        created_at = data.get("created_at", "")

        upsert_product(
            settings.db_path,
            product_id=product_id,
            name=name,
            image_path=image_path,
            fact_card_path=fact_card_path,
            created_at=created_at,
        )
        count += 1

    print(f"Migration complete: {count} products imported.")
    return count


if __name__ == "__main__":
    migrate()
