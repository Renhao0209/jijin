import os


def _get_env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None:
        return default
    try:
        return int(v)
    except ValueError:
        return default


ALLOWED_ORIGINS = [
    s.strip()
    for s in os.getenv("BACKEND_ALLOWED_ORIGINS", "*").split(",")
    if s.strip()
]
CACHE_TTL_EST = _get_env_int("BACKEND_CACHE_TTL_EST", 3)
CACHE_TTL_NAV = _get_env_int("BACKEND_CACHE_TTL_NAV", 3600)

WATCH_CODES = [
    s.strip()
    for s in os.getenv("BACKEND_WATCH_CODES", "").split(",")
    if s.strip()
]

# Optional data source tokens/cookies
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN", "").strip()
XUEQIU_COOKIE = os.getenv("XUEQIU_COOKIE", "").strip()
JOINQUANT_TOKEN = os.getenv("JOINQUANT_TOKEN", "").strip()
RICEQUANT_TOKEN = os.getenv("RICEQUANT_TOKEN", "").strip()
AKSHARE_ENABLED = os.getenv("AKSHARE_ENABLED", "0").strip() in {"1", "true", "True"}
