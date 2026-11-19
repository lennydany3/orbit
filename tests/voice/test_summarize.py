"""Spoken-summary shortening: one or two sentences, never URLs, code or lists."""
import re

from orbit.voice.summarize import spoken_summary


def test_short_reply_is_unchanged():
    assert spoken_summary("Opened Spotify.") == "Opened Spotify."


def test_long_reply_is_cut_to_two_sentences_and_points_to_the_screen():
    out = spoken_summary("Opened Spotify. Volume is now 30 percent. The song is Lofi Girl. It is playing on the Echo Pop.")
    assert out == "Opened Spotify. Volume is now 30 percent. The rest is on screen."


def test_urls_and_paths_are_never_spoken():
    out = spoken_summary("I opened https://github.com/lennydany3 for you. The file is at C:\\Users\\lenny\\Downloads\\a.txt now.")
    assert "http" not in out and "github.com" not in out and "C:\\" not in out and "a file" in out


def test_code_is_never_spoken():
    out = spoken_summary("Run `pip install x` to fix it.\n```python\nprint('hi')\n```\nThen retry.")
    assert "pip" not in out and "print" not in out and "`" not in out and "retry" in out


def test_lists_are_replaced_by_a_pointer():
    out = spoken_summary("Your open windows:\n- Spotify\n- Brave\n- Notepad")
    assert out == "Your open windows: I've put the full list on screen."
    assert "Brave" not in out
    assert spoken_summary("1. one\n2. two") == "I've put the details on screen."


def test_markdown_marks_are_not_read_out():
    out = spoken_summary("**Done.** I set the _volume_ to # 30.")
    assert not re.search(r"[*_#]", out)


def test_a_very_long_sentence_is_cut_at_a_word():
    out = spoken_summary("word " * 200)
    assert len(out) < 260 and out.count("rest is on screen") == 1 and not out.startswith("word  ")


def test_empty_and_symbol_only_replies_are_never_empty():
    assert spoken_summary("") == "Done."
    assert spoken_summary("   \n ") == "Done."
    assert spoken_summary("```code only```") == "I've put the details on screen."
