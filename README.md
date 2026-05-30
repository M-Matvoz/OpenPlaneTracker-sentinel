# OpenPlaneTracker Sentinel

The Sentinel container is the control plane for OpenPlaneTracker. It is responsible for:

- managing SDR containers
- initializing shared volumes and infrastructure
- deploying the UI container
- deploying the reverse proxy container
- deploying and updating the server container
- checking current and latest versions of Sentinel and Server containers

## Reverse proxy

Sentinel starts an `nginx` container named `openplanetracker-proxy`.

**Public routing (WAN-accessible):**

- `/` → `live-viewer:8000` (UI)
- `/api/*` → `openplanetracker-server:8080` (API)

The proxy listens on:

- host port `80` for HTTP
- host port `443` for HTTPS when TLS certs are present

**Admin access is NOT proxied** — see "LAN-only access" below.

## LAN-only access

Sentinel and server admin endpoints are **only accessible from the LAN**, not through the public nginx proxy:

- **Sentinel UI/Admin**: `http://<host>:8001` (LAN-only)
- **Server admin endpoints**: `http://<host>:8089/admin/*` (LAN-only)

These ports are bound to all LAN interfaces but not exposed externally through the proxy. This keeps the control plane secure and separate from the public-facing API and UI.

## TLS / HTTPS setup

TLS is optional. If the following files exist in the shared config volume, Sentinel will generate an HTTPS-enabled nginx config automatically:

- `/config/tls/fullchain.pem`
- `/config/tls/privkey.pem`

### Recommended volume layout

Put the certificate files into the shared `opt_config_data` volume under:

- `tls/fullchain.pem`
- `tls/privkey.pem`

From inside the nginx container, these are read as:

- `/etc/nginx/conf.d/tls/fullchain.pem`
- `/etc/nginx/conf.d/tls/privkey.pem`

### Example

If you are using Docker and want to copy existing certificates into the shared config volume, place them before Sentinel starts or mount them into the volume using a one-off container. Example file names:

- `fullchain.pem`
- `privkey.pem`

### Behavior when TLS is enabled

When both files are present:

- HTTP on port `80` redirects to HTTPS
- HTTPS on port `443` serves the UI and API through the same hostname

TLS certificates should be placed in the shared config volume at:

- `tls/fullchain.pem`
- `tls/privkey.pem`

### Behavior when TLS is not enabled

When the cert files are missing:

- nginx serves plain HTTP on port `80`
- no HTTPS listener is created

## Container and port mapping summary

**LAN-only (internal):**

- `sentinel` → port `8001` (Sentinel UI and admin API)
- `openplanetracker-server` → port `8089` (server admin endpoints)

**Public (through nginx proxy):**

- `openplanetracker-proxy` → host port `80` (HTTP) and optionally `443` (HTTPS)
  - Forwards `/` to `live-viewer:8000` (UI)
  - Forwards `/api/*` to `openplanetracker-server:8080` (API)

**Internal-only (Docker network):**

- `live-viewer` → internal port `8000` (UI, not exposed directly)
- `openplanetracker-server` → internal port `8080` (API server, proxied to `/api/*` on public proxy)

## Admin routing

Admin endpoints are **not proxied through nginx** to maintain security:

- Sentinel admin UI is accessible directly on port `8001` (LAN-only)
- Server admin endpoints are accessible directly on port `8089` (LAN-only)
- Sentinel proxies internal admin requests to the server over the Docker network

This architecture ensures:

- Admin control plane is isolated from the public API
- Sensitive operations (enable/disable external connections, register peers) are not exposed to WAN
- Only the `/api/*` endpoints and UI are accessible from external networks through the nginx proxy

## Shared pre-shared key

Sentinel generates a shared pre-shared key and stores it in the shared config volume as:

- `shared_psk.txt`

Sentinel passes that key to the server container as `OPT_SHARED_PSK`.

The key is used for:

- registering peer instances
- authenticating outgoing collated data pushes
- accepting incoming external data connections

## External connections and peer registration

The Sentinel admin UI can:

- enable or disable external incoming data connections
- register another instance using the shared key
- configure the server to push `collated.json` to another server every 2 seconds

Server admin endpoints exist internally and are proxied by Sentinel:

- `/admin/state`
- `/admin/external-connections/enable`
- `/admin/peers/register`
- `/admin/push-config`

## Version checks and one-click deploy

The Sentinel UI shows the current and latest image tags for:

- `sentinel`
- `openplanetracker-server`

If a newer image is available, the UI can trigger a one-click redeploy.

### Update flow

- Sentinel update endpoint pulls `mmatvoz/openplanetracker-sentinel:latest` and recreates the `sentinel` container.
- Server update endpoint pulls `mmatvoz/openplanetracker-server:latest` and recreates the `openplanetracker-server` container.

### Notes

- The server remains the only component that serves `/api/collated`.
- The UI container does not read ADSB containers directly.
- If you update Sentinel from the web UI, the page may disconnect while the container restarts.

## Notes

- The proxy only forwards traffic; it does not serve the collated JSON itself.
- The server remains the only component that serves `/api/collated`.
- UI code should use the proxy for browser requests and the Docker network name for internal container-to-container requests.
