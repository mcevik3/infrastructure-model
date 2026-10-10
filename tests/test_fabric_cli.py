import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from infra_model.cli import main
from infra_model.loader import load_documents
from conftest import ROOT


CONFIG = ROOT / "adapter-configs/fabric.yaml"


@pytest.mark.parametrize("example,service", [("fabric-like", "L2Bridge"), ("multi-site", "L2STS")])
@pytest.mark.parametrize("entrypoint", ["module", "script"])
def test_cli_yaml_determinism_and_installed_entrypoints(tmp_path, example, service, entrypoint):
    command = ([sys.executable, "-m", "infra_model"] if entrypoint == "module"
               else [str(Path(sys.executable).with_name("infra-model"))])
    command += ["render", "fabric", str(ROOT / "examples" / example), "--config", str(CONFIG)]
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    results = [subprocess.run(command, cwd=tmp_path, env=env, capture_output=True) for _ in range(2)]
    for result in results:
        assert result.returncode == 0, result.stderr.decode()
        assert result.stderr.count(b"UNKNOWN") == 2
        assert b"UNKNOWN" not in result.stdout and b"Valid:" not in result.stdout
        document = yaml.safe_load(result.stdout)
        assert document["site_topology_networks"]["networks"]["net1"]["type"] == service
    assert results[0].stdout == results[1].stdout
    assert list(tmp_path.iterdir()) == []  # The CLI writes only stdout/stderr.


@pytest.mark.parametrize("selection", ["openstack-lab", "cluster/openstack-lab"])
def test_cli_selection_from_all_examples(capsys, selection):
    assert main(["render", "fabric", str(ROOT / "examples"), "--config", str(CONFIG), "--cluster", selection]) == 0
    captured = capsys.readouterr()
    assert len(yaml.safe_load(captured.out)["site_topology_nodes"]["nodes"]) == 2
    assert "UNKNOWN" in captured.err


@pytest.mark.parametrize("path,extra,reason", [
    ("examples", [], "--cluster"),
    ("examples/fabric-like", ["--cluster", "missing"], "unknown Cluster"),
    ("examples/device-requirements", [], "only vm"),
    ("missing", [], "path does not exist"),
])
def test_cli_errors_never_emit_partial_yaml(capsys, path, extra, reason):
    assert main(["render", "fabric", str(ROOT / path), "--config", str(CONFIG), *extra]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert reason in captured.err


def test_cli_bad_config(capsys, tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("apiVersion: wrong\n")
    assert main(["render", "fabric", str(ROOT / "examples/fabric-like"), "--config", str(path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and str(path) in captured.err


def test_cli_config_required():
    with pytest.raises(SystemExit) as caught:
        main(["render", "fabric", "examples/fabric-like"])
    assert caught.value.code == 2


def test_cli_host_fixture_is_deterministic(tmp_path):
    documents = [doc.data for doc in load_documents(ROOT / "examples/fabric-like")]
    cluster = next(doc for doc in documents if doc["kind"] == "Cluster")
    cluster["spec"]["nodes"][0]["placement"] = {"hostRef": "server/requested-host"}
    documents.append({"apiVersion": "infra.model/v0alpha1", "kind": "Server",
                      "metadata": {"name": "requested-host"}, "spec": {"siteRef": "site/lab-site"}})
    source = tmp_path / "host.yaml"
    source.write_text(yaml.safe_dump_all(documents))
    config_data = yaml.safe_load(CONFIG.read_text())
    config_data["workers"] = {"server/requested-host": "provider-worker-7"}
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump(config_data))
    command = [sys.executable, "-m", "infra_model", "render", "fabric", str(source), "--config", str(config)]
    first, second = [subprocess.run(command, capture_output=True) for _ in range(2)]
    assert first.returncode == second.returncode == 0, (first.stderr, second.stderr)
    assert first.stdout == second.stdout
    rendered = yaml.safe_load(first.stdout)["site_topology_nodes"]["nodes"]
    assert rendered["node1"]["worker"] == "provider-worker-7"
    assert rendered["node1"]["site"] == "SRI"
    assert rendered["node2"]["worker"] == ""
