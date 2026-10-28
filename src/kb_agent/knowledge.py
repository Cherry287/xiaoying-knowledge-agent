"""Local, read-only tools with controllable failures and topic-complete retrieval."""
import copy
import json
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


class KnowledgeBase:
    def __init__(self, documents=None, aliases=None, failures=None):
        documents = load_json(ROOT / "data/documents.json") if documents is None else documents
        self.aliases = load_json(ROOT / "data/aliases.json") if aliases is None else aliases
        self.documents = {}
        required = {"id", "title", "version_date", "policy_key", "scope", "priority", "keywords", "text"}
        for document in copy.deepcopy(documents):
            if not isinstance(document, dict) or set(document) != required:
                raise ValueError("Invalid document fields")
            if any(not isinstance(document[k], str) or not document[k].strip()
                   for k in required - {"priority", "keywords"}):
                raise ValueError("Document strings must be non-empty")
            if not isinstance(document["keywords"], list) or any(not isinstance(k, str) for k in document["keywords"]):
                raise ValueError("Invalid document keywords")
            date.fromisoformat(document["version_date"])
            if document["id"] in self.documents:
                raise ValueError("Duplicate document ID")
            self.documents[document["id"]] = document
        self.failures = copy.deepcopy(failures or {})
        for key, count in self.failures.items():
            if not re.fullmatch(r"(search_documents|read_document)(:[\w.-]+)?", key):
                raise ValueError("Invalid failure injection target")
            if type(count) is not int or count < -1:
                raise ValueError("Failure count must be -1 (permanent) or non-negative")

    def normalize(self, query):
        query = query.lower()
        for alias, canonical in self.aliases.items():
            query = query.replace(alias, canonical)
        return query

    def invoke(self, name, arguments):
        specific = f"{name}:{arguments.get('doc_id', '')}"
        key = specific if specific in self.failures else name
        remaining = self.failures.get(key, 0)
        if remaining:
            if remaining > 0:
                self.failures[key] -= 1
            return {"ok": False, "error": {"code": "TOOL_UNAVAILABLE", "retryable": remaining > 0,
                                            "message": "合成故障注入：工具暂时不可用" if remaining > 0 else "合成故障注入：工具持续不可用"}}
        if name == "search_documents":
            return self.search(arguments["query"])
        if name == "read_document":
            doc = self.documents.get(arguments["doc_id"])
            if not doc:
                return {"ok": False, "error": {"code": "NOT_FOUND", "retryable": False, "message": "文档不存在"}}
            return {"ok": True, "document": copy.deepcopy(doc)}
        raise ValueError("Unknown tool")

    def search(self, query):
        normalized = self.normalize(query)
        scored = []
        for doc in self.documents.values():
            score = sum(len(word) for word in doc["keywords"] if word.lower() in normalized)
            if doc["policy_key"].lower() in normalized or doc["id"].lower() in normalized.split():
                score += 100
            if score:
                scored.append((score, doc))
        topics = {doc["policy_key"] for _, doc in scored}
        scores = {doc["id"]: score for score, doc in scored}
        found = [doc for doc in self.documents.values() if doc["policy_key"] in topics]
        found.sort(key=lambda doc: (-scores.get(doc["id"], 0), doc["id"]))
        return {"ok": True, "query": query, "normalized_query": normalized,
                "results": [{k: v for k, v in doc.items() if k not in ("text", "keywords")} for doc in found],
                "coverage": "all_documents_in_matched_policy_keys", "count": len(found)}
