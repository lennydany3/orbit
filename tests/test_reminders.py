"""Reminders: the file the window edits, what is said when one is due, and understanding "remind me to ... in 10 minutes"."""
import json
import os
import time
from datetime import datetime, timedelta

import pytest

from orbit import reminders as R
from orbit import userdata

NOW = datetime(2026, 11, 22, 14, 30)            # a Sunday afternoon


def ask(text, now=NOW):
    return R.parse_request(text, now)


def planned(text, now=NOW):
    got = ask(text, now)
    assert isinstance(got, R.Request), (text, got)
    return got.title, userdata.to_text(got.due), got.repeat


# ------------------------------------------------------------------ understanding a request
@pytest.mark.parametrize("text, expected", [
    ("remind me to call mom at 5pm", ("Call mom", "2026-11-22T17:00", "none")),
    ("remind me in 10 minutes to stretch", ("Stretch", "2026-11-22T14:40", "none")),
    ("remind me to buy milk tomorrow at 9", ("Buy milk", "2026-11-23T09:00", "none")),
    ("remind me to call the dentist at 9 am tomorrow", ("Call the dentist", "2026-11-23T09:00", "none")),
    ("set a reminder for tomorrow at 5 to pay the bill", ("Pay the bill", "2026-11-23T17:00", "none")),
    ("remind me to submit the form on friday at 3", ("Submit the form", "2026-11-27T15:00", "none")),
    ("remind me to call dad in half an hour", ("Call dad", "2026-11-22T15:00", "none")),
    ("remind me to check the oven in 1 hour 30 minutes", ("Check the oven", "2026-11-22T16:00", "none")),
    ("remind me in a couple of minutes to turn off the stove", ("Turn off the stove", "2026-11-22T14:32", "none")),
    ("remind me tonight at 8 to call Priya", ("Call Priya", "2026-11-22T20:00", "none")),
    ("remind me on 25 december to buy gifts", ("Buy gifts", "2026-12-25T09:00", "none")),
    ("remind me at 17:45 to leave", ("Leave", "2026-11-22T17:45", "none")),
    ("remind me to buy 2 apples at 5pm", ("Buy 2 apples", "2026-11-22T17:00", "none")),
])
def test_a_reminder_is_understood(text, expected):
    assert planned(text) == expected


def test_a_time_with_no_am_or_pm_is_the_next_one_that_comes_round():
    assert planned("remind me at 5 to leave")[1] == "2026-11-22T17:00", "5 in the afternoon is still ahead"
    assert planned("remind me at 3 to leave")[1] == "2026-11-22T15:00"
    assert planned("remind me at 7 to leave", datetime(2026, 11, 22, 22, 0))[1] == "2026-11-23T07:00", "nothing left today: tomorrow morning"
    assert planned("remind me tomorrow at 5 to leave")[1] == "2026-11-23T17:00", "1 to 6 with a day is the afternoon"
    assert planned("remind me tomorrow at 8 to leave")[1] == "2026-11-23T08:00", "7 to 11 with a day is the morning"


def test_a_time_that_already_passed_today_means_tomorrow():
    assert planned("remind me to call mom at 2pm")[1] == "2026-11-23T14:00"


def test_every_says_how_often_and_starts_at_the_next_one():
    assert planned("remind me every day at 8 to take my medicine") == ("Take my medicine", "2026-11-23T08:00", "daily")
    assert planned("remind me to drink water every weekday morning") == ("Drink water", "2026-11-23T08:00", "weekdays")
    assert planned("remind me every monday at 10 to send the report") == ("Send the report", "2026-11-23T10:00", "weekly")
    assert planned("remind me to pay rent every month on the 1st")[2] == "monthly"


def test_a_timer_and_an_alarm_are_reminders_with_a_plain_name():
    assert planned("set a timer for 10 minutes") == ("Timer", "2026-11-22T14:40", "none")
    assert planned("set a timer for two hours") == ("Timer", "2026-11-22T16:30", "none")
    assert planned("set an alarm for 7 am") == ("Alarm", "2026-11-23T07:00", "none")
    assert planned("wake me up at 6:30") == ("Alarm", "2026-11-23T06:30", "none"), "an alarm is the morning"


def test_a_missing_time_is_asked_for_not_guessed():
    assert "When should I remind you" in ask("remind me to stretch")
    assert "For how long" in ask("set a timer")
    assert "already passed" in ask("remind me today at 1pm to eat")


def test_things_that_are_not_reminders_are_not_touched():
    for text in ("open spotify", "what time is it", "hello there", "play some music at 5", "I need a reminder", "volume 30"):
        assert ask(text) is None, text


# ------------------------------------------------------------------ saying a time
def test_times_are_said_like_a_person_says_them():
    assert R.when_text(NOW + timedelta(minutes=10), NOW) == "in 10 minutes"
    assert R.when_text(NOW + timedelta(minutes=1), NOW) == "in 1 minute"
    assert R.when_text(datetime(2026, 11, 22, 17, 0), NOW) == "today at 5:00 PM"
    assert R.when_text(datetime(2026, 11, 23, 9, 5), NOW) == "tomorrow at 9:05 AM"
    assert R.when_text(datetime(2026, 11, 26, 12, 0), NOW) == "Thursday at 12:00 PM"
    assert R.when_text(datetime(2026, 12, 25, 0, 0), NOW) == "25 Dec at 12:00 AM"
    assert R.spoken_when(datetime(2026, 11, 23, 8, 0), "daily", NOW) == "every day at 8:00 AM"
    assert R.spoken_when(datetime(2026, 11, 23, 10, 0), "weekly", NOW) == "every Monday at 10:00 AM"
    assert R.spoken_when(datetime(2026, 12, 1, 9, 0), "monthly", NOW) == "on the 1st of every month at 9:00 AM"


def test_what_orbit_says_when_one_is_due():
    one = R.Reminder("r-1", "Call mom", NOW)
    assert R.announcement(one) == "Reminder: Call mom."
    assert R.announcement(one, late=True) == "Reminder, a bit late: Call mom."
    assert R.announcement(R.Reminder("r-2", "Timer", NOW)) == "Your timer is up."
    assert R.announcement(R.Reminder("r-3", "Alarm", NOW)) == "Your alarm is going off."


# ------------------------------------------------------------------ the next time of a repeating one
def test_a_repeating_reminder_moves_to_the_next_time_after_now():
    due = datetime(2026, 11, 20, 8, 0)                                        # a Friday
    assert R.advance(due, "daily", NOW) == datetime(2026, 11, 23, 8, 0)
    assert R.advance(due, "weekdays", NOW) == datetime(2026, 11, 23, 8, 0), "Saturday and Sunday are skipped"
    assert R.advance(due, "weekly", NOW) == datetime(2026, 11, 27, 8, 0)
    assert R.advance(datetime(2026, 1, 31, 9, 0), "monthly", datetime(2026, 1, 31, 10, 0)) == datetime(2026, 2, 28, 9, 0), "a short month"
    assert R.advance(due, "none", NOW) == due


# ------------------------------------------------------------------ the file
def store(tmp_path, **raw):
    file = tmp_path / "reminders.json"
    if raw:
        file.write_text(json.dumps({"version": 1, **raw}), encoding="utf-8")
    return R.ReminderStore(file)


def test_no_file_is_no_reminders(tmp_path):
    s = store(tmp_path)
    assert s.reminders == [] and s.history == [] and s.seen is None and s.take_due(NOW) == []


def test_a_broken_file_is_read_as_empty_and_never_raises(tmp_path):
    (tmp_path / "reminders.json").write_text("{not json", encoding="utf-8")
    assert R.ReminderStore(tmp_path / "reminders.json").reminders == []
    (tmp_path / "reminders.json").write_text("[1, 2]", encoding="utf-8")
    assert R.ReminderStore(tmp_path / "reminders.json").reminders == []


def test_a_reminder_is_saved_whole_and_read_back(tmp_path):
    s = store(tmp_path)
    added = s.add("  Call   mom ", datetime(2026, 11, 22, 17, 0), "none", NOW)
    again = R.ReminderStore(tmp_path / "reminders.json")
    assert [(r.id, r.title, userdata.to_text(r.due), r.repeat, r.enabled) for r in again.reminders] == [(added.id, "Call mom", "2026-11-22T17:00", "none", True)]
    assert userdata.to_text(again.reminders[0].created) == "2026-11-22T14:30"
    assert [p.name for p in tmp_path.iterdir()] == ["reminders.json"], "no temporary file is left behind"


def test_what_the_file_has_that_this_version_does_not_know_is_written_back_untouched(tmp_path):
    s = store(tmp_path, reminders=[{"id": "r-1", "title": "A", "due": "2026-11-23T09:00", "note": "mine"}], theme="dark")
    s.add("B", datetime(2026, 11, 24, 9, 0))
    data = json.loads((tmp_path / "reminders.json").read_text(encoding="utf-8"))
    assert data["theme"] == "dark" and data["reminders"][0]["note"] == "mine" and len(data["reminders"]) == 2


def test_bad_entries_are_left_out_and_good_ones_kept(tmp_path):
    s = store(tmp_path, reminders=[{"title": "no time"}, {"due": "2026-11-23T09:00"}, "x", 4, {"id": "r-1", "title": "Fine", "due": "2026-11-23T09:00", "repeat": "yearly"}])
    assert [(r.title, r.repeat) for r in s.reminders] == [("Fine", "none")], "an unknown repeat is a one-off"


def test_a_change_from_the_other_side_is_not_lost_by_this_sides_next_save(tmp_path):
    ours = store(tmp_path)
    ours.add("First", datetime(2026, 11, 23, 9, 0))
    theirs = R.ReminderStore(tmp_path / "reminders.json")          # the window opens the same file...
    theirs.add("Second", datetime(2026, 11, 23, 10, 0))           # ...and adds one
    ours.add("Third", datetime(2026, 11, 23, 11, 0))              # we never reloaded, but our save reads fresh first
    assert [r.title for r in R.ReminderStore(tmp_path / "reminders.json").reminders] == ["First", "Second", "Third"]


def test_changed_notices_an_edit_made_elsewhere(tmp_path):
    s = store(tmp_path)
    assert not s.changed()
    other = R.ReminderStore(tmp_path / "reminders.json")
    other.add("X", datetime(2026, 11, 23, 9, 0))
    assert s.changed()
    s.reload()
    assert not s.changed() and len(s.reminders) == 1


def test_the_limit_is_said_not_ignored(tmp_path):
    s = store(tmp_path, reminders=[{"id": f"r-{i}", "title": f"T{i}", "due": "2026-11-23T09:00"} for i in range(R.MAX_REMINDERS)])
    with pytest.raises(ValueError, match="as many reminders"):
        s.add("One more", NOW)


def test_removing_and_finding(tmp_path):
    s = store(tmp_path)
    a, b = s.add("Call mom", datetime(2026, 11, 23, 9, 0)), s.add("Buy milk", datetime(2026, 11, 23, 10, 0))
    assert [r.id for r in s.find("the mom reminder")] == [a.id] and s.find("") == [] and s.find("zzz") == []
    assert s.remove({a.id}) == 1 and [r.id for r in s.reminders] == [b.id]
    assert s.remove({"nope"}) == 0


def test_active_ones_come_soonest_first_and_a_switched_off_one_is_not_active(tmp_path):
    s = store(tmp_path, reminders=[{"id": "a", "title": "Later", "due": "2026-11-25T09:00"}, {"id": "b", "title": "Sooner", "due": "2026-11-23T09:00"},
                                   {"id": "c", "title": "Off", "due": "2026-11-23T08:00", "enabled": False}])
    assert [r.title for r in s.active()] == ["Sooner", "Later"]


# ------------------------------------------------------------------ firing
def test_a_one_off_that_is_due_is_taken_once_and_kept_in_the_history(tmp_path):
    s = store(tmp_path, reminders=[{"id": "r-1", "title": "Call mom", "due": "2026-11-22T14:00"}, {"id": "r-2", "title": "Later", "due": "2026-11-22T18:00"}])
    due = s.take_due(datetime(2026, 11, 22, 14, 0, 30))
    assert [(r.title, late) for r, late in due] == [("Call mom", False)]
    assert [r.title for r in s.reminders] == ["Later"]
    assert s.history[0]["title"] == "Call mom" and s.history[0]["at"] == "2026-11-22T14:00:30"
    assert s.take_due(datetime(2026, 11, 22, 14, 5)) == [], "it does not fire twice"
    assert R.ReminderStore(tmp_path / "reminders.json").history[0]["title"] == "Call mom", "and the window can see it fired"


def test_a_repeating_one_fires_and_moves_on(tmp_path):
    s = store(tmp_path, reminders=[{"id": "r-1", "title": "Meds", "due": "2026-11-22T08:00", "repeat": "daily"}])
    got = s.take_due(datetime(2026, 11, 22, 8, 0, 5))
    assert [r.title for r, _ in got] == ["Meds"]
    assert userdata.to_text(s.reminders[0].due) == "2026-11-23T08:00" and s.reminders[0].enabled


def test_one_that_came_due_while_orbit_was_off_is_still_said_but_called_late(tmp_path):
    s = store(tmp_path, reminders=[{"id": "r-1", "title": "Call mom", "due": "2026-11-22T12:00"}])
    [(r, late)] = s.take_due(datetime(2026, 11, 22, 14, 0))
    assert late is True and R.announcement(r, late) == "Reminder, a bit late: Call mom."


def test_one_that_is_very_old_is_put_away_quietly(tmp_path):
    s = store(tmp_path, reminders=[{"id": "r-1", "title": "Old", "due": "2026-11-20T12:00"}, {"id": "r-2", "title": "Old daily", "due": "2026-11-20T12:00", "repeat": "daily"}])
    assert s.take_due(NOW) == []
    assert [(r.title, userdata.to_text(r.due)) for r in s.reminders] == [("Old daily", "2026-11-23T12:00")]


def test_a_switched_off_reminder_never_fires(tmp_path):
    s = store(tmp_path, reminders=[{"id": "r-1", "title": "Off", "due": "2026-11-22T14:00", "enabled": False}])
    assert s.take_due(NOW) == [] and len(s.reminders) == 1


def test_the_history_is_kept_short(tmp_path):
    s = store(tmp_path, reminders=[{"id": f"r-{i}", "title": f"T{i}", "due": "2026-11-22T14:00"} for i in range(R.MAX_HISTORY + 5)])
    s.take_due(NOW)
    assert len(s.history) == R.MAX_HISTORY


def test_times_are_written_without_seconds_unless_there_are_some():
    assert userdata.to_text(datetime(2026, 11, 22, 17, 0)) == "2026-11-22T17:00"
    assert userdata.to_text(datetime(2026, 11, 22, 17, 0, 30)) == "2026-11-22T17:00:30"
    assert userdata.from_text("2026-11-22T17:00:30") == datetime(2026, 11, 22, 17, 0, 30)
    assert userdata.from_text("2026-11-22 17:00") is None and userdata.from_text(5) is None


# ------------------------------------------------------------------ what it was asked as (it decides how it rings)
def test_a_reminder_remembers_whether_it_was_a_timer_an_alarm_or_a_reminder(tmp_path):
    store = R.ReminderStore(tmp_path / "r.json")
    store.add("Pasta", NOW, "none", NOW, "timer")
    store.add("Wake up", NOW, "none", NOW, "alarm")
    store.add("Stretch", NOW, "none", NOW)
    store.add("Odd", NOW, "none", NOW, "whatever")
    assert [r.kind for r in store.reminders] == ["timer", "alarm", "reminder", "reminder"]
    saved = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))["reminders"]
    assert [r.get("kind") for r in saved] == ["timer", "alarm", None, None], "a plain reminder looks exactly as before: no new field"
    assert [r.kind for r in R.ReminderStore(tmp_path / "r.json").reminders] == ["timer", "alarm", "reminder", "reminder"]


def test_an_old_file_with_no_kind_or_a_strange_one_still_loads(tmp_path):
    (tmp_path / "r.json").write_text(json.dumps({"version": 1, "reminders": [
        {"id": "r-1", "title": "A", "due": "2026-11-23T09:00"}, {"id": "r-2", "title": "B", "due": "2026-11-23T09:00", "kind": 7},
        {"id": "r-3", "title": "C", "due": "2026-11-23T09:00", "kind": "sing"}]}), encoding="utf-8")
    assert [r.kind for r in R.ReminderStore(tmp_path / "r.json").reminders] == ["reminder"] * 3


def test_a_repeating_timer_keeps_its_kind_when_it_moves_on(tmp_path):
    store = R.ReminderStore(tmp_path / "r.json")
    store.add("Medicine", NOW, "daily", NOW, "alarm")
    (taken, _), = store.take_due(NOW + timedelta(minutes=1))
    assert taken.kind == "alarm" and [r.kind for r in store.reminders] == ["alarm"]


def test_the_asked_kind_comes_out_of_the_sentence():
    assert [ask(t).kind for t in ("set a timer for 5 minutes", "put a timer for 30 seconds", "start a countdown for 2 minutes", "wake me up at 6:30",
                                  "set an alarm for 7am", "remind me in 10 minutes", "set a remainder in one minute")] == \
        ["timer", "timer", "timer", "alarm", "alarm", "reminder", "reminder"]


def test_what_is_said_when_one_with_no_title_comes_due(tmp_path):
    store = R.ReminderStore(tmp_path / "r.json")
    plain = store.add("Reminder", NOW, "none", NOW)
    assert R.announcement(plain) == "This is your reminder." and R.announcement(plain, late=True) == "This is your reminder, a bit late."
    assert R.announcement(store.add("Reminder", NOW, "none", NOW, "timer")) == "Your timer is up."
    assert R.announcement(store.add("Reminder", NOW, "none", NOW, "alarm")) == "Your alarm is going off."
    assert R.announcement(store.add("Stretch", NOW, "none", NOW)) == "Reminder: Stretch."
    assert R.announcement(store.add("Pasta", NOW, "none", NOW, "timer")) == "Timer: Pasta."
    assert R.announcement(store.add("Wake the kids", NOW, "none", NOW, "alarm"), late=True) == "Alarm, a bit late: Wake the kids."


@pytest.mark.parametrize("text, seconds", [
    ("snooze for ten minutes", 600), ("in 30 seconds", 30), ("give me five more minutes", 300), ("remind me again in an hour", 3600),
    ("again in 1 hour 30 minutes", 5400), ("two minutes", 120), ("snooze", None), ("not now", None), ("thanks", None),
])
def test_a_length_of_time_said_anywhere_in_a_sentence(text, seconds):
    assert R.spoken_length(text) == seconds
