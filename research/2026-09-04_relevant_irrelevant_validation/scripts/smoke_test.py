"""One-query smoke test for run_validation.py, loaded by file path to avoid
the dashed/date-prefixed folder name being an invalid Python package path."""
import asyncio
import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

spec = importlib.util.spec_from_file_location("run_validation", Path(__file__).parent / "run_validation.py")
run_validation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_validation)


async def main():
    query, category, probe = run_validation.TEST_QUERIES[0]
    print(f"Smoke-testing query: {query!r}")
    result = await run_validation.run_one(0, query, category, probe)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    asyncio.run(main())
