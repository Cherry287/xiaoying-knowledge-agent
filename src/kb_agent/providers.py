"""Real Responses API, transparent offline policy, and interactive model bridge."""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

from .contracts import SYSTEM_PROMPT, TOOLS
from .engine import conflicting_groups


class ProviderError(Exception):
    pass


def action(name, reason, **kwargs):
    return {"name": name, "arguments": {**kwargs, "reason": reason}}


class MockProvider:
    """Deterministic policy over actual observations, NOT an LLM or ReAct evidence."""
    label = "offline_mock"
    is_mock = True

    def next(self, request, events):
        question = request.get("question", "").strip()
        if not question:
            return action("finish", "缺少问题，需要用户补充", status="needs_clarification", citations=[], gaps=["请提供要查询的问题。"])
        if not events:
            history = request.get("history", [])
            if any(p in question for p in ("它", "这个", "那", "还", "多久", "多少")) and history:
                last_user = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
                question = last_user + " " + question
            return action("search_documents", "根据问题及必要的上文检索资料", query=question)
        last = events[-1]
        if not last["result"]["ok"]:
            error = last["result"]["error"]
            failures = sum(not e["result"]["ok"] for e in events)
            if isinstance(error, dict) and error.get("retryable") and failures < 2:
                return {"name": last["action"], "arguments": {**last["arguments"], "reason": "观察到临时错误，选择重试一次"}}
            return action("finish", "工具持续失败，保留已取得的结果并停止", status="incomplete", citations=[], gaps=["工具不可用，证据获取未完成。"])
        found, read = {}, {}
        for event in events:
            result = event["result"]
            if result["ok"] and event["action"] == "search_documents":
                found.update({d["id"]: d for d in result["results"]})
            if result["ok"] and event["action"] == "read_document":
                read[result["document"]["id"]] = result["document"]
        for doc_id in found:
            if doc_id not in read:
                return action("read_document", "检索只给出元数据，需要读取候选原文", doc_id=doc_id)
        if not read:
            searches = sum(e["action"] == "search_documents" for e in events)
            if searches < 2:
                return action("search_documents", "没有命中，补充适用范围关键词再查一次", query=question + " 适用范围 规则")
            return action("finish", "补查仍无可用资料，不能凭常识作答", status="insufficient_evidence", citations=[], gaps=["知识库无相关资料，请提供制度或产品文档。"])
        conflicts = conflicting_groups(read.values())
        if conflicts:
            topic = next(iter(conflicts))
            if not any(e["action"] == "search_documents" and e["arguments"]["query"] == topic for e in events):
                return action("search_documents", "原文存在不同规定，按制度主题补查适用范围和优先级", query=topic)
            docs = [d for group in conflicts.values() for d in group]
            return action("finish", "补查后仍无优先级依据，列明冲突并请求确认", status="conflict",
                          citations=[{"doc_id": d["id"], "quote": d["text"]} for d in docs],
                          gaps=["请确认适用制度、适用范围或权威优先级；不能仅按版本日期选择。"])
        return action("finish", "已读取候选原文，未发现同主题冲突", status="answered",
                      citations=[{"doc_id": d["id"], "quote": d["text"]} for d in read.values()], gaps=[])


class ResponsesProvider:
    is_mock = False

    def __init__(self, api_key=None, model=None, base_url=None, timeout=30, transport=None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.model = model or os.getenv("OPENAI_MODEL")
        base_url = (base_url or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        if not self.api_key or not self.model:
            raise ProviderError("MISSING_CONFIG: set OPENAI_API_KEY and OPENAI_MODEL; no implicit Mock fallback")
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ProviderError("OPENAI_BASE_URL must be an HTTPS base URL without credentials/query/fragment")
        self.url, self.timeout = base_url + "/responses", timeout
        self.label = "responses_api:" + self.model
        self.messages, self.pending = [], None
        self.transport = transport or self._post

    def _post(self, payload):
        req = urllib.request.Request(self.url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                     headers={"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"}, method="POST")
        # Do not forward an Authorization header to a redirect destination.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        try:
            with urllib.request.build_opener(NoRedirect).open(req, timeout=self.timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise ProviderError(f"MODEL_HTTP_{exc.code}: check endpoint, model, permissions and quota") from None
        except (OSError, ValueError) as exc:
            raise ProviderError("MODEL_TRANSPORT_ERROR: " + type(exc).__name__) from None

    def next(self, request, events):
        if not self.messages:
            self.messages.append({"role": "user", "content": json.dumps(request, ensure_ascii=False)})
        if self.pending is not None:
            self.messages.append({"type": "function_call_output", "call_id": self.pending,
                                  "output": json.dumps(events[-1]["result"], ensure_ascii=False)})
        result = self.transport({"model": self.model, "instructions": SYSTEM_PROMPT,
                                 "input": self.messages, "tools": TOOLS, "tool_choice": "required",
                                 "parallel_tool_calls": False, "store": False, "max_output_tokens": 4000,
                                 "include": ["reasoning.encrypted_content"]})
        if result.get("status") != "completed":
            raise ProviderError("MODEL_INCOMPLETE: response not completed")
        output = result.get("output", [])
        calls = [item for item in output if item.get("type") == "function_call"]
        if len(calls) != 1:
            raise ProviderError("MODEL_PROTOCOL_ERROR: expected exactly one function call")
        call = calls[0]
        try:
            arguments = json.loads(call["arguments"])
            self.pending = call["call_id"]
        except (ValueError, KeyError, TypeError):
            raise ProviderError("MODEL_PROTOCOL_ERROR: invalid function arguments") from None
        # Preserve all output items (including opaque reasoning) only in memory for API continuity.
        # Trace stores public action arguments and metadata, not hidden reasoning.
        self.messages.extend(output)
        return {"name": call["name"], "arguments": arguments,
                "metadata": {"response_id": result.get("id"), "model": result.get("model", self.model),
                             "usage": result.get("usage", {})}}


class StdioProvider:
    """Transport for an external model/operator; no automatic inference happens here."""
    is_mock = False

    def __init__(self, label="external_operator", reader=None, writer=None):
        self.label = "stdio_bridge:" + label
        self.reader = reader or sys.stdin
        self.writer = writer or sys.stderr

    def next(self, request, events):
        packet = {"event": "model_request", "step": len(events) + 1, "input": request,
                  "observation": events[-1]["result"] if events else None}
        if not events:
            packet.update({"instructions": SYSTEM_PROMPT, "tools": TOOLS})
        print(json.dumps(packet, ensure_ascii=False), file=self.writer, flush=True)
        line = self.reader.readline()
        if not line:
            raise ProviderError("BRIDGE_CLOSED: external decision source disconnected")
        try:
            return json.loads(line)
        except ValueError:
            raise ProviderError("BRIDGE_PROTOCOL_ERROR: expected a JSON action line") from None
