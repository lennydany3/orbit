"""The messages between Orbit and its face: levels, message shapes and the one command a face may send."""
import json

import numpy as np
import pytest

from orbit.ipc import protocol
from orbit.ipc.demo import pcm_at
from orbit.ipc.protocol import ConfirmAnswer, ProtocolError, level_from_pcm, parse_command


# ------------------------------------------------------------------ loudness
def test_silence_and_nothing_are_level_zero():
    assert level_from_pcm(np.zeros(1280, np.int16)) == 0.0
    assert level_from_pcm(np.zeros(0, np.int16)) == 0.0
    assert level_from_pcm(None) == 0.0


def test_loud_speech_is_about_one_and_a_quiet_room_about_zero():
    assert level_from_pcm(np.full(1280, 8000, np.int16)) > 0.95          # -12 dBFS
    assert level_from_pcm(np.full(1280, 20, np.int16)) == 0.0            # -64 dBFS: below the floor


def amplitude_at(dbfs):
    return np.full(1280, 32768 * 10 ** (dbfs / 20), np.int16)


def test_real_microphone_speech_uses_the_middle_of_the_scale_not_the_top():
    # measured on recorded speech: typical -29 dBFS, loud -17 dBFS, a quiet room below -65
    typical, loud, room = (level_from_pcm(amplitude_at(d)) for d in (-29, -17, -70))
    assert 0.55 < typical < 0.75 and 0.9 < loud <= 1.0 and room == 0.0


def test_orbits_own_voice_is_measured_on_its_own_hotter_scale():
    # measured on Piper: typical -16 dBFS, loud -10 dBFS, between sentences below -50
    scale = protocol.VOICE_RANGE
    typical, loud, pause = (level_from_pcm(amplitude_at(d), scale) for d in (-16, -10, -52))
    assert 0.65 < typical < 0.8 and 0.85 < loud < 0.95 and pause == 0.0
    assert level_from_pcm(amplitude_at(-16), scale) < level_from_pcm(amplitude_at(-16)), "the same loudness reads lower than on the mic scale"
    assert typical < 0.85, "not pinned at the top the whole time Orbit talks"


def test_the_level_rises_with_the_volume():
    levels = [level_from_pcm(np.full(1280, a, np.int16)) for a in (50, 200, 800, 3000)]
    assert levels == sorted(levels) and len(set(levels)) == 4


@pytest.mark.parametrize("wanted", [0.0, 0.2, 0.5, 0.8, 1.0])
def test_the_demos_synthetic_audio_reads_back_as_the_level_it_was_made_for(wanted):
    assert level_from_pcm(pcm_at(wanted)) == pytest.approx(wanted, abs=0.03)
    assert level_from_pcm(pcm_at(wanted, protocol.VOICE_RANGE), protocol.VOICE_RANGE) == pytest.approx(wanted, abs=0.03)


# ------------------------------------------------------------------ messages
def test_state_messages_have_no_type_so_the_design_page_understands_them():
    assert protocol.orb_state("listening") == {"state": "listening"}
    assert protocol.orb_state("listening", reason="wake word") == {"state": "listening", "reason": "wake word"}
    assert protocol.orb_state("follow_up", seconds=8) == {"state": "follow_up", "seconds": 8.0}
    assert protocol.level(0.4234) == {"level": 0.42}
    assert "type" not in protocol.orb_state("idle") and "type" not in protocol.level(1)


def test_an_unknown_state_is_a_bug_not_a_message():
    with pytest.raises(ValueError):
        protocol.orb_state("dancing")


def test_levels_are_clamped_to_zero_and_one():
    assert protocol.level(7) == {"level": 1.0} and protocol.level(-1) == {"level": 0.0}


def test_every_message_is_plain_json():
    for msg in (protocol.hello("idle", 0), protocol.transcript("you", "hi"), protocol.reply("a", "b", "chat", "proud"),
                protocol.confirm(3, "q", "close window Notepad"), protocol.confirm_done(3, True, "ui"), protocol.error("no")):
        assert json.loads(json.dumps(msg)) == msg
    assert protocol.hello("idle", 0)["version"] == protocol.PROTOCOL_VERSION


def test_very_long_text_is_cut_not_sent_whole():
    long = "x" * 50_000
    assert len(protocol.transcript("you", long)["text"]) == protocol.MAX_TEXT_CHARS
    assert protocol.reply(long, long, "action", "neutral")["text"].endswith("…")


# ------------------------------------------------------------------ the one command
def test_a_well_formed_confirm_is_understood():
    assert parse_command('{"type":"confirm","id":3,"yes":true}') == ConfirmAnswer(3, True)
    assert parse_command(b'{"type":"confirm","id":0,"yes":false}') == ConfirmAnswer(0, False)


@pytest.mark.parametrize("raw", [
    "", "nonsense", "[]", "3", "null",
    '{"type":"text","text":"open spotify"}',                    # a face cannot send Orbit requests
    '{"type":"ptt"}', '{"id":1,"yes":true}',
    '{"type":"confirm","id":"3","yes":true}', '{"type":"confirm","id":3.5,"yes":true}', '{"type":"confirm","id":true,"yes":true}',
    '{"type":"confirm","id":3,"yes":"yes"}', '{"type":"confirm","id":3,"yes":1}', '{"type":"confirm","id":3}', '{"type":"confirm","yes":true}',
])
def test_anything_else_is_refused(raw):
    with pytest.raises(ProtocolError):
        parse_command(raw)


def test_an_oversized_command_is_refused_before_it_is_parsed():
    with pytest.raises(ProtocolError, match="too long"):
        parse_command('{"type":"confirm","id":1,"yes":true,"pad":"' + "x" * protocol.MAX_COMMAND_BYTES + '"}')
    with pytest.raises(ProtocolError):
        parse_command(b"\xff\xfe")
