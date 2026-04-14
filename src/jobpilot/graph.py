from __future__ import annotations

from functools import partial

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from jobpilot.agents import (
    apply_jobs_node,
    evaluate_cv_node,
    fetch_jds_node,
    learning_progress_node,
    learning_plan_node,
    load_profile_node,
    load_stories_node,
    render_pdfs_node,
    review_evaluation_node,
    review_jobs_node,
    review_tailored_node,
    score_jobs_node,
    search_jobs_node,
    tailor_resume_node,
)
from jobpilot.config import Settings
from jobpilot.state import JobPilotState
from jobpilot.storage import StoryStore


def build_graph(settings: Settings, store: StoryStore):
    graph = StateGraph(JobPilotState)

    graph.add_node("load_profile", partial(load_profile_node, settings=settings))
    graph.add_node("load_stories", partial(load_stories_node, settings=settings))
    graph.add_node("learning_progress", learning_progress_node)
    graph.add_node("search_jobs", partial(search_jobs_node, settings=settings))
    graph.add_node("score_jobs", score_jobs_node)
    graph.add_node("review_jobs", partial(review_jobs_node, settings=settings))
    graph.add_node("fetch_jds", fetch_jds_node)
    graph.add_node("tailor_resume", partial(tailor_resume_node, settings=settings))
    graph.add_node("review_tailored", partial(review_tailored_node, settings=settings))
    graph.add_node("evaluate_cv", evaluate_cv_node)
    graph.add_node("review_evaluation", partial(review_evaluation_node, settings=settings))
    graph.add_node("render_pdfs", partial(render_pdfs_node, settings=settings))
    graph.add_node("apply_jobs", partial(apply_jobs_node, store=store))
    graph.add_node("learning_plan", learning_plan_node)

    # Main pipeline
    graph.add_edge(START, "load_profile")
    graph.add_edge("load_profile", "load_stories")
    graph.add_edge("load_profile", "learning_progress")
    graph.add_edge("load_stories", "search_jobs")
    graph.add_edge("learning_progress", "score_jobs")
    graph.add_edge("learning_progress", "learning_plan")
    graph.add_edge("search_jobs", "score_jobs")
    graph.add_edge("score_jobs", "review_jobs")
    graph.add_edge("review_jobs", "fetch_jds")
    graph.add_edge("fetch_jds", "tailor_resume")
    graph.add_edge("tailor_resume", "review_tailored")
    graph.add_edge("review_tailored", "evaluate_cv")
    graph.add_edge("evaluate_cv", "review_evaluation")
    graph.add_edge("review_evaluation", "render_pdfs")
    graph.add_edge("render_pdfs", "apply_jobs")
    graph.add_edge("apply_jobs", END)
    graph.add_edge("learning_plan", END)

    memory = MemorySaver()
    return graph.compile(checkpointer=memory)
