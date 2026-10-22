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
from project_context import build_finding_context, file_tree

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
# Local LLM (configurable)
# ---------------------------------------------------------------------------
# Change model in the sidebar or here. Better models dramatically improve results.
MODEL_NAME = "ollama/llama3.2"  # fallback; overridden by sidebar if set

def get_llm(model: str, temperature: float = 0.3):
    return LLM(
        model=model,
        base_url="http://localhost:11434",
        temperature=temperature,
    )

# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
@tool("Run UX Audit")
def audit_tool() -> str:
    """Runs a full deterministic UX audit on the currently served page."""
    result = run_audit(get_current_page_url(), workspace=WORKSPACE, page_relpath=st.session_state.get("page_relpath", "index.html"))
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
            st.session_state.last_search_ok = False
            return "No useful results found."
        st.session_state.last_search_ok = True
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
if "score_history" not in st.session_state:
    st.session_state.score_history = []
if "last_search_ok" not in st.session_state:
    st.session_state.last_search_ok = None

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def find_best_index(root: Path) -> Path | None:
    """Find the most likely main HTML file after a ZIP extract.
    Prefers index.html with the most text content; ignores .git folders."""
    candidates = []
    for p in root.rglob("*.html"):
        if ".git" in p.parts:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
            word_count = len(text.split())
            candidates.append((word_count, p))
        except Exception:
            continue
    if not candidates:
        return None
    # Prefer files named index.html, then highest word count
    candidates.sort(key=lambda x: (x[1].name.lower() == "index.html", x[0]), reverse=True)
    return candidates[0][1]


def get_current_page_url() -> str:
    """Return the URL the audit/preview should use."""
    rel = st.session_state.get("page_relpath", "index.html")
    return f"{SERVER_URL}/{rel}"

if "page_relpath" not in st.session_state:
    st.session_state.page_relpath = "index.html"

# ---------------------------------------------------------------------------
# Sidebar – Project & Settings
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("📁 Project Workspace")
    uploaded = st.file_uploader("Upload HTML or ZIP of static site", type=["html", "htm", "zip"])

    if uploaded:
        if uploaded.name.endswith(".zip"):
            if WORKSPACE.exists():
                shutil.rmtree(WORKSPACE)
            WORKSPACE.mkdir()
            with zipfile.ZipFile(io.BytesIO(uploaded.getvalue())) as z:
                z.extractall(WORKSPACE)
            best = find_best_index(WORKSPACE)
            if best:
                rel = best.relative_to(WORKSPACE).as_posix()
                st.session_state.page_relpath = rel
                st.session_state.current_html = best.read_text(encoding="utf-8", errors="ignore")
                st.success(f"ZIP extracted. Using: {rel}")
            else:
                st.error("No HTML file found in the ZIP.")
        else:
            html = uploaded.getvalue().decode("utf-8")
            (WORKSPACE / "index.html").write_text(html, encoding="utf-8")
            st.session_state.page_relpath = "index.html"
            st.session_state.current_html = html
            st.success("index.html saved")

    # Version history
    st.subheader("📜 Versions")
    if st.session_state.versions:
        for i, v in enumerate(reversed(st.session_state.versions[-8:])):
            if st.button(f"{v['time']} – score {v.get('score', '?')}", key=f"ver{i}"):
                target = WORKSPACE / st.session_state.page_relpath
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(v["html"], encoding="utf-8")
                st.session_state.current_html = v["html"]
                st.rerun()

    st.subheader("⚙️ Settings")
    model_choice = st.selectbox(
        "Ollama model",
        ["llama3.2", "llama3.1:8b", "qwen2.5:7b", "qwen2.5:14b", "mistral:7b", "gemma2:9b"],
        index=0,
        help="Larger models (8B+) are much stronger at creative generation and accurate auditing."
    )
    max_iterations = st.slider("Max fix iterations", 1, 4, 2)
    score_threshold = st.slider("Stop when score ≥", 70, 95, 85)
    creative_mode = st.checkbox("Creative generation mode", value=True, help="Higher temperature and bolder design prompts")
    st.info(f"Serving: {get_current_page_url()}")
    with st.expander("Project files"):
        st.code(file_tree(WORKSPACE), language="text")

# ---------------------------------------------------------------------------
# Agents (created fresh each run so model/temperature can change)
# ---------------------------------------------------------------------------
def make_agents(model_name: str, creative: bool):
    gen_temp = 0.75 if creative else 0.4
    audit_temp = 0.15  # keep auditing factual

    llm_gen = get_llm(f"ollama/{model_name}", temperature=gen_temp)
    llm_audit = get_llm(f"ollama/{model_name}", temperature=audit_temp)

    auditor = Agent(
        role="UX & Design Auditor",
        goal="Produce a factual, prioritized audit. Base every claim on the tool output or direct observation of the HTML. Do not invent issues.",
        backstory="""You are a strict, evidence-based UX auditor.
Your ONLY source of concrete issues is the text returned by the Run UX Audit tool. 
Copy the tool’s findings exactly. Do not add, rename, or invent any problems. 
Only mention “wrong file” or “empty read” if the tool output itself contains an empty/near-empty finding. 
You may add one short clearly-labeled "Design notes" paragraph. 
Never output JSON or tool-call syntax.""",
        tools=[audit_tool, web_search_tool],
        llm=llm_audit,
        allow_delegation=False,
        verbose=False,
    )

    fixer = Agent(
        role="Frontend Developer",
        goal="Create distinctive, high-quality HTML pages or surgically improve existing ones. Output only raw HTML.",
        backstory="""You are a creative senior frontend developer with strong visual taste. 
You produce pages that feel intentional and atmospheric rather than generic templates. 
You freely choose pure CSS, Tailwind, Bootstrap, or custom styles based on what serves the concept best. 
When the internet is available you MUST use the Web Search tool before writing or fixing pages to gather real image URLs (prefer images.unsplash.com), color references, or accurate content. 
You avoid placeholder text and broken image sources. 
You ALWAYS return only the complete raw HTML document — no markdown fences, no commentary.""",
        tools=[web_search_tool],
        llm=llm_gen,
        allow_delegation=False,
        verbose=False,
    )
    return auditor, fixer

# ---------------------------------------------------------------------------
# File-aware fix context is now in project_context.py
# ---------------------------------------------------------------------------
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

prompt = st.chat_input("Ask the agents to audit, fix, or build something…")
if st.session_state.get("pending_fix"):
    prompt = st.session_state.pop("pending_fix")

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        status = st.status("Agents working…", expanded=True)
        progress_log = []

        # Load current HTML if exists
        index_path = WORKSPACE / st.session_state.page_relpath
        if index_path.exists():
            current_html = index_path.read_text(encoding="utf-8", errors="ignore")
            st.session_state.current_html = current_html
        else:
            current_html = ""

        # Create agents with current settings
        auditor, fixer = make_agents(model_choice, creative_mode)

        # Simple intent detection
        is_audit_only = any(w in prompt.lower() for w in ["audit", "check", "review", "judge", "evaluate"])
        is_generate = any(w in prompt.lower() for w in ["create", "build", "generate", "make a", "new"]) and not current_html

        if is_generate or not current_html:
            status.write("Planning sections and assets…")
            # Lightweight planner guidance injected into the generation prompt
            plan_hint = "Plan first: hero with real image, 2-3 content sections with short descriptions, clear CTA, single h1, stable image URLs."
            creative_hint = """Be bold and atmospheric. Create a distinctive visual identity:
- Unique color palette and strong typography (load real fonts via Google Fonts or system stacks — never invent font names like 'Cyberpunk Modern')
- Layered layout with clear hierarchy, not just stacked centered blocks
- Subtle CSS motion, glows, or texture where it fits the theme
- Real thematic imagery (search for Unsplash URLs) or pure CSS graphics — never via.placeholder.com or similar
Avoid generic centered-card templates.""" if creative_mode else "Keep the design clean and functional."
            task_gen = Task(
                description=f"""Create a complete, modern, accessible HTML page for this request:
{prompt}

{creative_hint}

{plan_hint}

Rules:
- Choose the styling approach that best serves the concept (pure CSS in <style>, Tailwind, Bootstrap, or custom). Do not default to Tailwind.
- If internet is available, use Web Search first for real image URLs, color inspiration, or reference sites.
- Real copy only (no Lorem ipsum). Every featured item needs a short description (aim for 80+ words total on the page).
- Prefer stable image URLs from images.unsplash.com (search first). Never use wallpaperflare, alamy, pinterest, leonardo.ai, or placeholder services.
- Single clear <h1>, semantic structure, viewport meta, good contrast.
- Output ONLY the raw HTML document. No markdown fences, no explanations.""",
                expected_output="Raw HTML document",
                agent=fixer,
            )
            crew = Crew(agents=[fixer], tasks=[task_gen], verbose=False)
            result = crew.kickoff()
            new_html = str(result).strip()
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
            audit = run_audit(get_current_page_url(), workspace=WORKSPACE, page_relpath=st.session_state.page_relpath)
            st.session_state.last_audit = audit
            final_score = audit["score"]
            progress_log.append(f"Audit #{iteration+1}: score {final_score}")

            # Always run the Auditor agent for design judgment when user asks to judge/audit
            if is_audit_only or iteration == 0:
                status.write("Auditor agent reviewing design quality and theme fit…")
                task_audit = Task(
                    description=f"""User request: {prompt}

1. Call the Run UX Audit tool.
2. Output the tool’s findings copied exactly.
3. Only if the tool explicitly reports an empty/near-empty page, say the wrong file may have been loaded.
4. Otherwise add one short Design notes paragraph and a one-sentence overall assessment.
5. Never invent findings.""",
                    expected_output="An evidence-only UX report.",
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
        st.session_state.score_history.append(final_score)
        (VERSIONS / f"v_{len(st.session_state.versions):03d}.html").write_text(version["html"], encoding="utf-8")

        # Final response with per-finding context
        audit_summary = format_audit_for_llm(st.session_state.last_audit or {"score": 0, "findings": []})
        judgment_block = f"\n\n**Auditor judgment:**\n{agent_judgment}" if agent_judgment else ""
        finding_context = build_finding_context(
            st.session_state.last_audit or {},
            st.session_state.page_relpath,
            WORKSPACE,
        )
        response = (
            f"**Done.** Deterministic score: **{final_score}/100**\n\n"
            f"{audit_summary}{judgment_block}\n\n"
            f"**Per-finding context & related files:**\n{finding_context}\n\n"
            f"You can switch to the Preview / Code / Audit tabs below."
        )
        status.update(label="Finished", state="complete")
        st.markdown(response)
        st.session_state.messages.append({"role": "assistant", "content": response})

# ---------------------------------------------------------------------------
# Results panel
# ---------------------------------------------------------------------------
if st.session_state.current_html:
    tab_preview, tab_code, tab_audit = st.tabs(["👁️ Live Preview", "📄 Code", "📋 Audit Scorecard"])

    with tab_preview:
        device = st.radio("Device", ["Desktop", "Tablet", "Mobile"], horizontal=True)
        widths = {"Desktop": 1100, "Tablet": 768, "Mobile": 390}
        w = widths[device]
        wrapped = f"""
        <div style="width:{w}px; margin:0 auto; border:1px solid #444; overflow:auto; height:600px;">
        {st.session_state.current_html}
        </div>
        """
        st.components.v1.html(wrapped, height=620, scrolling=True)
        st.download_button("Download index.html", st.session_state.current_html, "index.html", "text/html")
        if st.session_state.last_search_ok is not None:
            st.caption("Last web search: " + ("succeeded" if st.session_state.last_search_ok else "failed / offline"))

    with tab_code:
        st.code(st.session_state.current_html, language="html")

    with tab_audit:
        if st.session_state.last_audit:
            a = st.session_state.last_audit
            st.metric("UX Score", f"{a['score']}/100")
            if st.session_state.score_history:
                st.line_chart(st.session_state.score_history)
            for i, f in enumerate(a["findings"]):
                with st.expander(f"{i+1}. [{f['severity'].upper()}] {f['issue']}"):
                    st.write(f.get("details") or "")
                    st.write("**Fix:**", f["fix"])
                    if f.get("diff_hint"):
                        st.code(f["diff_hint"])
                    if st.button(f"Apply this fix", key=f"fix_{i}"):
                        st.session_state.pending_fix = f"Fix only this issue: {f['issue']}. {f['fix']}"
                        st.rerun()
            st.markdown(build_finding_context(a, st.session_state.page_relpath, WORKSPACE))
        else:
            st.info("Run an audit to see the scorecard.")

st.sidebar.markdown("---")
st.sidebar.caption("Assignment-ready: iterative loop, deterministic audit, version history, localhost-only serving, chat UX.")
