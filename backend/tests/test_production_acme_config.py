from pathlib import Path
import re

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = REPOSITORY_ROOT / "compose.prod.yml"
NGINX_PATH = REPOSITORY_ROOT / "deploy" / "nginx" / "default.conf"


def _nginx_location(config: str, selector: str) -> str:
    match = re.search(
        rf"location\s+{re.escape(selector)}\s*\{{(?P<body>.*?)\n\s*\}}",
        config,
        re.DOTALL,
    )
    assert match is not None, f"missing Nginx location: {selector}"
    return match.group("body")


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
    assert "ports" not in certbot
    assert {
        name for name, service in services.items() if service.get("ports")
    } == {"nginx"}

    volumes = compose["volumes"]
    assert {"postgres_data", "documents", "certbot_webroot", "letsencrypt_data"} <= set(
        volumes
    )
    assert all("certbot" not in service.get("depends_on", {}) for service in services.values())
