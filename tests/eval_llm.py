"""Score a model on tests/cases.yaml without running any tool: does it pick the expected FIRST tool call?

    python tests/eval_llm.py                  # default model from config
    python tests/eval_llm.py --model llama3.1:8b --step 2

Only cases with step <= --step are run. Use it to compare models/providers on the same set.
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from conftest import CURRENT_STEP, load_cases  # noqa: E402

from orbit import config  # noqa: E402
from orbit.agent.loop import SYSTEM_PROMPT  # noqa: E402
from orbit.llm.base import LLMError  # noqa: E402
from orbit.llm.ollama_llm import OllamaLLM  # noqa: E402
from orbit.tools.base import Registry  # noqa: E402
from orbit.tools.fake import register_fake_tools  # noqa: E402
from orbit.tools.browser_layer import register_browser_tools  # noqa: E402
from orbit.tools.os_layer import register_os_tools  # noqa: E402


def norm(v):
    return v.strip().lower() if isinstance(v, str) else v


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=config.MODEL)
    ap.add_argument("--step", type=int, default=CURRENT_STEP)
    args = ap.parse_args()

    registry = Registry()
    register_fake_tools(registry)
    register_os_tools(registry)
    register_browser_tools(registry)  # Step 4: register the api tools here too
    llm = OllamaLLM(model=args.model)

    passed = total = 0
    for case in load_cases():
        if case["step"] > args.step or case["via"] != "llm":  # rules cases are covered by test_rules.py
            continue
        total += 1
        exp = case["expect"][0]
        tools = registry.relevant(case["request"], config.MAX_TOOLS_PER_REQUEST)
        t0 = time.perf_counter()
        try:
            resp = llm.chat(
                [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": case["request"]}],
                [t.schema() for t in tools],
            )
        except LLMError as e:
            print(f"ERROR  {e}")
            return 1
        got = resp.tool_calls[0] if resp.tool_calls else None
        ok = bool(got) and got.name == exp["tool"] and all(
            norm(got.arguments.get(k)) == norm(v) for k, v in exp["args"].items()
        )
        passed += ok
        shown = f"{got.name}({got.arguments})" if got else f"no tool call: {resp.content[:60]!r}"
        print(f"{'PASS' if ok else 'FAIL'}  {time.perf_counter() - t0:4.1f}s  {case['request']!r} -> {shown}")
        if not ok:
            print(f"        expected {exp['tool']}({exp['args']})")
    print(f"\n{args.model}: {passed}/{total} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
