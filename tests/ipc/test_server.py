"""The WebSocket server, over real local sockets: who may connect, what they receive, what they may send."""
import json
import socket
import time

import pytest

pytest.importorskip("websockets")
from websockets.exceptions import ConnectionClosed, InvalidStatus  # noqa: E402
from websockets.sync.client import connect  # noqa: E402

from orbit.ipc import protocol  # noqa: E402
from orbit.ipc.bridge import UiBridge  # noqa: E402
from orbit.ipc.link import Link  # noqa: E402
from orbit.ipc.server import IpcServer, _Face  # noqa: E402

TOKEN = "s3cret-token"


@pytest.fixture
def server():
    commands = []

    def on_command(answer):
        commands.append(answer)
        return None if answer.id == 7 else "no such confirmation is open"

    s = IpcServer(port=0, token=TOKEN, on_command=on_command, snapshot=lambda: protocol.hello("thinking", 0.5))
    s.commands = commands
    s.start()
    yield s
    s.stop()


def url(s, token=TOKEN):
    return f"ws://127.0.0.1:{s.port}/?token={token}"


def wait_for(predicate, seconds=3.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def recv(ws, timeout=3.0):
    return json.loads(ws.recv(timeout=timeout))


# ------------------------------------------------------------------ who may connect
def test_it_only_listens_on_this_pc():
    s = IpcServer(port=0, token=TOKEN, on_command=lambda a: None, snapshot=dict)
    s.start()
    try:
        assert s._server.socket.getsockname()[0] == "127.0.0.1"
    finally:
        s.stop()


def test_a_face_with_the_token_gets_the_current_state_first(server):
    with connect(url(server)) as ws:
        assert recv(ws) == {"type": "hello", "version": 1, "state": "thinking", "level": 0.5}


@pytest.mark.parametrize("token", ["", "wrong", TOKEN + "x", TOKEN[:-1]])
def test_a_wrong_or_missing_token_is_refused(server, token):
    with pytest.raises(InvalidStatus) as e:
        connect(url(server, token))
    assert e.value.response.status_code == 403
    with pytest.raises(InvalidStatus):
        connect(f"ws://127.0.0.1:{server.port}/")
    assert server.faces == 0


def test_a_web_page_is_turned_away_even_with_the_token(server):
    with pytest.raises(InvalidStatus) as e:
        connect(url(server), additional_headers={"Origin": "https://evil.example"})
    assert e.value.response.status_code == 403
    with pytest.raises(InvalidStatus):
        connect(url(server), additional_headers={"Origin": "null"})          # a local file or sandboxed frame
    assert server.faces == 0


def test_a_page_you_name_may_connect():
    s = IpcServer(port=0, token=TOKEN, allowed_origins=("http://localhost:5173",), on_command=lambda a: None,
                  snapshot=lambda: protocol.hello("idle", 0))
    s.start()
    try:
        with connect(url(s), additional_headers={"Origin": "http://localhost:5173"}) as ws:
            assert recv(ws)["type"] == "hello"
        with pytest.raises(InvalidStatus):
            connect(url(s), additional_headers={"Origin": "http://localhost:5174"})
    finally:
        s.stop()


# ------------------------------------------------------------------ what a face receives
def test_published_messages_reach_every_face_in_order(server):
    with connect(url(server)) as a, connect(url(server)) as b:
        recv(a), recv(b)
        assert wait_for(lambda: server.faces == 2)
        for i in range(5):
            server.publish({"level": i / 10})
        server.publish({"state": "speaking"})
        for ws in (a, b):
            assert [recv(ws) for _ in range(6)] == [{"level": 0.0}, {"level": 0.1}, {"level": 0.2}, {"level": 0.3}, {"level": 0.4}, {"state": "speaking"}]


def test_a_face_that_connects_later_misses_nothing_after_its_hello(server):
    with connect(url(server)) as ws:
        assert recv(ws)["type"] == "hello"
        assert wait_for(lambda: server.faces == 1)
        server.publish({"state": "listening"})
        assert recv(ws) == {"state": "listening"}


def test_text_is_sent_as_unicode_not_escapes(server):
    with connect(url(server)) as ws:
        recv(ws)
        assert wait_for(lambda: server.faces == 1)
        server.publish(protocol.transcript("you", "café – नमस्ते"))
        assert recv(ws)["text"] == "café – नमस्ते"


def test_a_face_that_leaves_is_forgotten(server):
    with connect(url(server)) as ws:
        recv(ws)
        assert wait_for(lambda: server.faces == 1)
    assert wait_for(lambda: server.faces == 0)
    server.publish({"level": 1})                                         # nobody is there: nothing happens


def test_a_face_that_stops_reading_is_dropped_instead_of_slowing_orbit_down():
    class StuckConnection:
        closed = False

        def send(self, text):
            time.sleep(10)

        def close(self):
            self.closed = True

    s = IpcServer(port=0, token=TOKEN, on_command=lambda a: None, snapshot=dict)
    face = _Face(StuckConnection())                       # nothing drains its queue, like a face that stopped reading
    s._faces.append(face)
    started = time.perf_counter()
    for _ in range(400):
        s.publish({"level": 0.5})
    assert time.perf_counter() - started < 1.0, "publishing never waits for a face"
    assert face not in s._faces and face.connection.closed


# ------------------------------------------------------------------ what a face may send
def test_a_confirm_answer_reaches_orbit(server):
    with connect(url(server)) as ws:
        recv(ws)
        ws.send('{"type":"confirm","id":7,"yes":true}')
        assert wait_for(lambda: len(server.commands) == 1)
        assert server.commands[0] == protocol.ConfirmAnswer(7, True)


def test_a_refused_answer_is_explained_to_that_face(server):
    with connect(url(server)) as ws:
        recv(ws)
        ws.send('{"type":"confirm","id":99,"yes":true}')
        assert recv(ws) == {"type": "error", "message": "no such confirmation is open"}


@pytest.mark.parametrize("raw", ['{"type":"text","text":"delete everything"}', "garbage", "[]", '{"type":"confirm","id":"7","yes":true}'])
def test_anything_but_a_confirm_is_refused_and_changes_nothing(server, raw):
    with connect(url(server)) as ws:
        recv(ws)
        ws.send(raw)
        assert recv(ws)["type"] == "error"
        assert server.commands == []
        ws.send('{"type":"confirm","id":7,"yes":false}')                   # the connection is still fine afterwards
        assert wait_for(lambda: len(server.commands) == 1)


def test_an_oversized_message_closes_the_connection(server):
    with connect(url(server)) as ws:
        recv(ws)
        ws.send("x" * (protocol.MAX_COMMAND_BYTES + 100))
        with pytest.raises(ConnectionClosed):
            for _ in range(5):
                ws.recv(timeout=3)
    assert server.commands == []


# ------------------------------------------------------------------ lifecycle
def test_stop_disconnects_faces_and_frees_the_port():
    s = IpcServer(port=0, token=TOKEN, on_command=lambda a: None, snapshot=lambda: protocol.hello("idle", 0))
    s.start()
    port = s.port
    with connect(url(s)) as ws:
        recv(ws)
        s.stop()
        with pytest.raises(ConnectionClosed):
            for _ in range(5):
                ws.recv(timeout=3)
    probe = socket.socket()
    try:
        probe.bind(("127.0.0.1", port))                  # free again
    finally:
        probe.close()


def test_a_busy_port_is_an_error_the_caller_can_report():
    s = IpcServer(port=0, token=TOKEN, on_command=lambda a: None, snapshot=dict)
    s.start()
    try:
        other = IpcServer(port=s.port, token=TOKEN, on_command=lambda a: None, snapshot=dict)
        with pytest.raises(OSError):
            other.start()
    finally:
        s.stop()


# ------------------------------------------------------------------ the whole link
def test_the_link_carries_a_confirmation_both_ways():
    link = Link(port=0)
    link.start()
    try:
        assert len(link.token) >= 16, "a random token is made when none is given"
        with connect(link.url) as ws:
            assert recv(ws)["type"] == "hello"
            assert wait_for(lambda: link.server.faces == 1)
            cid = link.bridge.confirm_asked("os_close_window(...)", "close window Notepad")
            assert recv(ws) == {"type": "confirm", "id": cid, "question": "os_close_window(...)", "action": "close window Notepad"}
            ws.send(json.dumps({"type": "confirm", "id": cid, "yes": True}))
            assert wait_for(lambda: link.bridge.poll_answer() == protocol.ConfirmAnswer(cid, True))
            ws.send(json.dumps({"type": "confirm", "id": cid, "yes": False}))      # a second answer is refused
            assert recv(ws)["type"] == "error"
    finally:
        link.stop()


def test_two_links_get_different_tokens():
    a, b = Link(port=0), Link(port=0)
    assert a.token != b.token
    assert isinstance(a.bridge, UiBridge)
