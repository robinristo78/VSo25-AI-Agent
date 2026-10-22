import os
import io
import zipfile
import threading
import http.server
import socketserver
import shutil
from pathlib import Path
from datetime import datetime

import streamlit as st
from crewai import Agent, Task, Crew, LLM
from crewai.tools import tool

from audit_engine import run_audit, format_audit_for_llm

# ---------------------------------------------------------------------------
# Config & constants
# ---------------------------------------------------------------------------
ROOT = Path(__file__).parent.parent
WORKSPACE = ROOT / "workspace"
VERSIONS = ROOT / "versions"
WORKSPACE.mkdir(exist_ok=True)
VERSIONS.mkdir(exist_ok=True)
PORT = 8000
SERVER_URL = f"http://127.0.0.1:{PORT}"

st.set_page_config(page_title="UX Audit Workspace", page_icon="🎨", layout="wide")
st.title("🎨 AI UX Audit & Design Workspace")
st.caption("Local multi-agent UX auditing workspace. Generates or audits any static HTML page (pure CSS, Tailwind, Bootstrap, etc.). Core pipeline runs offline.")

# ---------------------------------------------------------------------------
# Background HTTP server (localhost only)
# ---------------------------------------------------------------------------
@st.cache_resource
def start_server():
    class WorkspaceHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(WORKSPACE.resolve()), **kwargs)
        def log_message(self, format, *args):
            pass

    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", PORT), WorkspaceHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return PORT

start_server()

# ---------------------------------------------------------------------------
# Local LLM
# ---------------------------------------------------------------------------
local_llm = LLM(
    model="ollama/llama3.2",
    base_url="http://localhost:11434",
    temperature=0.2,
)

# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
@tool("Run UX Audit")
def audit_tool() -> str:
    """Runs a full deterministic + structural UX audit on the currently served page and returns a scored report."""
    result = run_audit(f"{SERVER_URL}/index.html")
    return format_audit_for_llm(result)


@tool("Web Search")
def web_search_tool(query: str) -> str:
    """Search the web for design inspiration, real image URLs, current best practices, or accurate content.
    Use this when it will improve the result (e.g. finding Unsplash image links, color palettes, reference sites).
    Returns a short summary of top results. Works only when internet is available."""
    try:
        import requests
        from bs4 import BeautifulSoup
        headers = {"User-Agent": "Mozilla/5.0 (compatible; UX-Agent/1.0)"}
        url = f"https://html.duckduckgo.com/html/?q={query.replace(' ', '+')}"
        resp = requests.get(url, headers=headers, timeout=8)
        if resp.status_code != 200:
            return "Web search unavailable (no internet or blocked)."
        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        for a in soup.select(".result__a")[:5]:
            title = a.get_text(strip=True)
            href = a.get("href", "")
            results.append(f"- {title}: {href}")
        if not results:
            return "No useful results found."
        return "Web search results:\n" + "\n".join(results)
    except Exception as e:
        return f"Web search failed (likely offline): {e}"

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
if "messages" not in st.session_state:
    st.session_state.messages = []
if "versions" not in st.session_state:
    st.session_state.versions = []
if "current_html" not in st.session_state:
    st.session_state.current_html = ""
if "last_audit" not in st.session_state:
    st.session_state.last_audit = None

# ---------------------------------------------------------------------------
# Sidebar – Project & Settings
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("📁 Project Workspace")
    uploaded = st.file_uploader("Upload HTML or ZIP of static site", type=["html", "htm", "zip"])

    if uploaded:
        if uploaded.name.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(uploaded.getvalue())) as z:
                z.extractall(WORKSPACE)
            st.success("ZIP extracted to workspace/")
        else:
            html = uploaded.getvalue().decode("utf-8")
            (WORKSPACE / "index.html").write_text(html, encoding="utf-8")
            st.session_state.current_html = html
            st.success("index.html saved")

    # Version history
    st.subheader("📜 Versions")
    if st.session_state.versions:
        for i, v in enumerate(reversed(st.session_state.versions[-8:])):
            if st.button(f"{v['time']} – score {v.get('score', '?')}", key=f"ver{i}"):
                (WORKSPACE / "index.html").write_text(v["html"], encoding="utf-8")
                st.session_state.current_html = v["html"]
                st.rerun()

    st.subheader("⚙️ Settings")
    max_iterations = st.slider("Max fix iterations", 1, 4, 2)
    score_threshold = st.slider("Stop when score ≥", 70, 95, 85)
    st.info(f"Serving from http://127.0.0.1:{PORT}/index.html")

# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------
auditor = Agent(
    role="UX & Design Auditor",
    goal="Analyze the live page using the audit tool and produce a clear, prioritized, critical list of issues covering accessibility, content quality, broken assets, and design/theme fit. Be honest about mediocre or generic designs.",
    backstory="""You are a senior UX designer and accessibility engineer. 
You use the audit tool for facts, then add design judgment: placeholder text, broken images, weak hierarchy, generic styling, missing atmosphere, non-functional CTAs, and whether the page actually matches the requested theme (e.g. cyberpunk). 
When the internet is available you may use the Web Search tool to compare against real reference sites or find better image/content ideas.
Never give a perfect score if the page still contains dummy content, broken assets, or feels unfinished. Be concise and concrete.""",
    tools=[audit_tool, web_search_tool],
    llm=local_llm,
    allow_delegation=False,
    verbose=False,
)

fixer = Agent(
    role="Frontend Developer",
    goal="Write or update clean, modern, accessible HTML/CSS/JS. Choose the most appropriate approach (pure CSS, Tailwind, Bootstrap, vanilla JS, etc.) based on the request. Output only raw HTML.",
    backstory="""You are an expert web developer who is not locked into any single framework.
You decide whether to use pure CSS, Tailwind (via CDN), Bootstrap, or lightweight vanilla JS depending on what best serves the design.
When the internet is available, actively use the Web Search tool to find real image URLs (Unsplash, etc.), design references, color palettes, or accurate copy that improves the result.
When fixing an existing page, preserve its current styling approach unless the user asks to change it.
You ALWAYS output only the complete raw HTML document — no markdown fences, no explanations.""",
    tools=[web_search_tool],
    llm=local_llm,
    allow_delegation=False,
    verbose=False,
)

# ---------------------------------------------------------------------------
# Chat UI
# ---------------------------------------------------------------------------
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

prompt = st.chat_input("Ask the agents to audit, fix, or build something…")

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        status = st.status("Agents working…", expanded=True)
        progress_log = []

        # Load current HTML if exists
        index_path = WORKSPACE / "index.html"
        if index_path.exists():
            current_html = index_path.read_text(encoding="utf-8")
            st.session_state.current_html = current_html
        else:
            current_html = ""

        # Simple intent detection
        is_audit_only = any(w in prompt.lower() for w in ["audit", "check", "review", "judge", "evaluate"])
        is_generate = any(w in prompt.lower() for w in ["create", "build", "generate", "make a", "new"]) and not current_html

        if is_generate or not current_html:
            status.write("No existing page → generating new design…")
            task_gen = Task(
                description=f"""Create a complete, modern, accessible HTML page for this request:
{prompt}

Rules:
- Choose the styling approach that fits best (pure CSS in a <style> tag, Tailwind CDN, Bootstrap CDN, or a mix). Do NOT default to Tailwind unless it clearly helps.
- If internet is available, use the Web Search tool to find real theme-appropriate image URLs, design references, or accurate copy before writing the page.
- Include a proper viewport meta tag, semantic HTML, and good contrast.
- Use real image URLs (never src="#") and real copy (no Lorem ipsum).
- Output ONLY the raw HTML document. No markdown fences, no explanations.""",
                expected_output="Raw HTML document",
                agent=fixer,
            )
            crew = Crew(agents=[fixer], tasks=[task_gen], verbose=False)
            result = crew.kickoff()
            new_html = str(result).strip()
            # Guardrail: strip markdown fences if present
            if new_html.startswith("```"):
                new_html = "\n".join(new_html.splitlines()[1:-1])
            index_path.write_text(new_html, encoding="utf-8")
            st.session_state.current_html = new_html
            progress_log.append("Generated initial page")
            status.write("Initial page written.")

        # Iterative audit + fix loop
        final_score = 0
        agent_judgment = ""
        for iteration in range(max_iterations):
            status.write(f"Iteration {iteration + 1}/{max_iterations}: running deterministic audit…")
            audit = run_audit(f"{SERVER_URL}/index.html")
            st.session_state.last_audit = audit
            final_score = audit["score"]
            progress_log.append(f"Audit #{iteration+1}: score {final_score}")

            # Always run the Auditor agent for design judgment when user asks to judge/audit
            if is_audit_only or iteration == 0:
                status.write("Auditor agent reviewing design quality and theme fit…")
                task_audit = Task(
                    description=f"""User request: {prompt}

Use the Run UX Audit tool, then write a critical, honest UI/UX evaluation.
Cover:
- Structural/accessibility issues from the tool
- Content quality (placeholders, dummy text, broken images)
- Visual hierarchy, atmosphere, and whether it matches the intended theme
- Concrete, prioritized fixes

Be strict. Do not give a perfect score if the page still feels unfinished or generic.""",
                    expected_output="A concise critical UX report in plain English with a recommended overall score (0-100).",
                    agent=auditor,
                )
                crew_audit = Crew(agents=[auditor], tasks=[task_audit], verbose=False)
                agent_judgment = str(crew_audit.kickoff()).strip()
                progress_log.append("Auditor agent judgment completed")

            if is_audit_only or final_score >= score_threshold:
                status.write(f"Score {final_score} or audit-only request. Stopping.")
                break

            status.write(f"Score {final_score}. Asking fixer to address top issues…")
            current_html = index_path.read_text(encoding="utf-8")
            audit_text = format_audit_for_llm(audit)

            task_fix = Task(
                description=f"""CURRENT HTML:
```html
{current_html[:6000]}
```

AUDIT FINDINGS:
{audit_text}

USER REQUEST: {prompt}

Fix the highest-priority issues (broken images, placeholders, multiple h1s, dead links, weak theme, accessibility problems).
Preserve the existing styling approach unless the user explicitly asks to change it.
If internet is available, use the Web Search tool to find real replacement image URLs or design references.
Output ONLY the complete corrected raw HTML. No markdown fences.""",
                expected_output="Raw HTML",
                agent=fixer,
            )
            crew = Crew(agents=[fixer], tasks=[task_fix], verbose=False)
            fixed = str(crew.kickoff()).strip()
            if fixed.startswith("```"):
                fixed = "\n".join(fixed.splitlines()[1:-1])
            if "<html" in fixed.lower() or "<!DOCTYPE" in fixed.lower():
                index_path.write_text(fixed, encoding="utf-8")
                st.session_state.current_html = fixed
                progress_log.append(f"Applied fixes from iteration {iteration+1}")
            else:
                progress_log.append("Fixer returned invalid HTML, skipping")
                break

        # Save version
        version = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "html": st.session_state.current_html,
            "score": final_score,
            "prompt": prompt,
        }
        st.session_state.versions.append(version)
        (VERSIONS / f"v_{len(st.session_state.versions):03d}.html").write_text(version["html"], encoding="utf-8")

        # Final response
        audit_summary = format_audit_for_llm(st.session_state.last_audit or {"score": 0, "findings": []})
        judgment_block = f"\n\n**Auditor judgment:**\n{agent_judgment}" if agent_judgment else ""
        response = f"**Done.** Deterministic score: **{final_score}/100**\n\n{audit_summary}{judgment_block}\n\nYou can switch to the Preview / Code / Audit tabs below."
        status.update(label="Finished", state="complete")
        st.markdown(response)
        st.session_state.messages.append({"role": "assistant", "content": response})

# ---------------------------------------------------------------------------
# Results panel
# ---------------------------------------------------------------------------
if st.session_state.current_html:
    tab_preview, tab_code, tab_audit = st.tabs(["👁️ Live Preview", "📄 Code", "📋 Audit Scorecard"])

    with tab_preview:
        col1, col2 = st.columns([1, 3])
        with col1:
            device = st.radio("Device", ["Desktop", "Tablet", "Mobile"], horizontal=True)
            widths = {"Desktop": 1200, "Tablet": 768, "Mobile": 375}
            st.components.v1.html(
                st.session_state.current_html,
                height=600,
                scrolling=True,
            )
            # Note: real iframe width control is limited in components; for presentation this is fine
        st.download_button("Download index.html", st.session_state.current_html, "index.html", "text/html")

    with tab_code:
        st.code(st.session_state.current_html, language="html")

    with tab_audit:
        if st.session_state.last_audit:
            a = st.session_state.last_audit
            st.metric("UX Score", f"{a['score']}/100")
            for f in a["findings"]:
                with st.expander(f"[{f['severity'].upper()}] {f['category']}: {f['issue']}"):
                    if f.get("details"):
                        st.write(f"**Details:** {f['details']}")
                    st.write(f"**Suggested fix:** {f['fix']}")
        else:
            st.info("Run an audit to see the scorecard.")

st.sidebar.markdown("---")
st.sidebar.caption("Assignment-ready: iterative loop, deterministic audit, version history, localhost-only serving, chat UX.")
