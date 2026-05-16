"""Candidate knowledge graph for skill-aware job matching.

Parses profile.md into an in-memory graph of skills organised by category.
For each job, computes a deterministic skill-overlap score and injects a
grounded summary into the LLM scoring prompt — making scores more consistent
and less dependent on LLM mood (same idea as JustHireMe's scoring engine).

No external DB dependency — pure Python, rebuilt from profile.md at startup.
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger

import config

# ── Skill taxonomy ────────────────────────────────────────────────────────────
# Each entry: (regex_pattern, canonical_name, category)
# Patterns are compiled once at import time.

_RAW_SKILLS: list[tuple[str, str, str]] = [
    # Languages
    (r"\bpython\b",                      "python",            "language"),
    (r"\bsql\b",                         "sql",               "language"),
    (r"c\+\+",                           "c++",               "language"),
    (r"\bc\b(?!\+)",                     "c",                 "language"),
    (r"\bjavascript\b",                  "javascript",        "language"),
    (r"\btypescript\b",                  "typescript",        "language"),
    (r"\bjava\b",                        "java",              "language"),
    (r"\bgolang\b|\bgo\b",               "go",                "language"),
    (r"\brust\b",                        "rust",              "language"),
    (r"\bscala\b",                       "scala",             "language"),

    # Data & ML
    (r"\bpandas\b",                      "pandas",            "data_ml"),
    (r"\bnumpy\b",                       "numpy",             "data_ml"),
    (r"scikit.learn|sklearn",            "scikit-learn",      "data_ml"),
    (r"\btensorflow\b",                  "tensorflow",        "data_ml"),
    (r"\bpytorch\b",                     "pytorch",           "data_ml"),
    (r"\bfaiss\b",                       "faiss",             "data_ml"),
    (r"\bscann\b",                       "scann",             "data_ml"),
    (r"\barima\b",                       "arima",             "data_ml"),
    (r"\bsarimax\b",                     "sarimax",           "data_ml"),
    (r"\bstl\b.*decomp|decomp.*\bstl\b", "stl decomposition", "data_ml"),
    (r"machine.?learning",               "machine learning",  "data_ml"),
    (r"deep.?learning",                  "deep learning",     "data_ml"),
    (r"neural.?network",                 "neural network",    "data_ml"),
    (r"\bnlp\b|natural.language.process","nlp",               "data_ml"),
    (r"\bllm\b|large.language.model",    "llm",               "data_ml"),
    (r"gen(?:erative)?.?ai",             "generative ai",     "data_ml"),
    (r"time.?series",                    "time series",       "data_ml"),
    (r"\bforecasting\b",                 "forecasting",       "data_ml"),
    (r"two.tower|recommender",           "recommender",       "data_ml"),
    (r"\bclassif",                       "classification",    "data_ml"),
    (r"\bregression\b",                  "regression",        "data_ml"),
    (r"\bclustering\b",                  "clustering",        "data_ml"),
    (r"\bann\b.*retriev|retriev.*\bann\b","ann retrieval",    "data_ml"),

    # Analytics & BI
    (r"power.?bi",                       "powerbi",           "analytics"),
    (r"\btableau\b",                     "tableau",           "analytics"),
    (r"\bexcel\b",                       "excel",             "analytics"),
    (r"\bstreamlit\b",                   "streamlit",         "analytics"),
    (r"\blooker\b",                      "looker",            "analytics"),
    (r"\bmetabase\b",                    "metabase",          "analytics"),
    (r"cohort.?anal",                    "cohort analysis",   "analytics"),
    (r"funnel.?anal",                    "funnel analysis",   "analytics"),
    (r"a/?b.?test",                      "a/b testing",       "analytics"),
    (r"\bcl[tv]v?\b|customer.lifetime.value", "cltv",         "analytics"),
    (r"\bchurn\b",                       "churn analysis",    "analytics"),
    (r"product.?analytics",              "product analytics", "analytics"),
    (r"retention.?anal",                 "retention",         "analytics"),
    (r"data.?storytelling",              "data storytelling", "analytics"),
    (r"\bdashboard\b",                   "dashboard",         "analytics"),
    (r"market.?research",                "market research",   "analytics"),

    # Databases
    (r"postgresql|postgres",             "postgresql",        "database"),
    (r"\bmysql\b",                       "mysql",             "database"),
    (r"\bmongodb\b",                     "mongodb",           "database"),
    (r"\bsqlite\b",                      "sqlite",            "database"),
    (r"\bredis\b",                       "redis",             "database"),
    (r"\bsnowflake\b",                   "snowflake",         "database"),
    (r"\bbigquery\b",                    "bigquery",          "database"),
    (r"\belasticsearch\b",               "elasticsearch",     "database"),

    # Backend / APIs
    (r"node\.?js",                       "node.js",           "backend"),
    (r"\bexpress\b",                     "express",           "backend"),
    (r"\bfastapi\b",                     "fastapi",           "backend"),
    (r"\bdjango\b",                      "django",            "backend"),
    (r"\bflask\b",                       "flask",             "backend"),
    (r"rest.?api",                       "rest api",          "backend"),
    (r"\bgraphql\b",                     "graphql",           "backend"),
    (r"socket\.?io",                     "socket.io",         "backend"),

    # Frontend
    (r"react\.?js|\breact\b",            "react",             "frontend"),
    (r"\bhtml\b",                        "html",              "frontend"),
    (r"\bcss\b",                         "css",               "frontend"),

    # DevOps / Tools
    (r"\bgit\b",                         "git",               "devops"),
    (r"\bdocker\b",                      "docker",            "devops"),
    (r"\bkubernetes\b|\bk8s\b",          "kubernetes",        "devops"),
    (r"\baws\b",                         "aws",               "devops"),
    (r"\bgcp\b",                         "gcp",               "devops"),
    (r"\bci/?cd\b",                      "ci/cd",             "devops"),
    (r"\bfigma\b",                       "figma",             "tools"),
    (r"\bcrm\b",                         "crm",               "tools"),
    (r"\bjira\b",                        "jira",              "tools"),

    # Business / Product
    (r"product.?manag",                  "product management","business"),
    (r"business.?anal",                  "business analysis", "business"),
    (r"\bassociate.?pm\b|\bpm\b",        "product management","business"),
    (r"\bassociate.?ba\b|\bba\b",        "business analysis", "business"),
    (r"stakeholder",                     "stakeholder mgmt",  "business"),
    (r"\bagile\b",                       "agile",             "business"),
    (r"\bscrum\b",                       "scrum",             "business"),
    (r"lead.?generat",                   "lead generation",   "business"),
    (r"data.?engineer",                  "data engineering",  "business"),
    (r"campaign.?execut",                "campaign execution","business"),

    # CS Fundamentals
    (r"data.?struct",                    "data structures",   "cs"),
    (r"\balgorithm",                     "algorithms",        "cs"),
    (r"\bdsa\b",                         "dsa",               "cs"),
    (r"\bdbms\b",                        "dbms",              "cs"),
    (r"\boop\b|object.orient",           "oop",               "cs"),
    (r"operating.system|\bos\b",         "os",                "cs"),
    (r"computer.network",                "networks",          "cs"),
]

_COMPILED: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(pat, re.IGNORECASE), canonical, category)
    for pat, canonical, category in _RAW_SKILLS
]


def _extract_skills(text: str) -> dict[str, str]:
    """Return {canonical_skill: category} for every skill pattern found in text."""
    found: dict[str, str] = {}
    for pattern, canonical, category in _COMPILED:
        if pattern.search(text):
            found[canonical] = category
    return found


# ── Candidate graph ───────────────────────────────────────────────────────────

class CandidateGraph:
    """In-memory knowledge graph for a single candidate, built from profile.md."""

    def __init__(self) -> None:
        self.skills: dict[str, str] = {}   # canonical_skill → category
        self._ready = False

    def build(self, profile_text: str) -> None:
        """Parse profile.md and index all candidate skills."""
        self.skills = _extract_skills(profile_text)
        self._ready = True

        by_cat: dict[str, list[str]] = {}
        for skill, cat in self.skills.items():
            by_cat.setdefault(cat, []).append(skill)
        logger.info(
            f"[GRAPH] Candidate graph built: {len(self.skills)} skills "
            f"across {len(by_cat)} categories"
        )

    def match(self, job: dict[str, Any]) -> dict[str, Any]:
        """Compute deterministic skill-overlap between candidate graph and a job.

        Returns:
            overlap_score: int 0-100, or -1 when JD has no extractable skills
            matched: list of skills the candidate has that the job requires
            missing: list of job skills the candidate lacks
            summary: one-liner injected into the LLM scoring prompt
        """
        if not self._ready:
            return _null_match()

        jd_text = f"{job.get('title', '')} {job.get('description', '')}"
        job_skills = _extract_skills(jd_text)

        if not job_skills:
            return {
                "overlap_score": -1,
                "matched": [],
                "missing": [],
                "summary": "Graph: no skill keywords found in JD.",
            }

        matched = sorted(s for s in job_skills if s in self.skills)
        missing = sorted(s for s in job_skills if s not in self.skills)
        overlap = int(len(matched) / len(job_skills) * 100)

        summary = (
            f"Graph skill match {overlap}% ({len(matched)}/{len(job_skills)} skills). "
            f"Has: {', '.join(matched[:8]) or 'none'}."
            + (f" Lacks: {', '.join(missing[:5])}." if missing else "")
        )
        return {
            "overlap_score": overlap,
            "matched": matched,
            "missing": missing,
            "summary": summary,
        }


def _null_match() -> dict[str, Any]:
    return {"overlap_score": -1, "matched": [], "missing": [], "summary": ""}


# ── Module-level singleton ────────────────────────────────────────────────────

_graph: CandidateGraph | None = None


def get_graph() -> CandidateGraph:
    """Return the singleton graph, building from profile.md on first call."""
    global _graph
    if _graph is None:
        _graph = CandidateGraph()
    if not _graph._ready:
        path = config.PROFILE_PATH
        if path.exists():
            _graph.build(path.read_text(encoding="utf-8"))
        else:
            logger.warning("[GRAPH] profile.md not found — graph matching unavailable")
    return _graph


def match_job(job: dict[str, Any]) -> dict[str, Any]:
    """Match a single job against the candidate graph."""
    return get_graph().match(job)


def rebuild() -> None:
    """Force-rebuild the graph from profile.md (call after editing profile)."""
    global _graph
    _graph = None
    get_graph()
