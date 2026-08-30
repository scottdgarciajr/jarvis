import json

from jarvis.debug_log import DebugLog, LOG_SCHEMA_VERSION


def test_debug_log_writes_versioned_jsonl(tmp_path):
    path = tmp_path / "jarvis-debug.jsonl"
    DebugLog(path).write("server_mic_outcome", extracted_command="lights on", outcome="reply")

    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["event"] == "server_mic_outcome"
    assert record["log_schema_version"] == LOG_SCHEMA_VERSION
    assert record["jarvis_version"]
    assert "git_revision" in record
    assert record["extracted_command"] == "lights on"
    assert record["outcome"] == "reply"
