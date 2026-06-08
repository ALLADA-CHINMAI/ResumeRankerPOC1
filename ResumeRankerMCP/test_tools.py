"""
Direct tool test script — validates all MCP tool functions against live Azure.

Runs without the MCP protocol layer (no JSON-RPC, no stdio/HTTP transport).
Use this to verify Azure connectivity and tool correctness before registering
with Claude Desktop or GitHub Copilot.

Usage (from ResumeRankerPOC1/):
    # Mac/Linux:
    python -m ResumeRankerMCP.test_tools

    # Windows PowerShell:
    python -m ResumeRankerMCP.test_tools

    # Run only specific test:
    python -m ResumeRankerMCP.test_tools --test catalog
    python -m ResumeRankerMCP.test_tools --test search
    python -m ResumeRankerMCP.test_tools --test rank
    python -m ResumeRankerMCP.test_tools --test profile
    python -m ResumeRankerMCP.test_tools --test gaps

Requires: .env file at project root with Azure credentials.
"""

from __future__ import annotations

import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from ResumeRankerCommon.clients import validate_config

SAMPLE_JD = """
We are looking for a Senior Software Engineer with 5+ years of professional
software development experience. The ideal candidate has strong Python skills,
experience with cloud platforms (AWS or Azure), and a background working with
REST APIs and microservices. Experience with SQL databases and containerization
(Docker/Kubernetes) is a plus. Healthcare or enterprise SaaS domain experience
is highly valued.
"""


def _header(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)


def test_catalog():
    from ResumeRankerMCP.tools.catalog import list_candidates, list_job_descriptions

    _header("TEST: list_candidates")
    candidates = list_candidates()
    print(f"Found {len(candidates)} indexed resumes:")
    for c in candidates[:10]:
        print(f"  - {c}")
    if len(candidates) > 10:
        print(f"  ... and {len(candidates) - 10} more")

    _header("TEST: list_job_descriptions")
    jds = list_job_descriptions()
    print(f"Found {len(jds)} job descriptions in blob storage:")
    for j in jds[:5]:
        print(f"  - {j}")
    return candidates


def test_search(candidates: list[str]):
    from ResumeRankerMCP.tools.search import search_candidates

    _header("TEST: search_candidates")
    query = "software engineer Python cloud experience"
    print(f"Query: '{query}', top_k=5")
    results = search_candidates(query, top_k=5)
    for r in results:
        print(f"\n  [{r['score']:.4f}] {r['candidate_name']}")
        excerpt = r['excerpt'].replace('\n', ' ')[:120]
        print(f"  ...{excerpt}...")

    # Test subset filtering if we have candidates
    if len(candidates) >= 2:
        _header("TEST: search_candidates (filtered to 2 candidates)")
        subset = candidates[:2]
        print(f"Filtering to: {subset}")
        results_filtered = search_candidates(query, top_k=5, candidate_names=subset)
        print(f"Got {len(results_filtered)} results (all should be from the 2 selected)")
        names_returned = {r['candidate_name'] for r in results_filtered}
        print(f"Candidates returned: {names_returned}")


def test_ranking(candidates: list[str]):
    from ResumeRankerMCP.tools.ranking import rank_candidates_for_job, compare_candidates

    _header("TEST: rank_candidates_for_job")
    print(f"JD: {SAMPLE_JD.strip()[:100]}...")
    print("top_k=3")
    ranked = rank_candidates_for_job(SAMPLE_JD, top_k=3)
    for r in ranked:
        print(f"\n  #{ranked.index(r)+1} {r['candidate_name']}: {r['total_score']}/100")
        scores = r.get('scores', {})
        print(f"     exp={scores.get('experience','?')} tech={scores.get('technicalSkills','?')} "
              f"cert={scores.get('certifications','?')} edu={scores.get('education','?')} "
              f"loc={scores.get('location','?')} domain={scores.get('domainFit','?')}")

    if len(candidates) >= 2:
        _header("TEST: compare_candidates")
        compare_list = candidates[:2]
        print(f"Comparing: {compare_list}")
        compared = compare_candidates(SAMPLE_JD, compare_list)
        for r in compared:
            print(f"\n  {r['candidate_name']}: {r['total_score']}/100")
        return ranked


def test_profile(candidates: list[str]):
    if not candidates:
        print("No candidates available — skipping profile test.")
        return

    from ResumeRankerMCP.tools.profile import get_candidate_profile

    _header("TEST: get_candidate_profile")
    name = candidates[0]
    print(f"Profiling: {name}")
    profile = get_candidate_profile(name)
    print(f"  Name:        {profile.get('candidate_name')}")
    print(f"  Title:       {profile.get('current_or_recent_title')}")
    print(f"  Experience:  {profile.get('years_of_experience')} years")
    print(f"  Education:   {profile.get('education')}")
    print(f"  Skills:      {', '.join(profile.get('key_skills', [])[:5])}")
    print(f"  Certs:       {profile.get('certifications', [])}")
    print(f"  Domain:      {profile.get('domain_expertise', [])}")
    print(f"  Location:    {profile.get('location')}")
    print(f"  Summary:     {profile.get('summary', '')[:150]}...")


def test_gaps(candidates: list[str]):
    if not candidates:
        print("No candidates available — skipping gap analysis test.")
        return

    from ResumeRankerMCP.tools.profile import analyze_skill_gaps

    _header("TEST: analyze_skill_gaps")
    name = candidates[0]
    print(f"Analyzing gaps for: {name}")
    gaps = analyze_skill_gaps(SAMPLE_JD, name)
    print(f"  Match score:    {gaps.get('match_score')}/100")
    print(f"  Strengths:      {gaps.get('strengths', [])}")
    print(f"  Recommendation: {gaps.get('recommendation')}")
    gap_list = gaps.get('gaps', [])
    if gap_list:
        print(f"  Gaps ({len(gap_list)}):")
        for g in gap_list:
            print(f"    [{g.get('importance','?').upper()}] {g.get('skill')} — {g.get('notes','')[:80]}")


def main():
    parser = argparse.ArgumentParser(description="Test ResumeRanker MCP tools directly.")
    parser.add_argument(
        "--test",
        choices=["all", "catalog", "search", "rank", "profile", "gaps"],
        default="all",
        help="Which test(s) to run (default: all).",
    )
    args = parser.parse_args()

    print("ResumeRanker MCP Tool Tests")
    print("Validating Azure configuration...")
    try:
        validate_config()
        print("Azure config: OK")
    except ValueError as e:
        print(f"Azure config ERROR: {e}")
        print("Add missing variables to your .env file and retry.")
        sys.exit(1)

    candidates = []

    try:
        if args.test in ("all", "catalog"):
            candidates = test_catalog()

        if args.test in ("all", "search"):
            if not candidates and args.test == "search":
                from ResumeRankerMCP.tools.catalog import list_candidates
                candidates = list_candidates()
            test_search(candidates or [])

        if args.test in ("all", "rank"):
            if not candidates and args.test == "rank":
                from ResumeRankerMCP.tools.catalog import list_candidates
                candidates = list_candidates()
            test_ranking(candidates or [])

        if args.test in ("all", "profile"):
            if not candidates and args.test == "profile":
                from ResumeRankerMCP.tools.catalog import list_candidates
                candidates = list_candidates()
            test_profile(candidates or [])

        if args.test in ("all", "gaps"):
            if not candidates and args.test == "gaps":
                from ResumeRankerMCP.tools.catalog import list_candidates
                candidates = list_candidates()
            test_gaps(candidates or [])

    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    print("\n" + "="*60)
    print("  All tests completed.")
    print("="*60)


if __name__ == "__main__":
    main()
