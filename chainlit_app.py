import os

import chainlit as cl
from dotenv import load_dotenv
from openai import OpenAI

from config import AGENT_HOME, load_config
from prompt import current_system_message
from tools import set_llm, set_current_intent
from agent_events import run_agent_events


@cl.on_chat_start
async def start():
    load_dotenv(AGENT_HOME / ".env")
    config = load_config()
    client = OpenAI(
        api_key= os.getenv("OPENROUTER_API_KEY"), 
        base_url=config["base_url"],
        timeout=30.0,
    )
    set_llm(client, config["model"])

    messages = [current_system_message()]

    cl.user_session.set("client", client)
    cl.user_session.set("config", config)
    cl.user_session.set("messages", messages)

    await cl.Message(content="Agent ready. What should i do?").send()
    
async def ask_approval(command: str, reason: str) -> str:
    res = await cl.AskActionMessage(
        content=f"**Approval required**\n\nReason {reason}\n\n```\n{command}\n```",
        actions=[
            cl.Action(name="approve", payload={"decision": "approve"}, label="Allow"),
            cl.Action(name="deny", payload={"decision": "deny"}, label="Deny"),
        ],
    ).send()

    if res and res.get("payload", {}).get("decision") == "approve":
        return "approve"
    return "deny"

@cl.on_message
async def on_message(message: cl.Message):
    client = cl.user_session.get("client")
    config = cl.user_session.get("config")
    messages = cl.user_session.get("messages")

    user_text = message.content
    messages.append({"role": "user", "content": user_text})
    set_current_intent(user_text)

    gen = run_agent_events(
        client, 
        config["model"],
        messages,
        int(config["max_agent_steps"]),
        config["approval_mode"],
        intent=user_text,
    )

    answer_to_send = None

    while True:
        try: 
            if answer_to_send is None:
                event = next(gen)
            else:
                event = gen.send(answer_to_send)
                answer_to_send = None
        except StopIteration:
            break

        kind = event["type"]

        if kind == "assistant_message":
            await cl.Message(content=event["content"]).send()

        elif kind == "tool_start":
            await cl.Message(
                content=f"Running '{event['name']}'...", author="tool"
            ).send()

        elif kind == "tool_result":
            preview = event["result"][:1500]
            await cl.Message(
                content=f"```\n{preview}\n```", author="tool"
            ).send()

        elif kind =="approval_request":
            answer_to_send = await ask_approval(event["command"], event["reason"])

        elif kind == "max_steps":
            await cl.Message(content=event["content"]).send()