"""Self-contained HTML5 Canvas HUD overlay served at `/overlay` for OBS Studio."""

OVERLAY_HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>BrBrDb OBS Telemetry Overlay</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body {
    width: 100%;
    height: 100%;
    overflow: hidden;
    background: transparent;
    font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
  }
  canvas {
    display: block;
    width: 100vw;
    height: 100vh;
    object-fit: contain;
    background: transparent;
  }
</style>
</head>
<body>
<canvas id="hud" width="1920" height="1080"></canvas>
<script>
(() => {
  "use strict";

  const W = 1920;
  const H = 1080;
  const canvas = document.getElementById("hud");
  const ctx = canvas.getContext("2d");

  const params = new URLSearchParams(window.location.search);
  const SHOW_GAZE = params.get("gaze") !== "0";
  const SHOW_TRAIL = params.get("trail") !== "0";
  const USE_MPH = (params.get("units") || "kmh").toLowerCase() === "mph";
  const SPEED_MULT = USE_MPH ? 0.621371 : 1.0;
  const SPEED_UNIT = USE_MPH ? "mph" : "km/h";

  const C = {
    panel: "rgba(24, 24, 37, 0.84)",
    panelBorder: "rgba(137, 180, 250, 0.25)",
    surface: "rgba(49, 50, 68, 0.85)",
    grid: "rgba(205, 214, 244, 0.12)",
    text: "#CDD6F4",
    muted: "#A6ADC8",
    accent: "#89B4FA",
    green: "#A6E3A1",
    yellow: "#F9E2AF",
    red: "#F38BA8",
    cyan: "#94E2D5",
    turnBand: "rgba(137, 180, 250, 0.09)"
  };

  const state = {
    connected: false,
    track: null,
    bestLapSpeed: null,
    bestTurnsSpeed: null,
    currLapSpeed: [],
    turnDeltas: {},
    turnBests: {},
    turnTimes: {},
    lapNum: 0,
    lapTimeS: null,
    lapDeltaS: null,
    bestLapS: null,
    lastLapS: null,
    distM: null,
    speedKmh: 0,
    throttle: 0,
    brake: 0,
    clutch: 0,
    segment: null,
    pedalHist: [],
    gazeValid: false,
    gazeSmoothX: null,
    gazeSmoothY: null,
    gazeLastRxMs: 0,
    gazeTrail: []
  };

  function resetTrackBuffers(track) {
    state.track = track;
    const n = track && track.num_bins ? track.num_bins : 400;
    state.currLapSpeed = new Array(n).fill(null);
    state.bestLapSpeed = null;
    state.bestTurnsSpeed = null;
    state.turnDeltas = {};
    state.turnBests = {};
    state.turnTimes = {};
    state.lapDeltaS = null;
    state.bestLapS = null;
    state.lastLapS = null;
    state.segment = null;
  }

  function applyMessage(msg) {
    if (!msg || typeof msg !== "object") return;
    if (msg.type === "init") {
      if (msg.track) resetTrackBuffers(msg.track);
      if (Array.isArray(msg.best_lap_speed)) state.bestLapSpeed = msg.best_lap_speed;
      if (Array.isArray(msg.best_turns_speed)) state.bestTurnsSpeed = msg.best_turns_speed;
      if (Array.isArray(msg.curr_lap_speed)) state.currLapSpeed = msg.curr_lap_speed;
      if (msg.turn_deltas) state.turnDeltas = msg.turn_deltas;
      if (msg.turn_bests) state.turnBests = msg.turn_bests;
      if (msg.turn_times) state.turnTimes = msg.turn_times;
      if (msg.best_lap_s != null) state.bestLapS = msg.best_lap_s;
      if (msg.last_lap_s != null) state.lastLapS = msg.last_lap_s;
      if (msg.segment) state.segment = msg.segment;
      return;
    }
    if (msg.type === "track") {
      resetTrackBuffers(msg.track);
      return;
    }
    if (msg.type === "reset") {
      if (state.track) {
        const n = state.track.num_bins || 400;
        state.currLapSpeed = new Array(n).fill(null);
      }
      state.bestLapSpeed = null;
      state.bestTurnsSpeed = null;
      state.turnDeltas = {};
      state.turnBests = {};
      state.turnTimes = {};
      state.bestLapS = null;
      state.lastLapS = null;
      state.lapDeltaS = null;
      state.segment = null;
      return;
    }
    if (msg.type === "best_lap") {
      state.bestLapSpeed = msg.speeds || null;
      if (msg.best_lap_s != null) state.bestLapS = msg.best_lap_s;
      return;
    }
    if (msg.type === "best_turns") {
      state.bestTurnsSpeed = msg.speeds || null;
      if (msg.turn_bests) state.turnBests = msg.turn_bests;
      if (msg.turn_times) state.turnTimes = msg.turn_times;
      return;
    }
    if (msg.type === "tick") {
      const now = performance.now();
      if (msg.lap_reset && state.track) {
        state.currLapSpeed = new Array(state.track.num_bins || 400).fill(null);
      }
      if (Array.isArray(msg.lap_pts)) {
        for (let i = 0; i < msg.lap_pts.length; i++) {
          const idx = msg.lap_pts[i][0];
          const spd = msg.lap_pts[i][1];
          if (idx >= 0 && idx < state.currLapSpeed.length) {
            state.currLapSpeed[idx] = spd;
          }
        }
      }
      if (msg.lap_num != null) state.lapNum = msg.lap_num;
      state.lapTimeS = msg.lap_time_s != null ? msg.lap_time_s : state.lapTimeS;
      state.lapDeltaS = msg.lap_delta_s !== undefined ? msg.lap_delta_s : state.lapDeltaS;
      if (msg.best_lap_s !== undefined) state.bestLapS = msg.best_lap_s;
      if (msg.last_lap_s !== undefined) state.lastLapS = msg.last_lap_s;
      if (msg.dist_m !== undefined) state.distM = msg.dist_m;
      if (msg.speed_kmh != null) state.speedKmh = msg.speed_kmh;

      if (msg.throttle != null) state.throttle = clamp(msg.throttle, 0, 100);
      if (msg.brake != null) state.brake = clamp(msg.brake, 0, 100);
      if (msg.clutch != null) state.clutch = clamp(msg.clutch, 0, 100);
      state.pedalHist.push({ t: now, thr: state.throttle, brk: state.brake });
      while (state.pedalHist.length > 0 && now - state.pedalHist[0].t > 5500) {
        state.pedalHist.shift();
      }

      if (msg.segment !== undefined) state.segment = msg.segment;
      if (msg.turn_deltas) state.turnDeltas = msg.turn_deltas;
      if (msg.turn_times) state.turnTimes = msg.turn_times;

      if (msg.gaze && msg.gaze.valid && Number.isFinite(msg.gaze.x) && Number.isFinite(msg.gaze.y)) {
        updateGaze(msg.gaze.x, msg.gaze.y, now);
      } else {
        clearGaze();
      }
    }
  }

  function clearGaze() {
    state.gazeValid = false;
    state.gazeSmoothX = null;
    state.gazeSmoothY = null;
    state.gazeTrail.length = 0;
  }

  function updateGaze(nx, ny, now) {
    if (!state.gazeValid || state.gazeSmoothX === null) {
      state.gazeSmoothX = nx;
      state.gazeSmoothY = ny;
    } else {
      const dx = nx - state.gazeSmoothX;
      const dy = ny - state.gazeSmoothY;
      const dist = Math.hypot(dx, dy);
      const alpha = dist > 0.08 ? 0.88 : 0.42;
      state.gazeSmoothX += alpha * dx;
      state.gazeSmoothY += alpha * dy;
    }
    state.gazeValid = true;
    state.gazeLastRxMs = now;
    if (SHOW_TRAIL) {
      state.gazeTrail.push({ t: now, x: state.gazeSmoothX, y: state.gazeSmoothY });
      while (state.gazeTrail.length > 0 && now - state.gazeTrail[0].t > 180) {
        state.gazeTrail.shift();
      }
    }
  }

  function clamp(v, lo, hi) {
    return v < lo ? lo : (v > hi ? hi : v);
  }

  function fmtLap(sec) {
    if (sec == null || !Number.isFinite(sec)) return "--:--.---";
    const mins = Math.floor(sec / 60);
    const rem = sec - mins * 60;
    const remStr = rem < 10 ? "0" + rem.toFixed(3) : rem.toFixed(3);
    return mins + ":" + remStr;
  }

  function fmtDelta(sec) {
    if (sec == null || !Number.isFinite(sec)) return "--";
    const sign = sec >= 0 ? "+" : "-";
    return sign + Math.abs(sec).toFixed(3) + "s";
  }

  function fmtTurnSec(sec, isPred) {
    if (sec == null || !Number.isFinite(sec)) return "--";
    return (isPred ? "~" : "") + sec.toFixed(3) + "s";
  }

  function formatTurnLabel(t) {
    const raw = String(t.name || "").trim();
    return raw || ("Turn " + (t.index + 1));
  }

  function fitText(str, maxWidth) {
    if (ctx.measureText(str).width <= maxWidth) return str;
    let out = str;
    while (out.length > 1 && ctx.measureText(out + "\u2026").width > maxWidth) {
      out = out.slice(0, -1);
    }
    return out + "\u2026";
  }

  function isActiveTurn(t, distM, lapLen) {
    if (distM == null || !Number.isFinite(distM) || !(lapLen > 0)) return false;
    const segLen = (t.corner_length || 0) + (t.straight_length || 0);
    if (!(segLen > 0)) return false;
    const rel = (((distM - t.corner_start) % lapLen) + lapLen) % lapLen;
    return rel < segLen;
  }

  function deltaColor(sec) {
    if (sec == null || !Number.isFinite(sec)) return C.muted;
    if (sec <= -0.015) return C.green;
    if (sec <= 0.12) return C.yellow;
    return C.red;
  }

  function roundRect(x, y, w, h, r, fill, stroke) {
    ctx.beginPath();
    ctx.roundRect(x, y, w, h, r);
    if (fill) {
      ctx.fillStyle = fill;
      ctx.fill();
    }
    if (stroke) {
      ctx.strokeStyle = stroke;
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }
  }

  function drawGaze(now) {
    if (!SHOW_GAZE) return;
    if (!state.gazeValid || state.gazeSmoothX === null || now - state.gazeLastRxMs > 600) {
      if (state.gazeValid) clearGaze();
      return;
    }
    if (SHOW_TRAIL && state.gazeTrail.length >= 2) {
      for (let i = 1; i < state.gazeTrail.length; i++) {
        const p0 = state.gazeTrail[i - 1];
        const p1 = state.gazeTrail[i];
        const ageFrac = clamp(1.0 - (now - p1.t) / 180.0, 0, 1);
        ctx.beginPath();
        ctx.moveTo(clamp(p0.x, 0.012, 0.988) * W, clamp(p0.y, 0.012, 0.988) * H);
        ctx.lineTo(clamp(p1.x, 0.012, 0.988) * W, clamp(p1.y, 0.012, 0.988) * H);
        ctx.strokeStyle = "rgba(148, 226, 213, " + (0.45 * ageFrac).toFixed(2) + ")";
        ctx.lineWidth = 3;
        ctx.stroke();
      }
    }
    const gx = clamp(state.gazeSmoothX, 0.012, 0.988) * W;
    const gy = clamp(state.gazeSmoothY, 0.012, 0.988) * H;
    ctx.beginPath();
    ctx.arc(gx, gy, 26, 0, Math.PI * 2);
    ctx.strokeStyle = "rgba(17, 17, 27, 0.72)";
    ctx.lineWidth = 7;
    ctx.stroke();

    ctx.beginPath();
    ctx.arc(gx, gy, 26, 0, Math.PI * 2);
    ctx.strokeStyle = C.cyan;
    ctx.lineWidth = 3;
    ctx.stroke();

    ctx.beginPath();
    ctx.arc(gx, gy, 4.5, 0, Math.PI * 2);
    ctx.fillStyle = C.green;
    ctx.fill();
  }

  function drawTimingCard() {
    const x = W - 460;
    const y = 8;
    const w = 452;
    const track = state.track;
    const turns = track && Array.isArray(track.turns) ? track.turns : [];
    const lapLen = track && track.lap_length > 0 ? track.lap_length : 0;

    let visibleTurns = turns;
    if (turns.length > 24) {
      const maxVisible = 24;
      const activeIdx = turns.findIndex((t) => isActiveTurn(t, state.distM, lapLen));
      const anchor = activeIdx >= 0 ? activeIdx : 0;
      const start = clamp(
        Math.floor(anchor - maxVisible * 0.5),
        0,
        turns.length - maxVisible
      );
      visibleTurns = turns.slice(start, start + maxVisible);
    }

    const rowH = visibleTurns.length > 0
      ? clamp(Math.floor(636 / visibleTurns.length), 20, 26)
      : 24;
    const fontPx = rowH >= 24 ? 15 : 13;
    const h = visibleTurns.length > 0 ? 142 + visibleTurns.length * rowH + 12 : 158;
    roundRect(x, y, w, h, 14, C.panel, C.panelBorder);

    ctx.textBaseline = "top";
    ctx.textAlign = "left";

    // Current Lap Time + Delta Pill
    ctx.font = "700 40px 'Consolas', 'Segoe UI', monospace";
    ctx.fillStyle = C.text;
    ctx.fillText(fmtLap(state.lapTimeS), x + 18, y + 14);

    const dVal = state.lapDeltaS;
    const dCol = deltaColor(dVal);
    roundRect(x + w - 168, y + 17, 150, 38, 8, "rgba(17, 17, 27, 0.72)", dCol);
    ctx.font = "700 24px 'Consolas', 'Segoe UI', monospace";
    ctx.fillStyle = dCol;
    ctx.textAlign = "center";
    ctx.fillText(fmtDelta(dVal), x + w - 93, y + 24);
    ctx.textAlign = "left";

    // Proportional delta bar
    const barX = x + 18;
    const barY = y + 64;
    const barW = w - 36;
    const barH = 8;
    roundRect(barX, barY, barW, barH, 4, C.surface, null);
    const midX = barX + barW * 0.5;
    if (dVal != null && Number.isFinite(dVal)) {
      const frac = clamp(-dVal / 1.0, -1, 1);
      const fillW = Math.abs(frac) * (barW * 0.5);
      ctx.fillStyle = dVal <= 0 ? C.green : C.red;
      if (frac >= 0) {
        ctx.fillRect(midX, barY, fillW, barH);
      } else {
        ctx.fillRect(midX - fillW, barY, fillW, barH);
      }
    }
    ctx.fillStyle = C.text;
    ctx.fillRect(midX - 1, barY - 2, 2, barH + 4);

    // Best / Last lap row
    ctx.font = "600 16px 'Consolas', 'Segoe UI', monospace";
    ctx.fillStyle = C.muted;
    ctx.fillText("BEST " + fmtLap(state.bestLapS), x + 18, y + 82);
    ctx.textAlign = "right";
    ctx.fillText("LAST " + fmtLap(state.lastLapS), x + w - 18, y + 82);
    ctx.textAlign = "left";

    // Divider
    ctx.strokeStyle = C.grid;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x + 16, y + 110);
    ctx.lineTo(x + w - 16, y + 110);
    ctx.stroke();

    if (visibleTurns.length === 0) {
      ctx.font = "600 15px 'Segoe UI', sans-serif";
      ctx.fillStyle = C.muted;
      ctx.fillText(
        track ? "No turn splits defined" : "Waiting for telemetry...",
        x + 18,
        y + 122
      );
      return;
    }

    // All-Turns Table Headers
    ctx.font = "700 12px 'Segoe UI', sans-serif";
    ctx.fillStyle = C.muted;
    ctx.fillText("TURN", x + 18, y + 118);
    ctx.textAlign = "right";
    ctx.fillText("LAST", x + 264, y + 118);
    ctx.fillText("BEST", x + 348, y + 118);
    ctx.fillText("DELTA", x + w - 18, y + 118);

    const colName = "600 " + fontPx + "px 'Segoe UI', sans-serif";
    const colTime = "600 " + fontPx + "px 'Consolas', 'Segoe UI', monospace";
    const colDelta = "700 " + fontPx + "px 'Consolas', 'Segoe UI', monospace";

    for (let i = 0; i < visibleTurns.length; i++) {
      const t = visibleTurns[i];
      const ry = y + 140 + i * rowH;
      const textY = ry + Math.max(0, Math.floor((rowH - fontPx - 2) * 0.5));
      const active = isActiveTurn(t, state.distM, lapLen);
      if (active) {
        roundRect(
          x + 10,
          ry - 2,
          w - 20,
          rowH - 2,
          5,
          "rgba(137, 180, 250, 0.14)",
          null
        );
        ctx.fillStyle = C.accent;
        ctx.fillRect(x + 10, ry - 2, 3, rowH - 2);
      }

      const key = String(t.index);
      const tt = state.turnTimes[key] || null;
      const lastS = tt && tt.last_s != null ? tt.last_s : null;
      const isPred = Boolean(tt && tt.kind === "pred" && lastS != null);
      const rawBest = tt && tt.best_s != null ? tt.best_s : state.turnBests[key];
      const bestS = rawBest != null ? rawBest : null;
      const rawDelta = tt && tt.delta_s != null ? tt.delta_s : state.turnDeltas[key];
      const deltaS = rawDelta != null ? rawDelta : null;

      ctx.font = colName;
      ctx.fillStyle = active ? C.accent : C.text;
      ctx.textAlign = "left";
      ctx.fillText(fitText(formatTurnLabel(t), 160), x + 18, textY);

      ctx.font = colTime;
      ctx.textAlign = "right";
      ctx.fillStyle = isPred ? C.cyan : (lastS != null ? C.text : C.muted);
      ctx.fillText(fmtTurnSec(lastS, isPred), x + 264, textY);

      ctx.fillStyle = C.muted;
      ctx.fillText(fmtTurnSec(bestS, false), x + 348, textY);

      ctx.font = colDelta;
      ctx.fillStyle = deltaColor(deltaS);
      ctx.fillText(fmtDelta(deltaS), x + w - 18, textY);
    }
    ctx.textAlign = "left";
  }

  function drawPedalsCard(now) {
    const x = 28;
    const y = H - 248;
    const w = 330;
    const h = 220;
    roundRect(x, y, w, h, 14, C.panel, C.panelBorder);

    const gx = x + 14;
    const gy = y + 14;
    const gw = w - 28;
    const gh = h - 28;
    roundRect(gx, gy, gw, gh, 8, "rgba(17, 17, 27, 0.72)", C.grid);

    ctx.strokeStyle = C.grid;
    ctx.lineWidth = 1;
    for (let f = 0.25; f < 1.0; f += 0.25) {
      const ly = gy + gh * f;
      ctx.beginPath();
      ctx.moveTo(gx, ly);
      ctx.lineTo(gx + gw, ly);
      ctx.stroke();
    }

    const hist = state.pedalHist;
    if (hist.length >= 2) {
      const tMax = now;
      const tMin = tMax - 5000.0;
      const plotTrace = (key, color) => {
        ctx.beginPath();
        let started = false;
        for (let i = 0; i < hist.length; i++) {
          const pt = hist[i];
          const px = gx + clamp((pt.t - tMin) / 5000.0, 0, 1) * gw;
          const py = gy + gh - (clamp(pt[key], 0, 100) / 100.0) * (gh - 8) - 4;
          if (!started) {
            ctx.moveTo(px, py);
            started = true;
          } else {
            ctx.lineTo(px, py);
          }
        }
        ctx.strokeStyle = color;
        ctx.lineWidth = 2.5;
        ctx.stroke();
      };
      plotTrace("brk", C.red);
      plotTrace("thr", C.green);
    }
  }

  function computeMaxSpeed(trackLen) {
    let maxV = 90.0;
    const scan = (arr) => {
      if (!arr) return;
      for (let i = 0; i < arr.length; i++) {
        if (arr[i] != null && arr[i] > maxV) maxV = arr[i];
      }
    };
    scan(state.bestLapSpeed);
    scan(state.bestTurnsSpeed);
    scan(state.currLapSpeed);
    if (state.speedKmh > maxV) maxV = state.speedKmh;
    return Math.ceil((maxV * SPEED_MULT) / 20.0) * 20.0;
  }

  function drawSpeedSeries(arr, px, py, pw, ph, vMax, color, width, dashed) {
    if (!arr || arr.length < 2) return;
    const n = arr.length;
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    if (dashed) ctx.setLineDash([9, 6]);

    ctx.beginPath();
    let penDown = false;
    for (let i = 0; i < n; i++) {
      const raw = arr[i];
      if (raw == null || !Number.isFinite(raw)) {
        penDown = false;
        continue;
      }
      const x = px + (i / n) * pw;
      const vDisp = raw * SPEED_MULT;
      const y = py + ph - clamp(vDisp / vMax, 0, 1) * ph;
      if (!penDown) {
        ctx.moveTo(x, y);
        penDown = true;
      } else {
        ctx.lineTo(x, y);
      }
    }
    ctx.stroke();
    ctx.restore();
  }

  function drawSpeedPlot() {
    const px = 0;
    const py = 0;
    const pw = W - 468;
    const ph = 172;

    ctx.fillStyle = C.panel;
    ctx.fillRect(px, py, pw, ph);
    ctx.strokeStyle = C.panelBorder;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(px, py + ph);
    ctx.lineTo(px + pw, py + ph);
    ctx.lineTo(px + pw, py);
    ctx.stroke();

    const track = state.track;
    const lapLen = track && track.lap_length > 0 ? track.lap_length : 1000.0;
    const vMax = computeMaxSpeed(lapLen);

    // Y gridlines inside left edge
    ctx.textBaseline = "top";
    ctx.font = "600 13px 'Consolas', monospace";
    ctx.textAlign = "left";
    for (let frac = 0.5; frac <= 1.001; frac += 0.5) {
      const gy = py + ph - frac * ph;
      ctx.strokeStyle = C.grid;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(px, gy);
      ctx.lineTo(px + pw, gy);
      ctx.stroke();
      ctx.fillStyle = C.muted;
      const ly = gy + (frac >= 0.99 ? 4 : -15);
      ctx.fillText(String(Math.round(vMax * frac)), px + 6, ly);
    }

    // Turn Bands & Delta Indicators
    if (track && Array.isArray(track.turns)) {
      for (let i = 0; i < track.turns.length; i++) {
        const t = track.turns[i];
        const cs = t.corner_start;
        const ce = t.corner_end;
        const segments = ce >= cs ? [[cs, ce]] : [[cs, lapLen], [0, ce]];
        for (let s = 0; s < segments.length; s++) {
          const sx0 = px + (segments[s][0] / lapLen) * pw;
          const sx1 = px + (segments[s][1] / lapLen) * pw;
          ctx.fillStyle = C.turnBand;
          ctx.fillRect(sx0, py, Math.max(2, sx1 - sx0), ph);
        }
        const tDelta = state.turnDeltas[String(t.index)];
        if (tDelta != null && Number.isFinite(tDelta)) {
          ctx.fillStyle = deltaColor(tDelta);
          ctx.fillRect(
            px + (cs / lapLen) * pw,
            py,
            Math.max(4, (t.corner_length / lapLen) * pw),
            3
          );
        }
      }

      const orderedTurns = track.turns
        .slice()
        .sort((a, b) => a.corner_start - b.corner_start);
      const rowRightX = [-1e9, -1e9];
      ctx.font = "700 12px 'Segoe UI', sans-serif";
      for (let i = 0; i < orderedTurns.length; i++) {
        const t = orderedTurns[i];
        const rawX = px + (t.corner_start / lapLen) * pw + 4;
        const labelX = rawX < 36 ? 36 : rawX;
        const maxToEdge = px + pw - labelX - 4;
        if (maxToEdge < 16) continue;
        const fits0 = labelX >= rowRightX[0] + 6;
        const fits1 = labelX >= rowRightX[1] + 6;
        const rowIdx = fits0 ? 0 : (fits1 ? 1 : (rowRightX[0] <= rowRightX[1] ? 0 : 1));
        const labelTxt = fitText(formatTurnLabel(t), maxToEdge);
        const ly = py + 4 + rowIdx * 14;
        ctx.fillStyle = C.muted;
        ctx.fillText(labelTxt, labelX, ly);
        rowRightX[rowIdx] = labelX + ctx.measureText(labelTxt).width;
      }
    }

    // 1. Best Turn Reference Trace
    drawSpeedSeries(state.bestTurnsSpeed, px, py, pw, ph, vMax, C.yellow, 2.2, false);
    // 2. Best Lap Ghost Trace
    drawSpeedSeries(state.bestLapSpeed, px, py, pw, ph, vMax, C.accent, 2.2, true);
    // 3. CurrentTrace
    drawSpeedSeries(state.currLapSpeed, px, py, pw, ph, vMax, C.green, 3.0, false);

    // Live Position Marker
    if (state.distM != null && Number.isFinite(state.distM)) {
      const mx = px + clamp(state.distM / lapLen, 0, 1) * pw;
      const my = py + ph - clamp((state.speedKmh * SPEED_MULT) / vMax, 0, 1) * ph;
      ctx.beginPath();
      ctx.arc(mx, my, 7, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(166, 227, 161, 0.35)";
      ctx.fill();
      ctx.beginPath();
      ctx.arc(mx, my, 4, 0, Math.PI * 2);
      ctx.fillStyle = "#FFFFFF";
      ctx.fill();
    }
  }

  function connectEvents() {
    const es = new EventSource("/events");
    es.onopen = () => { state.connected = true; };
    es.onmessage = (ev) => {
      try {
        applyMessage(JSON.parse(ev.data));
      } catch (_err) {}
    };
    es.onerror = () => { state.connected = false; };
  }

  function render() {
    const now = performance.now();
    ctx.clearRect(0, 0, W, H);
    drawSpeedPlot();
    drawTimingCard();
    drawPedalsCard(now);
    drawGaze(now);
    requestAnimationFrame(render);
  }

  connectEvents();
  requestAnimationFrame(render);
})();
</script>
</body>
</html>
'''
