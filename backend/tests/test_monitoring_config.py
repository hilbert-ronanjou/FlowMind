import ast
import json
import re
import subprocess
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
MONITORING = ROOT / "deploy" / "monitoring"
OVERLAY = yaml.safe_load((ROOT / "compose.monitoring.yml").read_text(encoding="utf-8"))


def test_overlay_is_optional_and_cannot_override_application_services():
    assert set(OVERLAY) == {"services", "volumes", "secrets"}
    assert set(OVERLAY["services"]) == {"prometheus", "grafana"}
    for service in OVERLAY["services"].values():
        assert service["profiles"] == ["monitoring"]
        assert service["networks"] == ["app"]
        assert "network_mode" not in service
        assert "privileged" not in service
        assert "backend" not in service.get("depends_on", {})
    assert "ports" not in OVERLAY["services"]["prometheus"]
    assert OVERLAY["services"]["grafana"]["ports"] == ["127.0.0.1:3000:3000"]


def test_monitoring_has_pinned_official_images_and_bounded_resources():
    services = OVERLAY["services"]
    assert services["prometheus"]["image"] == "prom/prometheus:v3.13.3"
    assert services["grafana"]["image"] == "grafana/grafana:13.2.3"
    for service in services.values():
        assert service["mem_limit"] == "512m"
        assert service["memswap_limit"] == "512m"
        assert service["cpus"] == 0.5
        assert service["logging"]["options"] == {"max-size": "10m", "max-file": "3"}
    command = services["prometheus"]["command"]
    assert "--storage.tsdb.retention.time=7d" in command
    assert "--storage.tsdb.retention.size=1GB" in command
    assert not any("enable-admin-api" in value or "enable-lifecycle" in value for value in command)
    assert "prometheus_data:/prometheus" in services["prometheus"]["volumes"]
    assert "grafana_data:/var/lib/grafana" in services["grafana"]["volumes"]
    assert set(OVERLAY["volumes"]) == {"prometheus_data", "grafana_data"}


def test_prometheus_scrapes_only_the_existing_private_backend_endpoint():
    config = yaml.safe_load((MONITORING / "prometheus.yml").read_text(encoding="utf-8"))
    assert config["global"]["scrape_interval"] == "30s"
    assert config["global"]["scrape_timeout"] == "10s"
    assert set(config) == {"global", "scrape_configs"}
    assert config["scrape_configs"] == [{
        "job_name": "flowmind-backend",
        "metrics_path": "/metrics",
        "scheme": "http",
        "static_configs": [{"targets": ["backend:8000"]}],
    }]


def test_grafana_requires_login_and_reads_a_git_ignored_file_secret():
    grafana = OVERLAY["services"]["grafana"]
    env = grafana["environment"]
    assert "GF_SECURITY_ADMIN_PASSWORD" not in env
    assert env["GF_SECURITY_ADMIN_PASSWORD__FILE"] == "/run/secrets/grafana_admin_password"
    assert env["GF_AUTH_ANONYMOUS_ENABLED"] == "false"
    assert env["GF_AUTH_BASIC_ENABLED"] == "true"
    assert env["GF_AUTH_DISABLE_LOGIN_FORM"] == "false"
    assert env["GF_USERS_ALLOW_SIGN_UP"] == "false"
    assert env["GF_UNIFIED_ALERTING_ENABLED"] == "false"
    assert grafana["secrets"] == ["grafana_admin_password"]
    assert OVERLAY["secrets"] == {
        "grafana_admin_password": {"file": "./secrets/grafana_admin_password.txt"}
    }
    subprocess.run(
        ["git", "check-ignore", "--no-index", "secrets/grafana_admin_password.txt"],
        cwd=ROOT, check=True, capture_output=True,
    )
    assert not subprocess.run(
        ["git", "ls-files", "--", "secrets/"],
        cwd=ROOT, check=True, capture_output=True,
    ).stdout.strip()


def test_grafana_provisions_one_read_only_dashboard_and_default_private_source():
    provisioning = MONITORING / "grafana" / "provisioning"
    datasource = yaml.safe_load(
        (provisioning / "datasources" / "prometheus.yml").read_text(encoding="utf-8")
    )["datasources"]
    assert len(datasource) == 1
    assert datasource[0]["uid"] == "flowmind-prometheus"
    assert datasource[0]["url"] == "http://prometheus:9090"
    assert datasource[0]["access"] == "proxy"
    assert datasource[0]["isDefault"] is True
    assert datasource[0]["editable"] is False
    assert datasource[0]["jsonData"]["timeInterval"] == "30s"
    provider = yaml.safe_load(
        (provisioning / "dashboards" / "flowmind.yml").read_text(encoding="utf-8")
    )["providers"]
    assert len(provider) == 1
    assert provider[0]["allowUiUpdates"] is False
    assert provider[0]["options"]["path"] == "/etc/grafana/dashboards"
    assert len(list((MONITORING / "grafana" / "dashboards").glob("*.json"))) == 1
    mounts = OVERLAY["services"]["grafana"]["volumes"]
    assert "./deploy/monitoring/grafana/provisioning:/etc/grafana/provisioning:ro" in mounts
    assert "./deploy/monitoring/grafana/dashboards:/etc/grafana/dashboards:ro" in mounts


def _unique_json_keys(pairs):
    result = {}
    for key, value in pairs:
        assert key not in result, f"duplicate dashboard JSON key: {key}"
        result[key] = value
    return result


def test_dashboard_uses_all_existing_metric_families_and_only_bounded_labels():
    dashboard = json.loads(
        (MONITORING / "grafana" / "dashboards" / "flowmind.json").read_text(encoding="utf-8"),
        object_pairs_hook=_unique_json_keys,
    )
    assert dashboard["uid"] == "flowmind-overview"
    assert dashboard["refresh"] == "30s"
    assert dashboard["editable"] is False
    panels = dashboard["panels"]
    assert len(panels) == len({panel["id"] for panel in panels}) == 11
    # Read definitions rather than inventing a separate set of application metrics.
    tree = ast.parse((ROOT / "backend/app/core/observability.py").read_text(encoding="utf-8"))
    defined = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in {"Counter", "Histogram"}:
                defined[ast.literal_eval(node.args[0])] = node.func.id
    observed = set()
    for panel in panels:
        assert panel["datasource"] == {"type": "prometheus", "uid": "flowmind-prometheus"}
        for target in panel["targets"]:
            expr = target["expr"]
            assert 'job="flowmind-backend"' in expr
            assert not re.search(r"request_id|user_id|course_id|document_id", expr)
            for metric in re.findall(r"([a-z_][a-z0-9_]*)(?=\{)", expr):
                if metric == "up":
                    continue
                base = metric.removesuffix("_bucket")
                assert base in defined, f"unknown metric: {metric}"
                observed.add(base)
                if defined[base] == "Counter":
                    assert "rate(" in expr or "increase(" in expr
                else:
                    assert "histogram_quantile(" in expr
                    assert "rate(" in expr
            groupings = re.findall(r"by\s*\(([^)]+)\)", expr)
            assert all(
                label.strip() in {"le", "operation", "outcome", "direction"}
                for grouping in groupings for label in grouping.split(",")
            )
    assert observed == set(defined)
    tokens = next(panel for panel in panels if "tokens" in panel["title"])
    assert tokens["fieldConfig"]["defaults"]["noValue"] == "Not reported"
    assert "vector(0)" not in tokens["targets"][0]["expr"]


def test_promtool_low_traffic_fixtures_use_the_actual_dashboard_queries():
    dashboard = json.loads(
        (MONITORING / "grafana/dashboards/flowmind.json").read_text(encoding="utf-8")
    )
    expressions = {
        target["expr"].replace("$__rate_interval", "2m").replace("$__range", "6h")
        for panel in dashboard["panels"] for target in panel["targets"]
    }
    fixtures = yaml.safe_load(
        (MONITORING / "tests/flowmind-promql.test.yml").read_text(encoding="utf-8")
    )
    assert len(fixtures["tests"]) == 3
    for scenario in fixtures["tests"]:
        for case in scenario["promql_expr_test"]:
            assert case["expr"] in expressions
