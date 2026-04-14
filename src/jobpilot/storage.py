from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from supabase import Client, create_client


class StoryStore(Protocol):
    def list_stories(self) -> list[dict[str, Any]]:
        ...

    def save_application(self, application: dict[str, Any]) -> None:
        ...


@dataclass
class InMemoryStore:
    stories: list[dict[str, Any]] = field(default_factory=list)
    applications: list[dict[str, Any]] = field(default_factory=list)

    def list_stories(self) -> list[dict[str, Any]]:
        return self.stories

    @staticmethod
    def _get_legacy_stories() -> list[dict[str, Any]]:
        """Return the original hardcoded stories for one-time migration."""
        return [
            {
                "title": "Neuro-Symbolic Legal Reasoning Pipeline",
                "content": (
                    "Architected a multi-stage Neuro-Symbolic pipeline with Walkers Global "
                    "to automate conversion of US Tax Code into executable Prolog predicates, "
                    "achieving 64% end-to-end accuracy. Developed a deterministic reasoning engine "
                    "using Python, SWI-Prolog and LLMs (GPT-4o, Gemini 2.5 Pro) that replaces "
                    "redundant LLM calls with a verified symbolic knowledge base. Engineered an "
                    "automated feedback loop boosting syntactic correctness to over 90%."
                ),
                "tags": ["llm", "nlp", "prolog", "neuro-symbolic", "legal-ai", "python"],
            },
            {
                "title": "Huawei Research — Autonomous Networking AI",
                "content": (
                    "Evaluated open-source autonomous networking AI frameworks at Huawei Ireland "
                    "Research Centre, focusing on trustworthiness and explainability gaps in "
                    "telecoms. Authored technical insight reports on SOTA AI techniques for both "
                    "technical and business stakeholders."
                ),
                "tags": ["ai", "research", "explainability", "telecoms", "evaluation"],
            },
            {
                "title": "Golden Tax Project — National Tax Infrastructure",
                "content": (
                    "Appointed to the national task force for China's Golden Tax Project "
                    "(Phases III & IV), architecting technical and data frameworks for national "
                    "tax infrastructure. Developed a Python-based RPA tool to automate cross-system "
                    "data entry, replacing manual workflows. Designed a SQL-based full-text "
                    "retrieval system for internal tax counseling."
                ),
                "tags": ["python", "rpa", "sql", "automation", "infrastructure", "data"],
            },
            {
                "title": "Movie Recommendation AI Agent",
                "content": (
                    "Built an autonomous AI agent on the aiXplain platform integrating Movie DB "
                    "APIs, streaming tools, and translation services. Implemented context-aware "
                    "session management and a Reason-Act-Observe loop for reliable orchestration "
                    "across heterogeneous data sources."
                ),
                "tags": ["ai agent", "api", "agentic workflows", "python"],
            },
            {
                "title": "GliNER Fine-tuning for Financial NER",
                "content": (
                    "Fine-tuned GliNER LLMs for domain-specific NER in zero-shot and few-shot "
                    "financial scenarios. Leveraged FAISS and Sentence Transformers to curate "
                    "high-quality training data from 100k raw samples. Built an end-to-end "
                    "data labeling pipeline using Argilla."
                ),
                "tags": ["nlp", "ner", "pytorch", "faiss", "fine-tuning", "data"],
            },
        ]

    def save_application(self, application: dict[str, Any]) -> None:
        self.applications.append(application)


@dataclass
class SupabaseStore:
    url: str
    anon_key: str

    def __post_init__(self) -> None:
        self.client: Client = create_client(self.url, self.anon_key)

    def list_stories(self) -> list[dict[str, Any]]:
        result = self.client.table("stories").select("*").execute()
        return result.data or []

    def save_application(self, application: dict[str, Any]) -> None:
        self.client.table("applications").insert(application).execute()

