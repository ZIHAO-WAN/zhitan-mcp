import asyncio
import subprocess
import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest

from boss_zp.browser import canonical_job_url, normalize_city
from boss_zp.server import safe_call
from boss_zp.storage import BossError, GreetingLedger, bridge_pairing_token
from scripts.install_antigravity import merge_mcp_config
from scripts.install_codex import merge_config


def test_job_identity_discards_tracking():
    assert canonical_job_url("https://www.zhipin.com/job_detail/abc_123.html?securityId=private")[0] == "abc_123"
    assert "?" not in canonical_job_url("https://www.zhipin.com/job_detail/abc_123.html?securityId=private")[1]


@pytest.mark.parametrize("value", ["https://evil.test/job_detail/a.html", "../cookies", "https://www.zhipin.com@evil.test/job_detail/a.html", "https://www.zhipin.com/job_detail/%2e%2e.html", "", "a/b"])
def test_job_identity_rejects_foreign_or_ambiguous_targets(value):
    with pytest.raises(BossError, match="job_id"):
        canonical_job_url(value)


def test_greeting_reservation_is_cross_process(tmp_path):
    code = "from pathlib import Path; import sys; from boss_zp.storage import GreetingLedger; print(GreetingLedger(Path(sys.argv[1])).reserve('jobA'))"
    processes = [subprocess.Popen([sys.executable, '-c', code, str(tmp_path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(4)]
    results = [process.communicate(timeout=20) for process in processes]
    assert all(process.returncode == 0 for process in processes), results
    assert [stdout.strip() for stdout, _ in results].count('True') == 1


def test_ledger_atomically_reserves_once_and_retains_unknown(tmp_path):
    ledger = GreetingLedger(tmp_path)
    with ThreadPoolExecutor(8) as pool:
        outcomes = list(pool.map(lambda _: ledger.reserve("jobA"), range(16)))
    assert outcomes.count(True) == 1
    assert GreetingLedger(tmp_path).get("jobA") == "unknown"
    ledger.mark_sent("jobA")
    assert GreetingLedger(tmp_path).get("jobA") == "sent"
    assert not ledger.reserve("jobA")


def test_bridge_pairing_token_persists_in_local_directory(tmp_path):
    first = bridge_pairing_token(tmp_path)
    assert len(first) >= 40
    assert bridge_pairing_token(tmp_path) == first
    assert first not in str(tmp_path)


def test_config_merge_is_idempotent_and_preserves_other_settings(tmp_path):
    original = '# Keep this comment\nmodel = "existing"\n[mcp_servers.other]\ncommand = "keep"\n'
    merged = merge_config(original, tmp_path / "中文目录")
    assert merged.startswith(original.rstrip())
    parsed = tomllib.loads(merged)
    assert parsed["mcp_servers"]["other"] == {"command": "keep"}
    assert parsed["mcp_servers"]["boss_zhipin"]["tools"]["send_greetings"]["approval_mode"] == "prompt"
    assert parsed["mcp_servers"]["boss_zhipin"]["args"] == ["-m", "boss_zp.server"]
    assert merge_config(merged, tmp_path / "中文目录") == merged


def test_unmanaged_configuration_is_not_overwritten(tmp_path):
    with pytest.raises(ValueError, match="unmanaged"):
        merge_config('[mcp_servers.boss_zhipin]\ncommand = "other"\n', tmp_path)


def test_exception_messages_cannot_leak_tokens(caplog):
    async def fail():
        raise RuntimeError("Cookie=private-secret; securityId=private-token")
    result = asyncio.run(safe_call(fail()))
    assert not result.ok
    assert "private" not in result.model_dump_json()
    assert "private" not in caplog.text


def test_ledger_closes_handles_and_supports_batch_lookup(tmp_path):
    ledger = GreetingLedger(tmp_path)
    assert ledger.reserve("job1")
    ledger.mark_sent("job1")
    assert ledger.reserve("job2")
    assert ledger.get_many(["job1", "job2", "job3"]) == {"job1": "sent", "job2": "unknown"}
    # On Windows, an unclosed sqlite3 handle blocks unlinking the database file.
    ledger.path.unlink()
    assert not ledger.path.exists()


def test_normalize_city_only_strips_trailing_suffix():
    assert normalize_city("北京市") == "北京"
    assert normalize_city("沙市市") == "沙市"
    assert normalize_city("市北区") == "市北区"


def test_config_merge_enable_send_flag(tmp_path):
    merged = merge_config("", tmp_path, enable_send=True)
    parsed = tomllib.loads(merged)
    assert parsed["mcp_servers"]["boss_zhipin"]["env"]["BOSS_ZP_ENABLE_SEND"] == "1"
    default_merged = merge_config(merged, tmp_path, enable_send=False)
    assert "BOSS_ZP_ENABLE_SEND" not in tomllib.loads(default_merged)["mcp_servers"]["boss_zhipin"]["env"]


def test_antigravity_mcp_config_merge_preserves_other_servers(tmp_path):
    import json
    original = json.dumps({"mcpServers": {"other": {"command": "npx", "args": ["-y", "demo"]}}})
    merged = merge_mcp_config(original, tmp_path, enable_send=False)
    parsed = json.loads(merged)
    assert parsed["mcpServers"]["other"] == {"command": "npx", "args": ["-y", "demo"]}
    entry = parsed["mcpServers"]["boss_zhipin"]
    assert entry["args"] == ["-m", "boss_zp.server"]
    assert entry["env"]["PYTHONPATH"] == str(tmp_path)
    assert entry["env"]["BOSS_ZP_ENABLE_SEND"] == "0"
    assert merge_mcp_config(merged, tmp_path, enable_send=False) == merged
    enabled = json.loads(merge_mcp_config(merged, tmp_path, enable_send=True))
    assert enabled["mcpServers"]["boss_zhipin"]["env"]["BOSS_ZP_ENABLE_SEND"] == "1"
