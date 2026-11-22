"""Merge this server's settings without rewriting unrelated Codex configuration."""

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import tomllib
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BEGIN = "# BEGIN boss-zhipin-mcp managed configuration"
END = "# END boss-zhipin-mcp managed configuration"


def config_block(root: Path, *, enable_send: bool = False) -> str:
    python = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    # JSON double-quoted strings are valid TOML basic strings for these paths.
    quote = lambda value: json.dumps(str(value), ensure_ascii=False)
    env_lines = ['PYTHONUTF8 = "1"']
    if enable_send:
        env_lines.append('BOSS_ZP_ENABLE_SEND = "1"')
    return "\n".join([
        BEGIN, "[mcp_servers.boss_zhipin]",
        "command = " + quote(python),
        'args = ["-m", "boss_zp.server"]',
        "cwd = " + quote(root),
        "startup_timeout_sec = 20", "tool_timeout_sec = 1600", "enabled = true",
        "", "[mcp_servers.boss_zhipin.env]", *env_lines,
        "", "[mcp_servers.boss_zhipin.tools.send_greetings]", 'approval_mode = "prompt"', END, "",
    ])


def merge_config(original: str, root: Path, *, enable_send: bool = False) -> str:
    parsed = tomllib.loads(original)
    if original.count(BEGIN) != original.count(END) or original.count(BEGIN) > 1:
        raise ValueError("Managed block markers are inconsistent; configuration was not changed.")
    block = config_block(root, enable_send=enable_send)
    if BEGIN in original:
        result = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", lambda _: block, original, flags=re.S)
    else:
        if "boss_zhipin" in parsed.get("mcp_servers", {}):
            raise ValueError("An unmanaged boss_zhipin entry already exists; configuration was not changed.")
        result = original.rstrip() + "\n\n" + block
    tomllib.loads(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "config.toml")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--enable-send", action="store_true", help="Write BOSS_ZP_ENABLE_SEND=1 in managed MCP env")
    args = parser.parse_args()
    config = args.config.resolve()
    original = config.read_text(encoding="utf-8-sig") if config.exists() else ""
    merged = merge_config(original, ROOT, enable_send=args.enable_send)
    if args.dry_run:
        print(config_block(ROOT, enable_send=args.enable_send))
        print("Validated merge; unrelated configuration preserved.")
        return
    interpreter = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not interpreter.is_file():
        raise SystemExit("Create the project .venv before installing the connection.")
    if merged == original:
        print("Codex connection already matches; no change.")
        return
    config.parent.mkdir(parents=True, exist_ok=True)
    if config.exists():
        backup = config.with_name(config.name + ".boss-zhipin-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".bak")
        shutil.copy2(config, backup)
    fd, temporary = tempfile.mkstemp(prefix=".boss-zhipin-", suffix=".toml", dir=config.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(merged)
        # Avoid replacing concurrent edits made after the initial read.
        current = config.read_text(encoding="utf-8-sig") if config.exists() else ""
        if current != original:
            raise RuntimeError("Codex config changed concurrently; connection was not installed.")
        os.replace(temporary, config)
    finally:
        if Path(temporary).exists():
            Path(temporary).unlink()
    print("Installed ZhiTan MCP in " + str(config))
    print("Restart the Codex client or open a new session to load the tools.")


if __name__ == "__main__":
    main()
