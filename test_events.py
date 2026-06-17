import os
from dotenv import load_dotenv
from openai import OpenAI

from config import AGENT_HOME, load_config
from prompt import current_system_message
from tools import set_llm, set_current_intent
from agent_events import run_agent_events

load_dotenv(AGENT_HOME / ".env")
config = load_config()

client = OpenAI(
    api_key=os.getenv("OPENROUTER_API_KEY"),
    base_url=config["base_url"],
    timeout=30.0,
)
set_llm(client, config["model"])

user_text = "List the files in this folder."
messages = [current_system_message(), {"role": "user", "content": user_text}]
set_current_intent(user_text)

for event in run_agent_events(client, config["model"], messages, int(config["max_agent_steps"]), config["approval_mode"]):
    print(f"[{event['type']}]", event.get("content") or event.get("name") or "")