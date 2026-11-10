"""Central settings. Every value can be overridden with a PROCUREAI_* env var or .env file."""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PROCUREAI_", env_file=ROOT / ".env", extra="ignore")

    # SQLite by default so the project runs with zero setup. For PostgreSQL:
    #   PROCUREAI_DATABASE_URL=postgresql+psycopg://procureai:procureai@localhost:5432/procureai
    database_url: str = f"sqlite:///{(ROOT / 'procureai.db').as_posix()}"
    model_dir: Path = ROOT / "models"

    # Buyer profile used by the landed-cost model
    buyer_country: str = "United States"
    base_currency: str = "USD"
    # Static FX table (units of base currency per 1 unit of foreign currency).
    fx_rates: dict[str, float] = {"USD": 1.0, "EUR": 1.08, "GBP": 1.27, "INR": 0.012, "CNY": 0.14, "TRY": 0.029}

    # Cost-model assumptions (all surfaced in the API response so they can be challenged)
    cost_of_capital: float = 0.10          # annual, for payment-terms financing cost
    import_duty_rate: float = 0.04         # applied when the buyer bears import duty
    insurance_rate: float = 0.004          # of goods value, when buyer insures main carriage
    origin_charges_rate: float = 0.015     # EXW: export clearance + origin handling
    freight_base_rate: float = 0.02        # of goods value
    freight_rate_per_1000km: float = 0.008 # of goods value per 1000 km
    moq_excess_cost_factor: float = 0.5    # share of forced excess stock counted as cost
    delay_cost_rate_per_day: float = 0.005 # of goods value per day late

    # Optional LLM layer (extraction fallback + narrative). Off unless credentials exist.
    llm_model: str = "claude-opus-5-5"
    llm_enabled: bool = True


settings = Settings()
