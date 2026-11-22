"""Merge boss_zhipin into Antigravity's mcp_config.json without clobbering other servers."""

import argparse
import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER_KEY = "boss_zhipin"


def server_entry(root: Path, *, enable_send: bool = False) -> dict:
    python = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return {
        "command": str(python),
        "args": ["-m", "boss_zp.server"],
        "env": {
            "PYTHONUTF8": "1",
            "PYTHONPATH": str(root),
            "BOSS_ZP_ENABLE_SEND": "1" if enable_send else "0",
        },
    }


def merge_mcp_config(original: str, root: Path, *, enable_send: bool = False) -> str:
    stripped = original.strip()
    data = json.loads(stripped) if stripped else {}
    if not isinstance(data, dict):
        raise ValueError("Existing mcp_config.json root must be a JSON object.")
    servers = data.get("mcpServers")
    if servers is None:
        servers = {}
        data["mcpServers"] = servers
    elif not isinstance(servers, dict):
        raise ValueError("Existing mcpServers field must be a JSON object.")
    servers[SERVER_KEY] = server_entry(root, enable_send=enable_send)
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def default_config_path() -> Path:
    return Path(os.environ.get("ANTIGRAVITY_CONFIG_DIR", str(Path.home() / ".gemini" / "config"))) / "mcp_config.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--enable-send", action="store_true", help="Set BOSS_ZP_ENABLE_SEND=1 in env")
    args = parser.parse_args()
    config = args.config.resolve()
    original = config.read_text(encoding="utf-8-sig") if config.exists() else ""
    merged = merge_mcp_config(original, ROOT, enable_send=args.enable_send)
    if args.dry_run:
        print(merged)
        print("Validated Antigravity mcp_config.json merge; other servers preserved.")
        return
    interpreter = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not interpreter.is_file():
        raise SystemExit("Create the project .venv before installing the Antigravity MCP.")
    if merged == original:
        print("Antigravity MCP configuration already matches; no change.")
        return
    config.parent.mkdir(parents=True, exist_ok=True)
    if config.exists() and config.stat().st_size > 0:
        backup = config.with_name(config.name + ".boss-zhipin-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".bak")
        shutil.copy2(config, backup)
    fd, temporary = tempfile.mkstemp(prefix=".boss-zhipin-", suffix=".json", dir=config.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(merged)
        current = config.read_text(encoding="utf-8-sig") if config.exists() else ""
        if current != original:
            raise RuntimeError("Antigravity mcp_config.json changed concurrently; installation aborted.")
        os.replace(temporary, config)
    finally:
        if Path(temporary).exists():
            Path(temporary).unlink()
    print("Installed ZhiTan MCP in " + str(config))
    print("Refresh MCP Servers in Antigravity (Additional Options > MCP Servers > Refresh) or open a new session.")


if __name__ == "__main__":
    main()
