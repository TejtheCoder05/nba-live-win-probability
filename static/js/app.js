(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const ui = Object.fromEntries([
    "modeBadge", "connectionDot", "connectionText", "notice", "emptyState", "dashboard", "gameSelect",
    "period", "clock", "gameStatus", "awayMark", "awayCity", "awayName", "awayScore", "homeMark",
    "homeCity", "homeName", "homeScore", "awayCode", "homeCode", "awayProbability", "homeProbability",
    "awayBar", "homeBar", "lastUpdated", "finalNote", "possession", "scoreDiff", "awayFoulsLabel",
    "homeFoulsLabel", "awayFouls", "homeFouls", "replayControls", "speedSelect", "replayProgress",
    "playList", "sourceLabel", "probabilityChart"
  ].map((id) => [id, $(id)]));
  let selectedGame = null;
  let socket = null;
  let lastState = null;

  const pct = (value) => value == null ? "—" : `${(value * 100).toFixed(1)}%`;
  const teamName = (team) => team.name || team.tricode || "Team";
  const showNotice = (message) => {
    ui.notice.textContent = message || "";
    ui.notice.classList.toggle("hidden", !message);
  };

  async function loadGames() {
    try {
      const response = await fetch("/api/games");
      const payload = await response.json();
      ui.modeBadge.textContent = payload.mode === "replay" ? "VERIFIED REPLAY" : "LIVE FEED";
      showNotice(payload.error);
      if (!payload.games.length) {
        ui.emptyState.classList.remove("hidden");
        ui.dashboard.classList.add("hidden");
        return;
      }
      ui.emptyState.classList.add("hidden");
      ui.dashboard.classList.remove("hidden");
      ui.gameSelect.innerHTML = payload.games.map((game) =>
        `<option value="${game.game_id}">${game.away_team.tricode || teamName(game.away_team)} @ ${game.home_team.tricode || teamName(game.home_team)}</option>`
      ).join("");
      selectedGame = ui.gameSelect.value;
      connect();
    } catch (_error) {
      showNotice("The application API is unavailable. Check that the local server is running.");
    }
  }

  function connect() {
    if (typeof io !== "function") {
      showNotice("The real-time client could not load.");
      return;
    }
    socket = io({ transports: ["websocket", "polling"] });
    socket.on("connect", () => {
      ui.connectionDot.className = "online";
      ui.connectionText.textContent = "Connected";
      socket.emit("subscribe_game", { game_id: selectedGame });
    });
    socket.on("disconnect", () => {
      ui.connectionDot.className = "offline";
      ui.connectionText.textContent = "Reconnecting";
    });
    socket.on("game_state", render);
    socket.on("game_error", (data) => showNotice(data.message));
  }

  function setTeam(prefix, team) {
    ui[`${prefix}Mark`].textContent = team.tricode || "—";
    ui[`${prefix}City`].textContent = team.city || "";
    ui[`${prefix}Name`].textContent = teamName(team);
    ui[`${prefix}Score`].textContent = team.score ?? "—";
    ui[`${prefix}Code`].textContent = team.tricode || prefix.toUpperCase();
  }

  function render(state) {
    lastState = state;
    showNotice(state.error);
    setTeam("away", state.away_team);
    setTeam("home", state.home_team);
    ui.period.textContent = state.period_label;
    ui.clock.textContent = state.clock || "—";
    ui.gameStatus.textContent = state.status_text || state.feed_status;
    ui.awayProbability.textContent = pct(state.away_win_probability);
    ui.homeProbability.textContent = pct(state.home_win_probability);
    const homeWidth = state.home_win_probability == null ? 50 : state.home_win_probability * 100;
    ui.homeBar.style.width = `${homeWidth}%`;
    ui.awayBar.style.width = `${100 - homeWidth}%`;
    ui.lastUpdated.textContent = state.last_update ? `Updated ${new Date(state.last_update).toLocaleTimeString()}` : "—";
    ui.finalNote.classList.toggle("hidden", !state.final_override_applied);
    ui.possession.textContent = state.possession?.known ? state.possession.label : "Unknown";
    ui.scoreDiff.textContent = state.score_differential == null ? "—" : `${state.score_differential > 0 ? "+" : ""}${state.score_differential}`;
    ui.awayFoulsLabel.textContent = `${state.away_team.tricode || "Away"} fouls`;
    ui.homeFoulsLabel.textContent = `${state.home_team.tricode || "Home"} fouls`;
    ui.awayFouls.textContent = state.fouls?.away_period ?? "—";
    ui.homeFouls.textContent = state.fouls?.home_period ?? "—";
    ui.sourceLabel.textContent = state.data_source;
    ui.replayControls.classList.toggle("hidden", !state.replay);
    if (state.replay) {
      ui.speedSelect.value = String(state.replay.speed);
      ui.replayProgress.textContent = `${state.replay.cursor} / ${state.replay.total_actions} source actions`;
    }
    ui.playList.innerHTML = [...(state.recent_events || [])].reverse().map((play) =>
      `<li><time>${play.clock}</time><span>${escapeHtml(play.description)}</span><strong>${play.away_score}–${play.home_score}</strong></li>`
    ).join("") || "<li><span>Waiting for the opening event…</span></li>";
    drawChart(state.probability_history || []);
  }

  function escapeHtml(text) {
    const node = document.createElement("span");
    node.textContent = text;
    return node.innerHTML;
  }

  function drawChart(history) {
    const canvas = ui.probabilityChart;
    const ratio = window.devicePixelRatio || 1;
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    canvas.width = width * ratio;
    canvas.height = height * ratio;
    const ctx = canvas.getContext("2d");
    ctx.scale(ratio, ratio);
    ctx.clearRect(0, 0, width, height);
    ctx.strokeStyle = "rgba(255,255,255,.08)";
    ctx.lineWidth = 1;
    [0.25, 0.5, 0.75].forEach((value) => {
      const y = height * (1 - value);
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(width, y); ctx.stroke();
    });
    if (history.length < 2) return;
    const gradient = ctx.createLinearGradient(0, 0, width, 0);
    gradient.addColorStop(0, "#48a7ff"); gradient.addColorStop(1, "#ff8a3d");
    ctx.strokeStyle = gradient; ctx.lineWidth = 2.5; ctx.lineJoin = "round";
    ctx.beginPath();
    history.forEach((point, index) => {
      const x = (index / (history.length - 1)) * width;
      const y = (1 - point.home_probability) * (height - 10) + 5;
      index ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    ctx.stroke();
  }

  ui.gameSelect.addEventListener("change", () => {
    if (selectedGame && socket) socket.emit("unsubscribe_game", { game_id: selectedGame });
    selectedGame = ui.gameSelect.value;
    if (socket?.connected) socket.emit("subscribe_game", { game_id: selectedGame });
  });
  ui.replayControls.addEventListener("click", (event) => {
    const action = event.target.dataset?.action;
    if (action && socket) socket.emit("replay_control", { game_id: selectedGame, action });
  });
  ui.speedSelect.addEventListener("change", () => {
    socket?.emit("replay_control", { game_id: selectedGame, action: "speed", speed: Number(ui.speedSelect.value) });
  });
  window.addEventListener("resize", () => lastState && drawChart(lastState.probability_history || []));
  loadGames();
})();
