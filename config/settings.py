import os
import json
from pathlib import Path
from dotenv import load_dotenv

# Base Directory
BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env
dotenv_path = BASE_DIR / ".env"
if dotenv_path.exists():
    load_dotenv(dotenv_path)
else:
    load_dotenv()  # Fallback to system env or root .env

CONFIG_PATH = BASE_DIR / "config.json"

def get_env_bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.lower() in ("true", "1", "yes", "on")

def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "pinterest_keywords": {
            "Bridal": "Bridal Mehndi Design",
            "Arabic": "Arabic Mehndi Design",
            "Full Hand": "Full Hand Mehndi Design",
            "Back Hand": "Back Hand Mehndi Design",
            "Leg": "Leg Mehndi Design",
            "Minimal": "Minimal Mehndi Design",
            "Royal": "Royal Mehndi Design",
            "Finger": "Finger Mehndi Design",
            "Mandala": "Mandala Mehndi Design"
        },
        "default_image_count": 100,
        "scheduler": {
            "enabled": False,
            "interval": "daily",
            "time": "02:00",
            "day_of_week": "monday",
            "day_of_month": 1
        }
    }

def save_config(config_data: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=2, ensure_ascii=False)

class Settings:
    @property
    def base_dir(self) -> Path:
        return BASE_DIR

    @property
    def gdrive_parent_folder_id(self) -> str:
        return os.getenv("GDRIVE_PARENT_FOLDER_ID", "")

    @property
    def google_credentials_path(self) -> Path:
        cred_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "credentials.json")
        path = Path(cred_path)
        if not path.is_absolute():
            path = BASE_DIR / path
        return path

    @property
    def db_path(self) -> Path:
        db_p = os.getenv("DATABASE_PATH", "mehndi_agent.db")
        path = Path(db_p)
        if not path.is_absolute():
            path = BASE_DIR / path
        return path

    @property
    def downloads_dir(self) -> Path:
        dl_dir = os.getenv("DOWNLOADS_DIR", "downloads")
        path = Path(dl_dir)
        if not path.is_absolute():
            path = BASE_DIR / path
        return path

    @property
    def pinterest_headless(self) -> bool:
        return get_env_bool("PINTEREST_HEADLESS", True)

    @property
    def pinterest_delay(self) -> float:
        try:
            return float(os.getenv("PINTEREST_DELAY_SECONDS", "3.0"))
        except ValueError:
            return 3.0

    @property
    def max_retries(self) -> int:
        try:
            return int(os.getenv("MAX_RETRIES", "3"))
        except ValueError:
            return 3

    def get_keywords(self) -> dict:
        config = load_config()
        return config.get("pinterest_keywords", {})

    def get_default_image_count(self) -> int:
        config = load_config()
        return config.get("default_image_count", 100)

    def get_scheduler_config(self) -> dict:
        config = load_config()
        return config.get("scheduler", {
            "enabled": False,
            "interval": "daily",
            "time": "02:00",
            "day_of_week": "monday",
            "day_of_month": 1
        })

    def update_scheduler_config(self, enabled: bool, interval: str, time_str: str, day_of_week: str = "monday", day_of_month: int = 1):
        config = load_config()
        config["scheduler"] = {
            "enabled": enabled,
            "interval": interval,
            "time": time_str,
            "day_of_week": day_of_week,
            "day_of_month": day_of_month
        }
        save_config(config)

    def update_keywords(self, keywords: dict):
        config = load_config()
        config["pinterest_keywords"] = keywords
        save_config(config)

    def update_default_image_count(self, count: int):
        config = load_config()
        config["default_image_count"] = count
        save_config(config)

settings = Settings()
