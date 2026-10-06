import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from infra_model import InfrastructureModel, LoadError
from infra_model.cli import main
from infra_model.loader import load_documents
from conftest import ROOT


SITE = "apiVersion: infra.model/v0alpha1\nkind: Site\nmetadata: {name: test}\nspec: {}\n"


def test_recursive_loading_multi_document_and_empty_documents(tmp_path):
    (tmp_path / "nested").mkdir()
    (tmp_path / "a.yaml").write_text(SITE)
    (tmp_path / "nested" / "b.YML").write_text("---\n" + SITE.replace("name: test", "name: second") + "---\n# empty\n---\n" + SITE.replace("name: test", "name: third"))
    (tmp_path / "ignored.txt").write_text("invalid: [")
    docs = load_documents(tmp_path)
    assert [doc.data["metadata"]["name"] for doc in docs] == ["test", "second", "third"]
    assert docs[-1].source.endswith("b.YML#document=3")
    assert InfrastructureModel.load(tmp_path).validate().document_count == 3


def test_single_explicit_file_without_yaml_extension(tmp_path):
    file = tmp_path / "model"
    file.write_text(SITE)
    assert InfrastructureModel.load(file).validate().document_count == 1


@pytest.mark.parametrize("content,match", [
    ("kind: [", "while parsing"),
    ("- one\n- two\n", "must be a mapping"),
    ("hello", "must be a mapping"),
    ("kind: Site\nkind: Server", "duplicate key"),
    ("1: value", "keys must be strings"),
    ("!!python/object/apply:os.system ['exit 0']", "could not determine a constructor"),
    ("a: &cycle {b: *cycle}", "recursive YAML aliases"),
    ("a: .nan", "non-finite"),
    ("a: .inf", "non-finite"),
    ("a: !!set {x: null}", "unsupported YAML value"),
])
def test_bad_yaml(tmp_path, content, match):
    file = tmp_path / "bad.yaml"
    file.write_text(content)
    with pytest.raises(LoadError, match=match):
        InfrastructureModel.load(file)


@pytest.mark.parametrize("target", ["missing", "empty", "comments"])
def test_no_documents_is_error(tmp_path, target):
    path = tmp_path / target
    if target == "empty":
        path.mkdir()
    elif target == "comments":
        path.write_text("# empty\n---\n")
    with pytest.raises(LoadError):
        InfrastructureModel.load(path)


def test_invalid_utf8_is_load_error(tmp_path):
    file = tmp_path / "bad.yaml"
    file.write_bytes(b"\xff\xfe")
    with pytest.raises(LoadError):
        InfrastructureModel.load(file)


def test_yaml_syntax_error_identifies_second_document(tmp_path):
    file = tmp_path / "multi.yaml"
    file.write_text(SITE + "---\nkind: [")
    with pytest.raises(LoadError, match="multi.yaml#document=2"):
        InfrastructureModel.load(file)


def test_yaml_aliases_are_copied_and_dates_preserved(tmp_path):
    file = tmp_path / "alias.yaml"
    file.write_text(SITE + "status:\n  first: &shared {observedAt: 2026-01-01}\n  second: *shared\n")
    doc = load_documents(file)[0].data
    assert doc["status"]["first"]["observedAt"] == "2026-01-01"
    doc["status"]["first"]["observedAt"] = "changed"
    assert doc["status"]["second"]["observedAt"] == "2026-01-01"


@pytest.mark.parametrize("documents", [[], [None], [{"a": float("inf")}], [{1: "value"}], [{"a": {1, 2}}]])
def test_in_memory_inputs_are_json_compatible(documents):
    with pytest.raises(LoadError):
        InfrastructureModel.from_documents(documents)


def test_in_memory_cycle_rejected():
    cycle = {}
    cycle["loop"] = cycle
    with pytest.raises(LoadError, match="recursive"):
        InfrastructureModel.from_documents([cycle])


def test_cli_success(capsys):
    assert main(["validate", str(ROOT / "examples")]) == 0
    captured = capsys.readouterr()
    assert captured.out == "Valid: 20 documents, 70 entities, 2 warnings\n"
    assert captured.err.count("UNKNOWN") == 2


def test_cli_invalid_model(tmp_path, capsys):
    file = tmp_path / "bad.yaml"
    file.write_text(SITE + "---\napiVersion: infra.model/v0alpha1\nkind: Server\nmetadata: {name: broken}\nspec: {siteRef: site/missing}\n")
    assert main(["validate", str(file)]) == 1
    captured = capsys.readouterr()
    assert "bad.yaml#document=2/spec/siteRef" in captured.err
    assert "unresolved_reference" in captured.err
    assert captured.out == ""


def test_cli_load_error(tmp_path, capsys):
    assert main(["validate", str(tmp_path / "missing")]) == 1
    assert "path does not exist" in capsys.readouterr().err


def test_cli_warnings(documents, inventory, tmp_path, capsys):
    node = inventory["Cluster/openstack-lab"]["spec"]["nodes"][0]
    inventory["Cluster/openstack-lab"]["spec"]["nodes"] = [node]
    node.update(realization={"type": "baremetal"}, placement={"resourceRef": "server/amst-w2", "siteRef": "site/AMST"})
    inventory["Server/amst-w2"]["spec"].pop("profileRef")
    file = tmp_path / "warnings.yaml"
    file.write_text(yaml.safe_dump_all(documents))
    assert main(["validate", str(file)]) == 0
    captured = capsys.readouterr()
    assert "5 warnings" in captured.out
    assert "warning:" in captured.err and "unevaluated_requirement" in captured.err


@pytest.mark.parametrize("args", [[], ["validate"], ["unknown"]])
def test_cli_usage_errors(args):
    with pytest.raises(SystemExit) as caught:
        main(args)
    assert caught.value.code == 2


@pytest.mark.parametrize("entrypoint", ["module", "script"])
def test_installed_entrypoints_from_outside_repo(tmp_path, entrypoint):
    command = [sys.executable, "-m", "infra_model"] if entrypoint == "module" else [str(Path(sys.executable).with_name("infra-model"))]
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    result = subprocess.run([*command, "validate", str(ROOT / "examples")], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "20 documents" in result.stdout
