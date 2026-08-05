"""In-memory tracker for an in-progress Ollama model pull. A pull can take
many minutes, so the UI polls this state rather than holding one HTTP
request open for the whole download."""
from dataclasses import dataclass, field


@dataclass
class PullState:
    model: str = ""
    active: bool = False
    status: str = ""
    completed: int = 0
    total: int = 0
    error: str | None = None
    done: bool = False

    @property
    def pct(self) -> float:
        if not self.total:
            return 0.0
        return round(100 * self.completed / self.total, 1)


current_pull = PullState()


async def run_pull(provider, model: str) -> None:
    global current_pull
    current_pull = PullState(model=model, active=True, status="starting")
    try:
        async for event in provider.pull_model(model):
            current_pull.status = event.get("status", "")
            current_pull.completed = event.get("completed", current_pull.completed)
            current_pull.total = event.get("total", current_pull.total)
            if event.get("status") == "success":
                current_pull.done = True
        current_pull.done = True
    except Exception as exc:  # noqa: BLE001 - surface any failure to the UI, don't crash the app
        current_pull.error = str(exc)
    finally:
        current_pull.active = False
