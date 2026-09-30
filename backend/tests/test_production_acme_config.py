from pathlib import Path
import re
import subprocess

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = REPOSITORY_ROOT / "compose.prod.yml"
NGINX_PATH = REPOSITORY_ROOT / "deploy" / "nginx" / "default.conf"
HTTPS_NGINX_PATH = REPOSITORY_ROOT / "deploy" / "nginx" / "default.https.conf"


def _nginx_location(config: str, selector: str) -> str:
    matches = _nginx_locations(config, selector)
    assert matches, f"missing Nginx location: {selector}"
    return matches[0]


def _nginx_locations(config: str, selector: str) -> list[str]:
    return [
        match.group("body")
        for match in re.finditer(
            rf"location\s+{re.escape(selector)}\s*\{{(?P<body>.*?)\n\s*\}}",
            config,
            re.DOTALL,
        )
    ]


def test_acme_challenge_is_served_directly_without_proxying():
    config = NGINX_PATH.read_text(encoding="utf-8")

    assert "server_name flowmind.hilbertspace.cloud;" in config
    assert "listen 443" not in config
    assert "ssl_certificate" not in config
    acme = _nginx_location(config, "^~ /.well-known/acme-challenge/")
    assert "root /var/www/certbot;" in acme
    assert "try_files $uri =404;" in acme
    assert "proxy_pass" not in acme


def test_existing_api_and_frontend_routes_are_preserved():
    config = NGINX_PATH.read_text(encoding="utf-8")

    assert "proxy_pass $backend$request_uri;" in _nginx_location(
        config, "= /api/v1"
    )
    assert "proxy_pass $backend$request_uri;" in _nginx_location(
        config, "^~ /api/v1/"
    )
    assert "proxy_pass $frontend$request_uri;" in _nginx_location(config, "/")


def test_https_config_preserves_acme_and_routes_application_over_tls():
    config = HTTPS_NGINX_PATH.read_text(encoding="utf-8")

    assert "listen 443 ssl;" in config
    assert (
        "ssl_certificate /etc/letsencrypt/live/flowmind.hilbertspace.cloud/fullchain.pem;"
        in config
    )
    assert (
        "ssl_certificate_key /etc/letsencrypt/live/flowmind.hilbertspace.cloud/privkey.pem;"
        in config
    )
    assert "add_header Strict-Transport-Security" not in config

    acme = _nginx_location(config, "^~ /.well-known/acme-challenge/")
    assert "root /var/www/certbot;" in acme
    assert "try_files $uri =404;" in acme
    assert "return 308" not in acme
    assert "proxy_pass" not in acme

    root_locations = _nginx_locations(config, "/")
    assert len(root_locations) == 2
    assert (
        "return 308 https://flowmind.hilbertspace.cloud$request_uri;"
        in root_locations[0]
    )
    assert "proxy_pass $frontend$request_uri;" in root_locations[1]
    assert "proxy_pass $backend$request_uri;" in _nginx_location(
        config, "= /api/v1"
    )
    assert "proxy_pass $backend$request_uri;" in _nginx_location(
        config, "^~ /api/v1/"
    )


def test_certbot_is_optional_and_uses_separate_persistent_volumes():
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    services = compose["services"]
    certbot = services["certbot"]
    nginx = services["nginx"]

    assert certbot["image"] == "certbot/certbot:v5.8.0"
    assert certbot["profiles"] == ["acme"]
    assert certbot["restart"] == "no"
    assert "certbot_webroot:/var/www/certbot" in certbot["volumes"]
    assert "letsencrypt_data:/etc/letsencrypt" in certbot["volumes"]
    assert "certbot_webroot:/var/www/certbot:ro" in nginx["volumes"]
    assert "letsencrypt_data:/etc/letsencrypt:ro" in nginx["volumes"]
    assert (
        "./deploy/nginx/${NGINX_CONFIG:-default.conf}:/etc/nginx/conf.d/default.conf:ro"
        in nginx["volumes"]
    )
    assert nginx["environment"]["NGINX_CONFIG"] == "${NGINX_CONFIG:-default.conf}"
    assert any(str(port).endswith(":80") for port in nginx["ports"])
    assert any(str(port).endswith(":443:443") for port in nginx["ports"])
    assert "ports" not in certbot
    assert {
        name for name, service in services.items() if service.get("ports")
    } == {"nginx"}

    volumes = compose["volumes"]
    assert {"postgres_data", "documents", "certbot_webroot", "letsencrypt_data"} <= set(
        volumes
    )
    assert all("certbot" not in service.get("depends_on", {}) for service in services.values())


def test_no_certificate_or_private_key_material_is_tracked():
    tracked = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
    ).stdout.decode("utf-8").split("\0")

    assert not any(
        Path(path).suffix.lower() in {".pem", ".key", ".crt"}
        for path in tracked
        if path
    )
    for relative_path in tracked:
        if not relative_path:
            continue
        try:
            content = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        assert not re.search(
            r"-----BEGIN (?:RSA |EC |ENCRYPTED )?PRIVATE KEY-----", content
        )


def test_nginx_correlates_structured_logs_and_keeps_metrics_private():
    for path in (NGINX_PATH, HTTPS_NGINX_PATH):
        config = path.read_text(encoding="utf-8")
        assert "log_format flowmind escape=json" in config
        assert '"request_id":"$request_id"' in config
        assert '"path":"$log_path"' in config
        assert "$request_uri" not in config.split("log_format flowmind", 1)[1].split(";", 1)[0]
        assert "proxy_set_header X-Request-ID $request_id;" in config
        assert "add_header X-Request-ID $request_id always;" in config
        assert "proxy_hide_header X-Request-ID;" in config
        for forbidden in ("$http_authorization", "$request_body"):
            assert forbidden not in config
        assert "return 404;" in _nginx_location(config, "= /metrics")
