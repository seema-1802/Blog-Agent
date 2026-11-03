from __future__ import annotations

import operator
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict

import requests
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from pydantic import BaseModel, Field

# ============================================================
# CONFIGURATION
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

if not GROQ_API_KEY:
    raise RuntimeError(
        "GROQ_API_KEY is missing. Add GROQ_API_KEY=... to the .env file."
    )

OUTPUT_DIR = BASE_DIR / "outputs"
IMAGE_DIR = BASE_DIR / "images"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
IMAGE_DIR.mkdir(parents=True, exist_ok=True)

# You can override these model names in .env if Groq changes availability.
PLANNER_MODEL = os.getenv("GROQ_PLANNER_MODEL", "openai/gpt-oss-120b")
WORKER_MODEL = os.getenv("GROQ_WORKER_MODEL", "openai/gpt-oss-20b")
MAX_WORKER_RETRIES = 2
MAX_IMAGES = 3


llm = ChatGroq(
    model=PLANNER_MODEL,
    api_key=GROQ_API_KEY,
    temperature=0.4,
)
worker_llm = ChatGroq(
    model=WORKER_MODEL,
    api_key=GROQ_API_KEY,
    temperature=0.3,
)

# ============================================================
# UTILITIES
# ============================================================


def extract_text_content(content: Any) -> str:
    """Extract text from LangChain message content safely."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            elif isinstance(item, str):
                parts.append(item)
            elif hasattr(item, "text"):
                parts.append(str(item.text))
        return "\n".join(parts)
    return str(content)


def safe_slug(text: str) -> str:
    """Return a filesystem-safe slug."""
    value = str(text or "blog").strip().lower()
    value = re.sub(r"[^a-z0-9\s_-]", "", value)
    value = re.sub(r"[\s_-]+", "_", value).strip("_")
    return value[:60] or "blog"


def clean_mermaid_label(text: str, max_length: int = 55) -> str:
    """Clean Mermaid labels to avoid parser errors from generated punctuation."""
    value = str(text or "").replace("\n", " ")
    # Avoid brackets, parentheses, quotes, pipes and Mermaid-sensitive symbols.
    value = re.sub(r"[\[\]{}()\"'`|<>]", " ", value)
    value = re.sub(r"[^a-zA-Z0-9 _./:+-]", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:max_length] or "Component"


def render_mermaid_png(mermaid_code: str) -> bytes:
    """Render Mermaid text to PNG via Kroki."""
    response = requests.post(
        "https://kroki.io/mermaid/png",
        data=mermaid_code.encode("utf-8"),
        headers={"Content-Type": "text/plain; charset=utf-8"},
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"Kroki returned HTTP {response.status_code}: {response.text[:300]}"
        )
    if not response.content or len(response.content) < 500:
        raise RuntimeError("Kroki returned an empty or invalid image.")
    return response.content


def build_fallback_diagram(topic: str, diagram_index: int) -> str:
    """Create valid, distinct Mermaid diagrams when the LLM diagram output fails."""
    topic_label = clean_mermaid_label(topic, 40)

    if diagram_index == 1:
        return f'''flowchart TD
    A["User or Client"] --> B["Input for {topic_label}"]
    B --> C["{topic_label} Core"]
    C --> D["Processing Module"]
    C --> E[("Data Store")]
    D --> F["Result or Response"]
    E --> D
    F --> G["User Output"]'''

    if diagram_index == 2:
        return f'''flowchart TD
    A["{topic_label} Input"] --> B["Validate Input"]
    B --> C["Prepare Data"]
    C --> D["Run {topic_label} Workflow"]
    D --> E["Check Result"]
    E --> F["Return Output"]
    E -->|Needs correction| D'''

    return f'''flowchart TD
    A["Client Application"] --> B["Frontend Layer"]
    B --> C["API or Backend"]
    C --> D["{topic_label} Service"]
    D --> E[("Database")]
    D --> F[("Cache")]
    D --> G["External Services"]
    E --> H["Monitoring"]
    F --> H
    G --> H'''

# ============================================================
# STRUCTURED OUTPUT SCHEMAS
# ============================================================


class RouterDecision(BaseModel):
    needs_research: bool = Field(description="Whether current factual research is needed")
    queries: list[str] = Field(default_factory=list, description="Up to two search queries")



class Task(BaseModel):
    id: int
    title: str
    target_words: int = 250
    requires_research: bool = True
    requires_code: bool = False
    instructions: str



class Plan(BaseModel):
    blog_title: str
    audience: str = "General"
    tone: str = "Informative"
    introduction: str
    tasks: list[Task]



class EvidenceItem(BaseModel):
    title: str
    url: str
    content: str



class DiagramSpec(BaseModel):
    title: str
    target_section: str
    mermaid_code: str



class GlobalDiagramPlan(BaseModel):
    diagrams: list[DiagramSpec] = Field(
        description="Exactly three distinct, valid Mermaid diagrams"
    )



class State(TypedDict, total=False):
    topic: str
    mode: Literal["closed_book", "research"]
    needs_research: bool
    queries: list[str]
    evidence: list[EvidenceItem]
    plan: Plan
    as_of: str
    sections: Annotated[list[tuple[int, str]], operator.add]
    merged_md: str
    final: str
    output_path: str
    diagram_specs: list[dict]

# ============================================================
# RESEARCH
# ============================================================


router_llm = llm.with_structured_output(RouterDecision)


def router_node(state: State) -> dict:
    topic = state.get("topic", "").strip()
    mode = state.get("mode", "research")

    if mode == "closed_book":
        return {"needs_research": False, "queries": []}

    try:
        decision = router_llm.invoke(
            [
                SystemMessage(
                    content=(
                        "Create at most two specific web search queries for the topic. "
                        "Research current facts, official documentation, and credible sources. "
                        "Set needs_research to true."
                    )
                ),
                HumanMessage(content=f"Topic: {topic}"),
            ]
        )
        queries = [q.strip() for q in decision.queries if q.strip()][:2]
        return {"needs_research": True, "queries": queries or [topic]}
    except Exception as exc:
        print(f"[Router warning] {exc}")
        return {
            "needs_research": True,
            "queries": [topic, f"{topic} official documentation"],
        }


def single_tavily_search(query: str) -> list[EvidenceItem]:
    if not TAVILY_API_KEY:
        print("[Research] TAVILY_API_KEY not configured; skipping web research.")
        return []

    try:
        response = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": TAVILY_API_KEY,
                "query": query,
                "search_depth": "advanced",
                "max_results": 3,
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        evidence: list[EvidenceItem] = []

        for result in payload.get("results", []):
            url = str(result.get("url", "")).strip()
            if not url.startswith(("https://", "http://")):
                continue
            evidence.append(
                EvidenceItem(
                    title=str(result.get("title", "Reference"))[:300],
                    url=url,
                    content=str(result.get("content", ""))[:1200],
                )
            )
        return evidence
    except Exception as exc:
        print(f"[Tavily warning] Search failed for {query!r}: {exc}")
        return []


def research_node(state: State) -> dict:
    queries = (state.get("queries") or [state.get("topic", "")])[:2]
    results: list[EvidenceItem] = []
    seen_urls: set[str] = set()

    with ThreadPoolExecutor(max_workers=max(1, len(queries))) as executor:
        futures = [executor.submit(single_tavily_search, query) for query in queries]
        for future in as_completed(futures):
            try:
                items = future.result()
            except Exception as exc:
                print(f"[Research warning] {exc}")
                continue
            for item in items:
                if item.url not in seen_urls:
                    seen_urls.add(item.url)
                    results.append(item)

    return {"evidence": results}

# ============================================================
# BLOG PLANNER AND WRITERS
# ============================================================


def plan_node(state: State) -> dict:
    evidence = state.get("evidence", [])
    evidence_text = "\n".join(
        f"- {item.title}: {item.content[:350]}" for item in evidence[:6]
    )

    planner = llm.with_structured_output(Plan)
    plan = planner.invoke(
        [
            SystemMessage(
                content=(
                    "Plan a practical, well-organized technical blog with exactly four "
                    "sections/tasks. Write a complete introduction. Include implementation "
                    "steps and runnable code when relevant. Do not invent research citations."
                )
            ),
            HumanMessage(
                content=(
                    f"Topic: {state.get('topic', '')}\n"
                    f"Date: {state.get('as_of', date.today().isoformat())}\n"
                    f"Evidence:\n{evidence_text or 'No external evidence available.'}"
                )
            ),
        ]
    )

    plan.tasks = plan.tasks[:4]
    for index, task in enumerate(plan.tasks, start=1):
        task.id = index
    return {"plan": plan}



def fanout_workers(state: State):
    plan = state["plan"]
    return [
        Send(
            "worker",
            {
                "topic": state.get("topic", ""),
                "evidence": state.get("evidence", []),
                "task": task.model_dump(),
            },
        )
        for task in plan.tasks
    ]


def worker_node(state: dict) -> dict:
    task = Task.model_validate(state["task"])
    evidence: list[EvidenceItem] = state.get("evidence", [])


    source_text = "\n".join(
        f"- [{item.title}]({item.url}): {item.content[:300]}"
        for item in evidence[:3]
    ) or "No external evidence supplied. Avoid unsupported current claims."


    code_instruction = (
        "Include complete runnable code in appropriate fenced code blocks."
        if task.requires_code
        else "Avoid unnecessary code snippets."
    )

    prompt = f"""Write one section of the technical blog.

Topic: {state.get('topic', '')}
Section heading: {task.title}
Approximate target words: {task.target_words}
Instructions: {task.instructions}

Sources:
{source_text}

Rules:
- Begin with exactly: ## {task.title}
- {code_instruction}
- Use clear, beginner-friendly technical explanations.
- Do not fabricate facts, URLs, benchmarks, or citations.
- Keep the section focused and useful.
"""

    for attempt in range(1, MAX_WORKER_RETRIES + 2):
        try:
            response = worker_llm.invoke(
                [
                    SystemMessage(
                        content="You are an expert technical author. Output clean Markdown only."
                    ),
                    HumanMessage(content=prompt),
                ]
            )
            section_text = extract_text_content(response.content).strip()
            if section_text and len(section_text) > 80:
                # Close an accidentally unclosed code fence.
                if section_text.count("```") % 2:
                    section_text += "\n```\n"
                return {"sections": [(task.id, section_text)]}
        except Exception as exc:
            print(f"[Worker warning] task={task.id}, attempt={attempt}: {exc}")
            error_text = str(exc).lower()
            if "429" in error_text or "rate_limit" in error_text:
                time.sleep(20 * attempt)
            else:
                time.sleep(3 * attempt)

    return {
        "sections": [
            (
                task.id,
                f"## {task.title}\n\n_Section generation failed after retries. Please rerun._",
            )
        ]
    }


def reducer_node(state: State) -> dict:
    plan = state["plan"]
    sections = sorted(state.get("sections", []), key=lambda item: item[0])
    body = "\n\n".join(section for _, section in sections)
    evidence = state.get("evidence", [])

    unique_refs: dict[str, str] = {}
    for item in evidence:
        url = (item.url or "").strip()
        if url.startswith(("https://", "http://")) and url not in unique_refs:
            unique_refs[url] = (item.title or "Reference").strip()

    references_block = ""
    if unique_refs:
        refs = [f"- [{title}]({url})" for url, title in unique_refs.items()]
        references_block = "## References & Further Reading\n\n" + "\n".join(refs)

    try:
        conclusion_response = llm.invoke(
            [
                SystemMessage(
                    content="Write a concise two-paragraph conclusion. Output paragraphs only."
                ),
                HumanMessage(
                    content=f"Topic: {state.get('topic', '')}\n\nBlog context:\n{body[:5000]}"
                ),
            ]
        )
        conclusion = extract_text_content(conclusion_response.content).strip()
    except Exception as exc:
        print(f"[Conclusion warning] {exc}")
        conclusion = (
            f"The key takeaway is to understand the fundamentals of "
            f"{state.get('topic', 'the topic')} and apply them carefully. "
            "Validate the implementation, test edge cases, and keep the design "
            "maintainable as requirements evolve."
        )

    introduction = plan.introduction.strip()
    if not introduction.lower().startswith("## introduction"):
        introduction = f"## Introduction\n\n{introduction}"

    merged_markdown = (
        f"# {plan.blog_title}\n\n"
        f"{introduction}\n\n"
        f"{body}\n\n"
        f"## Conclusion\n\n{conclusion}\n\n"
        f"{references_block}\n"
    )
    return {"merged_md": merged_markdown}

# ============================================================
# MERMAID DIAGRAMS
# ============================================================


DIAGRAM_SYSTEM = """You are a software architect creating Mermaid diagrams for a technical blog.
Return exactly three distinct diagrams:
1. High-level system architecture.
2. Workflow, sequence, or data pipeline.
3. Deployment architecture.

Mermaid requirements:
- Use flowchart TD syntax.
- Use simple node IDs: A, B, C, D, etc.
- Use labels inside double quotes, e.g. A[\"API Gateway\"].
- Avoid parentheses, square brackets, braces, quotes, pipes, and Unicode in labels.
- Do not include Markdown fences.
- Keep each diagram specific to the topic and technically coherent.
- Mermaid code must begin with flowchart TD.
"""


def generate_diagrams_node(state: State) -> dict:
    topic = state.get("topic", "Technical Architecture")
    markdown = state.get("merged_md", "")
    diagram_plan: GlobalDiagramPlan | None = None

    try:
        planner = llm.with_structured_output(GlobalDiagramPlan)
        diagram_plan = planner.invoke(
            [
                SystemMessage(content=DIAGRAM_SYSTEM),
                HumanMessage(
                    content=f"Topic: {topic}\nBlog context:\n{markdown[:5000]}"
                ),
            ]
        )
    except Exception as exc:
        print(f"[Diagram planner warning] Using fallback diagrams: {exc}")

    diagrams: list[DiagramSpec] = []
    if diagram_plan:
        for item in diagram_plan.diagrams[:3]:
            code = (item.mermaid_code or "").replace("```mermaid", "").replace("```", "").strip()
            # Normalize Mermaid code header and sanitize likely invalid labels.
            code = re.sub(r"^graph\s+TD\s*$", "flowchart TD", code, flags=re.I | re.M)
            if code.startswith("flowchart TD"):
                diagrams.append(
                    DiagramSpec(
                        title=item.title,
                        target_section=item.target_section,
                        mermaid_code=code,
                    )
                )

    # Ensure there are exactly 3 diagrams. Missing or invalid diagrams use distinct fallbacks.
    while len(diagrams) < 3:
        index = len(diagrams) + 1
        diagrams.append(
            DiagramSpec(
                title=["System Architecture", "Workflow", "Deployment Architecture"][index - 1],
                target_section="Introduction",
                mermaid_code=build_fallback_diagram(topic, index),
            )
        )

    updated_markdown = markdown
    for index, diagram in enumerate(diagrams[:3], start=1):
        # Render test validates Mermaid. Invalid LLM code is replaced with fallback.
        try:
            render_mermaid_png(diagram.mermaid_code)
        except Exception as exc:
            print(f"[Mermaid validation] Diagram {index} fallback: {exc}")
            diagram.mermaid_code = build_fallback_diagram(topic, index)

        diagram_block = (
            f"\n\n### Diagram {index}: {diagram.title}\n\n"
            f"```mermaid\n{diagram.mermaid_code}\n```\n"
        )
        updated_markdown += diagram_block

    return {"merged_md": updated_markdown, "diagram_specs": [d.model_dump() for d in diagrams[:3]]}

# ============================================================
# PNG GENERATION AND MARKDOWN OUTPUT
# ============================================================


def generate_and_place_images(state: State) -> dict:
    plan = state.get("plan")
    blog_title = plan.blog_title if plan else state.get("topic", "blog")
    topic = state.get("topic", "Technical Architecture")
    markdown = state.get("merged_md", "")
    slug = safe_slug(blog_title)
    output_file = OUTPUT_DIR / f"{slug}.md"

    diagram_specs = state.get("diagram_specs", [])
    image_markdown: list[str] = []

    # Render each diagram independently, so all PNGs are not identical.
    for index in range(1, MAX_IMAGES + 1):
        if index <= len(diagram_specs):
            spec = diagram_specs[index - 1]
            mermaid_code = spec.get("mermaid_code", "")
            title = spec.get("title", f"Diagram {index}")
        else:
            mermaid_code = build_fallback_diagram(topic, index)
            title = f"Diagram {index}"

        filename = f"{safe_slug(topic)[:30]}_diagram_{index}.png"
        image_path = IMAGE_DIR / filename


        try:
            image_bytes = render_mermaid_png(mermaid_code)
            image_path.write_bytes(image_bytes)
            image_markdown.append(
                f"![{title}](../images/{filename})\n\n*Figure {index}: {title} for {topic}.*"
            )
            print(f"[Diagram image] Saved: {image_path}")
        except Exception as exc:
            print(f"[Diagram image warning] {filename}: {exc}")
            image_markdown.append(
                f"\n\n### {title}\n\n```mermaid\n{mermaid_code}\n```"
            )

    # Include generated PNGs at the end of the Markdown output.
    markdown += "\n\n## Architecture Diagrams\n\n" + "\n\n".join(image_markdown)
    output_file.write_text(markdown, encoding="utf-8")

    return {"final": markdown, "output_path": str(output_file)}

# ============================================================
# LANGGRAPH DEFINITION
# ============================================================


def build_graph():
    graph = StateGraph(State)

    graph.add_node("router", router_node)
    graph.add_node("research", research_node)
    graph.add_node("plan", plan_node)
    graph.add_node("worker", worker_node)
    graph.add_node("reducer", reducer_node)
    graph.add_node("diagrams", generate_diagrams_node)
    graph.add_node("generate_images", generate_and_place_images)

    graph.add_edge(START, "router")
    graph.add_conditional_edges(
        "router",
        lambda state: "research" if state.get("needs_research") else "plan",
        {"research": "research", "plan": "plan"},
    )
    graph.add_edge("research", "plan")
    graph.add_conditional_edges("plan", fanout_workers, ["worker"])
    graph.add_edge("worker", "reducer")
    graph.add_edge("reducer", "diagrams")
    graph.add_edge("diagrams", "generate_images")
    graph.add_edge("generate_images", END)

    return graph.compile()



app = build_graph()

# ============================================================
# RUNNER
# ============================================================


def run(topic: str, mode: Literal["closed_book", "research"] = "research") -> dict:
    topic = topic.strip()
    if not topic:
        raise ValueError("Topic cannot be empty.")

    initial_state: State = {
        "topic": topic,
        "mode": mode,
        "as_of": date.today().isoformat(),
        "queries": [],
        "evidence": [],
        "sections": [],
        "merged_md": "",
        "final": "",
        "output_path": "",
    }

    result = app.invoke(initial_state)
    print(
        "\nBlog generation completed.\n"
        f"Markdown written to: {result.get('output_path', '')}"
    )
    return result


if __name__ == "__main__":
    user_topic = input("Topic: ").strip()
    if user_topic:
        run(user_topic)
