
import streamlit as st
import uuid

from backend import chatbot, retriver_all_threads
from langchain_core.messages import HumanMessage

# Page setup
st.set_page_config(
    page_title="AI Chatbot",
    page_icon="🤖"
)

st.title("AI Chatbot")


# Initialize session state
if "thread_id" not in st.session_state:
    st.session_state.thread_id = str(uuid.uuid4())

if "message_history" not in st.session_state:
    st.session_state.message_history = []


# Load chat from SQLite checkpoint
def load_chat(thread_id):
    config = {
        "configurable": {
            "thread_id": thread_id
        }
    }

    state = chatbot.get_state(config)
    messages = state.values.get("messages", [])

    history = []

    for message in messages:
        if message.type == "human":
            history.append({
                "role": "user",
                "content": message.content
            })

        elif message.type == "ai":
            # Ignore tool-call-only AI messages
            if message.content:
                if isinstance(message.content, str):
                    content = message.content
                elif isinstance(message.content, list):
                    content = "\n".join(
                        block.get("text", "")
                        for block in message.content
                        if isinstance(block, dict)
                        and block.get("type") == "text"
                    )
                else:
                    content = ""

                if content:
                    history.append({
                        "role": "assistant",
                        "content": content
                    })

    return history


# Sidebar
with st.sidebar:
    st.title("Chat History")

    if st.button("➕ New Chat", use_container_width=True):
        st.session_state.thread_id = str(uuid.uuid4())
        st.session_state.message_history = []
        st.rerun()

    st.caption("Current Thread ID")
    st.code(st.session_state.thread_id)

    st.divider()
    st.subheader("Previous Chats")

    saved_threads = retriver_all_threads()

    for tid in reversed(saved_threads):
        label = (
            "🟢 " + tid[:8]
            if tid == st.session_state.thread_id
            else tid[:8]
        )

        if st.button(label, key=tid, use_container_width=True):
            st.session_state.thread_id = tid
            st.session_state.message_history = load_chat(tid)
            st.rerun()


# Display conversation
for message in st.session_state.message_history:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])


# Chat input
user_input = st.chat_input("Type here...")

if user_input:

    # Show user message
    st.session_state.message_history.append({
        "role": "user",
        "content": user_input
    })

    with st.chat_message("user"):
        st.markdown(user_input)

    # LangGraph + LangSmith configuration
    thread_id = st.session_state.thread_id

    config = {
        "configurable": {
            "thread_id": thread_id
        },
        "metadata": {
            "thread_id": thread_id
        },
        "run_name": "AI Chatbot"
    }

    # Generate assistant response
    with st.chat_message("assistant"):

        def generate():
            for chunk, metadata in chatbot.stream(
                {
                    "messages": [
                        HumanMessage(content=user_input)
                    ]
                },
                config=config,
                stream_mode="messages"
            ):

                # Only display chatbot node text
                if metadata.get("langgraph_node") != "chat_node":
                    continue

                content = chunk.content

                if isinstance(content, str):
                    if content:
                        yield content

                elif isinstance(content, list):
                    for block in content:
                        if (
                            isinstance(block, dict)
                            and block.get("type") == "text"
                        ):
                            text = block.get("text", "")
                            if text:
                                yield text

        answer = st.write_stream(generate())

        if not answer:
            st.warning(
                "No text received. Check the terminal for errors."
            )

    # Save answer in session history
    st.session_state.message_history.append({
        "role": "assistant",
        "content": answer or "No response received."
    })