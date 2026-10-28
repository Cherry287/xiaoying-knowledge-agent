"""Generate actual outputs and machine-checkable acceptance results."""
import argparse
import copy
import io
import json
from pathlib import Path
import platform
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.kb_agent.__main__ import write_json
from src.kb_agent.engine import Agent, utc_now
from src.kb_agent.knowledge import KnowledgeBase, load_json
from src.kb_agent.providers import MockProvider, ResponsesProvider


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=["mock", "openai"], default="mock")
    parser.add_argument("--output-dir", help="Default: examples/<provider>; does not overwrite interactive records")
    args = parser.parse_args()
    folder = Path(args.output_dir) if args.output_dir else ROOT / "examples" / args.provider
    folder.mkdir(parents=True, exist_ok=True)
    cases = {"clear_answer": "answered", "conflict": "conflict", "outside": "insufficient_evidence",
             "paraphrase": "answered", "context": "answered", "failure_once": "answered",
             "failure_permanent": "incomplete", "missing_question": "needs_clarification"}
    results = []
    for name, expected in cases.items():
        request = load_json(ROOT / f"examples/inputs/{name}.json")
        provider = MockProvider() if args.provider == "mock" else ResponsesProvider()
        result = Agent(KnowledgeBase(failures=request.get("failures")), provider).run(request)
        write_json(folder / f"{name}.json", result)
        citations = result["outcome"]["citations"]
        ok = result["outcome"]["status"] == expected
        if name == "conflict":
            ok = ok and {c["doc_id"] for c in citations} >= {"D01", "D02"}
        if name in ("clear_answer", "paraphrase", "context", "failure_once"):
            ok = ok and any(c["doc_id"] == "P01" for c in citations)
        if name == "outside":
            ok = ok and not citations
        results.append({"case": name, "expected": expected, "actual": result["outcome"]["status"], "passed": ok, "trace": f"{name}.json"})
    # Changed-corpus and budget probes are offline harness tests, kept in mock folder only.
    if args.provider == "mock":
        docs = load_json(ROOT / "data/documents.json")
        changed = copy.deepcopy(docs)
        changed[1]["text"] = changed[0]["text"]
        probes = [
            ("step_limit", docs, load_json(ROOT / "examples/inputs/conflict.json"), 2, "incomplete"),
            ("conflict_resolved_by_document_change", changed, load_json(ROOT / "examples/inputs/conflict.json"), 12, "answered"),
        ]
        changed_fact = copy.deepcopy(docs)
        next(d for d in changed_fact if d["id"] == "P01")["text"] = "影创标准版不支持自动生成字幕，仅支持手动输入字幕。"
        probes.append(("fact_changed", changed_fact, load_json(ROOT / "examples/inputs/paraphrase.json"), 12, "answered"))
        for name, documents, request, limit, expected in probes:
            result = Agent(KnowledgeBase(documents), MockProvider(), limit).run(request)
            write_json(folder / f"{name}.documents.json", documents)
            write_json(folder / f"{name}.input.json", request)
            write_json(folder / f"{name}.json", result)
            results.append({"case": name, "expected": expected, "actual": result["outcome"]["status"],
                            "passed": result["outcome"]["status"] == expected, "trace": f"{name}.json"})
    report = {"generated_at": utc_now(), "python": platform.python_version(), "platform": platform.platform(),
              "provider": args.provider, "cases": results, "passed": sum(r["passed"] for r in results), "total": len(results),
              "limitation": "Mock validates workflow only; API runs require separate credentials and review."}
    write_json(folder / "evaluation.json", report)
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    tested = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    (folder / "tests.txt").write_text(stream.getvalue(), encoding="utf-8")
    write_json(folder / "tests.json", {"run": tested.testsRun, "failures": len(tested.failures), "errors": len(tested.errors),
                                       "successful": tested.wasSuccessful(), "generated_at": utc_now()})
    print(json.dumps({"acceptance_passed": report["passed"], "acceptance_total": len(results),
                      "tests": tested.testsRun, "test_success": tested.wasSuccessful(), "output_dir": str(folder)}, ensure_ascii=False))
    return 0 if all(r["passed"] for r in results) and tested.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
