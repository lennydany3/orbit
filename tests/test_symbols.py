"""Pictures on buttons: "click the search icon", "the magnifying glass", "the three dots", "the x button"."""
import pytest

from orbit.tools.os_layer import symbols, uia
from orbit.tools.os_layer.screen import Word, find_glyph
from orbit.tools.os_layer.uia import Control


# ---------- which picture is meant (pure) ----------
@pytest.mark.parametrize("phrase, key", [
    ("the search icon", "search"), ("search symbol", "search"), ("magnifying glass", "search"), ("the magnifier", "search"),
    ("click the x button", "close"), ("x icon", "close"), ("three dots", "more"), ("the 3 dots", "more"), ("kebab menu", "more"),
    ("hamburger menu", "menu"), ("the hamburger", "menu"), ("three lines", "menu"), ("settings icon", "settings"), ("gear", "settings"),
    ("the cog", "settings"), ("plus sign", "add"), ("plus icon", "add"), ("the heart", "like"), ("thumbs up", "like"),
    ("microphone icon", "voice"), ("the mic", "voice"), ("trash can", "trash"), ("bell icon", "bell"), ("home icon", "home"),
    ("refresh icon", "refresh"), ("play icon", "play"),
])
def test_a_picture_is_recognised_from_what_people_call_it(phrase, key):
    found = symbols.identify(phrase)
    assert found is not None and found.key == key


@pytest.mark.parametrize("phrase", ["search", "sign in", "sign out", "settings", "close", "menu", "go", "the first result", "subscribe", ""])
def test_an_ordinary_name_is_not_taken_for_a_picture(phrase):
    """"click search" means the control CALLED Search; only picture words, or "icon"/"symbol"/"button" next to a word, mean a picture."""
    assert symbols.identify(phrase) is None


def test_each_symbol_lists_the_names_it_usually_has():
    assert "search" in symbols.identify("search icon").names and "more options" in symbols.identify("three dots").names
    assert "settings" in symbols.identify("gear").names and "menu" in symbols.identify("hamburger").names


def test_picture_and_box_words_are_told_apart():
    assert symbols.wants_a_picture("the search icon") and symbols.wants_a_picture("go button") and not symbols.wants_a_picture("search")
    assert symbols.wants_a_field("the search box") and symbols.wants_a_field("address bar") and not symbols.wants_a_field("search icon")


# ---------- finding the control for a picture ----------
def page(*items):
    return [Control(name, kind, False, None) for name, kind in items]


def best(items, query, kind="any"):
    found = uia.find(items, query, kind)
    return (found[0].name, found[0].kind) if found else None


def test_the_search_icon_is_the_search_button_not_the_search_box():
    items = page(("Search", "text field"), ("Search", "button"), ("Search tools", "link"))
    assert best(items, "search icon") == ("Search", "button")


def test_the_search_box_is_the_text_field_not_the_button():
    items = page(("Search", "button"), ("Search", "text field"))
    assert best(items, "search box") == ("Search", "text field")
    assert best(items, "search", kind="field") == ("Search", "text field")


def test_a_field_request_never_returns_a_button():
    assert uia.find(page(("Search", "button"), ("Sign in", "link")), "search", kind="field") == []


def test_a_picture_is_found_under_the_name_the_app_gives_it():
    items = page(("Home", "link"), ("Clear", "button"), ("More options", "button"), ("Main menu", "button"), ("Settings", "link"))
    assert best(items, "x button") == ("Clear", "button")
    assert best(items, "three dots") == ("More options", "button")
    assert best(items, "hamburger menu") == ("Main menu", "button")
    assert best(items, "gear icon") == ("Settings", "link")


def test_a_magnifying_glass_finds_the_google_search_button_when_that_is_what_the_page_calls_it():
    assert best(page(("Google Search", "button"), ("I'm Feeling Lucky", "button")), "the magnifying glass") == ("Google Search", "button")


def test_a_picture_nobody_named_is_not_found_and_nothing_is_invented():
    assert uia.find(page(("Home", "link"), ("About", "link")), "heart icon") == []


def test_an_exact_name_still_wins_over_a_picture_alias():
    items = page(("Close", "button"), ("Clear", "button"), ("Cancel", "button"))
    assert best(items, "close") == ("Close", "button")


def test_icon_and_box_words_are_not_part_of_a_controls_name():
    assert uia.match_score("the search icon", "Search") > 0 and uia.match_score("search box", "Search") > 0
    assert uia.match_score("search icon", "Icon packs") == 0                    # the word icon alone is not a match


# ---------- symbols OCR reads as characters ----------
def words(*items):
    return [Word(t, x, y, 20, 24) for t, x, y in items]


def test_a_symbol_the_ocr_engine_reads_as_a_character_is_found():
    found = find_glyph(words(("Settings", 40, 300), ("×", 900, 12), ("Done", 40, 500)), symbols.identify("close icon").glyphs)
    assert found == (910, 24, "×")


def test_a_close_mark_is_the_one_in_the_top_right_corner():
    ws = words(("x", 100, 500), ("X", 880, 8), ("x", 400, 600))
    assert find_glyph(ws, symbols.identify("x button").glyphs)[:2] == (890, 20)


def test_ordinary_text_is_not_taken_for_a_symbol():
    assert find_glyph(words(("Exit", 10, 10), ("plus", 60, 10), ("-", 90, 10), (">", 120, 10)), symbols.identify("plus icon").glyphs) is None
    assert find_glyph(words(("-", 90, 10), (">", 120, 10)), symbols.identify("minus icon").glyphs) is None     # hyphens are everywhere in text


def test_a_giant_character_is_a_heading_not_an_icon():
    big = [Word("+", 10, 10, 200, 200)]
    assert find_glyph(big, symbols.identify("plus icon").glyphs) is None
