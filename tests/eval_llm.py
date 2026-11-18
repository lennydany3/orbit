"""Score a model on a cases file without running any tool: does it pick the expected FIRST tool call?

    python tests/eval_llm.py                                   # cases.yaml, the default model
    python tests/eval_llm.py --file cases_fuzzy.yaml           # loose requests, WITH real names given to the model
    python tests/eval_llm.py --file cases_fuzzy.yaml --no-facts  # same, without them (to see what the facts buy)
    python tests/eval_llm.py --model llama3.1:8b

Only cases with step <= --step and via: llm are run. Use it to compare models/prompts on the same set.
The facts are fixed here (not read from your PC) so runs are comparable.
"""
import argparse
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from conftest import CURRENT_STEP, load_cases  # noqa: E402

from orbit import config  # noqa: E402
from orbit.agent import context  # noqa: E402
from orbit.agent.loop import Agent  # noqa: E402
from orbit.agent.router import Router  # noqa: E402
from orbit.agent.safety import Safety  # noqa: E402
from orbit.llm.base import LLMError  # noqa: E402
from orbit.llm.ollama_llm import OllamaLLM  # noqa: E402
from orbit.tools.api_layer.spotify import register_spotify_tools  # noqa: E402
from orbit.tools.base import Registry  # noqa: E402
from orbit.tools.browser_layer import register_browser_tools  # noqa: E402
from orbit.tools.fake import register_fake_tools  # noqa: E402
from orbit.tools.os_layer import register_os_tools  # noqa: E402

STATIC_FACTS = {
    "Spotify devices": "3 Spotify device(s). ATLAS (Computer) [active]; Lenny's Echo Pop (Speaker); CMF by Nothing Phone 1 (Smartphone)",
    "Open windows": ("8 windows open. Clock (applicationframehost); Calculator (applicationframehost); Speedtest (applicationframehost); "
                     "*Dexter is installed in this folder - Notepad (notepad); Spotify Premium (spotify); Claude (claude); "
                     "Kora desktop automation - kora - Visual Studio Code (code); YouTube - Brave (brave)"),
    "Now playing": "playing: 'Empty Space' by SadBois (SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify)",
    "Volume": "volume is 87%, not muted",
}


def norm(v):
    return v.strip().lower() if isinstance(v, str) else v


def arg_matches(got_args: dict, key: str, want) -> bool:
    if key.endswith("~"):  # "contains"
        return norm(want) in str(got_args.get(key[:-1], "")).lower()
    return norm(got_args.get(key)) == norm(want)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=config.MODEL)
    ap.add_argument("--step", type=int, default=CURRENT_STEP)
    ap.add_argument("--file", default="cases.yaml")
    ap.add_argument("--no-facts", action="store_true", help="do not give the model the real names")
    args = ap.parse_args()

    registry = Registry()
    register_fake_tools(registry)
    register_os_tools(registry)
    register_browser_tools(registry)
    register_spotify_tools(registry)
    use_facts = args.file != "cases.yaml" and not args.no_facts

    def facts(text: str, previous: str) -> str:
        if not use_facts:
            return ""
        return context.format_facts({label: STATIC_FACTS[label] for label, _ in context.wanted_sources(text, previous)})

    quiet = lambda kind, message: None  # noqa: E731
    agent = Agent(OllamaLLM(model=args.model), registry, Router(quiet), Safety(), quiet, facts_provider=facts)

    cases = load_cases() if args.file == "cases.yaml" else yaml.safe_load((Path(__file__).parent / args.file).read_text(encoding="utf-8"))
    passed = total = 0
    for case in cases:
        if case["step"] > args.step or case["via"] != "llm":  # rules cases are covered by test_rules.py
            continue
        total += 1
        exp = case["expect"][0]
        messages, schemas, _ = agent.prepare(case["request"], "")
        t0 = time.perf_counter()
        try:
            resp = agent.llm.chat(messages, schemas)
        except LLMError as e:
            print(f"ERROR  {e}")
            return 1
        resp = agent.rescue(resp)
        got = resp.tool_calls[0] if resp.tool_calls else None
        tools_ok = exp["tool"] if isinstance(exp["tool"], list) else [exp["tool"]]
        ok = bool(got) and got.name in tools_ok and all(arg_matches(got.arguments, k, v) for k, v in exp["args"].items())
        passed += ok
        shown = f"{got.name}({got.arguments})" if got else f"no tool call: {resp.content[:60]!r}"
        print(f"{'PASS' if ok else 'FAIL'}  {time.perf_counter() - t0:4.1f}s  {case['request']!r} -> {shown}")
        if not ok:
            print(f"        expected {exp['tool']}({exp['args']})")
    print(f"\n{args.model} {'with' if use_facts else 'without'} facts on {args.file}: {passed}/{total} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
