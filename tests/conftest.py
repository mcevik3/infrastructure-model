from pathlib import Path

import pytest

from infra_model.loader import load_documents


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def documents():
    return [document.data for document in load_documents(ROOT / "examples")]


@pytest.fixture
def inventory(documents):
    return {f"{doc['kind']}/{doc['metadata']['name']}": doc for doc in documents}
