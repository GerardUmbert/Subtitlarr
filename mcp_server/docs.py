"""Points the connecting assistant at Subtitlarr's own real documentation
on GitHub, so "how do I configure/use this app" questions get answered
from the actual current docs (fetched live by the assistant itself) —
not guessed from training data that may be stale or simply wrong about
this specific app.

Deliberately just a curated list of URLs + descriptions, not fetched or
baked in here: the assistant already has its own web-fetch capability,
this always reflects the current master branch (not just whatever's in
this specific running image), and there's no HTML-parsing/extraction
code to maintain here at all.
"""

_BASE_RAW = "https://raw.githubusercontent.com/GerardUmbert/Subtitlarr/master"
_REPO = "https://github.com/GerardUmbert/Subtitlarr"

DOC_LINKS = [
    {
        "title": "README",
        "url": f"{_BASE_RAW}/README.md",
        "description": (
            "Start here for setup and overview: how it works, translation "
            "engines and the recommended cascade order, comparing engines, "
            "this MCP server, Docker/Compose/Unraid install, all "
            "configuration options, known limitations."
        ),
    },
    {
        "title": "Full docs / settings reference",
        "url": "https://gerardumbert.github.io/Subtitlarr/docs.html",
        "description": (
            "The deep-reference page: the recommended multi-pass workflow "
            "in depth, how the engine cascade and content-block bisection "
            "actually behave, rate limits/cooldown, every setting with its "
            "default, queue-uploads explained, MCP tool reference, "
            "troubleshooting, and known limitations."
        ),
    },
    {
        "title": "Install guide",
        "url": "https://gerardumbert.github.io/Subtitlarr/install.html",
        "description": "Step-by-step install instructions for Docker, Docker Compose, and Unraid.",
    },
    {
        "title": "Engine setup / API keys",
        "url": "https://gerardumbert.github.io/Subtitlarr/api-keys.html",
        "description": "Where to get an API key for each cloud translation engine (Gemini, NVIDIA NIM, OpenRouter, Groq) and how to set up a local engine (Ollama, llama.cpp).",
    },
    {
        "title": "Changelog",
        "url": f"{_BASE_RAW}/CHANGELOG.md",
        "description": "Every notable change, release by release — useful for \"was this already fixed / when was X added\" questions.",
    },
    {
        "title": "AGENTS.md",
        "url": f"{_BASE_RAW}/AGENTS.md",
        "description": (
            "Contributor/dev-agent orientation: running it locally, "
            "running tests, project-specific gotchas (batch size vs. "
            "context window, the reconciler's response-parsing quirks, "
            "manual translation, this MCP server's own dev setup), and "
            "what not to do without being asked."
        ),
    },
]

# _REPO is kept for potential future links (e.g. specific source files
# or issues) even though nothing currently uses it — plans/ was
# considered but is gitignored (local design notes, never pushed), so
# there is no public URL for it.
