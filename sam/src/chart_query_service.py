#!/usr/bin/env python3
"""
chart_query_service.py
======================
Deterministic HTTP microservice for chart-ready JSON from chart_data.db.

Why this exists:
- Keep chart SQL and shaping deterministic and fast.
- Return compact, renderer-friendly payloads (labels + values + stats).
- Let LLM agents focus on narrative/interpretation rather than table shaping.
"""

import json
import math
import os
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import chart_db
import pipeline_config as _config

log = _config.get_logger("ChartQuery")


HOST = os.getenv("CHART_QUERY_HOST", "127.0.0.1")
PORT = int(os.getenv("CHART_QUERY_PORT", "8010"))
DEFAULT_WARNING_TEMP = float(os.getenv("WARNING_TEMP", "58.0"))
DEFAULT_CRITICAL_TEMP = float(os.getenv("CRITICAL_TEMP", "65.0"))

# Optional auth: when CHART_QUERY_API_KEY is set, every request except /health and OPTIONS
# must present it via header `X-API-Key: <key>` or query param `?key=<key>`. Empty/unset = open
# (backward-compatible default).
API_KEY = os.getenv("CHART_QUERY_API_KEY", "").strip()

# Optional CORS allowlist: comma-separated origins (e.g. "https://demo.example.com,http://localhost:3000").
# Unset or "*" preserves the historical wide-open Access-Control-Allow-Origin: * behaviour.
_raw_origins = os.getenv("CHART_QUERY_ALLOWED_ORIGINS", "*").strip()
ALLOWED_ORIGINS: list[str] | None
if _raw_origins in ("", "*"):
    ALLOWED_ORIGINS = None  # sentinel: emit "*"
else:
    ALLOWED_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _bounded_iso_window(minutes: int) -> tuple[str, str]:
    with chart_db.get_connection() as conn:
        row = conn.execute(
            """
            SELECT
              strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?) AS start_ts,
              strftime('%Y-%m-%dT%H:%M:%SZ', 'now') AS end_ts
            """,
            (f"-{minutes} minutes",),
        ).fetchone()
    return row["start_ts"], row["end_ts"]


def _coerce_iso_utc(ts: str) -> str:
    """
    Best-effort normalize an ISO timestamp to UTC Z form.
    Accepts trailing 'Z' or offset forms parseable by datetime.fromisoformat.
    """
    if not ts:
        raise ValueError("timestamp is empty")
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _parse_int(value: str, default: int, min_v: int, max_v: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(min_v, min(max_v, n))


def _parse_float(value: str | None, default: float) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _parse_bool(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _downsample_rows(rows: list[dict], max_points: int) -> list[dict]:
    if max_points <= 0 or len(rows) <= max_points:
        return rows
    stride = math.ceil(len(rows) / max_points)
    sampled = rows[::stride]
    if sampled[-1] != rows[-1]:
        sampled.append(rows[-1])
    return sampled[:max_points]


def _series_rows(
    sensor_id: str,
    source: str,
    minutes: int,
    resolution: str,
    window_start: str | None = None,
    window_end: str | None = None,
) -> tuple[list[dict], dict]:
    if window_start and window_end:
        start_ts = _coerce_iso_utc(window_start)
        end_ts = _coerce_iso_utc(window_end)
        if start_ts > end_ts:
            raise ValueError("window_start must be <= window_end")
        window_mode = "absolute"
    else:
        start_ts, end_ts = _bounded_iso_window(minutes)
        window_mode = "relative"

    if resolution == "points":
        sql = """
            SELECT ts, value, zone, source
            FROM chart_points
            WHERE sensor_id = ?
              AND ts >= ?
              AND ts <= ?
              AND (? = 'all' OR source = ?)
            ORDER BY ts ASC
        """
        params = (sensor_id, start_ts, end_ts, source, source)
    else:
        table = "chart_rollups_1m" if resolution == "1m" else "chart_rollups_10s"
        sql = f"""
            SELECT bucket_ts AS ts, source,
                   min_v, max_v, last_v, count_v,
                   (sum_v * 1.0 / NULLIF(count_v, 0)) AS avg_v
            FROM {table}
            WHERE sensor_id = ?
              AND bucket_ts >= ?
              AND bucket_ts <= ?
              AND (? = 'all' OR source = ?)
            ORDER BY bucket_ts ASC
        """
        params = (sensor_id, start_ts, end_ts, source, source)

    with chart_db.get_connection() as conn:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

    meta = {
        "sensor_id": sensor_id,
        "source": source,
        "resolution": resolution,
        "window_mode": window_mode,
        "window_start_utc": start_ts,
        "window_end_utc": end_ts,
        "returned_rows": len(rows),
    }
    return rows, meta


def _series_payload(rows: list[dict], max_points: int, value_key: str) -> dict:
    sampled = _downsample_rows(rows, max_points)
    labels = [r["ts"][11:16] if isinstance(r["ts"], str) and len(r["ts"]) >= 16 else r["ts"] for r in sampled]
    values = [r.get(value_key) for r in sampled]
    numeric = [float(v) for v in values if isinstance(v, (int, float))]

    stats = {
        "min_value": min(numeric) if numeric else None,
        "max_value": max(numeric) if numeric else None,
        "avg_value": (sum(numeric) / len(numeric)) if numeric else None,
        "source_row_count": len(rows),
        "rendered_row_count": len(sampled),
        "source_min_ts": rows[0]["ts"] if rows else None,
        "source_max_ts": rows[-1]["ts"] if rows else None,
        "rendered_min_ts": sampled[0]["ts"] if sampled else None,
        "rendered_max_ts": sampled[-1]["ts"] if sampled else None,
    }

    return {
        "rows": sampled,
        "labels_hhmm_utc": labels,
        "values": values,
        "stats": stats,
    }


def _build_plotly_spec(
    x_vals: list,
    y_vals: list,
    *,
    title: str,
    value_key: str,
    source: str,
    show_thresholds: bool,
    warning_temp: float,
    critical_temp: float,
) -> dict:
    numeric_vals = [float(v) for v in y_vals if isinstance(v, (int, float))]
    y_min = min(numeric_vals) if numeric_vals else None
    y_max = max(numeric_vals) if numeric_vals else None
    y_pad = 1.0 if y_min is None or y_max is None else max(1.0, (y_max - y_min) * 0.08)

    spec = {
        "data": [
            {
                "type": "scatter",
                "mode": "lines+markers",
                "name": source,
                "x": x_vals,
                "y": y_vals,
                "line": {"shape": "linear", "width": 2},
                "marker": {"size": 5},
                "hovertemplate": "ts=%{x}<br>value=%{y:.2f}<extra></extra>",
            }
        ],
        "layout": {
            "title": {"text": title},
            "xaxis": {"title": "timestamp (UTC)", "type": "date"},
            "yaxis": {"title": value_key, "rangemode": "tozero"},
            "template": "plotly_white",
            "hovermode": "x unified",
        },
    }

    if y_min is not None and y_max is not None:
        spec["layout"]["yaxis"]["range"] = [min(y_min, warning_temp, critical_temp) - y_pad, max(y_max, warning_temp, critical_temp) + y_pad]

    if show_thresholds and x_vals:
        # Horizontal threshold overlays for quick visual zone-crossing detection.
        shapes = [
            {
                "type": "line",
                "xref": "x",
                "yref": "y",
                "x0": x_vals[0],
                "x1": x_vals[-1],
                "y0": warning_temp,
                "y1": warning_temp,
                "line": {"color": "#f59e0b", "width": 1.5, "dash": "dot"},
            },
            {
                "type": "line",
                "xref": "x",
                "yref": "y",
                "x0": x_vals[0],
                "x1": x_vals[-1],
                "y0": critical_temp,
                "y1": critical_temp,
                "line": {"color": "#ef4444", "width": 1.5, "dash": "dot"},
            },
        ]
        annotations = [
            {
                "x": x_vals[-1],
                "y": warning_temp,
                "xref": "x",
                "yref": "y",
                "text": f"WARNING {warning_temp:.1f}°C",
                "showarrow": False,
                "xanchor": "left",
                "font": {"size": 11, "color": "#b45309"},
                "bgcolor": "rgba(245,158,11,0.10)",
            },
            {
                "x": x_vals[-1],
                "y": critical_temp,
                "xref": "x",
                "yref": "y",
                "text": f"CRITICAL {critical_temp:.1f}°C",
                "showarrow": False,
                "xanchor": "left",
                "font": {"size": 11, "color": "#991b1b"},
                "bgcolor": "rgba(239,68,68,0.10)",
            },
        ]
        spec["layout"]["shapes"] = shapes
        spec["layout"]["annotations"] = annotations

    return spec


class ChartQueryHandler(BaseHTTPRequestHandler):
    server_version = "chart-query-service/1.0"

    def _add_cors_headers(self) -> None:
        # If no allowlist is configured, retain historic wide-open behaviour.
        # When configured, echo the request Origin only if it matches the allowlist
        # (omit the header otherwise — browsers will block cross-origin reads).
        if ALLOWED_ORIGINS is None:
            self.send_header("Access-Control-Allow-Origin", "*")
        else:
            origin = self.headers.get("Origin", "")
            if origin and origin in ALLOWED_ORIGINS:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        # Allow X-API-Key so browsers may send the auth header on cross-origin fetches.
        self.send_header("Access-Control-Allow-Headers", "X-API-Key, Content-Type")

    def _is_authorized(self, query: dict) -> bool:
        # When no key is configured, the service is open (preserves prior behaviour).
        if not API_KEY:
            return True
        # Prefer header (no leakage to access logs), fall back to ?key= for browser-clickable URLs (Slack).
        header_key = self.headers.get("X-API-Key", "").strip()
        if header_key and header_key == API_KEY:
            return True
        qp_key = (query.get("key") or [""])[0].strip()
        if qp_key and qp_key == API_KEY:
            return True
        return False

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._add_cors_headers()
        self.end_headers()

    def _send_json(self, payload: dict, status: int = HTTPStatus.OK):
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self._add_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        q = parse_qs(parsed.query)

        try:
            # /health is intentionally always open (used by Docker/EC2 healthchecks and operators).
            if path == "/health":
                self._handle_health()
                return
            if not self._is_authorized(q):
                self._send_json(
                    {"error": "Unauthorized", "hint": "Provide CHART_QUERY_API_KEY via X-API-Key header or ?key= query param."},
                    status=HTTPStatus.UNAUTHORIZED,
                )
                return
            if path == "/sensors":
                self._handle_sensors(q)
                return
            if path == "/series":
                self._handle_series(q)
                return
            if path == "/plotly-spec":
                self._handle_plotly_spec(q)
                return
            if path == "/plotly-html":
                self._handle_plotly_html(q)
                return
            self._send_json({"error": "Not found", "path": path}, status=HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def log_message(self, fmt: str, *args):
        # Route BaseHTTPRequestHandler's access logs through our logger.
        # Per-request lines at DEBUG so default INFO stays quiet under normal traffic.
        log.debug("%s - %s", self.address_string(), fmt % args)

    def _handle_health(self):
        with chart_db.get_connection() as conn:
            r1 = conn.execute("SELECT COUNT(*) AS n, MIN(bucket_ts) AS min_ts, MAX(bucket_ts) AS max_ts FROM chart_rollups_1m").fetchone()
            rp = conn.execute("SELECT COUNT(*) AS n, MIN(ts) AS min_ts, MAX(ts) AS max_ts FROM chart_points").fetchone()
        self._send_json(
            {
                "status": "ok",
                "service": "chart-query-service",
                "db_path": chart_db.get_db_path(),
                "now_utc": _utc_now_iso(),
                "tables": {
                    "chart_rollups_1m": dict(r1),
                    "chart_points": dict(rp),
                },
            }
        )

    def _handle_sensors(self, q: dict):
        minutes = _parse_int((q.get("minutes") or ["120"])[0], default=120, min_v=1, max_v=24 * 60)
        start_ts, end_ts = _bounded_iso_window(minutes)
        with chart_db.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT sensor_id, source, COUNT(*) AS buckets,
                       MIN(bucket_ts) AS first_ts, MAX(bucket_ts) AS last_ts
                FROM chart_rollups_1m
                WHERE bucket_ts >= ? AND bucket_ts <= ?
                GROUP BY sensor_id, source
                ORDER BY buckets DESC, sensor_id ASC
                """,
                (start_ts, end_ts),
            ).fetchall()
        self._send_json(
            {
                "window_start_utc": start_ts,
                "window_end_utc": end_ts,
                "rows": [dict(r) for r in rows],
            }
        )

    def _handle_series(self, q: dict):
        sensor_id = (q.get("sensor_id") or [""])[0].strip()
        if not sensor_id:
            self._send_json({"error": "Missing required query parameter: sensor_id"}, status=HTTPStatus.BAD_REQUEST)
            return

        source = (q.get("source") or ["filtered"])[0].strip().lower()
        if source not in ("filtered", "suppressed", "all"):
            source = "filtered"

        resolution = (q.get("resolution") or ["1m"])[0].strip().lower()
        if resolution not in ("1m", "10s", "points"):
            resolution = "1m"

        minutes = _parse_int((q.get("minutes") or ["60"])[0], default=60, min_v=1, max_v=24 * 60)
        max_points = _parse_int((q.get("max_points") or ["120"])[0], default=120, min_v=1, max_v=5000)
        window_start = (q.get("window_start") or [None])[0]
        window_end = (q.get("window_end") or [None])[0]
        value_key = (q.get("value_key") or ["avg_v"])[0].strip().lower()
        if value_key not in ("avg_v", "last_v", "value", "min_v", "max_v"):
            value_key = "avg_v"

        rows, meta = _series_rows(
            sensor_id=sensor_id,
            source=source,
            minutes=minutes,
            resolution=resolution,
            window_start=window_start,
            window_end=window_end,
        )
        if resolution == "points" and value_key in ("avg_v", "last_v", "min_v", "max_v"):
            value_key = "value"
        if resolution != "points" and value_key == "value":
            value_key = "avg_v"

        payload = _series_payload(rows=rows, max_points=max_points, value_key=value_key)
        self._send_json(
            {
                "meta": {
                    **meta,
                    "requested_value_key": value_key,
                    "max_points": max_points,
                    "subset_rule": f"source='{source}'" if source != "all" else "source in ('filtered','suppressed')",
                },
                **payload,
            }
        )

    def _handle_plotly_spec(self, q: dict):
        sensor_id = (q.get("sensor_id") or [""])[0].strip()
        if not sensor_id:
            self._send_json({"error": "Missing required query parameter: sensor_id"}, status=HTTPStatus.BAD_REQUEST)
            return

        source = (q.get("source") or ["filtered"])[0].strip().lower()
        if source not in ("filtered", "suppressed", "all"):
            source = "filtered"

        resolution = (q.get("resolution") or ["1m"])[0].strip().lower()
        if resolution not in ("1m", "10s", "points"):
            resolution = "1m"

        minutes = _parse_int((q.get("minutes") or ["60"])[0], default=60, min_v=1, max_v=24 * 60)
        max_points = _parse_int((q.get("max_points") or ["120"])[0], default=120, min_v=1, max_v=5000)
        window_start = (q.get("window_start") or [None])[0]
        window_end = (q.get("window_end") or [None])[0]
        show_thresholds = _parse_bool((q.get("show_thresholds") or [None])[0], default=True)
        warning_temp = _parse_float((q.get("warning_temp") or [None])[0], DEFAULT_WARNING_TEMP)
        critical_temp = _parse_float((q.get("critical_temp") or [None])[0], DEFAULT_CRITICAL_TEMP)
        value_key = (q.get("value_key") or ["avg_v"])[0].strip().lower()
        if value_key not in ("avg_v", "last_v", "value", "min_v", "max_v"):
            value_key = "avg_v"

        rows, meta = _series_rows(
            sensor_id=sensor_id,
            source=source,
            minutes=minutes,
            resolution=resolution,
            window_start=window_start,
            window_end=window_end,
        )
        if resolution == "points" and value_key in ("avg_v", "last_v", "min_v", "max_v"):
            value_key = "value"
        if resolution != "points" and value_key == "value":
            value_key = "avg_v"

        payload = _series_payload(rows=rows, max_points=max_points, value_key=value_key)
        x_vals = [r["ts"] for r in payload["rows"]]
        y_vals = payload["values"]

        title = f"{sensor_id} {source} {resolution} ({minutes}m)"
        plotly_spec = _build_plotly_spec(
            x_vals,
            y_vals,
            title=title,
            value_key=value_key,
            source=source,
            show_thresholds=show_thresholds,
            warning_temp=warning_temp,
            critical_temp=critical_temp,
        )

        self._send_json(
            {
                "meta": {
                    **meta,
                    "requested_value_key": value_key,
                    "max_points": max_points,
                    "subset_rule": f"source='{source}'" if source != "all" else "source in ('filtered','suppressed')",
                    "show_thresholds": show_thresholds,
                    "warning_temp": warning_temp,
                    "critical_temp": critical_temp,
                },
                "stats": payload["stats"],
                "plotly_spec": plotly_spec,
            }
        )

    def _handle_plotly_html(self, q: dict):
        sensor_id = (q.get("sensor_id") or [""])[0].strip()
        if not sensor_id:
            self._send_json({"error": "Missing required query parameter: sensor_id"}, status=HTTPStatus.BAD_REQUEST)
            return

        source = (q.get("source") or ["filtered"])[0].strip().lower()
        if source not in ("filtered", "suppressed", "all"):
            source = "filtered"

        resolution = (q.get("resolution") or ["1m"])[0].strip().lower()
        if resolution not in ("1m", "10s", "points"):
            resolution = "1m"

        minutes = _parse_int((q.get("minutes") or ["60"])[0], default=60, min_v=1, max_v=24 * 60)
        max_points = _parse_int((q.get("max_points") or ["120"])[0], default=120, min_v=1, max_v=5000)
        window_start = (q.get("window_start") or [None])[0]
        window_end = (q.get("window_end") or [None])[0]
        show_thresholds = _parse_bool((q.get("show_thresholds") or [None])[0], default=True)
        warning_temp = _parse_float((q.get("warning_temp") or [None])[0], DEFAULT_WARNING_TEMP)
        critical_temp = _parse_float((q.get("critical_temp") or [None])[0], DEFAULT_CRITICAL_TEMP)
        value_key = (q.get("value_key") or ["avg_v"])[0].strip().lower()
        if value_key not in ("avg_v", "last_v", "value", "min_v", "max_v"):
            value_key = "avg_v"

        rows, meta = _series_rows(
            sensor_id=sensor_id,
            source=source,
            minutes=minutes,
            resolution=resolution,
            window_start=window_start,
            window_end=window_end,
        )
        if resolution == "points" and value_key in ("avg_v", "last_v", "min_v", "max_v"):
            value_key = "value"
        if resolution != "points" and value_key == "value":
            value_key = "avg_v"

        payload = _series_payload(rows=rows, max_points=max_points, value_key=value_key)
        x_vals = [r["ts"] for r in payload["rows"]]
        y_vals = payload["values"]
        title = f"{sensor_id} {source} {resolution} ({minutes}m)"

        spec = _build_plotly_spec(
            x_vals,
            y_vals,
            title=title,
            value_key=value_key,
            source=source,
            show_thresholds=show_thresholds,
            warning_temp=warning_temp,
            critical_temp=critical_temp,
        )

        meta_html = (
            f"window={meta.get('window_start_utc')}..{meta.get('window_end_utc')} | "
            f"rows={payload['stats'].get('source_row_count')} rendered={payload['stats'].get('rendered_row_count')} | "
            f"thresholds={'on' if show_thresholds else 'off'} (W={warning_temp:.1f} C, C={critical_temp:.1f} C)"
        )

        html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  <style>
    body {{ margin: 0; font-family: -apple-system, Segoe UI, Roboto, sans-serif; background: #0b1020; color: #e5e7eb; }}
    .meta {{ padding: 10px 14px; font-size: 12px; color: #9ca3af; border-bottom: 1px solid #1f2937; }}
    #chart {{ width: 100vw; height: calc(100vh - 42px); }}
  </style>
</head>
<body>
  <div class="meta">{meta_html}</div>
  <div id="chart"></div>
  <script>
    const spec = {json.dumps(spec, ensure_ascii=True)};
    Plotly.newPlot('chart', spec.data, spec.layout, {{responsive:true, displaylogo:false}});
  </script>
</body>
</html>"""

        body = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self._add_cors_headers()
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    chart_db.init_database()
    log.info("DB initialized at %s", chart_db.get_db_path())
    server = ThreadingHTTPServer((HOST, PORT), ChartQueryHandler)
    log.info("listening on http://%s:%s", HOST, PORT)
    log.info("endpoints: /health, /sensors, /series, /plotly-spec, /plotly-html")
    auth_state = "ENABLED (X-API-Key or ?key=)" if API_KEY else "disabled (set CHART_QUERY_API_KEY to enable)"
    cors_state = "* (open)" if ALLOWED_ORIGINS is None else ", ".join(ALLOWED_ORIGINS)
    log.info("Auth: %s", auth_state)
    log.info("CORS allow-origin: %s", cors_state)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("stopped by user")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
