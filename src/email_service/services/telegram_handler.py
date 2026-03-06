"""
Telegram command router.
Parses /slash commands and handles natural language via LLM intent classification.
All handler logic lives in cmd_*.py modules
"""

import logging
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from email_service.services import llm, chat_memory, telegram_notifier
from email_service.services.utils import parse_json

from email_service.services.cmd_import import (
    handle_import,
    import_start,
    import_pause,
    import_resume,
    import_status,
    import_history,
    account_info,
)

from email_service.services.cmd_email import (
    search,
    recent,
    show_email,
    delete_email,
    block_sender,
    unsubscribe,
    grammar,
    ask,
    chitchat,
)

from email_service.services.cmd_draft import (
    handle_draft,
    draft_reply,
    draft_new,
    send_email,
    handle_schedule,
    schedule_list,
    schedule_cancel,
)

from email_service.services.cmd_campaign import (
    handle_campaign,
    campaign_create,
    campaign_load,
    campaign_personalize,
    campaign_preview,
    campaign_results,
    campaign_start,
    campaign_pause,
    campaign_resume,
)

from email_service.services import campaign_engine

logger = logging.getLogger(__name__)

_template_dir = Path(__file__).parent.parent / "templates"
_env = Environment(loader=FileSystemLoader(str(_template_dir)))
_classify_template = _env.get_template("classify_intent.j2")

HELP_TEXT = """Available commands:

/search (query)
/ask (question)
/recent [count]
/show (number)
/delete (number)
/block (number)
/unsubscribe (number)
/grammar (text)
/draft reply (email_id) (instructions)
/draft new (recipient) (instructions)
/send [draft_id]
/send [draft_id] at HH:MM
/send [draft_id] in 2h30m
/schedule list
/schedule cancel (draft_id)
/import start (account) [count|all]
/import pause (account)
/import resume (account)
/import status
/import history (account)
/campaign create (name) (template) [subject]
/campaign load (name) (csv_file)
/campaign personalize (name)
/campaign preview (name) [count]
/campaign start (name)
/campaign pause (name)
/campaign resume (name)
/campaign status
/campaign results (name)
/help
/accounts

or just type naturally - I'll understand."""

# just direct commands without / that have the defined commands so it's possible just to execute them as is bypassing LLM for speed
_DIRECT_COMMANDS = {"import", "draft", "campaign", "schedule"}

COMMAND_DISPATCH = {
    "help": (lambda *_: HELP_TEXT, False),
    "accounts": (account_info, False),
    "search": (search, False),
    "recent": (recent, False),
    "import": (handle_import, False),
    "show": (show_email, False),
    "delete": (delete_email, False),
    "block": (block_sender, False),
    "unsubscribe": (unsubscribe, False),
    "grammar": (grammar, False),
    "ask": (ask, True),
    "draft": (handle_draft, False),
    "send": (send_email, False),
    "schedule": (handle_schedule, False),
    "campaign": (handle_campaign, False),
}


def handle_command(text: str, chat_id: str | int = "") -> str:
    text = text.strip()
    if not text:
        return "Empty message. Type '/help' for commands."

    chat_id_str = str(chat_id)
    chat_memory.save_message(chat_id_str, "user", text)

    # parses a command : /command always, direct word only if they are supported
    command = None
    if text.startswith("/"):
        parts = text[1:].split()
        command = parts[0].lower()
        other_args = parts[1:]
    else:
        parts = text.split()
        first_word = parts[0].lower()
        if first_word in _DIRECT_COMMANDS:
            command = first_word
            other_args = parts[1:]

    if command:
        handler_info = COMMAND_DISPATCH.get(command)
        if handler_info:
            handler, needs_chat_id = handler_info
            if needs_chat_id:
                reply = handler(other_args, chat_id_str)
            else:
                reply = handler(other_args)
        else:
            reply = f"Unknown command: /{command}\nType /help for available commands."
    else:
        reply = _llm_route(text, chat_id_str)

    chat_memory.save_message(chat_id_str, "assistant", reply)
    return reply


INTENT_DISPATCH = {
    "import_start": (import_start, ["account", "count"]),
    "import_pause": (import_pause, ["account"]),
    "import_resume": (import_resume, ["account"]),
    "import_status": (import_status, []),
    "import_history": (import_history, ["account"]),
    "draft_reply": (draft_reply, ["email_id", "instructions"]),
    "draft_new": (draft_new, ["recipient", "instructions"]),
    "send": (send_email, ["draft_id"]),
    "schedule_list": (schedule_list, []),
    "schedule_cancel": (schedule_cancel, ["draft_id"]),
    "accounts": (account_info, []),
    "search": (search, ["query", "after_date"]),
    "show_email": (show_email, ["number"]),
    "delete": (delete_email, ["number"]),
    "block": (block_sender, ["number"]),
    "unsubscribe": (unsubscribe, ["number"]),
    "grammar": (grammar, ["text"]),
    "ask": (ask, ["question"]),
    "recent": (recent, ["count"]),
    "help": (lambda: HELP_TEXT, []),
    "campaign_create": (campaign_create, ["name", "template_file", "subject_template"]),
    "campaign_load": (campaign_load, ["name", "csv_file"]),
    "campaign_personalize": (campaign_personalize, ["name"]),
    "campaign_preview": (campaign_preview, ["name", "count"]),
    "campaign_status": (campaign_engine.get_all_campaigns_status, []),
    "campaign_results": (campaign_results, ["name"]),
    "campaign_start": (campaign_start, ["name"]),
    "campaign_pause": (campaign_pause, ["name"]),
    "campaign_resume": (campaign_resume, ["name"]),
}


def _llm_route(text: str, chat_id: str) -> str:
    try:
        telegram_notifier.notify("Analyzing your message...")

        prompt = _classify_template.render(
            message=text, today=datetime.now().strftime("%Y-%m-%d")
        )
        raw = llm.generate(prompt)
        parsed = parse_json(raw)

        intent = parsed.get("intent", "chitchat")
        params = parsed.get("params", {})

        logger.info(f"LLM route: intent={intent}, params={params}")

        if intent == "chitchat":
            return chitchat(text, chat_id)

        if intent not in INTENT_DISPATCH:
            return chitchat(text, chat_id)

        handler, param_keys = INTENT_DISPATCH[intent]

        args = []
        for key in param_keys:
            if key in params and params[key] is not None:
                args.append(str(params[key]))

        if intent == "ask":
            return handler(args, chat_id)
        if param_keys:
            return handler(args)
        return handler()

    except Exception as e:
        logger.error(f"LLM routing failed: {e}")
        return "Sorry, I couldn't understand that. Type 'help' for available commands."
