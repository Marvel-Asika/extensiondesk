"""Runs the deterministic server contract cases (the S-cases in EVALS.md)."""

from extensiondesk.evals import run_server_cases

KNOWN_UNFIXED = {"S13"}  # Hausa delivery, documented in EVALS.md


async def test_server_contract_cases():
    results = await run_server_cases()
    assert len(results) >= 14
    failing = {r["id"] for r in results if not r["pass"]}
    assert failing == KNOWN_UNFIXED, [r for r in results if not r["pass"]]
