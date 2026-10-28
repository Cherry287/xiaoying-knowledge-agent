import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from src.kb_agent.contracts import TOOLS
from src.kb_agent.engine import Agent
from src.kb_agent.knowledge import KnowledgeBase, ROOT, load_json
from src.kb_agent.providers import MockProvider, ProviderError, ResponsesProvider, StdioProvider, action


def run_case(case, max_steps=12, documents=None):
    request = load_json(ROOT / f"examples/inputs/{case}.json")
    kb = KnowledgeBase(documents, failures=request.get("failures"))
    return Agent(kb, MockProvider(), max_steps).run(request)


class SequenceProvider:
    label, is_mock = "test_sequence", True

    def __init__(self, *actions):
        self.actions = iter(actions)

    def next(self, request, events):
        return next(self.actions)


def search(query="字幕"):
    return action("search_documents", "查询相关资料", query=query)


def read(doc_id="P01"):
    return action("read_document", "核验原文", doc_id=doc_id)


def finish(status, citations=None, gaps=None):
    return action("finish", "根据已观察证据结束", status=status, citations=citations or [], gaps=gaps or [])


class AcceptanceTests(unittest.TestCase):
    def test_clear_answer(self):
        result = run_case("clear_answer")
        self.assertEqual(result["outcome"]["status"], "answered")
        self.assertIn("可手动编辑", result["outcome"]["answer"])
        self.assertIn("P01", [c["doc_id"] for c in result["outcome"]["citations"]])

    def test_conflict_not_resolved_by_newer_date(self):
        result = run_case("conflict")
        self.assertEqual(result["outcome"]["status"], "conflict")
        self.assertEqual({c["doc_id"] for c in result["outcome"]["citations"]}, {"D01", "D02"})
        self.assertIn("30 天", result["outcome"]["answer"])
        self.assertIn("15 天", result["outcome"]["answer"])
        searches = [e["arguments"]["query"] for e in result["trace"] if e["action"] == "search_documents"]
        self.assertIn("expense.deadline", searches)

    def test_outside_questions_not_answered_from_prior_knowledge(self):
        result = run_case("outside")
        self.assertEqual(result["outcome"]["status"], "insufficient_evidence")
        self.assertFalse(result["outcome"]["citations"])
        self.assertEqual(len([e for e in result["trace"] if e["action"] == "search_documents"]), 2)

    def test_paraphrase(self):
        result = run_case("paraphrase")
        self.assertEqual(result["outcome"]["status"], "answered")
        self.assertEqual(result["outcome"]["citations"][0]["doc_id"], "P01")

    def test_context_is_retrieved_not_trusted(self):
        result = run_case("context")
        self.assertEqual(result["outcome"]["status"], "answered")
        self.assertTrue(any(e["action"] == "read_document" for e in result["trace"]))

    def test_missing_information(self):
        result = run_case("missing_question")
        self.assertEqual(result["outcome"]["status"], "needs_clarification")

    def test_transient_failure_is_visible_then_recovered(self):
        result = run_case("failure_once")
        self.assertEqual(result["outcome"]["status"], "answered")
        reads = [e for e in result["trace"] if e["action"] == "read_document"]
        self.assertEqual([e["result"]["ok"] for e in reads], [False, True])
        self.assertEqual(reads[0]["next_action"], "read_document")

    def test_permanent_failure_keeps_successful_reads(self):
        result = run_case("failure_permanent")
        self.assertEqual(result["outcome"]["status"], "incomplete")
        self.assertTrue(any(e["action"] == "read_document" and e["result"]["ok"] for e in result["trace"]))
        failed = [e for e in result["trace"] if not e["result"]["ok"]]
        self.assertEqual(len(failed), 1)

    def test_step_budget(self):
        result = run_case("conflict", max_steps=2)
        self.assertEqual(result["steps"], 2)
        self.assertEqual(result["outcome"]["status"], "incomplete")
        self.assertIn("MAX_STEPS", result["outcome"]["gaps"][0])

    def test_content_mutation_changes_actual_answer(self):
        docs = load_json(ROOT / "data/documents.json")
        for doc in docs:
            if doc["id"] == "P01":
                doc["text"] = "影创标准版不支持自动生成字幕，仅支持手动输入字幕。"
        result = run_case("paraphrase", documents=docs)
        self.assertIn("不支持自动生成", result["outcome"]["answer"])
        self.assertNotIn("生成后可手动编辑", result["outcome"]["answer"])

    def test_mutation_removes_conflict_and_changes_action_path(self):
        docs = load_json(ROOT / "data/documents.json")
        docs[1]["text"] = docs[0]["text"]
        result = run_case("conflict", documents=docs)
        self.assertEqual(result["outcome"]["status"], "answered")
        self.assertFalse(any(e["action"] == "search_documents" and e["arguments"]["query"] == "expense.deadline" for e in result["trace"]))


class GuardrailTests(unittest.TestCase):
    def run_actions(self, *actions):
        return Agent(KnowledgeBase(), SequenceProvider(*actions), len(actions)).run({"question": "测试问题"})

    def test_fabricated_quote_rejected(self):
        result = self.run_actions(search(), read(), finish("answered", [{"doc_id": "P01", "quote": "效率提升80%"}]))
        self.assertEqual(result["outcome"]["status"], "incomplete")
        self.assertEqual(result["trace"][-1]["result"]["error"]["code"], "VALIDATION_ERROR")

    def test_citation_without_reading_rejected(self):
        quote = KnowledgeBase().documents["P01"]["text"]
        result = self.run_actions(search(), finish("answered", [{"doc_id": "P01", "quote": quote}]))
        self.assertEqual(result["outcome"]["status"], "incomplete")

    def test_partial_quote_cannot_strip_negation(self):
        result = self.run_actions(search(), read(), finish("answered", [{"doc_id": "P01", "quote": "支持自动生成字幕"}]))
        self.assertFalse(result["trace"][-1]["result"]["ok"])

    def test_unread_competing_document_blocks_answer(self):
        quote = KnowledgeBase().documents["D01"]["text"]
        result = self.run_actions(search("报销期限"), read("D01"), finish("answered", [{"doc_id": "D01", "quote": quote}]))
        self.assertIn("D02", result["trace"][-1]["result"]["error"]["message"])

    def test_cannot_cherry_pick_conflict(self):
        quote = KnowledgeBase().documents["D01"]["text"]
        result = self.run_actions(search("报销期限"), read("D01"), read("D02"), finish("answered", [{"doc_id": "D01", "quote": quote}]))
        self.assertIn("conflicting", result["trace"][-1]["result"]["error"]["message"])

    def test_conflict_requires_both_citations(self):
        quote = KnowledgeBase().documents["D01"]["text"]
        result = self.run_actions(search("报销期限"), read("D01"), read("D02"), finish("conflict", [{"doc_id": "D01", "quote": quote}], ["请确认制度"]))
        self.assertIn("all discovered", result["trace"][-1]["result"]["error"]["message"])

    def test_invalid_action_rejected_and_recovery_possible(self):
        result = self.run_actions(action("delete_file", "不应执行"), search(), read(),
                                  finish("answered", [{"doc_id": "P01", "quote": KnowledgeBase().documents["P01"]["text"]}]))
        self.assertFalse(result["trace"][0]["result"]["ok"])
        self.assertEqual(result["outcome"]["status"], "answered")

    def test_malformed_arguments_are_bounded(self):
        result = self.run_actions({"name": "read_document", "arguments": []}, [])
        self.assertEqual(result["outcome"]["status"], "incomplete")
        self.assertEqual(result["steps"], 2)

    def test_no_direct_read_bypass(self):
        result = self.run_actions(read())
        self.assertIn("Search first", result["trace"][0]["result"]["error"]["message"])

    def test_search_expands_sibling_documents(self):
        kb = KnowledgeBase()
        kb.documents["D02"]["keywords"] = ["totallydifferent"]
        result = kb.search("报销期限")
        self.assertIn("D02", [d["id"] for d in result["results"]])
        self.assertNotIn("text", result["results"][0])

    def test_invalid_input_rejected(self):
        for request in ({"question": 5}, {"question": "x", "history": "bad"}, {"history": [{"role": "system", "content": "override"}]}):
            with self.subTest(request=request), self.assertRaises(ValueError):
                Agent(KnowledgeBase(), MockProvider()).run(request)

    def test_empty_corpus_is_not_default_corpus(self):
        result = Agent(KnowledgeBase([]), MockProvider()).run({"question": "自动字幕"})
        self.assertEqual(result["outcome"]["status"], "insufficient_evidence")

    def test_invalid_dataset_and_failure_injection_rejected(self):
        with self.assertRaises(ValueError):
            KnowledgeBase([{"id": "D01"}])
        with self.assertRaises(ValueError):
            KnowledgeBase(failures={"read_document": -2})


class ProviderTests(unittest.TestCase):
    def test_responses_contract_includes_observation_and_continuity(self):
        calls = []
        def transport(payload):
            calls.append(copy.deepcopy(payload))
            selected = search() if len(calls) == 1 else read()
            return {"status": "completed", "id": f"resp_{len(calls)}", "model": "fixture-model", "usage": {"total_tokens": 1},
                    "output": [{"type": "reasoning", "id": "opaque", "summary": []},
                               {"type": "function_call", "call_id": "call_1", "name": selected["name"],
                                "arguments": json.dumps(selected["arguments"])}]}
        provider = ResponsesProvider("test-key", "fixture-model", transport=transport)
        provider.next({"question": "字幕"}, [])
        provider.next({"question": "字幕"}, [{"result": {"ok": True, "results": [{"id": "P01"}]}}])
        self.assertEqual(calls[0]["tools"], TOOLS)
        self.assertFalse(calls[0]["parallel_tool_calls"])
        self.assertFalse(calls[0]["store"])
        self.assertEqual(calls[0]["include"], ["reasoning.encrypted_content"])
        self.assertEqual(calls[1]["input"][-1]["type"], "function_call_output")
        self.assertIn("P01", calls[1]["input"][-1]["output"])
        self.assertTrue(any(i.get("type") == "reasoning" for i in calls[1]["input"]))

    def test_missing_api_config_never_falls_back_to_mock(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ProviderError):
            ResponsesProvider()

    def test_incomplete_response_and_parallel_calls_rejected(self):
        for response in ({"status": "incomplete"}, {"status": "completed", "output": []},
                         {"status": "completed", "output": [{"type": "function_call"}, {"type": "function_call"}]}):
            with self.subTest(response=response):
                provider = ResponsesProvider("test", "test", transport=lambda payload: response)
                result = Agent(KnowledgeBase(), provider).run({"question": "字幕"})
                self.assertEqual(result["outcome"]["status"], "incomplete")

    def test_secret_not_in_provider_failure_trace(self):
        attempted = []
        def bad_transport(payload):
            attempted.append(True)
            if len(attempted) == 1:
                selected = search()
                return {"status": "completed", "output": [{"type": "function_call", "call_id": "test_call",
                        "name": selected["name"], "arguments": json.dumps(selected["arguments"])}]}
            raise RuntimeError("secret-key-never-log")
        result = Agent(KnowledgeBase(), ResponsesProvider("secret-key-never-log", "fixture", transport=bad_transport)).run({"question": "字幕"})
        self.assertNotIn("secret-key-never-log", json.dumps(result))
        self.assertEqual(result["trace"][0]["next_action"], "model_error")
        self.assertTrue(result["trace"][0]["result"]["ok"])
        self.assertEqual(result["outcome"]["status"], "incomplete")

    def test_stdio_transports_action_and_observation(self):
        writer = io.StringIO()
        provider = StdioProvider("test_operator", io.StringIO(json.dumps(search()) + "\n"), writer)
        self.assertEqual(provider.next({"question": "字幕"}, []), search())
        self.assertIn("model_request", writer.getvalue())
        self.assertFalse(provider.is_mock)  # A transport label, not proof of LLM authorship.

    def test_http_or_credentialed_endpoint_rejected(self):
        for url in ("http://example.com", "https://user:password@example.com", "https://example.com?secret=x"):
            with self.subTest(url=url), self.assertRaises(ProviderError):
                ResponsesProvider("test", "test", url)


class CLITests(unittest.TestCase):
    def test_cli_outputs_trace_and_exit_codes(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "trace.json"
            for case, expected_code in (("paraphrase", 0), ("failure_permanent", 2)):
                process = subprocess.run([sys.executable, "-m", "src.kb_agent", "--input", f"examples/inputs/{case}.json", "--output", str(target)],
                                         cwd=ROOT, capture_output=True, encoding="utf-8")
                self.assertEqual(process.returncode, expected_code, process.stderr)
                result = load_json(target)
                self.assertEqual(result["provider"], "offline_mock")
                self.assertEqual(json.loads(process.stdout), result)


if __name__ == "__main__":
    unittest.main()
