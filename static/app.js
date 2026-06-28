const state = {
  catalog: null,
  route: { name: "home" },
};

const app = document.querySelector("[data-app]");
const sidebar = document.querySelector("[data-sidebar]");
const searchForm = document.querySelector("[data-search-form]");
const searchInput = document.querySelector("[data-search-input]");
const appNameNodes = document.querySelectorAll("[data-app-name]");
const cardTemplate = document.querySelector("#video-card-template");

let activePlayer = null;
let youtubeApiPromise = null;
let searchRequestId = 0;

function byText(value) {
  return String(value || "").trim();
}

function routeFromHash() {
  const hash = window.location.hash.replace(/^#/, "") || "/";
  const parts = hash.split("/").filter(Boolean).map(decodeURIComponent);

  if (parts[0] === "watch" && parts[1]) {
    return { name: "watch", id: parts[1] };
  }
  if (parts[0] === "channel" && parts[1]) {
    return { name: "channel", id: parts[1] };
  }
  if (parts[0] === "search") {
    return { name: "search", query: parts.slice(1).join(" ") };
  }
  return { name: "home" };
}

function setHash(path) {
  window.location.hash = path;
}

async function fetchJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  const body = await response.json();
  if (!response.ok) {
    throw new Error(body.error || `Request failed: ${response.status}`);
  }
  return body;
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await response.json();
  if (!response.ok) {
    throw new Error(body.error || `Request failed: ${response.status}`);
  }
  return body;
}

async function init() {
  searchForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const query = searchInput.value.trim();
    setHash(query ? `#/search/${encodeURIComponent(query)}` : "#/");
  });

  window.addEventListener("hashchange", render);

  try {
    await refreshCatalog();
    render();
  } catch (error) {
    renderError(error.message);
  }
}

async function refreshCatalog() {
  state.catalog = await fetchJson("/api/catalog");
  document.title = state.catalog.appName;
  appNameNodes.forEach((node) => {
    node.textContent = state.catalog.appName;
  });
}

function render() {
  if (!state.catalog) {
    return;
  }
  state.route = routeFromHash();
  renderSidebar();

  if (state.route.name === "watch") {
    renderWatch(state.route.id);
  } else if (state.route.name === "channel") {
    destroyActivePlayer();
    renderChannel(state.route.id);
  } else if (state.route.name === "search") {
    destroyActivePlayer();
    renderSearch(state.route.query);
  } else {
    destroyActivePlayer();
    renderHome();
  }
}

function renderSidebar() {
  const channels = state.catalog.channels || [];
  const channelLinks = channels
    .map((channel) => {
      const active = state.route.name === "channel" && state.route.id === channel.id ? " active" : "";
      return `<a class="nav-link${active}" href="#/channel/${encodeURIComponent(channel.id)}">
        ${avatarHtml(channel.avatar, channel.name, "nav-avatar")}
        <span class="nav-label">${escapeHtml(channel.name)}</span>
        <span class="nav-count">${channel.videoCount}</span>
      </a>`;
    })
    .join("");

  sidebar.innerHTML = `
    <a class="nav-link${state.route.name === "home" ? " active" : ""}" href="#/">
      <span class="nav-icon" aria-hidden="true">${homeIcon()}</span>
      <span class="nav-label">Home</span>
    </a>
    <div class="nav-section-title">Channels</div>
    ${channelLinks}
  `;
}

function renderHome() {
  app.innerHTML = `
    <section class="shelf-head">
      <div>
        <h1>Home</h1>
        <p>${state.catalog.videoCount} approved videos</p>
      </div>
    </section>
    <section class="video-grid" data-grid></section>
  `;
  renderCards(app.querySelector("[data-grid]"), shuffleVideos(state.catalog.videos));
}

function renderChannel(channelId) {
  const channel = state.catalog.channels.find((item) => item.id === channelId);
  if (!channel) {
    renderEmpty("Channel not found", "This channel is not in the local allowlist.");
    return;
  }

  app.innerHTML = `
    <section class="shelf-head">
      <div>
        <h1>${escapeHtml(channel.name)}</h1>
        <p>${escapeHtml(channel.description || `${channel.videoCount} approved videos`)}</p>
      </div>
    </section>
    <section class="video-grid" data-grid></section>
  `;
  renderCards(app.querySelector("[data-grid]"), channel.videos);
}

async function renderSearch(query) {
  searchInput.value = query || "";
  const normalized = byText(query).toLowerCase();
  const requestId = (searchRequestId += 1);

  app.innerHTML = `
    <section class="shelf-head">
      <div>
        <h1>Search</h1>
        <p>${escapeHtml(normalized || "All approved videos")}</p>
      </div>
    </section>
    <section class="video-grid" data-grid>
      <div class="loading">Searching...</div>
    </section>
  `;

  let videos = state.catalog.videos;
  let archiveSearchEnabled = state.catalog.settings?.youtubeArchiveSearchEnabled || false;
  if (normalized) {
    try {
      const result = await fetchJson(`/api/search?q=${encodeURIComponent(normalized)}`);
      if (requestId !== searchRequestId) {
        return;
      }
      videos = result.videos;
      archiveSearchEnabled = result.apiEnabled;
    } catch (error) {
      renderError(error.message);
      return;
    }
  }

  if (videos.length) {
    renderCards(app.querySelector("[data-grid]"), videos);
  } else if (normalized && !archiveSearchEnabled) {
    app.querySelector("[data-grid]").innerHTML = emptyHtml(
      "No matches",
      "Only recent approved-channel videos are searchable right now. Add a YouTube Data API key to search older videos from approved channels."
    );
  } else {
    app.querySelector("[data-grid]").innerHTML = emptyHtml("No matches", "Try another title or channel.");
  }
}

async function renderWatch(videoId) {
  app.innerHTML = `<div class="loading">Loading video...</div>`;
  try {
    const data = await fetchJson(`/api/videos/${encodeURIComponent(videoId)}`);
    const { video } = data;
    app.innerHTML = `
      <section class="watch-layout">
        <div class="player-shell">
          <div class="player-frame">
            <iframe
              id="youtube-player"
              title="${escapeAttribute(video.title)}"
              src="${embedUrl(video.youtubeId)}"
              allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
              allowfullscreen></iframe>
          </div>
          <h1 class="watch-title">${escapeHtml(video.title)}</h1>
          <div class="watch-channel-row">
            <a class="watch-channel" href="#/channel/${encodeURIComponent(video.channelId)}">
              ${avatarHtml(video.channelAvatar, video.channelName, "avatar")}
              <span class="watch-channel-copy">
                <strong class="watch-channel-name">${escapeHtml(video.channelName)}</strong>
                <span class="watch-posted-date">${escapeHtml(relativeDate(video.published))}</span>
              </span>
            </a>
            <button class="icon-button" data-block-video="${escapeAttribute(video.youtubeId)}" data-channel-id="${escapeAttribute(video.channelId)}" type="button" aria-label="Hide this video" title="Hide this video">
              ${eyeOffIcon()}
            </button>
          </div>
          ${formatDescription(video.description)}
        </div>
      </section>
    `;
    bindBlockButtons(app);
    setDefaultPlaybackRate("youtube-player");
  } catch (error) {
    renderError(error.message);
  }
}

function renderCards(container, videos) {
  if (!videos.length) {
    renderEmpty("No videos yet", "Add approved videos to config/videos.local.json.");
    return;
  }

  container.textContent = "";
  videos.forEach((video) => {
    const node = cardTemplate.content.firstElementChild.cloneNode(true);
    node.querySelectorAll(".watch-card-link").forEach((link) => {
      link.href = `#/watch/${encodeURIComponent(video.youtubeId)}`;
    });
    node.querySelector(".thumb").src = video.thumbnail;
    node.querySelector(".thumb").alt = "";
    replaceAvatar(node.querySelector(".avatar-dot"), video.channelAvatar, video.channelName, "avatar");
    node.querySelector(".duration").textContent = video.duration || "";
    node.querySelector("h3").textContent = video.title;
    node.querySelector(".channel-name").textContent = video.channelName;
    node.querySelector(".posted-date").textContent = relativeDate(video.published);
    const hideButton = node.querySelector(".card-hide-button");
    hideButton.innerHTML = eyeOffIcon();
    hideButton.dataset.blockVideo = video.youtubeId;
    hideButton.dataset.channelId = video.channelId;
    container.appendChild(node);
  });
  bindBlockButtons(container);
}

function bindBlockButtons(scope) {
  scope.querySelectorAll("[data-block-video]").forEach((button) => {
    button.addEventListener("click", async () => {
      if (button.dataset.blockState === "blocked") {
        await unblockVideo(button.dataset.channelId, button.dataset.blockVideo, button);
      } else {
        await blockVideo(button.dataset.channelId, button.dataset.blockVideo, button);
      }
    });
  });
}

async function blockVideo(channelId, youtubeId, button) {
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  try {
    await postJson("/api/block-video", { channelId, youtubeId });
    await refreshCatalog();
    setBlockButtonState(button, "blocked");
  } catch (error) {
    button.disabled = false;
    button.removeAttribute("aria-busy");
    setBlockButtonState(button, "visible");
    renderError(error.message);
  }
}

async function unblockVideo(channelId, youtubeId, button) {
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  try {
    await postJson("/api/unblock-video", { channelId, youtubeId });
    await refreshCatalog();
    setBlockButtonState(button, "visible");
  } catch (error) {
    button.disabled = false;
    button.removeAttribute("aria-busy");
    setBlockButtonState(button, "blocked");
    renderError(error.message);
  }
}

function setBlockButtonState(button, stateName) {
  button.disabled = false;
  button.removeAttribute("aria-busy");
  button.dataset.blockState = stateName;
  if (stateName === "blocked") {
    button.innerHTML = undoIcon();
    button.setAttribute("aria-label", "Undo hide video");
    button.title = "Undo hide video";
    button.closest(".video-card")?.classList.add("is-blocked-pending");
    return;
  }
  button.innerHTML = eyeOffIcon();
  button.setAttribute("aria-label", "Hide this video");
  button.title = "Hide this video";
  button.closest(".video-card")?.classList.remove("is-blocked-pending");
}

function shuffleVideos(videos) {
  const shuffled = [...videos];
  for (let index = shuffled.length - 1; index > 0; index -= 1) {
    const swapIndex = Math.floor(Math.random() * (index + 1));
    [shuffled[index], shuffled[swapIndex]] = [shuffled[swapIndex], shuffled[index]];
  }
  return shuffled;
}

function relativeDate(value) {
  if (!value) {
    return "";
  }

  const published = new Date(value);
  if (Number.isNaN(published.getTime())) {
    return "";
  }

  const seconds = Math.max(0, Math.floor((Date.now() - published.getTime()) / 1000));
  const units = [
    ["year", 31536000],
    ["month", 2592000],
    ["week", 604800],
    ["day", 86400],
    ["hour", 3600],
    ["minute", 60],
  ];

  for (const [unit, unitSeconds] of units) {
    const count = Math.floor(seconds / unitSeconds);
    if (count >= 1) {
      return `${count} ${unit}${count === 1 ? "" : "s"} ago`;
    }
  }

  return "just now";
}

function formatDescription(value) {
  const text = byText(value);
  if (!text) {
    return "";
  }

  const paragraphs = text
    .split(/\n{2,}/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean)
    .map((paragraph) => `<p>${linkifyText(paragraph).replace(/\n/g, "<br>")}</p>`)
    .join("");

  return `<div class="watch-description">${paragraphs}</div>`;
}

function linkifyText(value) {
  const escaped = escapeHtml(value);
  return escaped.replace(/https?:\/\/[^\s<]+/g, (url) => {
    const cleanUrl = url.replace(/[.,;:!?)]$/, "");
    const trailing = url.slice(cleanUrl.length);
    return `<a href="${escapeAttribute(cleanUrl)}" target="_blank" rel="noopener noreferrer">${cleanUrl}</a>${trailing}`;
  });
}

function embedUrl(videoId) {
  const params = new URLSearchParams({
    controls: "1",
    rel: "0",
    modestbranding: "1",
    playsinline: "1",
    enablejsapi: "1",
    origin: window.location.origin,
  });
  return `https://www.youtube-nocookie.com/embed/${encodeURIComponent(videoId)}?${params.toString()}`;
}

function loadYouTubeApi() {
  if (window.YT && window.YT.Player) {
    return Promise.resolve(window.YT);
  }
  if (youtubeApiPromise) {
    return youtubeApiPromise;
  }

  youtubeApiPromise = new Promise((resolve) => {
    const previousReady = window.onYouTubeIframeAPIReady;
    window.onYouTubeIframeAPIReady = () => {
      if (typeof previousReady === "function") {
        previousReady();
      }
      resolve(window.YT);
    };

    if (!document.querySelector('script[src="https://www.youtube.com/iframe_api"]')) {
      const script = document.createElement("script");
      script.src = "https://www.youtube.com/iframe_api";
      document.head.appendChild(script);
    }
  });

  return youtubeApiPromise;
}

async function setDefaultPlaybackRate(playerId) {
  const playerNode = document.getElementById(playerId);
  if (!playerNode) {
    return;
  }

  queuePlaybackRateCommand(playerNode);
  const YT = await loadYouTubeApi();
  if (!document.getElementById(playerId)) {
    return;
  }

  destroyActivePlayer();
  let playbackRateApplied = false;
  activePlayer = new YT.Player(playerId, {
    events: {
      onReady: (event) => {
        playbackRateApplied = applyPlaybackRate(event.target);
      },
      onStateChange: (event) => {
        if (!playbackRateApplied && event.data === YT.PlayerState.PLAYING) {
          playbackRateApplied = applyPlaybackRate(event.target);
        }
      },
    },
  });
}

function applyPlaybackRate(player) {
  const rates = typeof player.getAvailablePlaybackRates === "function" ? player.getAvailablePlaybackRates() : [];
  const playbackRate = defaultPlaybackRate();
  if (!rates.length || rates.includes(playbackRate)) {
    player.setPlaybackRate(playbackRate);
    return true;
  }
  return false;
}

function defaultPlaybackRate() {
  const configured = Number(state.catalog?.settings?.defaultPlaybackRate);
  if (!Number.isFinite(configured)) {
    return 0.75;
  }
  return Math.min(2, Math.max(0.25, configured));
}

function destroyActivePlayer() {
  if (activePlayer && typeof activePlayer.destroy === "function") {
    activePlayer.destroy();
  }
  activePlayer = null;
}

function queuePlaybackRateCommand(iframe) {
  [0, 500, 1500].forEach((delay) => {
    window.setTimeout(() => {
      if (!iframe.isConnected || !iframe.contentWindow) {
        return;
      }
      iframe.contentWindow.postMessage(
        JSON.stringify({
          event: "command",
          func: "setPlaybackRate",
          args: [defaultPlaybackRate()],
        }),
        "https://www.youtube-nocookie.com"
      );
    }, delay);
  });
}

function avatarHtml(src, name, className) {
  if (!src) {
    return `<span class="avatar-dot ${escapeAttribute(className)}" aria-hidden="true"></span>`;
  }
  return `<img class="${escapeAttribute(className)}" src="${escapeAttribute(src)}" alt="${escapeAttribute(name)}">`;
}

function eyeOffIcon() {
  return `<svg aria-hidden="true" viewBox="0 0 24 24" focusable="false">
    <path d="M17.94 17.94A10.94 10.94 0 0 1 12 20c-7 0-11-8-11-8a20.29 20.29 0 0 1 5.06-5.94"/>
    <path d="M9.9 4.24A10.45 10.45 0 0 1 12 4c7 0 11 8 11 8a20.74 20.74 0 0 1-2.16 3.19"/>
    <path d="M14.12 14.12a3 3 0 0 1-4.24-4.24"/>
    <path d="M1 1l22 22"/>
  </svg>`;
}

function undoIcon() {
  return `<svg aria-hidden="true" viewBox="0 0 24 24" focusable="false">
    <path d="M3 7v6h6"/>
    <path d="M21 17a9 9 0 0 0-15-6.7L3 13"/>
  </svg>`;
}

function homeIcon() {
  return `<svg aria-hidden="true" viewBox="0 0 24 24" focusable="false">
    <path d="M3 10.5 12 3l9 7.5"/>
    <path d="M5 10v10h14V10"/>
    <path d="M9 20v-6h6v6"/>
  </svg>`;
}

function replaceAvatar(target, src, name, className) {
  if (!target) {
    return;
  }
  target.outerHTML = avatarHtml(src, name, className);
}

function renderEmpty(title, message) {
  app.innerHTML = emptyHtml(title, message);
}

function renderError(message) {
  app.innerHTML = `<div class="error"><strong>Something needs attention</strong><p>${escapeHtml(message)}</p></div>`;
}

function emptyHtml(title, message) {
  return `<div class="empty"><strong>${escapeHtml(title)}</strong><p>${escapeHtml(message)}</p></div>`;
}

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function escapeAttribute(value) {
  return escapeHtml(value).replace(/`/g, "&#96;");
}

init();
