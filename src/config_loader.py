import os
import socket
import yaml
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"


def find_central_env() -> Path | None:
    """
    중앙 키 파일(집 E:\\dev\\.env, 회사 D:\\dev\\.env 등)을 찾습니다.
    - DEV_ENV_FILE 환경변수가 있으면 그 파일을 사용
    - 없으면 프로젝트 폴더의 상위 폴더를 올라가며 처음 만나는 .env 사용
    드라이브 문자를 코드에 박지 않으므로 집/회사 어디서든 그대로 동작합니다.
    """
    override = os.getenv("DEV_ENV_FILE", "").strip()
    if override and Path(override).is_file():
        return Path(override)
    for parent in PROJECT_ROOT.parents:
        candidate = parent / ".env"
        if candidate.is_file():
            return candidate
    return None


def load_env_files() -> Path | None:
    """
    환경변수 로드. 우선순위(높은 순):
      1) 프로젝트 .env  - 이 프로젝트 전용 값(DATA_DIR 등), 또는 이 프로젝트만 다른 키를 쓸 때
      2) 이미 설정된 OS 환경변수 (PowerShell 프로필의 load-env.ps1 등)
      3) 중앙 .env      - 모든 프로젝트가 공유하는 API 키
    프로젝트 .env가 없어도 중앙 .env만으로 동작합니다. 반환값: 찾은 중앙 .env 경로
    """
    project_env = PROJECT_ROOT / ".env"
    if project_env.is_file():
        load_dotenv(project_env, override=True)
    central = find_central_env()
    if central:
        load_dotenv(central, override=False)
    return central


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
    central_env = load_env_files()

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
    merged["_meta"] = {"detected_env": env_name, "central_env": str(central_env) if central_env else None}
    # 앱키/시크릿은 yaml에 두지 않고 .env(프로젝트 또는 중앙)에서만 읽어 코드/설정파일과 분리합니다.
    merged["_secrets"] = {
        "kiwoom_app_key": os.getenv("KIWOOM_APP_KEY", ""),
        "kiwoom_app_secret": os.getenv("KIWOOM_APP_SECRET", ""),
    }

    for key in ("data_dir", "log_dir"):
        path = merged.get("paths", {}).get(key)
        if path:
            Path(path).mkdir(parents=True, exist_ok=True)

    return merged
