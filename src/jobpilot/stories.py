from __future__ import annotations

import json
import uuid
from datetime import date
from pathlib import Path

from pydantic import BaseModel, Field


class Story(BaseModel):
    id: str = Field(default_factory=lambda: f"story_{uuid.uuid4().hex[:8]}")
    title: str
    situation: str = ""
    action: str = ""
    result: str = ""
    tags: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    date_added: str = Field(default_factory=lambda: date.today().isoformat())
    date_occurred: str = ""
    source: str = "manual"  # manual, import, project, migration
    experience_id: str | None = None  # links to master_cv experience[].id or projects[].id

    @property
    def full_text(self) -> str:
        parts = [self.title, self.situation, self.action, self.result]
        return " ".join(p for p in parts if p)


class StoryBank:
    def __init__(self, data_dir: str = "data") -> None:
        self._data_dir = Path(data_dir)
        self._stories_path = self._data_dir / "stories.json"
        self._stories: list[Story] = []
        self._load()

    def _load(self) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        if self._stories_path.exists():
            raw = json.loads(self._stories_path.read_text(encoding="utf-8"))
            self._stories = [Story(**s) for s in raw]

    def _save_stories(self) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        data = [s.model_dump() for s in self._stories]
        self._stories_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    def list_stories(
        self,
        tag: str | None = None,
        skill: str | None = None,
    ) -> list[Story]:
        stories = self._stories
        if tag:
            tag_lower = tag.lower()
            stories = [s for s in stories if tag_lower in [t.lower() for t in s.tags]]
        if skill:
            skill_lower = skill.lower()
            stories = [
                s for s in stories if skill_lower in [sk.lower() for sk in s.skills]
            ]
        return stories

    def get_story(self, story_id: str) -> Story | None:
        for s in self._stories:
            if s.id == story_id:
                return s
        return None

    def add_story(self, story: Story, dedup: bool = True) -> Story | None:
        if dedup and self._stories:
            duplicate = self._check_duplicate(story)
            if duplicate is not None:
                return None  # signal that it was a duplicate
        self._stories.append(story)
        self._save_stories()
        return story

    def add_story_batch(self, stories: list[Story], dedup: bool = True) -> list[Story]:
        """Add multiple stories, deduplicating against existing and within the batch."""
        added = []
        for story in stories:
            result = self.add_story(story, dedup=dedup)
            if result is not None:
                added.append(result)
        return added

    def _check_duplicate(self, new_story: Story) -> Story | None:
        """Check if new_story is a duplicate of an existing story. Returns the existing story if duplicate, None otherwise."""
        try:
            return self._claude_dedup_check(new_story)
        except Exception:
            return self._keyword_dedup_check(new_story)

    def _keyword_dedup_check(self, new_story: Story) -> Story | None:
        """Simple keyword overlap check for dedup fallback."""
        new_tokens = set(new_story.full_text.lower().split())
        if len(new_tokens) < 3:
            return None
        for existing in self._stories:
            existing_tokens = set(existing.full_text.lower().split())
            if not existing_tokens:
                continue
            overlap = len(new_tokens & existing_tokens)
            similarity = overlap / max(len(new_tokens | existing_tokens), 1)
            if similarity > 0.7:
                return existing
        return None

    def _claude_dedup_check(self, new_story: Story) -> Story | None:
        """Ask Claude if this story duplicates an existing one."""
        import subprocess

        existing_summaries = "\n".join(
            f"{i}. [{s.id}] {s.title} — {s.result[:80]}"
            for i, s in enumerate(self._stories)
        )
        prompt = (
            "Does this new story duplicate any existing story below?\n"
            "A duplicate means it describes the SAME achievement or experience, "
            "even if worded differently.\n"
            "Related but distinct stories (e.g., different achievements at the same company) "
            "are NOT duplicates.\n\n"
            f"NEW STORY:\nTitle: {new_story.title}\n"
            f"Action: {new_story.action}\nResult: {new_story.result}\n\n"
            f"EXISTING STORIES:\n{existing_summaries}\n\n"
            'Return ONLY a JSON object: {"is_duplicate": true/false, "duplicate_of": "story_id or null"}\n'
            "No markdown fences."
        )

        result = subprocess.run(
            ["claude", "-p", "--output-format", "json", prompt],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr[:200])

        envelope = json.loads(result.stdout)
        response_text = envelope.get("result", "").strip()
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            lines = [l for l in lines[1:] if not l.strip().startswith("```")]
            response_text = "\n".join(lines).strip()

        data = json.loads(response_text)
        if data.get("is_duplicate") and data.get("duplicate_of"):
            return self.get_story(data["duplicate_of"])
        return None

    def update_story(self, story: Story) -> Story:
        for i, s in enumerate(self._stories):
            if s.id == story.id:
                self._stories[i] = story
                self._save_stories()
                return story
        raise ValueError(f"Story not found: {story.id}")

    def delete_story(self, story_id: str) -> None:
        self._stories = [s for s in self._stories if s.id != story_id]
        self._save_stories()

    def find_similar(
        self,
        query: str,
        top_k: int = 8,
    ) -> list[Story]:
        if not self._stories:
            return []

        if len(self._stories) <= top_k:
            return list(self._stories)

        try:
            return self._claude_rank(query, top_k)
        except Exception:
            return self._keyword_search(query, top_k)

    def _claude_rank(self, query: str, top_k: int) -> list[Story]:
        """Ask Claude Code to pick the most relevant stories."""
        import subprocess

        stories_summary = "\n".join(
            f"{i}. [{s.id}] {s.title} — {s.result[:100]}"
            for i, s in enumerate(self._stories)
        )
        prompt = (
            f"Given this job description:\n{query[:1500]}\n\n"
            f"Which of these stories are most relevant? Pick the top {top_k}.\n"
            f"Return ONLY a JSON array of the story IDs (strings), ordered by relevance.\n\n"
            f"Stories:\n{stories_summary}\n\n"
            "Return ONLY a valid JSON array of ID strings, no markdown fences."
        )

        result = subprocess.run(
            ["claude", "-p", "--output-format", "json", prompt],
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode != 0:
            raise RuntimeError(result.stderr[:200])

        import json as _json
        envelope = _json.loads(result.stdout)
        response_text = envelope.get("result", "").strip()

        # Strip markdown fences if present
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            lines = [l for l in lines[1:] if not l.strip().startswith("```")]
            response_text = "\n".join(lines).strip()

        selected_ids = _json.loads(response_text)
        id_to_story = {s.id: s for s in self._stories}
        ranked = [id_to_story[sid] for sid in selected_ids if sid in id_to_story]
        return ranked[:top_k]

    def _keyword_search(self, query: str, top_k: int) -> list[Story]:
        tokens = {t.strip().lower() for t in query.split() if len(t.strip()) > 2}
        if not tokens:
            return self._stories[:top_k]

        scored: list[tuple[int, Story]] = []
        for story in self._stories:
            text = story.full_text.lower()
            hits = sum(1 for t in tokens if t in text)
            if hits > 0:
                scored.append((hits, story))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored[:top_k]]
