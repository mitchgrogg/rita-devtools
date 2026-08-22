"""
rita-mitm — mitmproxy addon with REST API for configuring request delays
and request/response alterations.

Exposes an HTTP API on port 8082 for runtime configuration. Config is
persisted to /data/config.json (expected to be a Docker volume).

Usage:
    mitmweb -s addon.py
"""

import asyncio
import json
import logging
import re
import threading
import time
import urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler


logger = logging.getLogger(__name__)


DATA_DIR = "/data"
CONFIG_PATH = f"{DATA_DIR}/config.json"
API_PORT = 8082

ALTERATION_KINDS = ("request", "response")

DEFAULT_CONFIG = {
    "delays": {
        "global_ms": 0,
        "patterns": [],
    },
    "alterations": {
        "request": [],
        "response": [],
    },
}


def _normalize_config(config):
    """Coerce a config dict into the canonical shape.

    Migrates the legacy flat `alterations` list into `alterations.response`.
    """
    if not isinstance(config, dict):
        return json.loads(json.dumps(DEFAULT_CONFIG))

    alterations = config.get("alterations")
    if isinstance(alterations, list):
        config["alterations"] = {"request": [], "response": alterations}
    elif isinstance(alterations, dict):
        config["alterations"] = {
            kind: alterations[kind] if isinstance(alterations.get(kind), list) else []
            for kind in ALTERATION_KINDS
        }
    else:
        config["alterations"] = {kind: [] for kind in ALTERATION_KINDS}

    return config


class ConfigManager:
    """Thread-safe config persistence with pre-compiled regex patterns."""

    def __init__(self, path=CONFIG_PATH):
        self._path = path
        self._lock = threading.Lock()
        self._config = None
        self._delay_patterns = []       # [(compiled_re, delay_ms), ...]
        self._alteration_patterns = {}  # {kind: [(compiled_re, rule), ...]}
        self._load()

    def _load(self):
        try:
            with open(self._path, "r") as f:
                self._config = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self._config = json.loads(json.dumps(DEFAULT_CONFIG))
        self._config = _normalize_config(self._config)
        self._mutate()  # persists the migrated shape on first load

    def _save(self):
        try:
            with open(self._path, "w") as f:
                json.dump(self._config, f, indent=2)
        except OSError:
            pass

    def _compile_alterations(self, kind):
        compiled = []
        for rule in self._config["alterations"].get(kind, []):
            try:
                compiled.append((re.compile(rule["url_pattern"]), rule))
            except (re.error, KeyError, TypeError):
                pass
        return compiled

    def _compile_patterns(self):
        delays = self._config.get("delays", {})
        self._delay_patterns = []
        for p in delays.get("patterns", []):
            try:
                self._delay_patterns.append((re.compile(p["pattern"]), p["delay_ms"]))
            except (re.error, KeyError, TypeError):
                pass

        self._alteration_patterns = {
            kind: self._compile_alterations(kind) for kind in ALTERATION_KINDS
        }

    def _mutate(self):
        """Call after any config mutation to persist and recompile."""
        self._compile_patterns()
        self._save()

    # --- Public API (all acquire lock) ---

    def get_config(self):
        with self._lock:
            return json.loads(json.dumps(self._config))

    def set_config(self, config):
        with self._lock:
            self._config = _normalize_config(config)
            self._mutate()

    def get_delays(self):
        with self._lock:
            return json.loads(json.dumps(self._config.get("delays", DEFAULT_CONFIG["delays"])))

    def set_global_delay(self, delay_ms):
        with self._lock:
            self._config.setdefault("delays", {})["global_ms"] = delay_ms
            self._mutate()

    def remove_global_delay(self):
        with self._lock:
            self._config.setdefault("delays", {})["global_ms"] = 0
            self._mutate()

    def get_delay_patterns(self):
        with self._lock:
            return list(self._config.get("delays", {}).get("patterns", []))

    def add_delay_pattern(self, pattern, delay_ms):
        with self._lock:
            self._config.setdefault("delays", {}).setdefault("patterns", []).append(
                {"pattern": pattern, "delay_ms": delay_ms}
            )
            self._mutate()

    def delete_all_delay_patterns(self):
        with self._lock:
            self._config.setdefault("delays", {})["patterns"] = []
            self._mutate()

    def delete_delay_pattern(self, index):
        with self._lock:
            patterns = self._config.get("delays", {}).get("patterns", [])
            if 0 <= index < len(patterns):
                patterns.pop(index)
                self._mutate()
                return True
            return False

    def get_alterations(self, kind):
        with self._lock:
            return list(self._config["alterations"].get(kind, []))

    def add_alteration(self, kind, rule):
        with self._lock:
            self._config["alterations"].setdefault(kind, []).append(rule)
            self._mutate()

    def delete_all_alterations(self, kind):
        with self._lock:
            self._config["alterations"][kind] = []
            self._mutate()

    def delete_alteration(self, kind, index):
        with self._lock:
            alterations = self._config["alterations"].get(kind, [])
            if 0 <= index < len(alterations):
                alterations.pop(index)
                self._mutate()
                return True
            return False

    # --- Fast matching (called from mitmproxy hooks) ---

    def get_delay_for_url(self, url):
        """Return delay in ms for the given URL. Pattern-specific overrides global (first match wins)."""
        with self._lock:
            for regex, delay_ms in self._delay_patterns:
                if regex.search(url):
                    return delay_ms
            return self._config.get("delays", {}).get("global_ms", 0)

    def get_alteration_for_url(self, url, kind):
        """Return (rule, compiled_regex) for the first matching rule, or (None, None).

        The regex comes back because `rewrite_url` is an re.sub replacement
        applied to the pattern that matched.
        """
        with self._lock:
            for regex, rule in self._alteration_patterns[kind]:
                if regex.search(url):
                    return dict(rule), regex
            return None, None


# Routes that address a list of alteration rules, mapped to the kind they touch.
# "/api/alterations" is a deprecated alias kept for older clients.
ALTERATION_PATHS = {
    "/api/alterations": "response",
    "/api/alterations/request": "request",
    "/api/alterations/response": "response",
}


def _build_alteration(kind, data):
    """Build a stored rule from POST data, dropping unknown fields."""
    rule = {"url_pattern": data["url_pattern"]}
    if kind == "response":
        if "status_code" in data:
            rule["status_code"] = int(data["status_code"])
    else:
        if "rewrite_url" in data:
            rule["rewrite_url"] = str(data["rewrite_url"])
    if "body" in data:
        rule["body"] = data["body"]
    return rule


class APIHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the REST API."""

    config_manager: ConfigManager  # set on the class before server starts

    # Without this an idle connection blocks its worker thread indefinitely,
    # because rfile.readline() waits for a request line that never arrives.
    timeout = 10

    def log_message(self, format, *args):
        pass  # suppress default stderr logging

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return None
        return json.loads(self.rfile.read(length))

    def _respond(self, status, body=None):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if body is not None:
            self.wfile.write(json.dumps(body).encode())

    def _parse_path(self):
        """Return (path, trailing_index_or_None)."""
        path = self.path.rstrip("/")
        parts = path.rsplit("/", 1)
        if len(parts) == 2:
            try:
                return parts[0], int(parts[1])
            except ValueError:
                pass
        return path, None

    def do_GET(self):
        cm = self.config_manager
        path, _ = self._parse_path()

        if path == "/api/config":
            self._respond(200, cm.get_config())
        elif path == "/api/delays":
            self._respond(200, cm.get_delays())
        elif path == "/api/delays/patterns":
            self._respond(200, cm.get_delay_patterns())
        elif path in ALTERATION_PATHS:
            self._respond(200, cm.get_alterations(ALTERATION_PATHS[path]))
        else:
            self._respond(404, {"error": "not found"})

    def do_PUT(self):
        cm = self.config_manager
        path, _ = self._parse_path()

        try:
            data = self._read_json()
        except (json.JSONDecodeError, ValueError):
            self._respond(400, {"error": "invalid JSON"})
            return

        if path == "/api/config":
            if not isinstance(data, dict):
                self._respond(400, {"error": "expected JSON object"})
                return
            cm.set_config(data)
            self._respond(200, cm.get_config())
        elif path == "/api/delays/global":
            if not isinstance(data, dict) or "delay_ms" not in data:
                self._respond(400, {"error": "expected {\"delay_ms\": <int>}"})
                return
            cm.set_global_delay(int(data["delay_ms"]))
            self._respond(200, cm.get_delays())
        else:
            self._respond(404, {"error": "not found"})

    def do_POST(self):
        cm = self.config_manager
        path, _ = self._parse_path()

        try:
            data = self._read_json()
        except (json.JSONDecodeError, ValueError):
            self._respond(400, {"error": "invalid JSON"})
            return

        if path == "/api/delays/patterns":
            if not isinstance(data, dict) or "pattern" not in data or "delay_ms" not in data:
                self._respond(400, {"error": "expected {\"pattern\": \"...\", \"delay_ms\": <int>}"})
                return
            try:
                re.compile(data["pattern"])
            except re.error as e:
                self._respond(400, {"error": f"invalid regex: {e}"})
                return
            cm.add_delay_pattern(data["pattern"], int(data["delay_ms"]))
            self._respond(201, cm.get_delay_patterns())
        elif path in ALTERATION_PATHS:
            kind = ALTERATION_PATHS[path]
            if not isinstance(data, dict) or "url_pattern" not in data:
                self._respond(400, {"error": "expected {\"url_pattern\": \"...\", ...}"})
                return
            try:
                re.compile(data["url_pattern"])
            except re.error as e:
                self._respond(400, {"error": f"invalid regex: {e}"})
                return
            cm.add_alteration(kind, _build_alteration(kind, data))
            self._respond(201, cm.get_alterations(kind))
        else:
            self._respond(404, {"error": "not found"})

    def do_DELETE(self):
        cm = self.config_manager
        path, index = self._parse_path()

        if path == "/api/delays/global" and index is None:
            cm.remove_global_delay()
            self._respond(200, cm.get_delays())
        elif path == "/api/delays/patterns" and index is None:
            cm.delete_all_delay_patterns()
            self._respond(200, [])
        elif path == "/api/delays/patterns" and index is not None:
            if cm.delete_delay_pattern(index):
                self._respond(200, cm.get_delay_patterns())
            else:
                self._respond(404, {"error": "index out of range"})
        elif path in ALTERATION_PATHS:
            kind = ALTERATION_PATHS[path]
            if index is None:
                cm.delete_all_alterations(kind)
                self._respond(200, [])
            elif cm.delete_alteration(kind, index):
                self._respond(200, cm.get_alterations(kind))
            else:
                self._respond(404, {"error": "index out of range"})
        else:
            self._respond(404, {"error": "not found"})


class APIServer:
    """Runs the HTTP API server in a daemon thread."""

    def __init__(self, config_manager, port=API_PORT):
        self._port = port
        APIHandler.config_manager = config_manager
        # Threaded: the single-threaded HTTPServer handles requests inline, so
        # one stalled client would block the whole API — and then block done()
        # below, which runs on mitmproxy's event loop.
        self._server = ThreadingHTTPServer(("0.0.0.0", self._port), APIHandler)

    def start(self):
        thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        thread.start()

    def stop(self):
        # shutdown() blocks until serve_forever has exited. done() runs on
        # mitmproxy's event loop, so cap the wait rather than risk freezing the
        # proxy; server_close() releases the port either way.
        stopper = threading.Thread(target=self._server.shutdown, daemon=True)
        stopper.start()
        stopper.join(timeout=5)
        self._server.server_close()


# A replacement that starts with a scheme is meant to replace the whole origin.
_ABSOLUTE_URL = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")


def _rewrite_url(regex, replacement, url):
    """Apply a `rewrite_url` substitution, or return None if it is unusable.

    `rewrite_url` is an re.sub replacement, so it only rewrites the portion of
    the URL that matched. An absolute replacement therefore only makes sense
    for a pattern anchored to the start of the URL — anything else splices it
    into the middle and yields an unroutable host such as "api.prod.comhttps",
    which the client sees as a connection failure.
    """
    match = regex.search(url)
    if match is None:
        return None

    if match.start() != 0 and _ABSOLUTE_URL.match(replacement):
        logger.warning(
            "rita-mitm: skipping rewrite_url %r for %r — the pattern %r matches "
            "at offset %d, not the start of the URL, so an absolute replacement "
            "would corrupt it. Anchor the pattern with ^ to replace the origin.",
            replacement, url, regex.pattern, match.start(),
        )
        return None

    try:
        new_url = regex.sub(replacement, url)
    except re.error as e:
        logger.warning("rita-mitm: invalid rewrite_url replacement %r: %s", replacement, e)
        return None

    parts = urllib.parse.urlsplit(new_url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        logger.warning(
            "rita-mitm: skipping rewrite_url %r for %r — the result %r is not an "
            "absolute http(s) URL.", replacement, url, new_url,
        )
        return None

    return new_url


class RitaAddon:
    """mitmproxy addon that applies delays and request/response alterations."""

    def __init__(self):
        self._config = ConfigManager()
        self._api = None

    def load(self, loader):
        self._api = APIServer(self._config)
        self._api.start()

    def done(self):
        # mitmproxy hot-reloads this script whenever the file changes. Without
        # releasing the port here, the outgoing instance keeps listening and the
        # incoming one dies with "Address already in use", leaving the REST API
        # bound to a stale config that the running hooks never see.
        if self._api is not None:
            self._api.stop()
            self._api = None

    async def request(self, flow):
        flow.metadata["rita_start"] = time.monotonic()

        # Apply alteration to the outgoing request (first match wins)
        url = flow.request.pretty_url
        alteration, regex = self._config.get_alteration_for_url(url, "request")
        if not alteration:
            return

        if "rewrite_url" in alteration:
            new_url = _rewrite_url(regex, alteration["rewrite_url"], url)
            if new_url is not None and new_url != url:
                # Setting .url also updates the Host header and h2 authority.
                flow.request.url = new_url

        if "body" in alteration:
            flow.request.text = alteration["body"]

    async def response(self, flow):
        # Note: after a rewrite_url this is the rewritten URL, so response
        # alterations and delays match where the request actually went.
        url = flow.request.pretty_url

        # Apply alteration (first match wins)
        alteration, _ = self._config.get_alteration_for_url(url, "response")
        if alteration:
            if "status_code" in alteration:
                flow.response.status_code = alteration["status_code"]
            if "body" in alteration:
                flow.response.text = alteration["body"]

        # Apply delay (minimum total time approach)
        delay_ms = self._config.get_delay_for_url(url)
        if delay_ms > 0:
            start = flow.metadata.get("rita_start")
            if start is not None:
                elapsed_ms = (time.monotonic() - start) * 1000
                remaining = (delay_ms - elapsed_ms) / 1000
                if remaining > 0:
                    await asyncio.sleep(remaining)


addons = [RitaAddon()]
