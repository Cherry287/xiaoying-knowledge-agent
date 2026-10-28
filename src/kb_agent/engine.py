"""Bounded action/observation loop. Business choices belong to the provider."""
import hashlib
import json
from datetime import datetime, timezone

from .contracts import SCHEMAS, validate


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def validate_request(request):
    if not isinstance(request, dict) or set(request) - {"question", "history", "failures"}:
        raise ValueError("Input must contain only question, history and optional failures")
    if not isinstance(request.get("question", ""), str) or len(request.get("question", "")) > 8000:
        raise ValueError("question must be a string of <= 8000 characters")
    history = request.get("history", [])
    if not isinstance(history, list) or len(history) > 20:
        raise ValueError("history must be a list of <= 20 messages")
    for message in history:
        if (not isinstance(message, dict) or set(message) != {"role", "content"}
                or message["role"] not in ("user", "assistant")
                or not isinstance(message["content"], str) or len(message["content"]) > 8000):
            raise ValueError("Invalid history message")
    if not isinstance(request.get("failures", {}), dict):
        raise ValueError("failures must be an object")


def conflicting_groups(documents):
    groups = {}
    for doc in documents:
        groups.setdefault(doc["policy_key"], []).append(doc)
    # Conservative: differing whole paragraphs in a policy group need clarification.
    # Dates are not authority. This deliberately does not infer precedence.
    return {key: docs for key, docs in groups.items() if len({d["text"] for d in docs}) > 1}


class Agent:
    def __init__(self, kb, provider, max_steps=12):
        if type(max_steps) is not int or not 1 <= max_steps <= 50:
            raise ValueError("max_steps must be between 1 and 50")
        self.kb, self.provider, self.max_steps = kb, provider, max_steps
        self.discovered = {}
        self.read = {}
        self.events = []

    def finish(self, args):
        status, citations = args["status"], args["citations"]
        validated, seen = [], set()
        for citation in citations:
            doc = self.read.get(citation["doc_id"])
            if not doc or citation["quote"] != doc["text"]:
                raise ValueError("Citation must equal the complete text of an actually read document")
            if doc["id"] in seen:
                raise ValueError("Duplicate citation")
            seen.add(doc["id"])
            validated.append({"doc_id": doc["id"], "version_date": doc["version_date"],
                              "title": doc["title"], "quote": doc["text"], "policy_key": doc["policy_key"]})
        if status in ("answered", "conflict"):
            if not citations:
                raise ValueError("This status requires verified citations")
            keys = {c["policy_key"] for c in validated}
            pending = [d["id"] for d in self.discovered.values()
                       if d["policy_key"] in keys and d["id"] not in self.read]
            if pending:
                raise ValueError("Read same-topic candidates before finishing: " + ", ".join(pending))
            conflicts = conflicting_groups(d for d in self.read.values() if d["policy_key"] in keys)
            if status == "answered" and conflicts:
                raise ValueError("Unresolved conflicting evidence; cannot provide a single answer")
            if status == "answered" and args["gaps"]:
                raise ValueError("answered cannot contain unresolved gaps")
            if status == "conflict":
                if not conflicts or not args["gaps"]:
                    raise ValueError("conflict requires conflicting evidence and a clarification gap")
                if any(d["id"] not in seen for docs in conflicts.values() for d in docs):
                    raise ValueError("Cite all discovered conflicting documents")
        if status not in ("answered",) and not args["gaps"]:
            raise ValueError("Non-answer status requires an explanation in gaps")
        prefixes = {
            "answered": "根据已核验原文：", "conflict": "资料存在冲突，无法确定唯一答案：",
            "needs_clarification": "需要补充信息后继续。", "insufficient_evidence": "现有资料不足以回答。",
            "incomplete": "任务未完成。",
        }
        answer = prefixes[status]
        if validated:
            answer += "\n" + "\n".join(f"[{c['doc_id']} · {c['version_date']}] {c['quote']}" for c in validated)
        return {"status": status, "answer": answer, "citations": validated, "gaps": args["gaps"]}

    def run(self, request):
        validate_request(request)
        if self.events:
            raise ValueError("Agent instances are single-run; use a new instance")
        started = utc_now()
        outcome = None
        for step in range(1, self.max_steps + 1):
            try:
                action = self.provider.next(request, self.events)
            except Exception as exc:
                # Provider errors are deliberately sanitized: no auth/header/body dumps.
                from .providers import ProviderError
                message = str(exc) if isinstance(exc, ProviderError) else type(exc).__name__
                if self.events:
                    self.events[-1]["next_action"] = "model_error"
                self.events.append({"step": step, "action": "model_error", "arguments": {},
                                    "reason": "模型未返回可执行动作", "result": {"ok": False, "error": message},
                                    "summary": "模型调用失败", "next_action": "stop", "at": utc_now()})
                outcome = {"status": "incomplete", "answer": "任务未完成：模型调用失败。",
                           "citations": [], "gaps": [message]}
                break
            name = action.get("name", "") if isinstance(action, dict) else ""
            args = action.get("arguments", {}) if isinstance(action, dict) else {}
            if self.events:
                self.events[-1]["next_action"] = name
            result = None
            try:
                if name not in SCHEMAS:
                    raise ValueError("Unknown action")
                validate(args, SCHEMAS[name])
                if not args["reason"].strip() or len(args["reason"]) > 300:
                    raise ValueError("reason must be a brief public rationale of 1..300 characters")
                if name == "finish":
                    outcome = self.finish(args)
                    result = {"ok": True, "outcome": outcome}
                elif name == "read_document" and args["doc_id"] not in self.discovered:
                    raise ValueError("Search first; only discovered documents can be read")
                else:
                    result = self.kb.invoke(name, args)
                    if result["ok"] and name == "search_documents":
                        self.discovered.update({d["id"]: d for d in result["results"]})
                    if result["ok"] and name == "read_document":
                        self.read[result["document"]["id"]] = result["document"]
            except (ValueError, TypeError, KeyError) as exc:
                result = {"ok": False, "error": {"code": "VALIDATION_ERROR", "retryable": True, "message": str(exc)}}
            summary = (f"检索命中 {result['count']} 份资料" if "count" in result else
                       f"读取 {result['document']['id']} 原文成功" if "document" in result else
                       f"结束：{outcome['status']}" if outcome else "动作未成功；错误已反馈给模型")
            self.events.append({"step": step, "action": name, "arguments": args,
                                "reason": args.get("reason", "") if isinstance(args, dict) else "",
                                "result": result, "summary": summary, "at": utc_now(),
                                "model_metadata": action.get("metadata", {}) if isinstance(action, dict) else {},
                                "next_action": "stop" if outcome else None})
            if outcome:
                break
        if outcome is None:
            outcome = {"status": "incomplete", "answer": "任务未完成：已达到执行步数上限。",
                       "citations": [], "gaps": [f"MAX_STEPS：最多 {self.max_steps} 步，已保留工具结果供检查。"]}
            self.events[-1]["next_action"] = "stop_at_limit"
        fingerprint = hashlib.sha256(json.dumps(list(self.kb.documents.values()), ensure_ascii=False,
                                                sort_keys=True).encode()).hexdigest()
        return {"schema_version": 1, "provider": self.provider.label, "is_mock": self.provider.is_mock,
                "input": request, "started_at": started, "finished_at": utc_now(),
                "data_sha256": fingerprint, "max_steps": self.max_steps, "steps": len(self.events),
                "outcome": outcome, "trace": self.events}
