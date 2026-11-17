/*
 * Laurel Metrics -- the raw data browser.
 *
 * The page is useful before this script loads because the server renders the
 * first page of points.  This script adds the interactive range picker, the
 * pagination controls and the live long poll.  It is a classic ES5 script:
 * no modules, no build step and no HTML strings.
 */
(function () {
  "use strict";

  /* ------------------------------------------------------------- constants */

  var RANGE_MS = {
    "5m": 5 * 60 * 1000,
    "15m": 15 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "6h": 6 * 60 * 60 * 1000,
    "24h": 24 * 60 * 60 * 1000,
    "7d": 7 * 24 * 60 * 60 * 1000,
    "30d": 30 * 24 * 60 * 60 * 1000
  };

  var REFRESH_MS = {
    "2s": 2000,
    "5s": 5000,
    "10s": 10000,
    "30s": 30000,
    "1m": 60000
  };

  var root = null;
  var config = null;
  var timer = null;
  var generation = 0;
  var busy = false;
  var latestId = 0;
  var total = 0;
  var page = 1;
  var pageCount = 1;
  var newPointCount = 0;

  /* ---------------------------------------------------------------- helpers */

  function byRole(name) {
    return root ? root.querySelector('[data-role="' + name + '"]') : null;
  }

  function clear(node) {
    while (node && node.firstChild) {
      node.removeChild(node.firstChild);
    }
  }

  function describe(value) {
    if (value === null || value === undefined) {
      return "(nothing)";
    }
    if (typeof value === "string") {
      return value;
    }
    try {
      return JSON.stringify(value);
    } catch (error) {
      return String(value);
    }
  }

  function formatTime(timestamp) {
    var date = new Date(timestamp);
    if (isNaN(date.getTime())) {
      return String(timestamp);
    }
    return date.toISOString().replace("T", " ").slice(0, 19) + " UTC";
  }

  function formatDateInput(timestamp) {
    var date = new Date(timestamp);
    if (isNaN(date.getTime())) {
      return "";
    }
    return date.toISOString().slice(0, 16);
  }

  function parseUtc(value) {
    var text = String(value || "").trim();
    if (!text) {
      return null;
    }
    if (text.length === 16) {
      text += ":00";
    }
    text += "Z";
    var parsed = Date.parse(text);
    return isNaN(parsed) ? null : parsed;
  }

  function fetchJson(url) {
    return fetch(url, {
      headers: {
        Accept: "application/json"
      }
    }).then(function (response) {
      return response
        .json()
        .catch(function () {
          return null;
        })
        .then(function (data) {
          return { ok: response.ok, status: response.status, data: data };
        });
    });
  }

  /* --------------------------------------------------------------- errors */

  function clearError() {
    var target = byRole("raw-error");
    if (target) {
      clear(target);
    }
  }

  function renderError(error, fallbackMessage) {
    var target = byRole("raw-error");
    if (!target) {
      return;
    }

    var envelope = error || {};
    var box = document.createElement("div");
    box.className = "error-block";
    box.setAttribute("role", "alert");
    box.setAttribute("data-error-code", envelope.code || "RAW_REQUEST_FAILED");

    var title = document.createElement("p");
    title.className = "error-block-title";
    title.textContent = "The raw data request failed";
    box.appendChild(title);

    var code = document.createElement("p");
    code.className = "error-code";
    code.textContent = envelope.code || "RAW_REQUEST_FAILED";
    box.appendChild(code);

    var list = document.createElement("dl");
    list.className = "error-fields";
    var fields = [
      ["Message", envelope.message || fallbackMessage],
      ["Field", envelope.field],
      ["Expected", envelope.expected],
      ["Received", envelope.received],
      [
        "Hint",
        envelope.hint ||
          "Check the selected range and retry the request."
      ]
    ];

    fields.forEach(function (entry) {
      var term = document.createElement("dt");
      term.textContent = entry[0];
      var definition = document.createElement("dd");
      definition.textContent = describe(entry[1]);
      list.appendChild(term);
      list.appendChild(definition);
    });

    box.appendChild(list);
    clear(target);
    target.appendChild(box);
  }

  function renderLocalRangeError(message, hint) {
    renderError(
      {
        code: "INVALID_TIME_RANGE",
        message: message,
        field: "range",
        expected: "a start earlier than the end",
        received: null,
        hint: hint
      },
      message
    );
  }

  /* -------------------------------------------------------------- rendering */

  function renderRows(points) {
    var body = byRole("rows");
    var empty = byRole("empty");
    if (!body) {
      return;
    }

    clear(body);
    points.forEach(function (point) {
      var row = document.createElement("tr");
      var value = point.value;
      if (typeof value === "boolean") {
        value = value ? "true" : "false";
      }

      var values = [
        formatTime(point.ts_ms),
        value === null || value === undefined ? "-" : String(value),
        JSON.stringify(point.tags || {}),
        formatTime(point.received_at_ms),
        String(point.id)
      ];

      values.forEach(function (cellValue, index) {
        var cell = document.createElement("td");
        cell.textContent = cellValue;
        if (index === 4) {
          cell.className = "num";
        }
        if (index === 2) {
          cell.className = "raw-tags";
        }
        row.appendChild(cell);
      });

      body.appendChild(row);
    });

    if (empty) {
      empty.hidden = points.length !== 0;
    }
  }

  function updatePageInfo(payload) {
    total = Number(payload.total) || 0;
    pageCount = Math.max(1, Math.ceil(total / config.size));

    var totalNode = byRole("total-count");
    if (totalNode) {
      totalNode.textContent = String(total);
    }

    var pageInfo = byRole("page-info");
    if (pageInfo) {
      pageInfo.textContent =
        "Page " + page + " of " + pageCount + " · " + total + " points";
    }

    var previous = byRole("previous-page");
    if (previous) {
      previous.disabled = page <= 1;
    }
    var next = byRole("next-page");
    if (next) {
      next.disabled = page >= pageCount;
    }
  }

  function updateLiveStatus() {
    var status = byRole("live-status");
    if (!status) {
      return;
    }
    status.textContent =
      "Last update " +
      formatTime(Date.now()) +
      " · " +
      newPointCount +
      " new point" +
      (newPointCount === 1 ? "" : "s");
  }

  function updateCsvLink(range) {
    var link = byRole("csv-download");
    if (!link) {
      return;
    }
    link.setAttribute(
      "href",
      "/metrics/" +
        encodeURIComponent(config.metric) +
        "/raw.csv?from=" +
        range.from_ms +
        "&to=" +
        range.to_ms
    );
  }

  /* ----------------------------------------------------------------- range */

  function currentRange() {
    var rangeSelect = byRole("range-select");
    var startInput = byRole("range-start");
    var endInput = byRole("range-end");
    if (!rangeSelect) {
      return null;
    }

    var selected = rangeSelect.value;
    if (selected === "custom") {
      var start = parseUtc(startInput && startInput.value);
      var end = parseUtc(endInput && endInput.value);
      if (start === null || end === null) {
        renderLocalRangeError(
          "The custom range needs a valid start and end.",
          "Enter both dates and times before applying the range."
        );
        return null;
      }
      if (start >= end) {
        renderLocalRangeError(
          "The start of the custom range must be earlier than its end.",
          "Move the start into the past or the end into the future."
        );
        return null;
      }
      config.range = "custom";
      return { from_ms: start, to_ms: end, mode: "custom" };
    }

    var span = RANGE_MS[selected] || RANGE_MS["24h"];
    var now = Date.now();
    config.range = selected;
    var range = { from_ms: now - span, to_ms: now, mode: selected };
    if (startInput) {
      startInput.value = formatDateInput(range.from_ms);
    }
    if (endInput) {
      endInput.value = formatDateInput(range.to_ms);
    }
    return range;
  }

  function toggleCustomFields() {
    var rangeSelect = byRole("range-select");
    var startInput = byRole("range-start");
    var endInput = byRole("range-end");
    var custom = Boolean(rangeSelect && rangeSelect.value === "custom");
    if (startInput) {
      startInput.disabled = !custom;
    }
    if (endInput) {
      endInput.disabled = !custom;
    }
  }

  /* --------------------------------------------------------------- loading */

  function loadTable(resetPage) {
    if (busy) {
      return Promise.resolve(null);
    }
    if (resetPage) {
      page = 1;
    }

    var range = currentRange();
    if (!range) {
      return Promise.resolve(null);
    }

    config.from_ms = range.from_ms;
    config.to_ms = range.to_ms;
    config.page = page;
    updateCsvLink(range);

    var offset = (page - 1) * config.size;
    var url =
      "/api/raw?metric=" +
      encodeURIComponent(config.metric) +
      "&from=" +
      range.from_ms +
      "&to=" +
      range.to_ms +
      "&limit=" +
      config.size +
      "&offset=" +
      offset +
      "&order=" +
      config.order;

    busy = true;
    clearError();

    return fetchJson(url).then(
      function (result) {
        busy = false;
        if (!result.ok) {
          var envelope =
            result.data && result.data.error ? result.data.error : null;
          renderError(
            envelope,
            "The raw points could not be loaded (HTTP " + result.status + ")."
          );
          return null;
        }
        if (!result.data) {
          renderError(
            null,
            "The server answered with an unreadable body (HTTP " +
              result.status +
              ")."
          );
          return null;
        }

        latestId = result.data.latest_id;
        renderRows(result.data.points || []);
        updatePageInfo(result.data);
        updateLiveStatus();
        return result.data;
      },
      function (error) {
        busy = false;
        renderError(null, "The raw points could not be loaded: " + error);
        return null;
      }
    );
  }

  /* ------------------------------------------------------------ live polling */

  function stopPolling() {
    generation += 1;
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  }

  function scheduleNext(delayMs, token) {
    if (token !== generation) {
      return;
    }
    timer = setTimeout(function () {
      timer = null;
      pollOnce(token);
    }, delayMs);
  }

  function refreshIntervalMs() {
    var select = byRole("refresh-interval");
    if (!select || select.value === "off") {
      return 0;
    }
    return REFRESH_MS[select.value] || 0;
  }

  function pollOnce(token) {
    if (token !== generation) {
      return;
    }
    if (busy) {
      scheduleNext(100, token);
      return;
    }

    var interval = refreshIntervalMs();
    if (!interval) {
      return;
    }

    var range = currentRange();
    if (!range) {
      scheduleNext(interval, token);
      return;
    }

    if (interval > 10000) {
      loadTable(false).then(function () {
        scheduleNext(interval, token);
      });
      return;
    }

    var url =
      "/api/raw/wait?metric=" +
      encodeURIComponent(config.metric) +
      "&since_id=" +
      latestId +
      "&from=" +
      range.from_ms +
      "&to=" +
      range.to_ms +
      "&timeout=" +
      interval / 1000;

    busy = true;
    fetchJson(url).then(
      function (result) {
        busy = false;
        if (token !== generation) {
          return;
        }
        if (!result.ok) {
          var envelope =
            result.data && result.data.error ? result.data.error : null;
          renderError(
            envelope,
            "The live update failed (HTTP " + result.status + ")."
          );
          scheduleNext(interval, token);
          return;
        }
        if (!result.data) {
          renderError(null, "The live update returned an unreadable body.");
          scheduleNext(interval, token);
          return;
        }

        var points = result.data.points || [];
        newPointCount += points.length;
        latestId = result.data.latest_id;
        updateLiveStatus();

        if (points.length) {
          loadTable(false).then(function () {
            scheduleNext(0, token);
          });
          return;
        }
        scheduleNext(0, token);
      },
      function (error) {
        busy = false;
        if (token !== generation) {
          return;
        }
        renderError(null, "The live update failed: " + error);
        scheduleNext(interval, token);
      }
    );
  }

  function startPolling() {
    stopPolling();
    var token = generation;
    scheduleNext(0, token);
  }

  /* ----------------------------------------------------------------- events */

  function applyRange(resetPage) {
    var range = currentRange();
    if (!range) {
      return Promise.resolve(null);
    }
    if (resetPage) {
      page = 1;
    }
    config.from_ms = range.from_ms;
    config.to_ms = range.to_ms;
    config.page = page;
    updateCsvLink(range);
    return loadTable(resetPage).then(function () {
      startPolling();
    });
  }

  function changeMetric(value) {
    config.metric = value;
    latestId = 0;
    newPointCount = 0;
    page = 1;
    if (window.history && window.history.replaceState) {
      window.history.replaceState(
        null,
        "",
        "/metrics/" + encodeURIComponent(value) + "/raw"
      );
    }
    loadTable(true).then(function () {
      startPolling();
    });
  }

  function bindEvents() {
    var picker = byRole("raw-picker");
    if (picker) {
      picker.addEventListener("submit", function (event) {
        event.preventDefault();
        applyRange(true);
      });
    }

    var metricSelect = byRole("metric-select");
    if (metricSelect) {
      metricSelect.addEventListener("change", function () {
        changeMetric(metricSelect.value);
      });
    }

    var rangeSelect = byRole("range-select");
    if (rangeSelect) {
      rangeSelect.addEventListener("change", function () {
        toggleCustomFields();
        applyRange(true);
      });
    }

    var startInput = byRole("range-start");
    if (startInput) {
      startInput.addEventListener("change", function () {
        applyRange(true);
      });
    }

    var endInput = byRole("range-end");
    if (endInput) {
      endInput.addEventListener("change", function () {
        applyRange(true);
      });
    }

    var sizeSelect = byRole("page-size");
    if (sizeSelect) {
      sizeSelect.addEventListener("change", function () {
        config.size = Number(sizeSelect.value);
        applyRange(true);
      });
    }

    var refreshSelect = byRole("refresh-interval");
    if (refreshSelect) {
      refreshSelect.addEventListener("change", function () {
        config.refresh = refreshSelect.value;
        startPolling();
      });
    }

    var refreshButton = byRole("refresh");
    if (refreshButton) {
      refreshButton.addEventListener("click", function () {
        loadTable(false);
      });
    }

    var previous = byRole("previous-page");
    if (previous) {
      previous.addEventListener("click", function () {
        if (page > 1) {
          page -= 1;
          loadTable(false);
        }
      });
    }

    var next = byRole("next-page");
    if (next) {
      next.addEventListener("click", function () {
        if (page < pageCount) {
          page += 1;
          loadTable(false);
        }
      });
    }
  }

  function init() {
    root = document.querySelector('[data-role="raw-browser"]');
    if (!root) {
      return;
    }

    try {
      config = JSON.parse(root.getAttribute("data-config"));
    } catch (error) {
      renderError(null, "The configuration of the raw data page is broken.");
      return;
    }

    page = Number(config.page) || 1;
    config.size = Number(config.size) || 50;
    config.order = config.order || "desc";
    config.refresh = config.refresh || "2s";
    latestId = Number(config.latest_id) || 0;
    total = Number(config.total) || 0;
    pageCount = Math.max(1, Math.ceil(total / config.size));

    bindEvents();
    toggleCustomFields();
    updateLiveStatus();
    startPolling();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
