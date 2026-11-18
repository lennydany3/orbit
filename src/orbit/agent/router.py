"""Router: run a tool on the most reliable layer, verify it, retry once, then fall back to the next layer.

Rules enforced here (in code, not in the prompt):
- candidates are tried in layer order: api -> os -> browser -> vision
- an action only counts as success if its verifier confirms it (or it has none, and the result says so)
- permanent failures (not found, bad target) are not retried, only passed to the next layer
- a risky (confirm) or non-idempotent tool (a click) is never retried automatically, so it can't be done twice by accident
- every retry and every fallback is logged visibly
"""
import time
from typing import Callable

from pydantic import BaseModel

from .. import config
from ..tools.base import Tool, ToolResult, VerifyResult

Log = Callable[[str, str], None]


class Router:
    def __init__(self, log: Log, verify_retries: int = config.VERIFY_RETRIES):
        self.log = log
        self.verify_retries = verify_retries

    def run(self, candidates: list[Tool], params: BaseModel) -> ToolResult:
        """`candidates` = same-capability tools, best layer first (Registry.alternatives)."""
        last_error = "no tool available"
        for i, tool in enumerate(candidates):
            result, last_error = self._try_tool(tool, params)
            if result is not None:
                return result
            if i + 1 < len(candidates):
                nxt = candidates[i + 1]
                self.log("fallback", f"{tool.layer} layer failed ({last_error}); falling back to {nxt.layer}: {nxt.name}")
        names = " -> ".join(f"{t.layer}:{t.name}" for t in candidates)
        return ToolResult.failure(f"could not complete and verify the action on any layer ({names}). Last error: {last_error}")

    def _try_tool(self, tool: Tool, params: BaseModel) -> tuple[ToolResult | None, str]:
        attempts = 1 if (tool.risk == "confirm" or not tool.idempotent) else 1 + self.verify_retries
        error = ""
        for attempt in range(1, attempts + 1):
            if attempt > 1:
                self.log("retry", f"{tool.name}: attempt {attempt}/{attempts}")
            start = time.perf_counter()
            result = _safe(lambda: tool.run(params), lambda e: ToolResult.failure(f"{type(e).__name__}: {e}"))
            took = time.perf_counter() - start
            if not result.ok:
                error = result.error
                self.log("act", f"{tool.layer}:{tool.name} failed in {took:.2f}s: {error}")
                if not result.retryable:
                    break
                continue
            self.log("act", f"{tool.layer}:{tool.name} ran in {took:.2f}s: {result.summary}")
            if tool.verify is None:
                self.log("verify", f"{tool.name}: no verifier, result NOT verified")
                return result, ""
            verdict = _safe(lambda: tool.verify(params, result), lambda e: VerifyResult(False, f"verifier crashed: {e}"))
            if verdict.ok:
                result.verified = True
                self.log("verify", f"{tool.name}: OK ({verdict.detail})")
                return result, ""
            error = f"verification failed: {verdict.detail}"
            self.log("verify", f"{tool.name}: FAILED ({verdict.detail})")
        return None, error


def _safe(fn, on_error):
    try:
        return fn()
    except Exception as e:  # a tool bug must never crash the agent
        return on_error(e)
