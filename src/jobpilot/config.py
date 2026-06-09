from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass
class Settings:
    supabase_url: str = ""
    supabase_anon_key: str = ""
    auto_approve: bool = False
    daily_limit: int = 5
    profile_path: str = "data/profile.json"
    job_source: str = "linkedin"
    rapidapi_key: str = ""
    remotive_api_url: str = "https://remotive.com/api/remote-jobs"
    arbeitnow_api_url: str = "https://www.arbeitnow.com/api/job-board-api"
    scheduled: bool = False
    output_dir: str = "output"
    connections_csv: str = "data/connections.csv"


def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        supabase_url=os.getenv("SUPABASE_URL", ""),
        supabase_anon_key=os.getenv("SUPABASE_ANON_KEY", ""),
        auto_approve=os.getenv("AUTO_APPROVE", "false").lower() == "true",
        daily_limit=int(os.getenv("DAILY_LIMIT", "5")),
        profile_path=os.getenv("PROFILE_PATH", "data/profile.json"),
        job_source=os.getenv("JOB_SOURCE", "jsearch").lower(),
        rapidapi_key=os.getenv("RAPIDAPI_KEY", ""),
        remotive_api_url=os.getenv("REMOTIVE_API_URL", "https://remotive.com/api/remote-jobs"),
        arbeitnow_api_url=os.getenv("ARBEITNOW_API_URL", "https://www.arbeitnow.com/api/job-board-api"),
        connections_csv=os.getenv("CONNECTIONS_CSV", "data/connections.csv"),
    )

