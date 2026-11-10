"""First-run setup: create tables, seed demo data and train the model if missing."""
from sqlalchemy import func, select

from app.config import settings
from app.db import Supplier, init_db, session_scope
from app.ml.train import MODEL_FILE


def bootstrap() -> None:
    init_db()
    with session_scope() as s:
        empty = s.scalar(select(func.count()).select_from(Supplier)) == 0
    if empty:
        from app.seed import seed

        seed(reset=False)
    if not (settings.model_dir / MODEL_FILE).exists():
        from app.ml.train import train

        train()


if __name__ == "__main__":
    bootstrap()
    print("Ready.")
