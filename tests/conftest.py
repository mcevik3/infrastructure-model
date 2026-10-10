from pathlib import Path

import pytest

from infra_model.loader import load_documents


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def documents():
    # Keep the original regression scenarios independent of additional examples.
    return [document.data for name in ("amst", "chameleon-like", "fabric-like")
            for document in load_documents(ROOT / "examples" / name)]


@pytest.fixture
def inventory(documents):
    return {f"{doc['kind']}/{doc['metadata']['name']}": doc for doc in documents}


@pytest.fixture
def amst_network_scope(documents):
    # Placement regressions move the FABRIC-like nodes to AMST inventory.
    for document in documents:
        if document["kind"] == "Network":
            document["spec"]["siteRefs"].append("site/AMST")
