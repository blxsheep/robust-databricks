"""
_config.py
Centralised pipeline config loader.

Reads .config_file (committed pipeline constants) then overlays .env
(gitignored environment-specific values) from the repo root. Later keys
override earlier ones, so .env can override any .config_file value.

Usage:
    from _config import cfg
    spark.table(cfg["BRONZE_TABLE"])
    int(cfg["NUM_ROWS"])
    float(cfg["DBU_RATE_USD"])
"""

from pathlib import Path

try:
    _REPO_ROOT = Path(__file__).parent.parent.parent
except NameError:
    _REPO_ROOT = Path("/Workspace/Users/c.voranipit@gmail.com/robust-databricks")


def _parse(path: Path) -> dict[str, str]:
    """Parse a key=value file, skipping comments and blank lines."""
    if not path.exists():
        return {}
    result = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
    return result


cfg: dict[str, str] = {
    **_parse(_REPO_ROOT / ".config_file"),
    **_parse(_REPO_ROOT / ".env"),
}
