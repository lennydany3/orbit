"""The scripted demo turn: it must send what the real voice loop sends, in the same order."""
from orbit.ipc import protocol
from orbit.ipc.bridge import UiBridge
from orbit.ipc.demo import play_turn


def run(confirm=False, answer=None):
    sent = []
    now = [0.0]
    bridge = UiBridge(sent.append, clock=lambda: now[0])
    asked = []

    def sleep(seconds):
        now[0] += seconds                                     # a fake clock: the demo's pauses are not really waited out
        if answer is not None and not asked:
            ask = [m for m in sent if m.get("type") == "confirm"]
            if ask:
                asked.append(ask[0])
                bridge.on_command(protocol.ConfirmAnswer(ask[0]["id"], answer))

    play_turn(bridge, sleep=sleep, confirm=confirm)
    return sent


def states(sent):
    return [m["state"] for m in sent if "state" in m and "type" not in m]


def test_a_plain_turn_goes_through_every_state_the_sphere_has_for_a_turn():
    sent = run()
    assert states(sent) == ["wake", "listening", "transcribing", "thinking", "success", "speaking", "follow_up", "idle"]
    assert [m["type"] for m in sent if m.get("type") in ("transcript", "reply")] == ["transcript", "reply"]
    follow = next(m for m in sent if m.get("state") == "follow_up")
    assert follow["seconds"] == 8.0
    assert any("level" in m and m["level"] > 0.3 for m in sent), "the demo voice moves the level"


def test_a_confirm_turn_asks_and_acts_on_a_yes():
    sent = run(confirm=True, answer=True)
    assert states(sent)[2:7] == ["transcribing", "thinking", "confirming", "thinking", "success"]
    done = next(m for m in sent if m.get("type") == "confirm_done")
    assert done["answer"] is True and done["by"] == "ui"
    assert next(m for m in sent if m.get("type") == "reply")["text"] == "Okay, I closed Notepad."


def test_a_confirm_turn_with_a_no_leaves_it_alone():
    sent = run(confirm=True, answer=False)
    assert next(m for m in sent if m.get("type") == "reply")["text"] == "Okay, I left Notepad alone."


def test_a_confirm_nobody_answers_is_a_no_after_ten_seconds():
    sent = run(confirm=True, answer=None)
    done = next(m for m in sent if m.get("type") == "confirm_done")
    assert done["answer"] is False and done["by"] == "timeout"
