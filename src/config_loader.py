import os
import socket
import yaml
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"


def detect_env() -> str:
    env_name = os.getenv("ENV_NAME", "").strip().lower()
    if env_name in ("home", "office"):
        return env_name
    hostname = socket.gethostname().lower()
    host_map = {
        # TODO: 본인 컴퓨터의 실제 호스트명으로 등록
        "home-pc": "home",
        "office-pc": "office",
    }
    return host_map.get(hostname, "home")


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _expand_env_vars(obj):
    if isinstance(obj, dict):
        return {k: _expand_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env_vars(v) for v in obj]
    if isinstance(obj, str):
        return os.path.expandvars(obj)
    return obj


def load_config() -> dict:
    load_dotenv(PROJECT_ROOT / ".env")

    with open(CONFIG_DIR / "base.yaml", "r", encoding="utf-8") as f:
        base = yaml.safe_load(f) or {}

    env_name = detect_env()
    env_file = CONFIG_DIR / f"{env_name}.yaml"
    override = {}
    if env_file.exists():
        with open(env_file, "r", encoding="utf-8") as f:
            override = yaml.safe_load(f) or {}

    merged = _deep_merge(base, override)
    merged = _expand_env_vars(merged)
    merged["_meta"] = {"detected_env": env_name}
    # 앱키/시크릿은 yaml에 두지 않고 .env에서만 읽어 코드/설정파일과 분리합니다.
    merged["_secrets"] = {
        "kiwoom_app_key": os.getenv("KIWOOM_APP_KEY", ""),
        "kiwoom_app_secret": os.getenv("KIWOOM_APP_SECRET", ""),
    }

    for key in ("data_dir", "log_dir"):
        path = merged.get("paths", {}).get(key)
        if path:
            Path(path).mkdir(parents=True, exist_ok=True)

    return merged
