# rita-devtools

rita-devtools (Router for Inspecting Traffic and Attenuation) are a collection of setup scripts that turn a Raspberry Pi (or similarly configured device) into a network testing appliance. The Pi acts as a Wi-Fi hotspot that routes traffic through its ethernet connection, with tools for intercepting and modifying HTTP traffic.

## What It Does

Running `setup.sh` installs and configures five things:

1. **Docker** — Installs Docker Engine from the official Debian repository.
2. **Wi-Fi Hotspot** — Configures the Pi as a 5 GHz Wi-Fi access point using `hostapd` and `dnsmasq`. Devices that connect get internet access routed through the Pi's ethernet (`eth0`) connection via NAT.
3. **mitmproxy + rita-mitm** — Runs [mitmproxy](https://mitmproxy.org/) in a Docker container with the web UI enabled and the [rita-mitm addon](#rita-mitm) loaded, allowing you to inspect and modify HTTP/HTTPS traffic passing through the Pi. The addon exposes a REST API for configuring request delays and request/response alterations at runtime.
4. **rita-devtools-tui** — Installs the latest release of [rita-devtools-tui](https://github.com/mitchgrogg/rita-devtools-tui), a TUI app for configuring rita-mitm, and sets the `RITA_MITM_URL` environment variable to point to the local rita-mitm instance.
5. **SSH Welcome Message** — Adds a custom MOTD that shows the mitmproxy web UI URL when you SSH into the Pi.

## Prerequisites

- A Raspberry Pi (or similar SBC) with both Wi-Fi and Ethernet
- Ethernet cable connected to your router/modem for upstream internet
- Debian-based OS installed and accessible via SSH or a terminal

## Usage

The main entry point is `setup.sh`, which requires three arguments:

```
sudo bash setup.sh <hotspot-ssid> <hotspot-password> <mitmproxy-web-password>
```

- `hotspot-ssid` — The name of the Wi-Fi network the Pi will broadcast
- `hotspot-password` — Password for the Wi-Fi network (minimum 8 characters)
- `mitmproxy-web-password` — Password for the mitmproxy web interface

### Option 1: Clone the repo

```bash
git clone https://github.com/mitchgrogg/rita-devtools.git
cd rita-devtools
sudo bash setup.sh MyNetwork mypassword mitmproxypass
```

### Option 2: Download and extract

```bash
curl -L https://github.com/mitchgrogg/rita-devtools/archive/refs/heads/main.tar.gz | tar xz
cd rita-devtools-main
sudo bash setup.sh MyNetwork mypassword mitmproxypass
```

### Option 3: One-liner

```bash
curl -L https://github.com/mitchgrogg/rita-devtools/archive/refs/heads/main.tar.gz | tar xz && cd rita-devtools-main && sudo bash setup.sh MyNetwork mypassword mitmproxypass
```

Replace `MyNetwork`, `mypassword`, and `mitmproxypass` with your desired values.

## After Setup

- **Connect devices** to the Wi-Fi network you configured.
- **mitmproxy web UI** is available at `http://<pi-eth0-ip>:8081`. Configure devices to use `<pi-eth0-ip>:8080` as their HTTP proxy to inspect traffic.
- **rita-mitm API** is available at `http://<pi-eth0-ip>:8082/api/config` for configuring delays and request/response alterations.
- **rita-devtools-tui** is preinstalled and preconfigured. Run `rita-devtools-tui` to configure rita-mitm from the terminal. It can also be [downloaded](https://github.com/mitchgrogg/rita-devtools-tui/releases) onto another machine to configure rita-mitm remotely — set `RITA_MITM_URL` to `http://<pi-eth0-ip>:8082` before running it.
- **SSH login** will show the mitmproxy web UI URL.

## Running Individual Scripts

Each script in `setup-scripts/` can be run independently:

| Script                               | Purpose                             |
| ------------------------------------ | ----------------------------------- |
| `setup-docker.sh`                    | Install Docker Engine               |
| `setup-hotspot.sh <ssid> <password>` | Configure Wi-Fi hotspot             |
| `setup-mitmproxy.sh <password>`      | Run mitmproxy in Docker             |
| `setup-tui.sh`                       | Install rita-devtools-tui           |
| `setup-ssh-welcome.sh`               | Configure SSH MOTD with status info |

All scripts must be run as root (`sudo`).

## Tested On

- **Raspberry Pi 5** with **Debian Trixie**

## Should Also Work On

- **Raspberry Pi 3 / 4** — Have built-in Wi-Fi and the same hostapd/dnsmasq stack works. The hotspot script is designed for these models.
- **Raspberry Pi OS (Bookworm)** — The Docker install uses the Debian repository and detects the version codename automatically, so it should work on any current Raspberry Pi OS release.
- **Other Debian-based distros** (Ubuntu Server, Armbian) on ARM SBCs with Wi-Fi and Ethernet — The scripts use standard Debian packaging (`apt`), `systemd`, and `iptables`, all of which are available on these platforms. You may need to adjust the Wi-Fi interface name if it's not `wlan0`.
- **x86 Debian/Ubuntu machines** with a Wi-Fi adapter and Ethernet — Nothing in the scripts is ARM-specific except the typical Raspberry Pi use case. The Docker install, mitmproxy, and iptables NAT configuration are all architecture-agnostic.

> **Note:** The hotspot script uses 5 GHz (channel 36, `hw_mode=a`). If your Wi-Fi adapter doesn't support 5 GHz, you'll need to edit `setup-scripts/setup-hotspot.sh` to use 2.4 GHz (`hw_mode=g`, e.g. `CHANNEL=6`).

## rita-mitm

rita-mitm is a mitmproxy addon that exposes a REST API (port 8082) for configuring request delays and request/response alterations at runtime. Configuration is persisted to a Docker volume and survives container restarts.

### TUI App

[rita-devtools-tui](https://github.com/mitchgrogg/rita-devtools-tui) is preinstalled on the rita-devtools host and preconfigured to connect to the local rita-mitm instance. Run `rita-devtools-tui` to use it.

It can also be [downloaded](https://github.com/mitchgrogg/rita-devtools-tui/releases) onto another machine to configure rita-mitm remotely. Set the `RITA_MITM_URL` environment variable to point to the rita-mitm instance (e.g. `export RITA_MITM_URL=http://<pi-eth0-ip>:8082`) before running it.

The REST API can also be used directly or with custom tooling.

### API endpoints

| Method   | Path                           | Description                                                                |
| -------- | ------------------------------ | -------------------------------------------------------------------------- |
| `GET`    | `/api/config`                  | Get entire config                                                          |
| `PUT`    | `/api/config`                  | Replace entire config                                                      |
| `GET`    | `/api/delays`                  | Get delay config                                                           |
| `PUT`    | `/api/delays/global`           | Set global delay `{"delay_ms": 500}`                                       |
| `DELETE` | `/api/delays/global`           | Remove global delay                                                        |
| `GET`    | `/api/delays/patterns`         | List delay patterns                                                        |
| `POST`   | `/api/delays/patterns`         | Add delay pattern `{"pattern": "...", "delay_ms": 1000}`                   |
| `DELETE` | `/api/delays/patterns`         | Delete all delay patterns                                                  |
| `DELETE` | `/api/delays/patterns/<index>` | Delete one by index                                                        |
| `GET`    | `/api/alterations/request`     | List request alteration rules                                              |
| `POST`   | `/api/alterations/request`     | Add request alteration `{"url_pattern": "...", "rewrite_url": "...", "body": "..."}` |
| `DELETE` | `/api/alterations/request`     | Delete all request alterations                                             |
| `DELETE` | `/api/alterations/request/<index>` | Delete one by index                                                    |
| `GET`    | `/api/alterations/response`    | List response alteration rules                                             |
| `POST`   | `/api/alterations/response`    | Add response alteration `{"url_pattern": "...", "status_code": 503, "body": "..."}` |
| `DELETE` | `/api/alterations/response`    | Delete all response alterations                                            |
| `DELETE` | `/api/alterations/response/<index>` | Delete one by index                                                   |
| `GET`    | `/api/alterations`             | _Deprecated_ alias for `/api/alterations/response`                         |
| `POST`   | `/api/alterations`             | _Deprecated_ alias for `/api/alterations/response`                         |
| `DELETE` | `/api/alterations`             | _Deprecated_ alias for `/api/alterations/response`                         |
| `DELETE` | `/api/alterations/<index>`     | _Deprecated_ alias for `/api/alterations/response/<index>`                 |

### Examples

```bash
# Set a global 1-second delay on all proxied requests
curl -X PUT http://localhost:8082/api/delays/global -d '{"delay_ms": 1000}'

# Add a 3-second delay for API requests (overrides global for matching URLs)
curl -X POST http://localhost:8082/api/delays/patterns -d '{"pattern": "/api/.*", "delay_ms": 3000}'

# Simulate a 503 error for health check endpoints
curl -X POST http://localhost:8082/api/alterations/response -d '{"url_pattern": "/health", "status_code": 503, "body": "down"}'

# Point every production API call at staging instead
curl -X POST http://localhost:8082/api/alterations/request -d '{"url_pattern": "^https://api\\.prod\\.com", "rewrite_url": "https://api.staging.com"}'

# Replace the body of every login request
curl -X POST http://localhost:8082/api/alterations/request -d '{"url_pattern": "/v1/login", "body": "{\"user\": \"test\"}"}'

# View current config
curl http://localhost:8082/api/config

# Clear all delay patterns
curl -X DELETE http://localhost:8082/api/delays/patterns
```

### How delays work

Delays use the "minimum total time" approach: the delay value represents the minimum total request time, not additional delay. If you set a 5-second delay and the upstream responds in 2 seconds, the addon sleeps for the remaining 3 seconds. If the upstream takes longer than the configured delay, no extra sleep is added.

Pattern-specific delays override the global delay (first regex match wins). Patterns are matched against the full URL including scheme, host, path, and query string.

### How alterations work

Alterations come in two kinds, each an ordered list of rules under `alterations.request` and `alterations.response` in the config. Within a kind, the first rule whose `url_pattern` regex matches wins; the two kinds are matched independently, so a single flow can be hit by one of each.

**Request alterations** are applied before the request leaves the proxy:

- `rewrite_url` retargets the request. It is a regular expression replacement applied to the portion of the URL that `url_pattern` matched, so the rest of the URL survives and backreferences work — matching `^https://api\.prod\.com` with `rewrite_url` `https://api.staging.com` turns `https://api.prod.com/v1/users?id=3` into `https://api.staging.com/v1/users?id=3`. The `Host` header is updated to follow.

  **Anchor the pattern with `^` when the replacement is a full URL.** Because only the matched portion is replaced, an unanchored pattern splices the replacement into the middle of the URL — pattern `api\.prod\.com` with replacement `https://api.staging.com` would produce `https://https://api.staging.com/...`, an unroutable host that the client sees as a connection failure. rita-mitm detects this, skips the rewrite, and logs a warning naming the rule rather than corrupting the request. Rewrites that only touch the path (pattern `/v1/`, replacement `/v2/`) do not need anchoring. A rewrite whose result is not an absolute `http`/`https` URL, or whose replacement template is invalid, is skipped and logged the same way.
- `body` replaces the outgoing request body.

**Response alterations** are applied to the response coming back, overwriting `status_code` and/or `body`. The upstream request is still made — request alterations cannot short-circuit a flow.

Rules are matched against the full URL including scheme, host, path, and query string. Response alterations and delays match the URL **after** any request rewrite, i.e. where the request actually went.

Configurations written before request alterations existed stored `alterations` as a flat list. Those are migrated to `alterations.response` when they are loaded, and the old `/api/alterations` routes still work.

## License

MIT
