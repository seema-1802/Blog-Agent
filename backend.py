
import os
import sqlite3
from dotenv import load_dotenv

from typing import TypedDict, Annotated
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage
from langchain_core.tools import tool
from langchain_groq import ChatGroq
from langgraph.prebuilt import ToolNode, tools_condition
from langchain_community.tools import DuckDuckGoSearchRun
from langgraph.checkpoint.sqlite import SqliteSaver

load_dotenv()

if not os.getenv("GROQ_API_KEY"):
    raise ValueError("GROQ_API_KEY not found in .env")

# LLM
llm = ChatGroq(
    model="openai/gpt-oss-20b",
    temperature=0.1,
    streaming=True,
)

#

ddg_search = DuckDuckGoSearchRun(region="us-en")

@tool
def search_tool(query: str) -> str:
    """Search the web for current news and information."""
    try:
        result = ddg_search.invoke(query)

        if not result or not result.strip():
            return "No search results found. Please try again."

        return result

    except Exception as e:
        print(f"Search tool error: {type(e).__name__}: {e}")
        return (
            "Web search is temporarily unavailable. "
            "Please try again later. I could not verify current information."
        )

@tool
def calculator(
    first_num: float,
    second_num: float,
    operation: str
) -> dict:
    """Perform basic arithmetic: add, sub, mul, div."""
    try:
        if operation == "add":
            result = first_num + second_num
        elif operation == "sub":
            result = first_num - second_num
        elif operation == "mul":
            result = first_num * second_num
        elif operation == "div":
            if second_num == 0:
                return {"error": "Division by zero is not allowed"}
            result = first_num / second_num
        else:
            return {"error": f"Unsupported operation '{operation}'"}

        return {
            "first_num": first_num,
            "second_num": second_num,
            "operation": operation,
            "result": result,
        }
    except Exception as e:
        return {"error": str(e)}


tools = [search_tool, calculator]
llm_with_tools = llm.bind_tools(tools)


class ChatState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


def chat_node(state: ChatState):
    messages = state["messages"]
    response = llm_with_tools.invoke(messages)
    return {"messages": [response]}


tool_node = ToolNode(tools)

# SQLite checkpoint
conn = sqlite3.connect(
    database="chatbot.db",
    check_same_thread=False
)
checkpointer = SqliteSaver(conn=conn)
checkpointer.setup()

# Graph
graph = StateGraph(ChatState)

graph.add_node("chat_node", chat_node)
graph.add_node("tools", tool_node)

graph.add_edge(START, "chat_node")
graph.add_conditional_edges("chat_node", tools_condition)
graph.add_edge("tools", "chat_node")

chatbot = graph.compile(checkpointer=checkpointer)


# Retrieve all saved thread IDs
def retriver_all_threads():
    all_thread_ids = set()

    for checkpoint in checkpointer.list(None):
        thread_id = checkpoint.config["configurable"].get("thread_id")

        if thread_id:
            all_thread_ids.add(thread_id)

    return list(all_thread_ids)


#   # response = chatbot.invoke(
                #     {
                #         "messages": [
                #             HumanMessage(content=user_input)
                #         ]
                #     },
                #     config=config
                # )