"""Replay recorded decisions through real local tools; verify artifact integrity.

Replays are deterministic verification, never new model-decision evidence.
"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.kb_agent.__main__ import write_json
from src.kb_agent.engine import Agent, utc_now
from src.kb_agent.knowledge import KnowledgeBase, load_json


class ReplayProvider:
    label, is_mock = "recorded_action_replay", True

    def __init__(self, trace):
        self.actions = iter(trace)

    def next(self, request, events):
        event = next(self.actions)
        return {"name": event["action"], "arguments": event["arguments"]}


def verify(path):
    recorded = load_json(path)
    if "trace" not in recorded:
        return None
    data_path = path.with_name(path.stem + ".documents.json")
    docs = load_json(data_path if data_path.exists() else ROOT / "data/documents.json")
    request = recorded["input"]
    replayed = Agent(KnowledgeBase(docs, failures=request.get("failures")),
                     ReplayProvider(recorded["trace"]), recorded["max_steps"]).run(request)
    checks = {
        "data_fingerprint": replayed["data_sha256"] == recorded["data_sha256"],
        "outcome": replayed["outcome"] == recorded["outcome"],
        "steps": replayed["steps"] == recorded["steps"],
        "tool_results": [e["result"] for e in replayed["trace"]] == [e["result"] for e in recorded["trace"]],
        "next_actions": [e["next_action"] for e in replayed["trace"]] == [e["next_action"] for e in recorded["trace"]],
    }
    return {"file": path.relative_to(ROOT).as_posix(), "passed": all(checks.values()), "checks": checks}


def main():
    checked = []
    for path in sorted((ROOT / "examples").glob("*/*.json")):
        result = verify(path)
        if result is not None:
            checked.append(result)
    report = {"generated_at": utc_now(), "total": len(checked), "passed": sum(c["passed"] for c in checked),
              "limitation": "Replay verifies artifacts against tools, not model provenance or generalization.", "results": checked}
    write_json(ROOT / "examples/trace-verification.json", report)
    print(json.dumps({"verified": report["passed"], "total": report["total"]}))
    return 0 if checked and all(c["passed"] for c in checked) else 1


if __name__ == "__main__":
    raise SystemExit(main())
