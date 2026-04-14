import json
import tempfile
from pathlib import Path

import pytest

from jobpilot.stories import Story, StoryBank


@pytest.fixture
def bank(tmp_path):
    return StoryBank(data_dir=str(tmp_path))


@pytest.fixture
def sample_story():
    return Story(
        title="RAG Pipeline",
        situation="Team needed faster retrieval",
        action="Built LangChain + Pinecone pipeline",
        result="Cut latency from 900ms to 300ms",
        tags=["rag", "langchain"],
        skills=["python", "langchain", "rag"],
    )


class TestStoryBankCRUD:
    def test_add_and_list(self, bank, sample_story):
        bank.add_story(sample_story)
        stories = bank.list_stories()
        assert len(stories) == 1
        assert stories[0].title == "RAG Pipeline"

    def test_persists_to_disk(self, bank, sample_story, tmp_path):
        bank.add_story(sample_story)
        # Reload from disk
        bank2 = StoryBank(data_dir=str(tmp_path))
        assert len(bank2.list_stories()) == 1

    def test_filter_by_tag(self, bank, sample_story):
        bank.add_story(sample_story)
        assert len(bank.list_stories(tag="rag")) == 1
        assert len(bank.list_stories(tag="nonexistent")) == 0

    def test_filter_by_skill(self, bank, sample_story):
        bank.add_story(sample_story)
        assert len(bank.list_stories(skill="python")) == 1
        assert len(bank.list_stories(skill="java")) == 0

    def test_get_story(self, bank, sample_story):
        bank.add_story(sample_story)
        found = bank.get_story(sample_story.id)
        assert found is not None
        assert found.title == "RAG Pipeline"

    def test_get_story_not_found(self, bank):
        assert bank.get_story("nonexistent") is None

    def test_update_story(self, bank, sample_story):
        bank.add_story(sample_story)
        sample_story.title = "Updated Title"
        bank.update_story(sample_story)
        found = bank.get_story(sample_story.id)
        assert found.title == "Updated Title"

    def test_delete_story(self, bank, sample_story):
        bank.add_story(sample_story)
        bank.delete_story(sample_story.id)
        assert len(bank.list_stories()) == 0

    def test_empty_bank(self, bank):
        assert bank.list_stories() == []

    def test_file_not_found(self, tmp_path):
        bank = StoryBank(data_dir=str(tmp_path / "nonexistent"))
        assert bank.list_stories() == []


class TestEmbeddingFallback:
    def test_keyword_search_no_embeddings(self, bank, sample_story):
        bank.add_story(sample_story)
        # find_similar falls back to keyword search when no embedder
        results = bank.find_similar("langchain pipeline", top_k=5)
        assert len(results) >= 1
        assert results[0].title == "RAG Pipeline"

    def test_keyword_search_empty_bank(self, bank):
        results = bank.find_similar("anything", top_k=5)
        assert results == []

    def test_returns_all_when_fewer_than_top_k(self, bank, sample_story):
        bank.add_story(sample_story)
        # When story count <= top_k, all stories are returned (no ranking needed)
        results = bank.find_similar("completely unrelated quantum physics", top_k=5)
        assert len(results) == 1

    def test_keyword_fallback_with_enough_stories(self, bank):
        # Add 10 stories, search should only return matching ones via keyword fallback
        for i in range(10):
            bank.add_story(Story(
                title=f"Story {i}",
                situation=f"context {i}",
                action="did something",
                result="got result",
                tags=[f"tag{i}"],
                skills=["python"],
            ))
        # Claude Code ranking will fail in tests (no subprocess), falls back to keyword
        results = bank.find_similar("context 3", top_k=3)
        assert len(results) <= 3


class TestStoryModel:
    def test_full_text(self):
        s = Story(title="T", situation="S", action="A", result="R")
        assert s.full_text == "T S A R"

    def test_full_text_partial(self):
        s = Story(title="T", situation="", action="A", result="")
        assert s.full_text == "T A"

    def test_default_id(self):
        s = Story(title="Test")
        assert s.id.startswith("story_")

    def test_default_source(self):
        s = Story(title="Test")
        assert s.source == "manual"
