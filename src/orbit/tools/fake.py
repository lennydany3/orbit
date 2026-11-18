"""Fake tools that prove the whole loop without touching the PC.

fake_echo        (os layer)      verify can be told to fail once / always -> exercises retry
fake_echo_browser (browser layer) same capability, always verifies        -> exercises layer fallback
fake_danger      (os layer)      risk=confirm                             -> exercises the safety gate
"""
from typing import Literal

from pydantic import BaseModel, Field

from .base import Registry, Tool, ToolResult, VerifyResult


class EchoParams(BaseModel):
    text: str = Field(description="text to echo back")
    verify_mode: Literal["ok", "fail_once", "fail_always"] = Field(
        default="ok", description="test only: how the os-layer verifier behaves"
    )


class DangerParams(BaseModel):
    text: str = Field(description="what to pretend to delete")


def register_fake_tools(registry: Registry) -> None:
    runs: dict[str, int] = {}  # how many times the os-layer echo ran per text

    def echo_os(p: EchoParams) -> ToolResult:
        runs[p.text] = runs.get(p.text, 0) + 1
        return ToolResult.success(f"echoed '{p.text}' (os layer, run #{runs[p.text]})", data=p.text)

    def verify_echo_os(p: EchoParams, r: ToolResult) -> VerifyResult:
        if p.verify_mode == "fail_always":
            return VerifyResult(False, "fake verifier always fails")
        if p.verify_mode == "fail_once" and runs.get(p.text, 0) < 2:
            return VerifyResult(False, "fake verifier fails on the first run")
        return VerifyResult(True, "echo confirmed")

    def echo_browser(p: EchoParams) -> ToolResult:
        return ToolResult.success(f"echoed '{p.text}' (browser layer)", data=p.text)

    def danger(p: DangerParams) -> ToolResult:
        return ToolResult.success(f"pretended to delete '{p.text}'")

    registry.register(Tool(
        name="fake_echo", description="Echo some text back (test tool).",
        params=EchoParams, layer="os", risk="low", run=echo_os, verify=verify_echo_os,
    ))
    registry.register(Tool(
        name="fake_echo_browser", description="Echo some text back (test tool, fallback layer).",
        params=EchoParams, layer="browser", risk="low", run=echo_browser,
        verify=lambda p, r: VerifyResult(True, "echo confirmed"), capability="fake_echo",
    ))
    registry.register(Tool(
        name="fake_danger", description="Pretend to delete something (test tool, needs confirmation).",
        params=DangerParams, layer="os", risk="confirm", run=danger,
        verify=lambda p, r: VerifyResult(True, "nothing was really deleted"),
    ))
