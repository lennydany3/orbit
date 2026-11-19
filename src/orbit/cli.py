"""Text REPL. Ctrl+C while a request runs stops it; Ctrl+C at the prompt (or 'quit') exits."""
import argparse
import sys

from . import config
from .agent.loop import Agent
from .agent.router import Router
from .agent.safety import Safety
from .llm.base import LLMError
from .llm.factory import make_llm
from .logging_setup import ActionLog, setup_logging
from .tools.api_layer import register_api_tools, spotify_auth
from .tools.base import Registry
from .tools.browser_layer import register_browser_tools
from .tools.browser_layer.session import session as browser_session
from .tools.fake import register_fake_tools
from .tools.os_layer import register_os_tools

HELP = """Commands: help | tools | clear (forget the conversation) | auth spotify | quit
Anything else is a request. Fast rules (no model): open spotify | open github | play lofi on youtube | fullscreen | volume 30 | volume up | mute | pause | play | next | previous
Anything else goes to the model:  switch to chrome | what's playing? | what windows are open?
(start with --fake-tools to also get the echo/danger test tools)"""


def run_auth(service: str) -> int:
    """`python -m orbit auth spotify`: one-time login. The browser opens; approve there."""
    if service != "spotify":
        print(f"Unknown service '{service}'. Available: spotify")
        return 2
    client_id = spotify_auth.client_id_from_env()
    if not client_id:
        print(f"Set {spotify_auth.CLIENT_ID_ENV} first (your Spotify app's Client ID), then open a new terminal.")
        return 2
    print("Opening Spotify in your browser. Approve the request there (waiting up to 3 minutes)...")
    try:
        spotify_auth.authorize(client_id)
    except spotify_auth.AuthError as e:
        print(f"Login failed: {e}")
        return 1
    print("Connected. The login is stored in the Windows Credential Manager.")
    return 0


def spotify_status(registry: Registry) -> str:
    """One line at startup, so a missing Spotify setup is visible instead of silently disabling the tools."""
    if any(t.layer == "api" for t in registry.all()):
        if spotify_auth.is_logged_in():
            return "Spotify: connected."
        return "Spotify: not logged in yet. Run:  auth spotify"
    return f"Spotify: OFF ({spotify_auth.CLIENT_ID_ENV} is not set), so Spotify requests will not work."


def build_agent(use_llm: bool = True, model: str = config.MODEL, fake_tools: bool = False, llm_provider: str | None = None,
                friend: bool = True) -> Agent:
    logger = setup_logging()
    log = ActionLog(logger=logger)
    registry = Registry()
    register_api_tools(registry)   # empty until Step 4
    register_os_tools(registry)
    register_browser_tools(registry)
    if fake_tools:
        register_fake_tools(registry)  # test tools from Step 1; off by default so the model isn't tempted to use them
    llm, description = make_llm(llm_provider, ollama_model=model) if use_llm else (None, "rules only")
    chat_llm = llm.with_temperature(config.CHAT_TEMPERATURE) if hasattr(llm, "with_temperature") else llm
    agent = Agent(llm, registry, Router(log), Safety(), log, chat_llm=chat_llm, friend_mode=friend and config.FRIEND_MODE)
    agent.llm_description = description   # shown in the start-up banner
    return agent


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    finally:
        browser_session.close()  # close the Orbit browser window when Orbit exits


def _main(argv: list[str] | None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="orbit", description=f"{config.APP_NAME}: local desktop assistant")
    parser.add_argument("request", nargs="*", help="run one request and exit (otherwise start the REPL)")
    parser.add_argument("--no-llm", action="store_true", help="rules only, never call the model")
    parser.add_argument("--fake-tools", action="store_true", help="also register the Step 1 test tools")
    parser.add_argument("--browser", choices=["brave", "chrome"], default=config.BROWSER,
                        help=f"browser Orbit opens for websites (default {config.BROWSER})")
    parser.add_argument("--browser-mode", choices=["mine", "orbit"], default=config.BROWSER_MODE,
                        help="mine = open sites in your own Brave profile; orbit = Orbit's separate automated window")
    parser.add_argument("--model", default=config.MODEL, help=f"Ollama model, when the local model is used (default {config.MODEL})")
    parser.add_argument("--llm", choices=["auto", "groq", "ollama"], default=None,
                        help="language model: groq = cloud (needs GROQ_API_KEY), ollama = local, auto = groq if a key is set (default)")
    parser.add_argument("--no-friend", action="store_true", help="PC commands only: no friendly chat (same as ORBIT_FRIEND=0)")
    parser.add_argument("--voice", action="store_true", help="talk to Orbit: hold Right Ctrl or say the wake word (all local)")
    parser.add_argument("--mic", default=None, help="with --voice: microphone index or part of its name (default: ORBIT_MIC / Windows default)")
    parser.add_argument("--no-wake", action="store_true", help="with --voice: hold-to-talk only, no wake word")
    parser.add_argument("--no-follow-up", action="store_true", help="with --voice: do not listen for a follow-up after a reply")
    args = parser.parse_args(argv)

    if len(args.request) == 2 and args.request[0].lower() == "auth":
        return run_auth(args.request[1].lower())

    browser_session.use(args.browser)
    config.BROWSER_MODE = args.browser_mode
    try:
        agent = build_agent(use_llm=not args.no_llm, model=args.model, fake_tools=args.fake_tools, llm_provider=args.llm,
                            friend=not args.no_friend)
    except LLMError as e:
        print(f"Cannot start the language model: {e}")
        return 2

    if args.request:
        print(agent.handle(" ".join(args.request)))
        return 0

    mode = "rules only" if args.no_llm else f"model {agent.llm_description}"
    print(f"{config.APP_NAME} ready ({mode}). Type 'help' for hints, 'quit' to exit. Ctrl+C stops a running request.")
    print("  " + spotify_status(agent.registry))
    if args.voice:
        # Voice never replaces the REPL: if it cannot start, or the kill switch stops it, you carry on in text mode.
        from .voice import config as voice_config
        from .voice.loop import run_voice

        result = run_voice(agent, device=args.mic or voice_config.MIC_DEVICE, wake_enabled=not args.no_wake,
                           follow_up=False if args.no_follow_up else None)
        if result in ("quit", "timeout"):
            return 0
        print("Voice is off. Text mode: type a request ('help' for hints, 'quit' to exit).")
    while True:
        try:
            text = input(f"{config.APP_NAME}> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not text:
            continue
        if text.lower() in ("quit", "exit"):
            return 0
        if text.lower().startswith("auth "):
            run_auth(text.split(None, 1)[1].strip().lower())
            continue
        if text.lower() in ("clear", "forget"):
            agent.forget()
            print("  Conversation memory cleared.")
            continue
        if text.lower() == "help":
            print(HELP)
            continue
        if text.lower() == "tools":
            for t in agent.registry.exposed():
                alts = [a.layer for a in agent.registry.alternatives(t)]
                print(f"  {t.signature()}  layer={'>'.join(alts)} risk={t.risk}  - {t.description}")
            continue
        try:
            print(agent.handle(text))
        except KeyboardInterrupt:
            print("\n  [stopped] request cancelled by Ctrl+C")


if __name__ == "__main__":
    raise SystemExit(main())
