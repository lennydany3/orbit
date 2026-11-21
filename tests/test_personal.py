"""Reminders, notes, tasks and saved playlists said in plain words: answered without the language model, and nothing else is touched."""
from datetime import datetime, timedelta

import pytest

from orbit.agent.loop import Agent
from orbit.agent.personal import Personal
from orbit.agent.router import Router
from orbit.agent.safety import Safety
from orbit.notes import NotesStore
from orbit.playlists import Link, MusicStore
from orbit.reminders import ReminderStore
from orbit.tools.base import Registry, Tool, ToolResult
from orbit.voice.loop import describe_action

NOW = datetime(2026, 11, 22, 14, 30)
GYM = Link("playlist", "37i9dQZF1DXcBWIGoYBM5M")


class PlayParams:
    pass


class Played:
    """A stand-in agent: records the Spotify calls and answers the confirmation question."""

    def __init__(self, ok=True, yes=True):
        self.calls, self.ok, self.questions = [], ok, []
        self.safety = type("S", (), {"confirm": lambda s, q: (self.questions.append(q), yes)[1]})()

    def execute(self, name, args):
        self.calls.append((name, args))
        return ToolResult.success(f"playing {args['label']} on this PC") if self.ok else ToolResult.failure("Spotify is not logged in")


@pytest.fixture
def me(tmp_path):
    p = Personal(ReminderStore(tmp_path / "r.json"), NotesStore(tmp_path / "n.json"), MusicStore(tmp_path / "m.json"), clock=lambda: NOW)
    p.music.add(GYM, "Gym Mix")
    return p


def say(me, text, agent=None):
    reply = me.handle(text, agent or Played())
    return None if reply is None else reply.text


# ------------------------------------------------------------------ reminders
def test_a_reminder_is_set_and_confirmed_in_plain_words(me):
    assert say(me, "remind me to call mom at 5pm") == "Okay, I'll remind you to call mom today at 5:00 PM."
    assert say(me, "Hey Orbit, please remind me in 10 minutes to stretch.") == "Okay, I'll remind you to stretch in 10 minutes."
    assert [r.title for r in me.reminders.active()] == ["Stretch", "Call mom"]


def test_a_timer_and_an_alarm(me):
    assert say(me, "set a timer for 5 minutes") == "Okay, a timer for 5 minutes."
    assert say(me, "wake me up at 6:30") == "Okay, I set an alarm for tomorrow at 6:30 AM."


def test_a_timer_is_said_back_in_the_length_it_was_asked_for(me):
    """The log: "30 seconds" was answered as "1 minute", and "put a timer for 30 minutes" was not understood at all."""
    assert say(me, "Can you set a timer for 30 seconds?") == "Okay, a timer for 30 seconds."
    assert say(me, "Can you put a timer for 30 minutes?") == "Okay, a timer for 30 minutes."
    assert say(me, "start a timer for 45 seconds") == "Okay, a timer for 45 seconds."
    assert say(me, "set a timer for 1 minute 30 seconds") == "Okay, a timer for 1 minute 30 seconds."
    assert say(me, "set a countdown for 2 hours") == "Okay, a timer for 2 hours."
    assert [r.kind for r in me.reminders.reminders] == ["timer"] * 5
    due = sorted(r.due for r in me.reminders.reminders)[0]
    assert (due - NOW).total_seconds() == 30, "and it is due in exactly the 30 seconds"


def test_a_reminder_with_nothing_to_be_reminded_of_is_not_called_reminder_to_reminder(me):
    assert say(me, "Set a reminder in one minute.") == "Okay, I'll remind you in 1 minute."
    assert say(me, "remind me in 20 seconds") == "Okay, I'll remind you in 20 seconds."
    assert say(me, "set a reminder to stretch in 10 minutes") == "Okay, I'll remind you to stretch in 10 minutes."


def test_remainder_is_what_speech_to_text_hears_for_reminder(me):
    assert say(me, "set a remainder in one minute") == "Okay, I'll remind you in 1 minute."
    assert say(me, "I want a remainder for 5 minutes to stretch").endswith("to stretch in 5 minutes.")
    assert say(me, "cancel the remainder about stretch") == "Okay, I cancelled the reminder: Stretch."
    assert say(me, "can you cancel the remainder?") == "Okay, I cancelled the reminder."
    assert me.reminders.reminders == []


def test_a_request_with_no_time_is_answered_with_what_is_missing_and_saves_nothing(me):
    reply = me.handle("remind me to stretch", Played())
    assert "When should I remind you" in reply.text and reply.ok is False and me.reminders.reminders == []


def test_the_reminders_are_listed_soonest_first(me):
    assert say(me, "what are my reminders") == "You have no reminders."
    say(me, "remind me to call mom at 5pm")
    say(me, "remind me to stretch in 10 minutes")
    assert say(me, "what are my reminders") == "You have 2 reminders: Stretch, in 10 minutes and Call mom, today at 5:00 PM."
    assert say(me, "do I have any reminders today") == say(me, "list my reminders")


def test_a_reminder_is_cancelled_by_what_it_is_about(me):
    say(me, "remind me to call mom at 5pm")
    say(me, "remind me to buy milk tomorrow at 9")
    assert say(me, "cancel the reminder about mom") == "Okay, I cancelled the reminder: Call mom."
    assert [r.title for r in me.reminders.reminders] == ["Buy milk"]
    assert "couldn't find a reminder about dentist" in say(me, "delete my dentist reminder")


def test_cancelling_with_no_name_works_only_when_there_is_one_to_mean(me):
    assert say(me, "cancel my reminders") == "You have no reminders to cancel."
    say(me, "remind me to call mom at 5pm")
    assert say(me, "cancel the reminder") == "Okay, I cancelled the reminder: Call mom."
    say(me, "remind me to call mom at 5pm")
    say(me, "remind me to buy milk tomorrow at 9")
    assert "Say which one" in say(me, "cancel the reminder")
    assert len(me.reminders.reminders) == 2


def test_cancelling_everything_asks_first_in_the_words_orbit_speaks(me):
    say(me, "remind me to call mom at 5pm")
    say(me, "remind me to buy milk tomorrow at 9")
    declined = Played(yes=False)
    assert say(me, "cancel all my reminders", declined) == "Okay, I kept your reminders."
    assert len(me.reminders.reminders) == 2
    assert describe_action(declined.questions[0]) == "delete all 2 of your reminders"
    assert say(me, "delete all my reminders", Played(yes=True)) == "Okay, I deleted all 2 of your reminders."
    assert me.reminders.reminders == []


# ------------------------------------------------------------------ tasks
def test_tasks_are_added_listed_and_finished(me):
    assert say(me, "add milk to my tasks") == "Okay, I added Milk to your tasks. You have 1 task to do."
    assert say(me, "add a task: buy eggs") == "Okay, I added Buy eggs to your tasks. You have 2 tasks to do."
    assert say(me, "put call the bank on my to do list").startswith("Okay, I added Call the bank")
    assert say(me, "what's on my to do list") == "You have 3 tasks to do: Milk, Buy eggs and Call the bank."
    assert say(me, "mark milk as done") == "Okay, I marked Milk as done."
    assert say(me, "tick off buy eggs") == "Okay, I marked Buy eggs as done."
    assert say(me, "how many tasks do I have") == "You have 1 task to do: Call the bank."
    assert say(me, "clear my completed tasks") == "Okay, I cleared 2 finished tasks."
    assert [t.text for t in me.notes.tasks] == ["Call the bank"]


def test_an_empty_task_is_asked_for(me):
    assert "What should the task be" in say(me, "add a task")


def test_i_finished_something_is_only_a_task_when_it_names_one(me):
    say(me, "add buy eggs to my tasks")
    assert say(me, "I finished the eggs") == "Okay, I marked Buy eggs as done."
    assert say(me, "I finished my exam") is None, "that is chat"
    assert say(me, "mark homework as done") == "I couldn't find a task called homework."


def test_checking_something_else_is_not_a_task(me):
    say(me, "add buy eggs to my tasks")
    for text in ("check the weather", "check my email", "mark the spot", "set the volume to 30", "complete the form"):
        assert say(me, text) is None, text


# ------------------------------------------------------------------ named lists (the shopping list)
def test_several_things_go_onto_a_named_list_in_one_sentence(me):
    """The log: "add tomato, pasta and milkshake to the shopping list" was answered by chat, and nothing was kept."""
    assert say(me, "Can you add tomato, pasta, and milkshake in the shopping list?") == "Okay, I added Tomato, Pasta and Milkshake to your shopping list."
    assert [(t.text, t.list) for t in me.notes.tasks] == [("Tomato", "shopping"), ("Pasta", "shopping"), ("Milkshake", "shopping")]
    assert say(me, "Can you tell me what are my shopping lists?") == "On your shopping list you have Tomato, Pasta and Milkshake."
    assert say(me, "what's on my shopping list") == say(me, "read me the shopping list") == say(me, "show my shopping list")


def test_groceries_and_grocery_list_are_the_shopping_list(me):
    say(me, "add eggs and bread to the grocery list")
    say(me, "put rice on my groceries")
    assert [t.text for t in me.notes.tasks if t.list == "shopping"] == ["Eggs", "Bread", "Rice"]
    assert say(me, "what's on my groceries") == "On your shopping list you have Eggs, Bread and Rice."


def test_any_list_can_be_named_and_the_plain_tasks_are_left_alone(me):
    assert say(me, "add toothbrush to my packing list") == "Okay, I added Toothbrush to your packing list."
    assert say(me, "put sunscreen on the packing list") == "Okay, I added Sunscreen to your packing list. It has 2 things on it now."
    say(me, "add call the bank to my tasks")
    assert say(me, "what's on my to do list") == "You have 1 task to do: Call the bank."
    assert say(me, "what lists do I have") == "You have 2 lists: tasks with 1 thing and packing with 2 things."


def test_a_plain_task_with_and_in_it_stays_one_task(me):
    say(me, "add a task: call mom and dad")
    say(me, "add buy bread and eggs to my tasks")
    assert [t.text for t in me.notes.tasks] == ["Call mom and dad", "Buy bread and eggs"]


def test_things_are_removed_from_and_ticked_off_a_list(me):
    say(me, "add tomato, pasta and milk to my shopping list")
    assert say(me, "remove pasta from my shopping list") == "Okay, I removed Pasta from your shopping list."
    assert say(me, "take eggs off my shopping list") == "I couldn't find Eggs on your shopping list."
    assert say(me, "mark tomato as done") == "Okay, I marked Tomato as done on your shopping list."
    assert say(me, "what's on my shopping list") == "On your shopping list you have Milk."


def test_a_list_is_cleared_only_after_asking(me):
    say(me, "add tomato, pasta and milk to my shopping list")
    say(me, "add call the bank to my tasks")
    declined = Played(yes=False)
    assert say(me, "clear my shopping list", declined) == "Okay, I kept your shopping list."
    assert describe_action(declined.questions[0]) == "delete everything on your shopping list (3 items)"
    assert len(me.notes.tasks) == 4
    assert say(me, "empty my shopping list", Played(yes=True)) == "Okay, I cleared your shopping list."
    assert [t.text for t in me.notes.tasks] == ["Call the bank"], "only that list went"
    assert say(me, "clear my shopping list") == "Your shopping list is already empty."


def test_an_empty_list_is_said_plainly(me):
    assert say(me, "what's on my shopping list") == "Your shopping list is empty."
    assert say(me, "what lists do I have") == "You have no lists with anything on them."


# ------------------------------------------------------------------ notes
def test_a_note_is_saved_and_read_back(me):
    assert say(me, "note down the wifi is on the router") == "Okay, I saved a note: The wifi is on the router."
    assert say(me, "take a note that I parked on level 3") == "Okay, I saved a note: I parked on level 3."
    assert say(me, "what notes do I have") == "You have 2 notes. The latest: I parked on level 3 and The wifi is on the router."
    assert say(me, "read my last note").startswith("Your latest note is ")
    assert [n.title for n in me.notes.notes] == ["The wifi is on the router", "I parked on level 3"]


def test_a_note_with_nothing_in_it_is_asked_for(me):
    reply = me.handle("take a note", Played())
    assert "What should I note down" in reply.text and me.notes.notes == []
    assert say(me, "what notes do I have") == "You have no notes yet."


# ------------------------------------------------------------------ playlists
def test_a_saved_playlist_is_played_by_the_name_you_gave_it(me):
    agent = Played()
    assert say(me, "play my gym playlist", agent) == "Playing Gym Mix on this PC."
    assert agent.calls == [("api_spotify_play_saved", {"uri": "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M", "label": "Gym Mix"})]
    assert say(me, "put on gym mix on spotify", agent).startswith("Playing Gym Mix")


def test_a_failure_to_play_is_said_and_marked_as_failed(me):
    reply = me.handle("play my gym playlist", Played(ok=False))
    assert reply.ok is False and reply.text == "Failed: Spotify is not logged in"


def test_anything_that_is_not_one_of_your_playlists_is_left_to_the_normal_search(me):
    agent = Played()
    for text in ("play lofi on spotify", "play some jazz", "play the next song", "play", "pause"):
        assert say(me, text, agent) is None, text
    assert agent.calls == []


def test_the_playlists_can_be_listed_and_played_by_id(me):
    assert say(me, "what playlists do I have") == "You have 1 saved playlist: Gym Mix."
    agent = Played()
    assert me.play_by_id(me.music.playlists[0].id, agent).text == "Playing Gym Mix on this PC."
    assert me.play_by_id("p-gone", agent).ok is False and len(agent.calls) == 1


# ------------------------------------------------------------------ through the agent
def make_agent(me, **kw):
    registry = Registry()
    log = lambda kind, msg: None  # noqa: E731
    return Agent(None, registry, Router(log), Safety(confirm=lambda q: False), log, friend_mode=False, personal=me, **kw)


def test_the_agent_answers_these_before_the_rules_and_the_model(me):
    agent = make_agent(me)                                          # no model at all: only the personal handler can answer
    assert agent.handle("remind me to call mom at 5pm") == "Okay, I'll remind you to call mom today at 5:00 PM."
    assert agent.last_kind == "action" and agent.last_failed is False
    assert agent.handle("remind me to stretch").startswith("When should I remind you") and agent.last_failed is True
    assert "No rule matches that" in agent.handle("open the garage door"), "anything else goes on as before"


def test_a_sentence_with_several_parts_is_not_split_into_separate_requests(me):
    agent = make_agent(me)
    agent.handle("remind me to call mom and buy milk at 5pm")
    assert [r.title for r in me.reminders.reminders] == ["Call mom and buy milk"]


def test_the_play_button_runs_through_the_agent(me):
    class Spotify:
        calls = []

    registry = Registry()
    seen = []
    registry.register(Tool("api_spotify_play_saved", "x", type("P", (__import__("pydantic").BaseModel,), {"__annotations__": {"uri": str, "label": str}}),
                           "api", "low", lambda p: (seen.append((p.uri, p.label)), ToolResult.success(f"playing {p.label}"))[1],
                           model_visible=False))
    log = lambda kind, msg: None  # noqa: E731
    agent = Agent(None, registry, Router(log), Safety(confirm=lambda q: False), log, friend_mode=False, personal=me)
    assert agent.play_saved(me.music.playlists[0].id) == "Playing Gym Mix."
    assert seen == [("spotify:playlist:37i9dQZF1DXcBWIGoYBM5M", "Gym Mix")]
    assert agent.play_saved("p-gone") == "That playlist is not saved any more." and agent.last_failed


def test_a_saved_playlist_says_so_when_spotify_is_not_set_up():
    class NoSpotify:
        registry = Registry()                                       # no api_spotify_play_saved tool: SPOTIFY_CLIENT_ID is not set

        def execute(self, name, args):
            raise AssertionError("it must not try")

    playlist = type("P", (), {"link": GYM, "title": "Gym Mix"})()
    reply = Personal._play(playlist, NoSpotify())
    assert reply.ok is False and "Spotify is not set up" in reply.text


# ------------------------------------------------------------------ snoozing what just rang (after the talk key silenced it)
def test_snooze_rings_again_what_rang_last(me):
    from datetime import timedelta

    say(me, "set a timer for 30 seconds")
    say(me, "remind me to stretch in 40 seconds")
    me.clock = lambda: NOW + timedelta(seconds=60)
    assert [r.title for r, _ in me.reminders.take_due(NOW + timedelta(seconds=60))] == ["Timer", "Stretch"]
    assert say(me, "snooze") == "Okay, I'll ring again in 5 minutes."
    assert sorted((r.title, r.kind) for r in me.reminders.reminders) == [("Stretch", "reminder"), ("Timer", "timer")]
    assert all(r.due == NOW + timedelta(seconds=360) for r in me.reminders.reminders)
    me.reminders.take_due(NOW + timedelta(seconds=400))
    me.clock = lambda: NOW + timedelta(seconds=401)
    assert say(me, "remind me again in 10 minutes") == "Okay, I'll ring again in 10 minutes."
    assert say(me, "five more minutes") == "Okay, I'll ring again in 5 minutes."


def test_there_is_nothing_to_snooze_unless_something_rang_lately(me):
    assert say(me, "snooze") == "There is nothing to snooze: nothing rang just now."
    say(me, "set a timer for 30 seconds")
    me.reminders.take_due(NOW + timedelta(seconds=40))
    me.clock = lambda: NOW + timedelta(hours=2)
    assert say(me, "snooze it") == "There is nothing to snooze: nothing rang just now."


def test_a_timer_and_an_alarm_are_remembered_as_what_they_were_asked_as(me):
    say(me, "set a timer for 30 seconds")
    say(me, "wake me up at 6:30")
    say(me, "remind me to stretch in 10 minutes")
    assert sorted(r.kind for r in me.reminders.reminders) == ["alarm", "reminder", "timer"]
    me.reminders.reload()
    assert sorted(r.kind for r in me.reminders.reminders) == ["alarm", "reminder", "timer"], "and it is in the file"
