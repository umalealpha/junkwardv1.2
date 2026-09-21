"""core.doc_parse — Omni local-first document parsing.

Tier 0 (deterministic route: xlsx→calamine, born-digital PDF→pdfplumber) →
Tier 1 (local OCR: RapidOCR) → rule-based confidence gate. The caller escalates
to a local LLM (Ollama) then DeepSeek/Gemini ONLY when ParseResult.escalate is
True. Keeps customer PII local for the common case and cuts external-API cost.

All modules are deliberately Django-free + import-guarded so they degrade
gracefully where an optional dependency (rapidocr / python-calamine / docling)
is not installed. See project_omni_local_doc_parse memory + swarm wf_a892a25e-901.
"""

from .cascade import ParseResult, parse  # noqa: F401
