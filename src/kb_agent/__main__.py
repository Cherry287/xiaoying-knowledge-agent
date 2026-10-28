import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from .engine import Agent, validate_request
from .knowledge import KnowledgeBase, ROOT, load_json
from .providers import MockProvider, ProviderError, ResponsesProvider, StdioProvider


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic replacement avoids leaving a partial trace after interruption.
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="题目 3：企业知识问答 Agent（全部资料为合成数据）")
    parser.add_argument("--input", required=True, help="UTF-8 JSON request")
    parser.add_argument("--provider", choices=["mock", "openai", "stdio"], default="mock")
    parser.add_argument("--documents", default=str(ROOT / "data/documents.json"))
    parser.add_argument("--max-steps", type=int, default=12)
    parser.add_argument("--output", help="Save outcome and full public trace to this JSON file")
    parser.add_argument("--operator-label", default="external_operator", help="stdio provenance, self-reported")
    args = parser.parse_args()
    try:
        request = load_json(args.input)
        validate_request(request)
        kb = KnowledgeBase(load_json(args.documents), failures=request.get("failures"))
        provider = {"mock": MockProvider, "openai": ResponsesProvider,
                    "stdio": lambda: StdioProvider(args.operator_label)}[args.provider]()
        result = Agent(kb, provider, args.max_steps).run(request)
        if args.output:
            write_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result["outcome"]["status"] == "incomplete" else 0
    except (OSError, ValueError, ProviderError) as exc:
        # Input/configuration errors do not pretend to be a completed Agent run.
        error = {"status": "incomplete", "error": "CONFIG_OR_INPUT_ERROR", "detail": str(exc)}
        if args.output:
            try:
                write_json(args.output, error)
            except OSError:
                pass
        print(json.dumps(error, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
