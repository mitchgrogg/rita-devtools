"""
rita-mitm — mitmproxy addon with REST API for configuring request delays
and response alterations.

Exposes an HTTP API on port 8082 for runtime configuration. Config is
persisted to /data/config.json (expected to be a Docker volume).

Usage:
    mitmweb -s addon.py
"""

import asyncio
import json
import re
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler


DATA_DIR = "/data"
CONFIG_PATH = f"{DATA_DIR}/config.json"
API_PORT = 8082

DEFAULT_CONFIG = {
    "delays": {
        "global_ms": 0,
        "patterns": [],
    },
    "alterations": [],
}


class ConfigManager:
    """Thread-safe config persistence with pre-compiled regex patterns."""

    def __init__(self, path=CONFIG_PATH):
        self._path = path
        self._lock = threading.Lock()
        self._config = None
        self._delay_patterns = []      # [(compiled_re, delay_ms), ...]
        self._alteration_patterns = []  # [(compiled_re, rule), ...]
        self._load()

    def _load(self):
        try:
            with open(self._path, "r") as f:
                self._config = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self._config = json.loads(json.dumps(DEFAULT_CONFIG))
        self._compile_patterns()

    def _save(self):
        try:
            with open(self._path, "w") as f:
                json.dump(self._config, f, indent=2)
        except OSError:
            pass

    def _compile_patterns(self):
        delays = self._config.get("delays", {})
        self._delay_patterns = []
        for p in delays.get("patterns", []):
            try:
                self._delay_patterns.append((re.compile(p["pattern"]), p["delay_ms"]))
            except re.error:
                pass

        self._alteration_patterns = []
        for rule in self._config.get("alterations", []):
            try:
                self._alteration_patterns.append(
                    (re.compile(rule["url_pattern"]), rule)
                )
            except re.error:
                pass

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
            self._config = config
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

    def get_alterations(self):
        with self._lock:
            return list(self._config.get("alterations", []))

    def add_alteration(self, rule):
        with self._lock:
            self._config.setdefault("alterations", []).append(rule)
            self._mutate()

    def delete_all_alterations(self):
        with self._lock:
            self._config["alterations"] = []
            self._mutate()

    def delete_alteration(self, index):
        with self._lock:
            alterations = self._config.get("alterations", [])
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

    def get_alteration_for_url(self, url):
        """Return the first matching alteration rule dict, or None."""
        with self._lock:
            for regex, rule in self._alteration_patterns:
                if regex.search(url):
                    return dict(rule)
            return None


class APIHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the REST API."""

    config_manager: ConfigManager  # set on the class before server starts

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
        elif path == "/api/alterations":
            self._respond(200, cm.get_alterations())
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
        elif path == "/api/alterations":
            if not isinstance(data, dict) or "url_pattern" not in data:
                self._respond(400, {"error": "expected {\"url_pattern\": \"...\", ...}"})
                return
            try:
                re.compile(data["url_pattern"])
            except re.error as e:
                self._respond(400, {"error": f"invalid regex: {e}"})
                return
            rule = {"url_pattern": data["url_pattern"]}
            if "status_code" in data:
                rule["status_code"] = int(data["status_code"])
            if "body" in data:
                rule["body"] = data["body"]
            cm.add_alteration(rule)
            self._respond(201, cm.get_alterations())
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
        elif path == "/api/alterations" and index is None:
            cm.delete_all_alterations()
            self._respond(200, [])
        elif path == "/api/alterations" and index is not None:
            if cm.delete_alteration(index):
                self._respond(200, cm.get_alterations())
            else:
                self._respond(404, {"error": "index out of range"})
        else:
            self._respond(404, {"error": "not found"})


class APIServer:
    """Runs the HTTP API server in a daemon thread."""

    def __init__(self, config_manager, port=API_PORT):
        self._port = port
        APIHandler.config_manager = config_manager
        self._server = HTTPServer(("0.0.0.0", self._port), APIHandler)

    def start(self):
        thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        thread.start()


class RitaAddon:
    """mitmproxy addon that applies delays and response alterations."""

    def __init__(self):
        self._config = ConfigManager()

    def load(self, loader):
        api = APIServer(self._config)
        api.start()

    async def request(self, flow):
        flow.metadata["rita_start"] = time.monotonic()

    async def response(self, flow):
        url = flow.request.pretty_url

        # Apply alteration (first match wins)
        alteration = self._config.get_alteration_for_url(url)
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
