
from __future__ import annotations

from datetime import date
from io import BytesIO
import json
from pathlib import Path
import re
from typing import Any, Dict, Iterator, List, Optional, Tuple
import zipfile

import pandas as pd
import streamlit as st
import streamlit_mermaid as st_mermaid

from bwa_backend import OUTPUT_DIR, app


# ---------------------------------------------------------
# REGEX
# ---------------------------------------------------------

_MD_IMG_RE = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\((?P<src>[^)]+)\)"
)

_MERMAID_RE = re.compile(
    r"```mermaid\s*\n(.*?)\n```",
    flags=re.DOTALL
)

_CAPTION_LINE_RE = re.compile(
    r"^\*(?P<cap>.+)\*$"
)


# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------

def safe_slug(title: str) -> str:
    s = title.strip().lower()
    s = re.sub(r"[^a-z0-9 _-]+", "", s)
    return re.sub(r"\s+", "_", s).strip("_") or "blog"


def extract_title_from_md(md: str, fallback: str) -> str:
    for line in md.splitlines():
        if line.startswith("# "):
            return line[2:].strip() or fallback
    return fallback


def bundle_zip(
    md_text: str,
    md_filename: str,
    images_dir: Path
) -> bytes:

    buf = BytesIO()

    with zipfile.ZipFile(
        buf,
        "w",
        compression=zipfile.ZIP_DEFLATED
    ) as z:

        z.writestr(md_filename, md_text.encode("utf-8"))

        if images_dir.exists() and images_dir.is_dir():
            for p in images_dir.rglob("*"):
                if p.is_file():
                    z.write(
                        p,
                        arcname=f"images/{p.name}"
                    )

    return buf.getvalue()


def images_zip(images_dir: Path) -> Optional[bytes]:

    if not images_dir.exists() or not images_dir.is_dir():
        return None

    buf = BytesIO()

    with zipfile.ZipFile(
        buf,
        "w",
        compression=zipfile.ZIP_DEFLATED
    ) as z:

        for p in images_dir.rglob("*"):
            if p.is_file():
                z.write(p, arcname=p.name)

    return buf.getvalue()


def try_stream(
    graph_app,
    inputs: Dict[str, Any]
) -> Iterator[Tuple[str, Any]]:

    try:
        for step in graph_app.stream(
            inputs,
            stream_mode="updates"
        ):
            yield ("updates", step)

        final_state = graph_app.invoke(inputs)
        yield ("final", final_state)

        return

    except Exception:
        pass

    out = graph_app.invoke(
        inputs,
        config={"max_concurrency": 1}
    )

    yield ("final", out)


def extract_latest_state(
    current_state: Dict[str, Any],
    step_payload: Any
) -> Dict[str, Any]:

    if isinstance(step_payload, dict):

        if (
            len(step_payload) == 1
            and isinstance(
                next(iter(step_payload.values())),
                dict
            )
        ):
            inner = next(iter(step_payload.values()))
            current_state.update(inner)

        else:
            current_state.update(step_payload)

    return current_state


# ---------------------------------------------------------
# IMAGE PATH RESOLUTION
# ---------------------------------------------------------

def resolve_image_path(src: str) -> Optional[Path]:

    if not src:
        return None

    if src.startswith(
        ("http://", "https://", "data:")
    ):
        return None

    image_path = Path(src)

    candidates = [
        image_path if image_path.is_absolute() else None,
        OUTPUT_DIR / image_path,
        OUTPUT_DIR / "images" / image_path.name,
    ]

    for candidate in candidates:

        if candidate and candidate.is_file():
            return candidate.resolve()

    return None


def get_spec_value(spec: Any, key: str, default=None):

    if isinstance(spec, dict):
        return spec.get(key, default)

    return getattr(spec, key, default)


def get_blog_images(blog: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Current generated blog:
        Uses image_specs from graph output.

    Previous blog:
        Extracts image references from saved Markdown.

    Both return a list of image paths and captions.
    """

    results = []
    seen = set()

    # -----------------------------------------------------
    # 1. Images from image_specs
    # -----------------------------------------------------

    image_specs = blog.get("image_specs") or []

    for spec in image_specs:

        filename = get_spec_value(
            spec,
            "filename",
            ""
        )

        caption = get_spec_value(
            spec,
            "caption",
            ""
        )

        if not filename:
            continue

        img_path = resolve_image_path(str(filename))

        if img_path and img_path.exists():

            key = str(img_path.resolve())

            if key not in seen:

                results.append({
                    "path": img_path,
                    "caption": caption or img_path.name
                })

                seen.add(key)

    # -----------------------------------------------------
    # 2. Images from Markdown
    #    Used especially when loading previous blogs.
    # -----------------------------------------------------

    markdown_text = blog.get("final") or ""

    for match in _MD_IMG_RE.finditer(markdown_text):

        alt = (match.group("alt") or "").strip()

        src = (match.group("src") or "").strip()

        img_path = resolve_image_path(src)

        if img_path and img_path.exists():

            key = str(img_path.resolve())

            if key not in seen:

                results.append({
                    "path": img_path,
                    "caption": alt or img_path.name
                })

                seen.add(key)

    return results


# ---------------------------------------------------------
# MARKDOWN RENDERING
# ---------------------------------------------------------

def render_markdown_with_local_images(md: str):

    matches = list(_MD_IMG_RE.finditer(md))

    if not matches:
        display_blog_with_mermaid(md)
        return

    parts = []

    last = 0

    for match in matches:

        before = md[last:match.start()]

        if before:
            parts.append(("md", before))

        alt = (match.group("alt") or "").strip()

        src = (match.group("src") or "").strip()

        parts.append((
            "img",
            f"{alt}|||{src}"
        ))

        last = match.end()

    tail = md[last:]

    if tail:
        parts.append(("md", tail))

    i = 0

    while i < len(parts):

        kind, payload = parts[i]

        if kind == "md":

            display_blog_with_mermaid(payload)

            i += 1

            continue

        alt, src = payload.split("|||", 1)

        caption = None

        # Caption immediately after image:
        # *This is image caption*
        if i + 1 < len(parts) and parts[i + 1][0] == "md":

            nxt = parts[i + 1][1].lstrip()

            if nxt.strip():

                first_line = nxt.splitlines()[0].strip()

                mcap = _CAPTION_LINE_RE.match(first_line)

                if mcap:

                    caption = mcap.group("cap").strip()

                    remaining = "\n".join(
                        nxt.splitlines()[1:]
                    )

                    parts[i + 1] = (
                        "md",
                        remaining
                    )

        # Online image
        if src.startswith(("http://", "https://")):

            st.image(
                src,
                caption=caption or alt or None,
                width="stretch"
            )

        # Local image
        else:

            img_path = resolve_image_path(src)

            if img_path and img_path.exists():

                st.image(
                    str(img_path),
                    caption=caption or alt or None,
                    width="stretch"
                )

            else:

                st.warning(
                    f"Image not found: `{src}`"
                )

        i += 1


def display_blog_with_mermaid(markdown_text: str):

    parts = _MERMAID_RE.split(markdown_text)

    for i, part in enumerate(parts):

        if i % 2 == 1:

            st_mermaid.st_mermaid(
                part.strip(),
                height="350px"
            )

        else:

            if part.strip():

                st.markdown(
                    part,
                    unsafe_allow_html=False
                )


# ---------------------------------------------------------
# PAST BLOGS
# ---------------------------------------------------------

def list_past_blogs() -> List[Path]:

    if not OUTPUT_DIR.exists():
        return []

    files = [
        p
        for p in OUTPUT_DIR.glob("*.md")
        if p.is_file()
    ]

    files.sort(
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )

    return files


# ---------------------------------------------------------
# UI SETUP
# ---------------------------------------------------------

st.set_page_config(
    page_title="LangGraph Blog Writer",
    layout="wide"
)

st.title("Blog Writing Agent")


# ---------------------------------------------------------
# SESSION STATE
# ---------------------------------------------------------

if "last_out" not in st.session_state:
    st.session_state["last_out"] = None

if "run_logs" not in st.session_state:
    st.session_state["run_logs"] = []


# ---------------------------------------------------------
# SIDEBAR
# ---------------------------------------------------------

with st.sidebar:

    st.header("Generate New Blog")

    topic = st.text_area(
        "Topic",
        height=120
    )

    as_of = st.date_input(
        "As-of date",
        value=date.today()
    )

    run_btn = st.button(
        "🚀 Generate Blog",
        type="primary"
    )

    st.divider()

    st.subheader("Past blogs")

    past_files = list_past_blogs()

    if not past_files:

        st.caption(
            f"No saved blogs found in `{OUTPUT_DIR.name}/`."
        )

    else:

        options = [
            (
                extract_title_from_md(
                    p.read_text(
                        encoding="utf-8",
                        errors="replace"
                    ),
                    p.stem
                )
                + f" · {p.name}"
            )
            for p in past_files[:50]
        ]

        file_map = dict(
            zip(options, past_files)
        )

        selected_label = st.radio(
            "Select a blog",
            options=options,
            label_visibility="collapsed"
        )

        if st.button("📂 Load selected blog"):

            sel_file = file_map[selected_label]

            md_text = sel_file.read_text(
                encoding="utf-8",
                errors="replace"
            )

            # Important:
            # Previous blog image_specs are empty.
            # Images will be extracted from its Markdown.
            st.session_state["last_out"] = {
                "plan": None,
                "evidence": [],
                "image_specs": [],
                "final": md_text,
            }

            st.session_state["run_logs"] = [
                f"Loaded previous blog: {sel_file.name}"
            ]


# ---------------------------------------------------------
# TABS
# ---------------------------------------------------------

tab_plan, tab_evidence, tab_preview, tab_images, tab_logs = st.tabs(
    [
        "🧩 Plan",
        "🔎 Evidence",
        "📝 Markdown Preview",
        "🖼️ Images",
        "🧾 Logs"
    ]
)


# ---------------------------------------------------------
# GENERATE BLOG
# ---------------------------------------------------------

if run_btn:

    if not topic.strip():

        st.warning("Please enter a topic.")

        st.stop()

    inputs = {
        "topic": topic.strip(),
        "mode": "research",
        "as_of": as_of.isoformat(),
    }

    status = st.status(
        "Running graph...",
        expanded=True
    )

    current_state: Dict[str, Any] = {}

    logs = []

    for kind, payload in try_stream(app, inputs):

        if kind == "updates":

            current_state = extract_latest_state(
                current_state,
                payload
            )

            status.write(
                f"Updated: `{list(payload.keys()) if isinstance(payload, dict) else 'step'}`"
            )

            logs.append(
                f"[{kind}] {json.dumps(payload, default=str)[:400]}"
            )

        elif kind == "final":

            # Store complete graph output.
            # Images tab and Markdown Preview both use this.
            st.session_state["last_out"] = payload

            st.session_state["run_logs"] = logs

            status.update(
                label="✅ Complete!",
                state="complete",
                expanded=False
            )


# ---------------------------------------------------------
# DISPLAY OUTPUT
# ---------------------------------------------------------

out = st.session_state.get("last_out")


if out:

    # -----------------------------------------------------
    # PLAN TAB
    # -----------------------------------------------------

    with tab_plan:

        st.subheader("Plan")

        plan_obj = out.get("plan")

        if not plan_obj:

            st.info(
                "No plan data available for this session."
            )

        else:

            p_dict = (
                plan_obj.model_dump()
                if hasattr(plan_obj, "model_dump")
                else (
                    plan_obj
                    if isinstance(plan_obj, dict)
                    else {}
                )
            )

            st.write(
                "**Title:**",
                p_dict.get("blog_title", "Untitled")
            )

            c1, c2, c3 = st.columns(3)

            c1.write(
                f"**Audience:** {p_dict.get('audience', 'General')}"
            )

            c2.write(
                f"**Tone:** {p_dict.get('tone', 'Informative')}"
            )

            c3.write(
                f"**Type:** {p_dict.get('blog_kind', 'Article')}"
            )

            tasks = p_dict.get("tasks", [])

            if tasks:

                st.dataframe(
                    pd.DataFrame(tasks),
                    width="stretch",
                    hide_index=True
                )


    # -----------------------------------------------------
    # EVIDENCE TAB
    # -----------------------------------------------------

    with tab_evidence:

        st.subheader("Research Evidence")

        ev = out.get("evidence") or []

        if ev:

            ev_list = [
                e.model_dump()
                if hasattr(e, "model_dump")
                else e
                for e in ev
            ]

            st.dataframe(
                pd.DataFrame(ev_list),
                width="stretch",
                hide_index=True
            )

        else:

            st.info(
                "No research evidence attached."
            )


    # -----------------------------------------------------
    # MARKDOWN PREVIEW TAB
    # -----------------------------------------------------

    with tab_preview:

        st.subheader("Blog Markdown Preview")

        final_md = out.get("final") or ""

        if final_md:

            render_markdown_with_local_images(
                final_md
            )

            slug = safe_slug(
                extract_title_from_md(
                    final_md,
                    "blog"
                )
            )

            st.download_button(
                "⬇️ Download Markdown",
                data=final_md.encode("utf-8"),
                file_name=f"{slug}.md",
                mime="text/markdown"
            )

            st.download_button(
                "📦 Download Bundle",
                data=bundle_zip(
                    final_md,
                    f"{slug}.md",
                    OUTPUT_DIR
                ),
                file_name=f"{slug}_bundle.zip",
                mime="application/zip"
            )

        else:

            st.info(
                "No blog content available."
            )


    # -----------------------------------------------------
    # IMAGES TAB
    # -----------------------------------------------------

    with tab_images:

        st.subheader("Blog Images")

        # Images belonging to the currently selected blog.
        # For generated blog: image_specs + Markdown.
        # For previous blog: Markdown image paths.
        blog_images = get_blog_images(out)

        if blog_images:

            st.caption(
                f"{len(blog_images)} image(s) found for this blog."
            )

            for image_data in blog_images:

                img_path = image_data["path"]

                caption = image_data["caption"]

                st.image(
                    str(img_path),
                    caption=caption,
                    width="stretch"
                )

        else:

            st.info(
                "No images generated or found for this blog."
            )


# ---------------------------------------------------------
# LOGS TAB
# ---------------------------------------------------------

with tab_logs:

    st.subheader("Run Logs")

    log_text = "\n".join(
        st.session_state.get("run_logs", [])
    )

    st.text_area(
        "Event history",
        value=log_text,
        height=400
    )
