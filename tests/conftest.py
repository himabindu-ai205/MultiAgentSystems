import pytest

from agents.facts import FactsAgent
from data_layer import load_all
from orchestrator.drafter import SCHEME_LABELS
from schemas import Session, new_request_id


@pytest.fixture(scope="session")
def data():
    """The real data, loaded and validated once for the whole test run."""
    return load_all()


@pytest.fixture(scope="session")
def real_answers(data):
    """A real covered FactsResult for every scheme and every section."""
    facts = FactsAgent(data.corpus)
    questions = [
        "exit load of {}",
        "benchmark of {}",
        "minimum sip in {}",
        "key fund facts of {}",
        "how are returns taxed in {}",
        "investment objective of {}",
        "who manages {}",
    ]
    results = []
    for label in SCHEME_LABELS.values():
        for question in questions:
            result = facts.run(question.format(label))
            assert result.status == "covered", (label, question)
            results.append(result)
    return results


@pytest.fixture()
def session():
    return Session(request_id=new_request_id(), redacted_text="my withdrawal is stuck, PAN is [PAN]")
