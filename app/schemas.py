"""Data models for an extracted community notice.

Everything is a plain string / bool / list so the JSON schema we hand to
Ollama's structured-output mode stays inside the subset llama.cpp supports
(no anyOf, no nullable unions). Empty string means "not stated in the notice".
"""

from typing import Any, ClassVar, Dict, List

from pydantic import BaseModel, Field


class DateInfo(BaseModel):
    _primary: ClassVar[str] = "value"

    label: str = ""          # "Registration deadline"
    value: str = ""          # exactly as written in the notice
    iso_date: str = ""       # YYYY-MM-DD, only when unambiguous
    iso_end_date: str = ""   # YYYY-MM-DD for ranges / multi-day events
    source_ref: str = ""     # "page 2" | "image" | "pasted text"


class Fee(BaseModel):
    _primary: ClassVar[str] = "label"

    label: str = ""
    amount: str = ""
    currency: str = ""
    iso_date: str = ""       # payment deadline, if stated
    source_ref: str = ""


class Contact(BaseModel):
    _primary: ClassVar[str] = "name"

    name: str = ""
    role: str = ""
    phone: str = ""
    email: str = ""
    source_ref: str = ""


class Link(BaseModel):
    _primary: ClassVar[str] = "url"

    label: str = ""
    url: str = ""


class ActionItem(BaseModel):
    _primary: ClassVar[str] = "step"

    step: str = ""
    detail: str = ""
    required: bool = True
    source_ref: str = ""


class Evidence(BaseModel):
    _primary: ClassVar[str] = "quote"

    field: str = ""
    quote: str = ""
    source_ref: str = ""


class MissingInfo(BaseModel):
    _primary: ClassVar[str] = "field"

    field: str = ""
    issue: str = ""          # absent | unreadable | unclear
    note: str = ""


class Notice(BaseModel):
    title: str = ""
    notice_type: str = "other"      # admission | exam | scholarship | event | closure | holiday | job | tender | other
    issuing_body: str = ""
    summary: str = ""
    audience: str = ""              # who it affects
    dates: List[DateInfo] = Field(default_factory=list)
    venue: str = ""
    eligibility: str = ""
    required_documents: List[str] = Field(default_factory=list)
    optional_documents: List[str] = Field(default_factory=list)
    fees: List[Fee] = Field(default_factory=list)
    contacts: List[Contact] = Field(default_factory=list)
    links: List[Link] = Field(default_factory=list)
    action_items: List[ActionItem] = Field(default_factory=list)
    evidence: List[Evidence] = Field(default_factory=list)
    missing: List[MissingInfo] = Field(default_factory=list)
    needs_clearer_image: bool = False
    answer: str = ""


class ChecklistStep(BaseModel):
    _primary: ClassVar[str] = "step"

    order: int = 1
    step: str = ""
    detail: str = ""
    required: bool = True
    source_ref: str = ""


class Checklist(BaseModel):
    summary: str = ""
    steps: List[ChecklistStep] = Field(default_factory=list)


class EnrichmentMatch(BaseModel):
    _primary: ClassVar[str] = "id"

    id: str = ""
    reason: str = ""


class EnrichmentResult(BaseModel):
    matches: List[EnrichmentMatch] = Field(default_factory=list)


def to_ollama_schema(model: Any) -> Dict[str, Any]:
    """JSON schema safe for Ollama structured outputs.

    Pydantic emits $defs/$ref for nested models; llama.cpp's schema converter
    is picky, so we inline definitions and force every property to be required
    (a model that must emit "" for unknown fields is more predictable than one
    that silently omits keys).
    """
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def inline(node: Any) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                return inline(defs[ref.split("/")[-1]])
            return {k: inline(v) for k, v in node.items()}
        if isinstance(node, list):
            return [inline(v) for v in node]
        return node

    def force_required(node: Any) -> None:
        if isinstance(node, dict):
            props = node.get("properties")
            if node.get("type") == "object" and isinstance(props, dict):
                node["required"] = list(props.keys())
            for value in node.values():
                force_required(value)
        elif isinstance(node, list):
            for value in node:
                force_required(value)

    schema = inline(schema)
    force_required(schema)
    schema.pop("additionalProperties", None)
    return schema
