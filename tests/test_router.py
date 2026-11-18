import pytest
from pydantic import BaseModel

from orbit.agent.router import Router
from orbit.tools.base import Registry, Tool, ToolResult, VerifyResult
from orbit.tools.fake import EchoParams, register_fake_tools


class P(BaseModel):
    x: int = 0


def mk(name, layer, run, verify=None, risk="low", capability="cap"):
    return Tool(name=name, description="d", params=P, layer=layer, risk=risk, run=run, verify=verify, capability=capability)


OK = lambda p: ToolResult.success("done")  # noqa: E731
VERIFIED = lambda p, r: VerifyResult(True, "fine")  # noqa: E731
NOT_VERIFIED = lambda p, r: VerifyResult(False, "nope")  # noqa: E731


def kinds(logs):
    return [k for k, _ in logs]


def test_registry_exposes_best_layer_and_orders_alternatives():
    reg = Registry()
    reg.register(mk("via_browser", "browser", OK))
    reg.register(mk("via_api", "api", OK))
    reg.register(mk("via_os", "os", OK))
    assert [t.name for t in reg.exposed()] == ["via_api"]
    assert [t.name for t in reg.alternatives(reg.exposed()[0])] == ["via_api", "via_os", "via_browser"]
    assert reg.get_exposed("via_os") is None  # the model can't skip the better layer by naming a worse one


def test_registry_rejects_mismatched_params_for_same_capability():
    class Other(BaseModel):
        y: str = ""

    reg = Registry()
    reg.register(mk("a", "os", OK))
    with pytest.raises(ValueError):
        reg.register(Tool(name="b", description="d", params=Other, layer="browser", risk="low", run=OK, capability="cap"))


def test_success_is_verified(logs):
    r = Router(lambda k, m: logs.append((k, m))).run([mk("t", "os", OK, VERIFIED)], P())
    assert r.ok and r.verified


def test_no_verifier_is_reported_as_unverified(logs):
    r = Router(lambda k, m: logs.append((k, m))).run([mk("t", "os", OK)], P())
    assert r.ok and not r.verified
    assert "NOT verified" in r.for_model()


def test_failed_verify_retries_once_then_falls_back(logs):
    runs = []
    os_tool = mk("os_t", "os", lambda p: runs.append("os") or ToolResult.success("os"), NOT_VERIFIED)
    br_tool = mk("br_t", "browser", lambda p: runs.append("browser") or ToolResult.success("br"), VERIFIED)
    r = Router(lambda k, m: logs.append((k, m))).run([os_tool, br_tool], P())
    assert runs == ["os", "os", "browser"]  # one retry, then the next layer
    assert r.ok and r.verified and r.summary == "br"
    assert "retry" in kinds(logs) and "fallback" in kinds(logs)  # both are visible in the log


def test_all_layers_fail_is_an_error_never_silent_success(logs):
    r = Router(lambda k, m: logs.append((k, m))).run(
        [mk("a", "os", OK, NOT_VERIFIED), mk("b", "browser", OK, NOT_VERIFIED)], P()
    )
    assert not r.ok
    assert "verification failed" in r.error and "os:a" in r.error


def test_exception_in_tool_is_caught_and_falls_back(logs):
    def boom(p):
        raise RuntimeError("kaboom")

    r = Router(lambda k, m: logs.append((k, m))).run([mk("a", "os", boom), mk("b", "browser", OK, VERIFIED)], P())
    assert r.ok and r.summary == "done"


def test_risky_tool_is_never_retried(logs):
    runs = []
    tool = mk("danger", "os", lambda p: runs.append(1) or ToolResult.success("x"), NOT_VERIFIED, risk="confirm")
    r = Router(lambda k, m: logs.append((k, m))).run([tool], P())
    assert runs == [1] and not r.ok


def test_fake_echo_retry_then_fallback_end_to_end(logs):
    reg = Registry()
    register_fake_tools(reg)
    router = Router(lambda k, m: logs.append((k, m)))
    tool = reg.get_exposed("fake_echo")

    once = router.run(reg.alternatives(tool), EchoParams(text="a", verify_mode="fail_once"))
    assert once.ok and "os layer" in once.summary and "fallback" not in kinds(logs)  # second try on the os layer passed

    always = router.run(reg.alternatives(tool), EchoParams(text="b", verify_mode="fail_always"))
    assert always.ok and "browser layer" in always.summary and "fallback" in kinds(logs)


def test_permanent_failure_is_not_retried_but_still_tries_next_layer(logs):
    runs = []

    def not_found(p):
        runs.append("os")
        return ToolResult.failure("not found", retryable=False)

    r = Router(lambda k, m: logs.append((k, m))).run([mk("a", "os", not_found), mk("b", "browser", OK, VERIFIED)], P())
    assert runs == ["os"] and r.ok and "retry" not in kinds(logs) and "fallback" in kinds(logs)
