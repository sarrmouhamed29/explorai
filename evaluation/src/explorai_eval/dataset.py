import json
from dataclasses import dataclass
from pathlib import Path

# Shared fixture: lives in ai/ to keep the annotated set close to the AI package.
FIXTURE_PATH: Path = (
    Path(__file__).parent.parent.parent.parent
    / "ai"
    / "tests"
    / "fixtures"
    / "questions"
    / "questions.json"
)


@dataclass(frozen=True)
class Question:
    question: str
    paper: str
    expected_quote: str | None
    expected_answer: str | None = None


def load_questions(path: Path = FIXTURE_PATH) -> list[Question]:
    """Load questions from *path*, raising ValueError on missing required keys."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    questions: list[Question] = []
    for entry in raw:
        for key in ("question", "paper", "expected_quote"):
            if key not in entry:
                raise ValueError(f"Missing required key {key!r} in entry: {entry!r}")
        questions.append(
            Question(
                question=entry["question"],
                paper=entry["paper"],
                expected_quote=entry["expected_quote"],
                expected_answer=entry.get("expected_answer"),
            )
        )
    return questions
