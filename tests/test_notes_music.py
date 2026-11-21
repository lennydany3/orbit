"""Notes and tasks, and the saved playlists: the files the window edits and Orbit reads and adds to."""
import json
from datetime import datetime
from pathlib import Path

import pytest

from orbit import playlists as P
from orbit.notes import NotesStore

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 22, 14, 30)


# ------------------------------------------------------------------ notes
def notes(tmp_path, **raw):
    file = tmp_path / "notes.json"
    if raw:
        file.write_text(json.dumps({"version": 1, **raw}), encoding="utf-8")
    return NotesStore(file)


def test_no_file_is_no_notes_and_no_tasks(tmp_path):
    n = notes(tmp_path)
    assert n.notes == [] and n.tasks == []


def test_the_first_sentence_of_what_you_say_is_the_title(tmp_path):
    n = notes(tmp_path)
    one = n.add_note("The wifi is on the router.", NOW)
    two = n.add_note("Gym plan. Monday legs, Wednesday push, Friday pull.", NOW)
    assert (one.title, one.body) == ("The wifi is on the router", "")
    assert (two.title, two.body) == ("Gym plan", "Gym plan. Monday legs, Wednesday push, Friday pull.")
    again = NotesStore(tmp_path / "notes.json")
    assert [x.title for x in again.notes] == ["The wifi is on the router", "Gym plan"]


def test_a_very_long_first_sentence_is_cut_at_a_word(tmp_path):
    n = notes(tmp_path)
    note = n.add_note("word " * 40, NOW)
    assert len(note.title) <= 80 and note.title.endswith("…") and note.body.startswith("word word")


def test_the_newest_change_comes_first(tmp_path):
    n = notes(tmp_path, notes=[{"id": "n-1", "title": "Old", "updated": "2026-11-20T09:00"}, {"id": "n-2", "title": "New", "updated": "2026-11-22T09:00"}])
    assert [x.title for x in n.recent_notes()] == ["New", "Old"]


def test_tasks_are_added_found_finished_and_cleared(tmp_path):
    n = notes(tmp_path)
    milk, eggs = n.add_task("Buy milk", NOW), n.add_task("buy eggs", NOW)
    assert [t.text for t in n.open_tasks()] == ["Buy milk", "buy eggs"]
    assert [t.id for t in n.find_tasks("the milk task")] == [milk.id] and n.find_tasks("") == []
    assert n.finish_task(milk.id) and [t.text for t in n.open_tasks()] == ["buy eggs"]
    assert n.find_tasks("milk") == [] and [t.id for t in n.find_tasks("milk", open_only=False)] == [milk.id]
    assert n.finish_task(milk.id, done=False) and len(n.open_tasks()) == 2
    n.finish_task(eggs.id)
    assert n.clear_done() == 1 and [t.text for t in n.tasks] == ["Buy milk"]
    assert not n.finish_task("nope")


def test_the_other_sides_changes_survive_our_save_and_unknown_fields_stay(tmp_path):
    ours = notes(tmp_path, notes=[{"id": "n-1", "title": "A", "body": "x", "pinned": True}], theme="dark")
    NotesStore(tmp_path / "notes.json").add_task("From the window", NOW)
    ours.add_note("From voice", NOW)
    data = json.loads((tmp_path / "notes.json").read_text(encoding="utf-8"))
    assert data["theme"] == "dark" and data["notes"][0]["pinned"] is True
    assert [t["text"] for t in data["tasks"]] == ["From the window"] and len(data["notes"]) == 2


def test_bad_entries_are_left_out(tmp_path):
    n = notes(tmp_path, notes=[{"title": "", "body": "  "}, "x", {"id": "n-1", "title": "Ok"}], tasks=[{"text": ""}, 3, {"id": "t-1", "text": "Fine", "done": "yes"}])
    assert [x.title for x in n.notes] == ["Ok"] and [(t.text, t.done) for t in n.tasks] == [("Fine", False)]


# ------------------------------------------------------------------ links to Spotify (the window reads these the same way)
@pytest.mark.parametrize("case", json.loads((FIXTURES / "spotify_links.json").read_text(encoding="utf-8")), ids=lambda c: c["in"][:50] or "empty")
def test_a_spotify_link_is_understood_the_way_the_window_understands_it(case):
    got = P.parse_link(case["in"])
    if "kind" in case:
        assert got == P.Link(case["kind"], case["id"])
    else:
        assert isinstance(got, str) and got, "a sentence saying why not"


def test_a_link_knows_its_address_and_its_page():
    link = P.parse_link("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=zzz")
    assert link.uri == "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M" and link.url == "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"


def test_the_reasons_a_link_is_refused_help():
    assert "Orbit plays your music on Spotify" in P.parse_link("https://music.apple.com/us/playlist/x/pl.abc")
    assert "short links" in P.parse_link("https://spotify.link/abc")
    assert "not a Spotify link" in P.parse_link("gym mix")
    assert "Paste a Spotify link" in P.parse_link("")


# ------------------------------------------------------------------ saved playlists
GYM = P.parse_link("spotify:playlist:37i9dQZF1DXcBWIGoYBM5M")
CHILL = P.parse_link("spotify:playlist:37i9dQZF1DX4WYpdgoIcn6")
ALBUM = P.parse_link("spotify:album:4aawyAB9vmqN3uQ7FjRGTy")


def music(tmp_path):
    return P.MusicStore(tmp_path / "music.json")


def test_a_saved_link_is_kept_once(tmp_path):
    m = music(tmp_path)
    a = m.add(GYM, "Gym Mix", NOW)
    b = m.add(GYM, "Another name", NOW)
    assert a.id == b.id and len(m.playlists) == 1
    again = music(tmp_path)
    assert [(p.kind, p.spotify_id, p.name) for p in again.playlists] == [("playlist", GYM.spotify_id, "Gym Mix")]


def test_a_spoken_name_finds_the_playlist_it_means(tmp_path):
    m = music(tmp_path)
    for link, name in ((GYM, "Gym Mix"), (CHILL, "Chill Vibes"), (ALBUM, "Gym Hero")):
        m.add(link, name)
    assert [p.name for p in m.find("gym")] == ["Gym Mix", "Gym Hero"]
    assert [p.name for p in m.find("my chill vibes playlist")] == ["Chill Vibes"]
    assert [p.name for p in m.find("gym mix")][0] == "Gym Mix", "the same words first"
    assert m.find("the playlist") == [] and m.find("") == [] and m.find("jazz") == []


def test_a_playlist_with_no_name_yet_is_never_matched_and_is_called_by_its_kind(tmp_path):
    m = music(tmp_path)
    p = m.add(GYM)
    assert m.find("gym") == [] and p.title == "your Spotify playlist" and m.unnamed() == [p]
    assert m.set_name(p.id, "  Gym   Mix ") and [x.name for x in m.find("gym")] == ["Gym Mix"]
    assert not m.set_name(p.id, "Other", only_if_unnamed=True), "a name you gave is not replaced by the one Spotify has"


def test_the_names_are_filled_in_from_spotify_and_a_failure_changes_nothing(tmp_path):
    m = music(tmp_path)
    m.add(GYM)
    m.add(CHILL, "Mine")
    assert P.resolve_names(m, fetch=lambda link: None) == 0 and m.unnamed()
    assert P.resolve_names(m, fetch=lambda link: "Gym Mix (from Spotify)") == 1
    assert [p.name for p in music(tmp_path).playlists] == ["Gym Mix (from Spotify)", "Mine"]


def test_spotifys_public_page_is_asked_for_the_title_and_nothing_about_you():
    seen = {}

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"title": "  Gym   Mix "}

    class Http:
        def get(self, url, params=None, timeout=None):
            seen.update(url=url, params=params)
            return Resp()

    assert P.fetch_title(GYM, http=Http()) == "Gym Mix"
    assert seen == {"url": P.OEMBED, "params": {"url": GYM.url}}

    class Broken:
        def get(self, *a, **k):
            raise OSError("offline")

    assert P.fetch_title(GYM, http=Broken()) is None


def test_bad_entries_in_the_music_file_are_left_out(tmp_path):
    (tmp_path / "music.json").write_text(json.dumps({"playlists": [{"kind": "playlist", "spotify_id": "short"}, {"kind": "genre", "spotify_id": GYM.spotify_id},
                                                                   "x", {"kind": "album", "spotify_id": ALBUM.spotify_id, "name": "Ok", "color": "red"}]}), encoding="utf-8")
    m = music(tmp_path)
    assert [p.name for p in m.playlists] == ["Ok"] and m.playlists[0].extra == {"color": "red"}


# ------------------------------------------------------------------ the example files the window writes (the Rust side reads the same ones)
def test_the_example_notes_file_is_read_as_the_window_wrote_it():
    n = NotesStore(FIXTURES / "notes_example.json")
    assert [(x.title, x.body) for x in n.notes][0] == ("Gym plan", "Monday legs\nWednesday push\nFriday pull")
    assert n.notes[0].extra == {"pinned": True}, "what this version does not know is kept to be written back"
    assert [(t.text, t.done) for t in n.tasks] == [("Buy milk", False), ("Buy eggs", True), ("Pay rent", False)]
    assert [x.title for x in n.recent_notes()] == ["Wifi", "Gym plan", "Ideas"]


def test_the_example_music_file_is_read_as_the_window_wrote_it():
    m = P.MusicStore(FIXTURES / "music_example.json")
    assert [(p.kind, p.name) for p in m.playlists] == [("playlist", "Gym Mix"), ("playlist", "Chill Vibes"), ("album", "")]
    assert m.playlists[1].extra == {"color": "teal"} and [p.id for p in m.unnamed()] == ["p-9a9a9a9a"]
    assert [p.name for p in m.find("my gym playlist")] == ["Gym Mix"]


def test_the_example_reminders_file_is_read_as_the_window_wrote_it():
    from orbit.reminders import ReminderStore

    r = ReminderStore(FIXTURES / "reminders_example.json")
    assert [(x.title, x.repeat, x.enabled) for x in r.reminders] == [("Call mom", "none", True), ("Take medicine", "daily", True), ("Water the plants", "weekly", False)]
    assert r.reminders[0].extra == {"note": "mine"}
    assert [h["title"] for h in r.history] == ["Stretch"] and r.seen == datetime(2026, 11, 22, 7, 0)
    assert [x.title for x in r.active()] == ["Call mom", "Take medicine"]
