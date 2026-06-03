"""
ResumeRanker MCP Server — FastMCP entrypoint.

Exposes 7 tools to any MCP-compatible agent (GitHub Copilot, Claude Desktop,
custom agents). Resumes and JDs are ingested via the Streamlit UI; this server
is query-only.

Transport modes:
  stdio:  python -m ResumeRankerMCP.main --transport stdio
          Used by Claude Desktop and GitHub Copilot (VSCode Agent mode).

  http:   python -m ResumeRankerMCP.main --transport http --port 8000
          Used for cloud/enterprise HTTP deployments. Clients connect to
          http://host:port/sse for SSE stream and /messages for requests.

Python 3.10+ required (FastMCP dependency).
"""

from __future__ import annotations

import sys
import os
import argparse
import logging

# Project root on sys.path so ResumeRankerCore is importable from any cwd.
# Must come before any ResumeRankerCore imports.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Trigger tiktoken tokenizer download BEFORE stdio protocol loop opens.
# tiktoken prints to stdout on first use, which corrupts the MCP stdio stream.
try:
    from ResumeRankerCore.text_utils import chunk_text as _warmup
    _warmup("")
except Exception:
    pass

from fastmcp import FastMCP
from dotenv import load_dotenv

from ResumeRankerCore.clients import validate_config
from ResumeRankerMCP.server_config import configure_logging

from ResumeRankerMCP.tools.catalog import list_candidates, list_job_descriptions
from ResumeRankerMCP.tools.search import search_candidates, get_jd_text
from ResumeRankerMCP.tools.ranking import rank_candidates_for_job, compare_candidates
from ResumeRankerMCP.tools.profile import get_candidate_profile, analyze_skill_gaps

load_dotenv()
configure_logging()

mcp = FastMCP(
    name="ResumeRanker",
    version="1.0.0",
    instructions=(
        "Azure-backed resume query engine for enterprise candidate screening.\n\n"
        "Typical workflow:\n"
        "1. list_candidates() — see what resumes are in the system\n"
        "2. search_candidates(query) — fast hybrid search to find relevant candidates\n"
        "3. rank_candidates_for_job(jd_text) — GPT-4o 6-dimension scoring (100 pts total)\n"
        "4. get_candidate_profile(name) — structured extraction of skills, experience, certs\n"
        "5. analyze_skill_gaps(jd_text, name) — strengths, gaps, hire/no-hire recommendation\n"
        "6. compare_candidates(jd_text, names) — side-by-side scoring of specific candidates\n\n"
        "Scoring rubric: experience/35 + technicalSkills/40 + certifications/5 + "
        "education/5 + location/5 + domainFit/10 = 100 total."
    ),
)

# Register all 7 tools. Functions are defined in their domain modules (testable
# independently) and registered here so the modules stay import-independent.
for _fn in [
    list_candidates,
    list_job_descriptions,
    search_candidates,
    get_jd_text,
    rank_candidates_for_job,
    compare_candidates,
    get_candidate_profile,
    analyze_skill_gaps,
]:
    mcp.tool()(_fn)


# Static MCP resource: agents can read the scoring rubric to understand result data.
@mcp.resource("resume-ranker://scoring-rubric")
def scoring_rubric() -> str:
    """The 6-dimension scoring rubric used by rank_candidates_for_job and compare_candidates."""
    return (
        "# ResumeRanker Scoring Rubric (Total: 100 points)\n\n"
        "| Dimension       | Max | What it evaluates |\n"
        "|-----------------|-----|-------------------|\n"
        "| experience      | 35  | Years + depth of relevant work history |\n"
        "| technicalSkills | 40  | Coverage of required skills, tools, languages |\n"
        "| certifications  | 5   | Required or preferred certifications/licenses |\n"
        "| education       | 5   | Degree, field, level vs. job requirements |\n"
        "| location        | 5   | Geographic fit or remote readiness |\n"
        "| domainFit       | 10  | Industry/domain alignment with the role |\n\n"
        "Scores are assigned by GPT-4o after reading the full JD and resume.\n"
        "Stage 1 hybrid search (BM25+vector) pre-filters to top 15 candidates before scoring."
    )


def main():
    parser = argparse.ArgumentParser(
        description="ResumeRanker MCP Server — query engine for resume ranking and candidate analysis."
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http", "sse"],
        default="stdio",
        help=(
            "Transport mode: "
            "'stdio' for Claude Desktop / GitHub Copilot (default), "
            "'http' or 'sse' for cloud/enterprise HTTP deployment."
        ),
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind when using HTTP transport (default: 0.0.0.0).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind when using HTTP transport (default: 8000).",
    )
    args = parser.parse_args()

    # Validate Azure config before accepting any connections — fail fast with a
    # clear error rather than failing opaquely on the first tool call.
    try:
        validate_config()
    except ValueError as e:
        logging.critical(
            "Azure configuration error — set the required environment variables "
            "or add them to your .env file.\n%s",
            e,
        )
        sys.exit(1)

    if args.transport == "stdio":
        logging.info("Starting ResumeRanker MCP server (stdio transport).")
        mcp.run(transport="stdio")
    else:
        logging.info(
            "Starting ResumeRanker MCP server (HTTP/SSE transport) on %s:%d.",
            args.host,
            args.port,
        )
        mcp.run(transport="sse", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
