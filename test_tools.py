"""
test_tools.py – Live integration tests for Finlex MCP.
Connects to the real Finlex API. Run: python3 test_tools.py
"""

import asyncio
import sys
import time

from tools_finlex import (
    get_decision,
    get_outline,
    get_proposal,
    get_statute,
    search_decisions,
    search_statutes,
)


async def run_tests():
    failures = []

    print("=== TEST 1: get_statute('55/2001') – Employment Contracts Act ===")
    try:
        result = await get_statute("55/2001")
        assert len(result) > 200, f"Too short: {len(result)} chars"
        assert "55/2001" in result or "työsopimus" in result.lower()
        print(f"PASS — {len(result)} chars")
        print(result[:300], "\n")
    except Exception as e:
        failures.append(f"TEST 1 FAILED: {e}")
        print(f"FAIL: {e}\n")

    time.sleep(1)

    print("=== TEST 2: get_statute('55/2001', chunk=2) – pagination ===")
    try:
        result = await get_statute("55/2001", chunk=2)
        assert len(result) > 100, f"Too short: {len(result)} chars"
        # chunk 2 should NOT contain the header from chunk 1
        print(f"PASS — {len(result)} chars")
        print(result[:200], "\n")
    except Exception as e:
        failures.append(f"TEST 2 FAILED: {e}")
        print(f"FAIL: {e}\n")

    time.sleep(1)

    print("=== TEST 3: get_statute('55/2001', section='1') – section filter ===")
    try:
        result = await get_statute("55/2001", section="1")
        assert len(result) > 50, f"Too short: {len(result)} chars"
        assert "[PART" not in result, "Section result should not be paginated"
        print(f"PASS — {len(result)} chars (no pagination)")
        print(result[:300], "\n")
    except Exception as e:
        failures.append(f"TEST 3 FAILED: {e}")
        print(f"FAIL: {e}\n")

    time.sleep(1)

    print("=== TEST 4: get_statute('731/1999') – Constitution of Finland ===")
    try:
        result = await get_statute("731/1999")
        assert len(result) > 200
        assert "731" in result or "perustuslaki" in result.lower()
        print(f"PASS — {len(result)} chars")
        print(result[:200], "\n")
    except Exception as e:
        failures.append(f"TEST 4 FAILED: {e}")
        print(f"FAIL: {e}\n")

    time.sleep(1)

    print("=== TEST 5: get_statute('39/1889') – Penal Code (long, uses -001 suffix) ===")
    try:
        result = await get_statute("39/1889")
        assert len(result) <= 25_000, f"CONTEXT OVERFLOW: {len(result)} chars"
        assert len(result) > 500
        assert "[PART" in result, "Long statute should show pagination footer"
        print(f"PASS — {len(result)} chars, pagination footer present")
        print(result[-200:], "\n")
    except Exception as e:
        failures.append(f"TEST 5 FAILED: {e}")
        print(f"FAIL: {e}\n")

    time.sleep(1)

    print("=== TEST 6: search_statutes(2024, 2024) ===")
    try:
        result = await search_statutes(2024, 2024)
        assert "2024" in result
        assert len(result) > 100
        print(f"PASS")
        print(result[:300], "\n")
    except Exception as e:
        failures.append(f"TEST 6 FAILED: {e}")
        print(f"FAIL: {e}\n")

    time.sleep(1)

    print("=== TEST 7: search_decisions('okv', 2024, 2025) ===")
    try:
        result = await search_decisions("okv", 2024, 2025)
        assert len(result) > 100
        assert "2024" in result or "2025" in result
        print(f"PASS")
        print(result[:300], "\n")
    except Exception as e:
        failures.append(f"TEST 7 FAILED: {e}")
        print(f"FAIL: {e}\n")

    time.sleep(1)

    print("=== TEST 8: get_proposal(2024, 215) – HE 215/2024 ===")
    try:
        result = await get_proposal(2024, 215)
        assert len(result) > 200
        assert "215" in result or "hallitus" in result.lower()
        print(f"PASS — {len(result)} chars")
        print(result[:300], "\n")
    except Exception as e:
        failures.append(f"TEST 8 FAILED: {e}")
        print(f"FAIL: {e}\n")

    time.sleep(1)

    print("=== TEST 9: search_decisions('kko') – unavailable court error ===")
    try:
        result = await search_decisions("kko")
        assert len(result) > 20
        assert "ERROR" in result or "not available" in result.lower()
        print(f"PASS — error message: {result[:150]}\n")
    except Exception as e:
        failures.append(f"TEST 9 FAILED: {e}")
        print(f"FAIL: {e}\n")

    time.sleep(1)

    print("=== TEST 10: get_statute bad citation ===")
    try:
        result = await get_statute("badcitation")
        assert "ERROR" in result
        print(f"PASS — error message: {result}\n")
    except Exception as e:
        failures.append(f"TEST 10 FAILED: {e}")
        print(f"FAIL: {e}\n")

    time.sleep(1)

    print("=== TEST 11: get_outline('1290/2002') – Työttömyysturvalaki structure ===")
    try:
        result = await get_outline("1290/2002")
        assert len(result) > 100, f"Too short: {len(result)} chars"
        assert "§" in result or "luku" in result.lower() or "LUKU" in result
        # Should NOT contain body text, only headings
        assert len(result) < 10_000, f"Outline too large — probably includes body text: {len(result)}"
        print(f"PASS — {len(result)} chars")
        print(result[:500], "\n")
    except Exception as e:
        failures.append(f"TEST 11 FAILED: {e}")
        print(f"FAIL: {e}\n")

    time.sleep(1)

    print("=== TEST 12: chunk-1 of long statute includes SECTION INDEX ===")
    try:
        result = await get_statute("1290/2002")
        assert "SECTION INDEX" in result, "Chunk 1 of long statute should include SECTION INDEX"
        assert "[PART 1/" in result
        print(f"PASS — section index present in chunk 1")
        # Show the index portion
        idx = result.find("SECTION INDEX")
        print(result[idx:idx+400], "\n")
    except Exception as e:
        failures.append(f"TEST 12 FAILED: {e}")
        print(f"FAIL: {e}\n")

    print("=" * 50)
    if failures:
        print(f"\nFAILED ({len(failures)}):")
        for f in failures:
            print(f"  {f}")
        sys.exit(1)
    else:
        print("\nALL TESTS PASSED")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(run_tests())
