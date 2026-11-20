/*
 * Laurel Metrics -- the front end of the web UI.
 *
 * Plain script, no module, no build step: the two vendored libraries
 * (Chart.js and SortableJS) are loaded as classic scripts and expose their
 * globals.  The script has two responsibilities:
 *
 *   1. render every widget of a dashboard from its stored configuration, and
 *   2. drive the widget form (type aware operators, tag discovery) and the
 *      drag & drop reordering of the widget grid.
 *
 * Every failure is shown on the widget itself with the code, the message and
 * the hint of the error envelope: a chart never fails silently.
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
    "30d": 30 * 24 * 60 * 60 * 1000,
    "90d": 90 * 24 * 60 * 60 * 1000,
    "1y": 365 * 24 * 60 * 60 * 1000
  };

  /* The ranges the dashboard level picker offers, in the order they are shown.
     A wide window is safe because the server sizes the bucket from the span:
     a year is returned as one point per day, not as a year of raw points. */
  var GLOBAL_RANGES = [
    { value: "15m", label: "Last 15 minutes" },
    { value: "1h", label: "Last 1 hour" },
    { value: "6h", label: "Last 6 hours" },
    { value: "24h", label: "Last 24 hours" },
    { value: "7d", label: "Last 7 days" },
    { value: "30d", label: "Last 30 days" },
    { value: "90d", label: "Last 90 days" },
    { value: "1y", label: "Last 1 year" },
    { value: "prev-year", label: "Previous year" },
    { value: "custom", label: "Custom range" }
  ];

  /* Colombia does not observe daylight saving time, so its offset from UTC is
     a constant five hours.  Every timestamp the user reads is rendered in
     that local time. */
  var TIMEZONE_OFFSET_MS = -5 * 60 * 60 * 1000;

  /* The range used when the URL does not ask for one. */
  var DEFAULT_RANGE = "1h";

  var NUMERIC_OPERATORS = ["=", "!=", ">", ">=", "<", "<=", "between"];
  var STRING_OPERATORS = [
    "=",
    "!=",
    "contains",
    "not_contains",
    "starts_with",
    "ends_with",
    "regex"
  ];
  var BOOLEAN_OPERATORS = ["is_true", "is_false"];

  /* The aggregations each metric type accepts.  The list mirrors the one the
     query API enforces, so the form never offers an aggregation the server
     would reject. */
  var AGGS_BY_TYPE = {
    counter: ["sum", "avg", "min", "max", "count", "last"],
    gauge: ["sum", "avg", "min", "max", "count", "last"],
    timer: ["sum", "avg", "min", "max", "count", "last"],
    string: ["count", "last"],
    bool: ["sum", "avg", "min", "max", "count", "last"]
  };

  var charts = {};

  /* The range the dashboard is currently showing.  It is a dashboard level
     decision, so it overrides the range stored in each widget. */
  var activeRange = DEFAULT_RANGE;

  /* An absolute window chosen by the user.  It is kept apart from the range
     token because its bounds do not follow the clock. */
  var customWindow = null;

  /* The last selection of this browser.  It is restored when the page is
     opened without a range in the URL, so a reload does not reset the view. */
  var STORAGE_KEY = "laurel.dashboard.range";

  /* ---------------------------------------------------------------- helpers */

  function valueKind(metricType) {
    if (metricType === "string") {
      return "string";
    }
    if (metricType === "bool") {
      return "boolean";
    }
    return "numeric";
  }

  function fieldKind(field, metricType) {
    if (field === "ts") {
      return "numeric";
    }
    if (field === "value") {
      return valueKind(metricType);
    }
    if (field.indexOf("tags.") === 0) {
      return "string";
    }
    return "numeric";
  }

  function operatorsFor(kind) {
    if (kind === "numeric") {
      return NUMERIC_OPERATORS;
    }
    if (kind === "boolean") {
      return BOOLEAN_OPERATORS;
    }
    return STRING_OPERATORS;
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

  /* The epoch milliseconds of a value that may arrive as a number, as a
     numeric string (Chart.js stringifies a label) or as a Date. */
  function toEpochMs(value) {
    if (typeof value === "number") {
      return value;
    }
    if (value instanceof Date) {
      return value.getTime();
    }
    if (typeof value === "string") {
      var text = value.trim();
      if (/^-?\d+$/.test(text)) {
        return Number(text);
      }
      return Date.parse(text);
    }
    return NaN;
  }

  function formatTime(ts) {
    var ms = toEpochMs(ts);
    if (isNaN(ms)) {
      return String(ts);
    }
    var local = new Date(ms + TIMEZONE_OFFSET_MS);
    return local.toISOString().replace("T", " ").slice(0, 19) + " GMT-5";
  }

  function clear(node) {
    while (node && node.firstChild) {
      node.removeChild(node.firstChild);
    }
  }

  /* --------------------------------------------------------- widget errors */

  function renderError(body, error, fallbackMessage) {
    var envelope = error || {};
    var box = document.createElement("div");
    box.className = "widget-error";

    var code = document.createElement("p");
    code.className = "error-code";
    code.textContent = envelope.code || "QUERY_FAILED";
    box.appendChild(code);

    var list = document.createElement("dl");
    list.className = "error-fields";

    var fields = [
      ["Message", envelope.message || fallbackMessage],
      ["Field", envelope.field],
      ["Expected", envelope.expected],
      ["Received", envelope.received],
      // The hint is never left empty: a widget that fails without an
      // explanation is a widget the user cannot act on.
      [
        "Hint",
        envelope.hint ||
          "Check the server logs for the full traceback and retry the request."
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
    clear(body);
    body.appendChild(box);
  }

  /* -------------------------------------------------------------- queries */

  /* The previous calendar year in the local time of Colombia.  The bounds are
     the first and the last millisecond of the year the user means. */
  function previousYearWindow(nowMs) {
    var year = new Date(nowMs + TIMEZONE_OFFSET_MS).getUTCFullYear() - 1;
    var fromLocal = Date.UTC(year, 0, 1, 0, 0, 0, 0);
    var toLocal = Date.UTC(year + 1, 0, 1, 0, 0, 0, 0) - 1;
    return {
      from: fromLocal - TIMEZONE_OFFSET_MS,
      to: toLocal - TIMEZONE_OFFSET_MS
    };
  }

  /* The bounds of the window the picker currently asks for.  A rolling range
     follows the clock; the previous year and a custom range are absolute. */
  function resolveWindow(nowMs) {
    if (activeRange === "prev-year") {
      return previousYearWindow(nowMs);
    }
    if (activeRange === "custom") {
      if (customWindow) {
        return { from: customWindow.from, to: customWindow.to };
      }
      return { from: nowMs - RANGE_MS["24h"], to: nowMs };
    }
    var span = RANGE_MS[activeRange] || RANGE_MS[DEFAULT_RANGE];
    return { from: nowMs - span, to: nowMs };
  }

  function windowSpanMs(nowMs) {
    var bounds = resolveWindow(nowMs === undefined ? Date.now() : nowMs);
    return bounds.to - bounds.from;
  }

  function buildPayload(config) {
    var windowBounds = resolveWindow(Date.now());
    var bucket = config.bucket;

    return {
      metric: config.metric,
      from: windowBounds.from,
      to: windowBounds.to,
      agg: config.agg,
      bucket:
        bucket === null || bucket === undefined || bucket === "" || bucket === "auto"
          ? "auto"
          : Number(bucket),
      group_by: config.group_by || null,
      filters: Array.isArray(config.filters) ? config.filters : []
    };
  }

  function postJson(url, payload) {
    return fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json"
      },
      body: JSON.stringify(payload)
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

  /* ------------------------------------------------------- dashboard range */

  function rangeLabel(range) {
    var found = GLOBAL_RANGES.filter(function (entry) {
      return entry.value === range;
    });
    return found.length ? found[0].label : range;
  }

  function isKnownRange(value) {
    return GLOBAL_RANGES.some(function (entry) {
      return entry.value === value;
    });
  }

  /* The range asked for by the URL, or the default one when the URL is silent
     or asks for a range the picker does not offer.  A custom range reads its
     two absolute bounds from the same URL. */
  function rangeFromUrl() {
    var params = new URLSearchParams(window.location.search);
    var value = params.get("range");
    if (value === "custom") {
      var from = Number(params.get("from"));
      var to = Number(params.get("to"));
      if (isFinite(from) && isFinite(to) && from < to) {
        customWindow = { from: from, to: to };
        return "custom";
      }
      customWindow = null;
      return DEFAULT_RANGE;
    }
    return isKnownRange(value) ? value : DEFAULT_RANGE;
  }

  /* Write the range into the URL so the view can be shared and restored. */
  function rangeToUrl(range) {
    var url = new URL(window.location.href);
    url.searchParams.set("range", range);
    if (range === "custom" && customWindow) {
      url.searchParams.set("from", String(customWindow.from));
      url.searchParams.set("to", String(customWindow.to));
    } else {
      url.searchParams.delete("from");
      url.searchParams.delete("to");
    }
    return url.toString();
  }

  /* The last selection saved in this browser, or null when there is none or
     the storage is denied. */
  function savedRange() {
    try {
      var raw = window.localStorage.getItem(STORAGE_KEY);
      if (!raw) {
        return null;
      }
      var saved = JSON.parse(raw);
      return saved && typeof saved.range === "string" ? saved : null;
    } catch (error) {
      return null;
    }
  }

  /* Remember the selection.  A browser that denies storage is not a reason to
     break the picker: the selection still works for the current view. */
  function saveRange(range) {
    try {
      var saved = { range: range };
      if (range === "custom" && customWindow) {
        saved.from = customWindow.from;
        saved.to = customWindow.to;
      }
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(saved));
    } catch (error) {
      return;
    }
  }

  /* The selection to restore on load.  The URL wins over the last choice saved
     in this browser, which wins over the default. */
  function initialRange() {
    var params = new URLSearchParams(window.location.search);
    if (params.get("range")) {
      return rangeFromUrl();
    }
    var saved = savedRange();
    if (saved && isKnownRange(saved.range)) {
      if (saved.range !== "custom") {
        return saved.range;
      }
      if (
        typeof saved.from === "number" &&
        typeof saved.to === "number" &&
        saved.from < saved.to
      ) {
        customWindow = { from: saved.from, to: saved.to };
        return "custom";
      }
    }
    return DEFAULT_RANGE;
  }

  /* Show the range in the widget metadata and in the picker label. */
  function syncRangeLabels(range) {
    var label = document.querySelector('[data-role="time-picker-label"]');
    if (label) {
      label.textContent = rangeLabel(range);
    }
    Array.prototype.forEach.call(
      document.querySelectorAll('[data-role="range"]'),
      function (node) {
        node.textContent = rangeLabel(range);
      }
    );
  }

  function renderAllWidgets() {
    Array.prototype.forEach.call(
      document.querySelectorAll("[data-widget-id]"),
      function (widget) {
        renderWidget(widget);
      }
    );
  }

  /* Apply a range to the whole dashboard: the widgets are re-queried with the
     new window, the selection is remembered and the URL follows it. */
  function applyRange(range, options) {
    var push = !options || options.push !== false;
    activeRange = range;
    saveRange(range);
    syncRangeLabels(range);
    if (push && window.history && window.history.pushState) {
      window.history.pushState({ range: range }, "", rangeToUrl(range));
    } else if (window.history && window.history.replaceState) {
      window.history.replaceState({ range: range }, "", rangeToUrl(range));
    }
    renderAllWidgets();
  }

  function initTimePicker() {
    var picker = document.querySelector('[data-role="time-picker"]');
    if (!picker) {
      return;
    }

    var button = picker.querySelector('[data-role="time-picker-button"]');
    var menu = picker.querySelector('[data-role="time-picker-menu"]');
    if (!button || !menu) {
      return;
    }

    var customPanel = picker.querySelector('[data-role="custom-panel"]');
    var customStart = picker.querySelector('[data-role="custom-start"]');
    var customEnd = picker.querySelector('[data-role="custom-end"]');
    var customApply = picker.querySelector('[data-role="custom-apply"]');
    var customError = picker.querySelector('[data-role="custom-error"]');

    function closeMenu() {
      menu.hidden = true;
      button.setAttribute("aria-expanded", "false");
    }

    function openMenu() {
      menu.hidden = false;
      button.setAttribute("aria-expanded", "true");
    }

    /* The datetime-local value of an epoch, in the local time of Colombia. */
    function localInputValue(ts) {
      var local = new Date(ts + TIMEZONE_OFFSET_MS);
      return local.toISOString().slice(0, 16);
    }

    /* The epoch of a datetime-local value read as Colombia time. */
    function parseInput(text) {
      var value = String(text || "").trim();
      if (!value) {
        return null;
      }
      // Some browsers omit the seconds; Date.parse needs them.
      if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)) {
        value += ":00";
      }
      var parsed = Date.parse(value + "Z");
      if (isNaN(parsed)) {
        return null;
      }
      return parsed - TIMEZONE_OFFSET_MS;
    }

    function showCustomError(message) {
      if (customError) {
        customError.textContent = message;
      }
    }

    /* Reveal the two absolute bounds, prefilled with the window on screen so
       the user edits a real range instead of an empty form. */
    function openCustomPanel() {
      if (!customPanel) {
        return;
      }
      var bounds = resolveWindow(Date.now());
      if (customStart && !customStart.value) {
        customStart.value = localInputValue(bounds.from);
      }
      if (customEnd && !customEnd.value) {
        customEnd.value = localInputValue(bounds.to);
      }
      customPanel.hidden = false;
      showCustomError("");
    }

    function applyCustom() {
      var from = parseInput(customStart && customStart.value);
      var to = parseInput(customEnd && customEnd.value);
      if (from === null || to === null) {
        showCustomError("Pick a valid start and an end.");
        return;
      }
      if (from >= to) {
        showCustomError("The start must be earlier than the end.");
        return;
      }
      customWindow = { from: from, to: to };
      closeMenu();
      applyRange("custom");
      button.focus();
    }

    function choose(range) {
      if (range === "custom") {
        // The custom entry reveals the bounds, it does not apply a window yet.
        openCustomPanel();
        return;
      }
      customWindow = null;
      closeMenu();
      applyRange(range);
      button.focus();
    }

    button.addEventListener("click", function () {
      if (menu.hidden) {
        openMenu();
      } else {
        closeMenu();
      }
    });

    Array.prototype.forEach.call(
      menu.querySelectorAll("[data-range]"),
      function (option) {
        option.addEventListener("click", function () {
          choose(option.getAttribute("data-range"));
        });
      }
    );

    if (customApply) {
      customApply.addEventListener("click", applyCustom);
    }

    document.addEventListener("click", function (event) {
      if (!picker.contains(event.target)) {
        closeMenu();
      }
    });

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && !menu.hidden) {
        closeMenu();
        button.focus();
      }
    });

    // The browser back button moves between the ranges of the session.
    window.addEventListener("popstate", function () {
      applyRange(initialRange(), { push: false });
    });

    activeRange = initialRange();
    if (activeRange === "custom" && customWindow) {
      // A shared or saved custom window fills the bounds of the form.
      if (customStart) {
        customStart.value = localInputValue(customWindow.from);
      }
      if (customEnd) {
        customEnd.value = localInputValue(customWindow.to);
      }
    }
    syncRangeLabels(activeRange);
  }

  /* ------------------------------------------------------- confirm dialog */

  /* The form waiting for the confirmation.  It is kept here so the dialog
     knows what it has to submit when the user confirms. */
  var pendingConfirmForm = null;

  function openConfirmDialog(message, form) {
    var dialog = document.querySelector('[data-role="confirm-dialog"]');
    if (!dialog) {
      return false;
    }
    var text = dialog.querySelector('[data-role="confirm-dialog-message"]');
    if (text) {
      text.textContent = message;
    }
    pendingConfirmForm = form;
    if (typeof dialog.showModal === "function") {
      dialog.showModal();
    } else {
      dialog.setAttribute("open", "open");
    }
    return true;
  }

  function closeConfirmDialog() {
    var dialog = document.querySelector('[data-role="confirm-dialog"]');
    pendingConfirmForm = null;
    if (!dialog) {
      return;
    }
    if (typeof dialog.close === "function") {
      dialog.close();
    } else {
      dialog.removeAttribute("open");
    }
  }

  function initConfirmDialog() {
    var dialog = document.querySelector('[data-role="confirm-dialog"]');
    if (!dialog) {
      return;
    }

    var accept = dialog.querySelector('[data-role="confirm-accept"]');
    var cancel = dialog.querySelector('[data-role="confirm-cancel"]');

    if (cancel) {
      cancel.addEventListener("click", closeConfirmDialog);
    }

    if (accept) {
      accept.addEventListener("click", function () {
        var form = pendingConfirmForm;
        closeConfirmDialog();
        if (form) {
          form.submit();
        }
      });
    }

    // The Escape key and the browser close the dialog without confirming:
    // the pending form is dropped, so nothing is deleted.
    dialog.addEventListener("close", function () {
      pendingConfirmForm = null;
    });

    Array.prototype.forEach.call(
      document.querySelectorAll("form[data-role='confirm-form']"),
      function (form) {
        form.addEventListener("submit", function (event) {
          event.preventDefault();
          openConfirmDialog(
            form.getAttribute("data-confirm") || "Are you sure?",
            form
          );
        });
      }
    );
  }

  /* ------------------------------------------------------------ rendering */

  function renderTable(body, config, data) {
    var table = document.createElement("table");
    table.className = "widget-table";

    var head = document.createElement("thead");
    var headRow = document.createElement("tr");
    ["Series", "Bucket", "Value", "Points"].forEach(function (label) {
      var cell = document.createElement("th");
      cell.textContent = label;
      headRow.appendChild(cell);
    });
    head.appendChild(headRow);
    table.appendChild(head);

    var bodyRow = document.createElement("tbody");
    (data.series || []).forEach(function (serie) {
      (serie.points || []).forEach(function (point) {
        var row = document.createElement("tr");
        var label = serie.key === null || serie.key === undefined ? "no tag" : serie.key;
        [
          label,
          formatTime(point.ts),
          point.value === null || point.value === undefined ? "-" : point.value,
          point.count
        ].forEach(function (value) {
          var cell = document.createElement("td");
          cell.textContent = String(value);
          row.appendChild(cell);
        });
        bodyRow.appendChild(row);
      });
    });
    table.appendChild(bodyRow);

    clear(body);
    body.appendChild(table);
  }

  function computeStat(points, agg) {
    var usable = points.filter(function (point) {
      return point.value !== null && point.value !== undefined;
    });

    if (agg === "count") {
      return points.length;
    }
    if (!usable.length) {
      return null;
    }
    if (agg === "last") {
      var newest = usable.reduce(function (best, point) {
        return point.ts > best.ts ? point : best;
      }, usable[0]);
      return newest.value;
    }

    var numbers = usable.map(function (point) {
      return Number(point.value);
    });
    var total = numbers.reduce(function (sum, value) {
      return sum + value;
    }, 0);

    if (agg === "sum") {
      return total;
    }
    if (agg === "avg") {
      return total / numbers.length;
    }
    if (agg === "min") {
      return Math.min.apply(null, numbers);
    }
    if (agg === "max") {
      return Math.max.apply(null, numbers);
    }
    return total;
  }

  function renderStat(body, config, data) {
    var points = [];
    (data.series || []).forEach(function (serie) {
      (serie.points || []).forEach(function (point) {
        points.push(point);
      });
    });

    var value = computeStat(points, config.agg);

    var caption = document.createElement("p");
    caption.className = "stat-caption";
    caption.textContent =
      config.metric +
      " (" +
      config.agg +
      ") over " +
      activeRange;

    var stat = document.createElement("div");
    stat.className = "stat";

    var number = document.createElement("span");
    number.className = "stat-value";
    number.textContent =
      value === null || value === undefined
        ? "-"
        : typeof value === "number" && !isNaN(value)
        ? String(Math.round(value * 1000) / 1000)
        : String(value);
    stat.appendChild(number);

    if (config.unit) {
      var unit = document.createElement("span");
      unit.className = "stat-unit";
      unit.textContent = config.unit;
      stat.appendChild(unit);
    }

    clear(body);
    body.appendChild(caption);
    body.appendChild(stat);
  }

  /* Build the whole time axis of the window.  The server only returns the
     buckets that carry data, so a period without a measurement would collapse
     the axis and hide the silence.  The full axis keeps the empty stretches
     visible: a gap in the chart means the metric said nothing. */
  function buildTimeAxis(fromMs, toMs, bucketSec) {
    var step = bucketSec * 1000;
    if (!step || step <= 0) {
      return null;
    }
    var start = Math.floor(fromMs / step) * step;
    var count = Math.floor((toMs - start) / step) + 1;
    // A pathological window would freeze the browser: the axis is not built
    // and the chart falls back to the timestamps the server returned.
    if (count > 5000) {
      return null;
    }
    var axis = [];
    for (var ts = start; ts <= toMs; ts += step) {
      axis.push(ts);
    }
    return axis;
  }

  /* The title of a tooltip is the timestamp of the point, in the local time.
     Without it the default title is the raw epoch label, a long number that
     means nothing to the reader. */
  function tooltipTitle(items) {
    if (!items || !items.length) {
      return "";
    }
    return formatTime(items[0].label);
  }

  function renderChart(body, config, data) {
    var canvas = document.createElement("canvas");
    canvas.setAttribute("role", "img");
    canvas.setAttribute(
      "aria-label",
      config.title || config.metric || "metric chart"
    );

    // The canvas lives in a box with a fixed height: Chart.js sizes the
    // canvas from its parent, and an auto height parent would collapse the
    // chart.
    var box = document.createElement("div");
    box.className = "chart-box";
    box.appendChild(canvas);

    clear(body);
    body.appendChild(box);

    var series = data.series || [];
    var timestamps = [];
    if (data.bucket_sec && data.from_ms && data.to_ms) {
      timestamps = buildTimeAxis(data.from_ms, data.to_ms, data.bucket_sec) || [];
    }
    if (!timestamps.length) {
      // Without a usable window the axis is built from the timestamps the
      // server returned, so the chart still shows the data it has.
      series.forEach(function (serie) {
        (serie.points || []).forEach(function (point) {
          if (timestamps.indexOf(point.ts) === -1) {
            timestamps.push(point.ts);
          }
        });
      });
      timestamps.sort(function (a, b) {
        return a - b;
      });
    }

    var datasets = series.map(function (serie) {
      var byTs = {};
      (serie.points || []).forEach(function (point) {
        byTs[point.ts] = point.value;
      });
      return {
        label: serie.key === null || serie.key === undefined ? "no tag" : String(serie.key),
        data: timestamps.map(function (ts) {
          var value = byTs[ts];
          return value === null || value === undefined ? null : value;
        }),
        borderWidth: 2,
        // A period without data is drawn as a break: joining both sides would
        // claim the metric kept a value it never reported.
        spanGaps: false
      };
    });

    var type = config.chart_type === "bar" ? "bar" : "line";
    var options = {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: {
        legend: { display: datasets.length > 1 },
        tooltip: {
          callbacks: {
            title: tooltipTitle
          }
        }
      },
      scales: {
        x: {
          ticks: {
            maxTicksLimit: 8,
            callback: function (value) {
              var label = this.getLabelForValue(value);
              var text = formatTime(label);
              // A window of more than a day needs the date on the axis: two
              // moments of different days would otherwise look the same.
              if (windowSpanMs() > 24 * 60 * 60 * 1000) {
                return text.slice(5, 10) + " " + text.slice(11, 16);
              }
              return text.slice(11, 19);
            }
          }
        },
        y: { beginAtZero: false }
      }
    };

    if (typeof Chart === "undefined") {
      renderError(body, null, "The chart library could not be loaded.");
      return;
    }

    charts[config.id] = new Chart(canvas, {
      type: type,
      data: {
        labels: timestamps,
        datasets: datasets
      },
      options: options
    });
  }

  function renderWidget(widget) {
    var body = widget.querySelector('[data-role="body"]');
    if (!body) {
      return;
    }

    var config;
    try {
      config = JSON.parse(widget.getAttribute("data-config"));
    } catch (error) {
      renderError(body, null, "The configuration of the widget is broken.");
      return;
    }

    if (charts[config.id]) {
      charts[config.id].destroy();
      delete charts[config.id];
    }

    postJson("/api/query", buildPayload(config)).then(
      function (result) {
        if (!result.ok) {
          var envelope = result.data && result.data.error ? result.data.error : null;
          renderError(body, envelope, "The query failed with HTTP " + result.status + ".");
          return;
        }
        if (!result.data) {
          // A successful answer that is not JSON cannot be rendered: the
          // widget shows the error instead of staying on the placeholder.
          renderError(
            body,
            null,
            "The server answered with an unreadable body (HTTP " + result.status + ")."
          );
          return;
        }
        if (config.chart_type === "table") {
          renderTable(body, config, result.data);
        } else if (config.chart_type === "stat") {
          renderStat(body, config, result.data);
        } else {
          renderChart(body, config, result.data);
        }
      },
      function (error) {
        renderError(body, null, "The query could not be sent: " + error);
      }
    );
  }

  /* --------------------------------------------------- widget detail modal */

  /* The widget rendered inside the modal.  It is kept apart from the tile so
     its chart instance and its DOM node can be destroyed on close. */
  var modalWidget = null;

  function teardownModalWidget() {
    if (!modalWidget) {
      return;
    }
    if (charts[modalWidget.chartId]) {
      charts[modalWidget.chartId].destroy();
      delete charts[modalWidget.chartId];
    }
    if (modalWidget.element && modalWidget.element.parentNode) {
      modalWidget.element.parentNode.removeChild(modalWidget.element);
    }
    modalWidget = null;
  }

  function closeWidgetModal() {
    teardownModalWidget();
    var dialog = document.querySelector('[data-role="widget-modal"]');
    if (!dialog) {
      return;
    }
    if (typeof dialog.close === "function") {
      dialog.close();
    } else {
      dialog.removeAttribute("open");
    }
  }

  /* Render a copy of the widget into the dialog.  The copy gets its own chart
     key, so the tile keeps its instance and the detail follows the dashboard
     range like every other widget. */
  function openWidgetModal(sourceWidget) {
    var dialog = document.querySelector('[data-role="widget-modal"]');
    var body = dialog && dialog.querySelector('[data-role="widget-modal-body"]');
    var title = dialog && dialog.querySelector('[data-role="widget-modal-title"]');
    if (!dialog || !body || !title) {
      return;
    }

    var config;
    try {
      config = JSON.parse(sourceWidget.getAttribute("data-config"));
    } catch (error) {
      return;
    }

    closeWidgetModal();

    var titleNode = sourceWidget.querySelector(".widget-title");
    title.textContent = titleNode ? titleNode.textContent : "Widget";

    config.id = "detail-" + config.id;
    var article = document.createElement("article");
    article.className = "widget widget-detail";
    article.setAttribute("data-widget-id", String(config.id));
    article.setAttribute("data-config", JSON.stringify(config));

    var widgetBody = document.createElement("div");
    widgetBody.className = "widget-body";
    widgetBody.setAttribute("data-role", "body");
    widgetBody.textContent = "Loading the data\u2026";
    article.appendChild(widgetBody);

    body.textContent = "";
    body.appendChild(article);
    modalWidget = { element: article, chartId: config.id };

    if (typeof dialog.showModal === "function") {
      dialog.showModal();
    } else {
      dialog.setAttribute("open", "open");
    }

    renderWidget(article);
  }

  function initWidgetModal() {
    var dialog = document.querySelector('[data-role="widget-modal"]');
    if (!dialog) {
      return;
    }

    var close = dialog.querySelector('[data-role="widget-modal-close"]');
    if (close) {
      close.addEventListener("click", closeWidgetModal);
    }

    // The Escape key and the backdrop close the dialog natively: the copies
    // are torn down when the close event arrives.
    dialog.addEventListener("close", teardownModalWidget);
    dialog.addEventListener("click", function (event) {
      if (event.target === dialog) {
        closeWidgetModal();
      }
    });

    Array.prototype.forEach.call(
      document.querySelectorAll('[data-role="expand"]'),
      function (button) {
        button.addEventListener("click", function () {
          var widget = button.closest("[data-widget-id]");
          if (widget) {
            openWidgetModal(widget);
          }
        });
      }
    );
  }

  /* --------------------------------------------------------- drag & drop */

  function persistOrder(grid) {
    var status = document.querySelector('[data-role="order-status"]');
    var ids = Array.prototype.map.call(
      grid.querySelectorAll("[data-widget-id]"),
      function (element) {
        return parseInt(element.getAttribute("data-widget-id"), 10);
      }
    );
    var dashboardId = grid.getAttribute("data-dashboard-id");

    function report(text, failed) {
      if (!status) {
        return;
      }
      status.textContent = text;
      status.classList.toggle("is-error", Boolean(failed));
    }

    postJson("/dashboards/" + dashboardId + "/order", { widget_ids: ids }).then(
      function (result) {
        if (result.ok) {
          report("The new order was saved.", false);
          return;
        }
        var envelope = result.data && result.data.error ? result.data.error : null;
        report(
          "The new order was not saved: " +
            (envelope ? envelope.code + " - " + envelope.message : "HTTP " + result.status),
          true
        );
      },
      function (error) {
        report("The new order was not saved: " + error, true);
      }
    );
  }

  function initGrid() {
    var grid = document.getElementById("widget-grid");
    if (!grid || typeof Sortable === "undefined") {
      return;
    }

    Sortable.create(grid, {
      animation: 150,
      handle: '[data-role="handle"]',
      ghostClass: "sortable-ghost",
      chosenClass: "sortable-chosen",
      onEnd: function () {
        persistOrder(grid);
      }
    });
  }

  /* ------------------------------------------------------------ the form */

  function setOptions(select, options, selected) {
    clear(select);
    options.forEach(function (option) {
      var element = document.createElement("option");
      element.value = option.value;
      element.textContent = option.label;
      if (option.value === selected) {
        element.selected = true;
      }
      select.appendChild(element);
    });
  }

  function currentMetricType(form) {
    var select = form.querySelector('[data-role="metric-select"]');
    if (!select) {
      return "counter";
    }
    var option = select.options[select.selectedIndex];
    return (option && option.getAttribute("data-type")) || "counter";
  }

  function refreshAggregations(form) {
    var select = form.querySelector('[data-role="agg-select"]');
    if (!select) {
      return;
    }
    var allowed = AGGS_BY_TYPE[currentMetricType(form)] || AGGS_BY_TYPE.counter;
    var previous = select.value;
    var options = allowed.map(function (agg) {
      return { value: agg, label: agg };
    });
    setOptions(select, options, allowed.indexOf(previous) === -1 ? allowed[0] : previous);
  }

  function applyFieldOptions(form, row, metricType, tagKeys) {
    var fieldSelect = row.querySelector('[data-role="filter-field"]');
    var opSelect = row.querySelector('[data-role="filter-op"]');
    var valueInput = row.querySelector('[data-role="filter-value"]');

    var fields = [
      { value: "value", label: "value" },
      { value: "ts", label: "ts" }
    ];
    tagKeys.forEach(function (key) {
      fields.push({ value: "tags." + key, label: "tags." + key });
    });

    // The selected field is kept on the row itself: the select is rebuilt
    // from scratch every time, so it cannot carry the selection.
    var selectedField = row.getAttribute("data-field") || "value";
    if (!fields.some(function (option) {
      return option.value === selectedField;
    })) {
      selectedField = "value";
    }
    setOptions(fieldSelect, fields, selectedField);
    row.setAttribute("data-field", selectedField);

    var kind = fieldKind(selectedField, metricType);
    var operators = operatorsFor(kind);
    // The operator is kept when it is still valid for the field, so changing
    // the metric does not silently reset a valid comparison.
    var previousOperator = opSelect.value;
    var selectedOperator =
      operators.indexOf(previousOperator) === -1 ? operators[0] : previousOperator;
    setOptions(
      opSelect,
      operators.map(function (op) {
        return { value: op, label: op };
      }),
      selectedOperator
    );

    valueInput.disabled = kind === "boolean";
    if (kind === "boolean") {
      valueInput.placeholder = "ignored";
    } else if (kind === "numeric") {
      valueInput.placeholder = "10";
    } else {
      valueInput.placeholder = "text";
    }
  }

  /* Return the number a text holds, or null when it does not hold one. */
  function toNumber(text) {
    if (typeof text !== "string") {
      return null;
    }
    var trimmed = text.trim();
    if (!/^-?\d+(\.\d+)?$/.test(trimmed)) {
      return null;
    }
    var number = Number(trimmed);
    return isFinite(number) ? number : null;
  }

  function readRow(row, metricType) {
    var field = row.querySelector('[data-role="filter-field"]').value;
    var op = row.querySelector('[data-role="filter-op"]').value;
    var raw = row.querySelector('[data-role="filter-value"]').value;
    var value = raw;
    var kind = fieldKind(field, metricType);

    if (op === "between") {
      var bounds = String(raw)
        .split(",")
        .map(function (part) {
          return part.trim();
        })
        .filter(function (part) {
          return part.length > 0;
        });
      // A bound that is not a number is kept as text: the server then answers
      // with the explicit INVALID_FILTER_VALUE error instead of the browser
      // silently sending a null.
      value = bounds.map(function (part) {
        var number = toNumber(part);
        return number === null ? part : number;
      });
    } else if (op === "is_true" || op === "is_false") {
      value = null;
    } else if (kind === "numeric") {
      // Only a numeric field carries a number.  A textual field keeps its
      // text even when it looks like a number: "10" is not 10.
      var number = toNumber(raw);
      value = number === null ? raw : number;
    }

    return { field: field, op: op, value: value };
  }

  function syncFilters(form) {
    var textarea = form.querySelector('[data-role="filters-json"]');
    if (!textarea) {
      return;
    }
    var rows = form.querySelectorAll('[data-role="filter-row"]');
    var filters = [];
    var metricType = currentMetricType(form);
    Array.prototype.forEach.call(rows, function (row) {
      filters.push(readRow(row, metricType));
    });
    textarea.value = JSON.stringify(filters);
  }

  function buildRow(form, metricType, tagKeys, filter) {
    var row = document.createElement("div");
    row.className = "filter-row";
    row.setAttribute("data-role", "filter-row");

    var fieldLabel = document.createElement("label");
    fieldLabel.className = "field field-inline";
    var fieldCaption = document.createElement("span");
    fieldCaption.className = "field-label";
    fieldCaption.textContent = "Field";
    var fieldSelect = document.createElement("select");
    fieldSelect.setAttribute("data-role", "filter-field");
    fieldSelect.setAttribute("name", "filter_field");
    fieldLabel.appendChild(fieldCaption);
    fieldLabel.appendChild(fieldSelect);

    var opLabel = document.createElement("label");
    opLabel.className = "field field-inline";
    var opCaption = document.createElement("span");
    opCaption.className = "field-label";
    opCaption.textContent = "Operator";
    var opSelect = document.createElement("select");
    opSelect.setAttribute("data-role", "filter-op");
    opSelect.setAttribute("name", "filter_op");
    opLabel.appendChild(opCaption);
    opLabel.appendChild(opSelect);

    var valueLabel = document.createElement("label");
    valueLabel.className = "field field-inline";
    var valueCaption = document.createElement("span");
    valueCaption.className = "field-label";
    valueCaption.textContent = "Value";
    var valueInput = document.createElement("input");
    valueInput.setAttribute("type", "text");
    valueInput.setAttribute("data-role", "filter-value");
    valueInput.setAttribute("name", "filter_value");
    valueLabel.appendChild(valueCaption);
    valueLabel.appendChild(valueInput);

    var remove = document.createElement("button");
    remove.type = "button";
    remove.className = "button button-small";
    remove.setAttribute("data-role", "filter-remove");
    remove.textContent = "Remove";

    row.appendChild(fieldLabel);
    row.appendChild(opLabel);
    row.appendChild(valueLabel);
    row.appendChild(remove);

    row.setAttribute("data-field", filter ? filter.field : "value");
    valueInput.value =
      filter && filter.value !== null && filter.value !== undefined
        ? Array.isArray(filter.value)
          ? filter.value.join(", ")
          : String(filter.value)
        : "";

    form
      .querySelector('[data-role="filter-rows"]')
      .appendChild(row);

    applyFieldOptions(form, row, metricType, tagKeys);

    if (filter && filter.op) {
      // The operator is only restored when it is still valid for the field:
      // assigning an unknown value to a select leaves it empty, which would
      // send an empty operator to the server.
      var known = Array.prototype.some.call(opSelect.options, function (option) {
        return option.value === filter.op;
      });
      if (known) {
        opSelect.value = filter.op;
      }
    }

    remove.addEventListener("click", function () {
      row.remove();
      syncFilters(form);
    });

    fieldSelect.addEventListener("change", function () {
      row.setAttribute("data-field", fieldSelect.value);
      applyFieldOptions(form, row, metricType, tagKeys);
      syncFilters(form);
    });

    opSelect.addEventListener("change", syncFilters.bind(null, form));
    valueInput.addEventListener("input", syncFilters.bind(null, form));

    return row;
  }

  function rebuildRows(form, metricType, tagKeys) {
    var container = form.querySelector('[data-role="filter-rows"]');
    var textarea = form.querySelector('[data-role="filters-json"]');
    if (!container || !textarea) {
      return;
    }

    var filters = [];
    try {
      var parsed = JSON.parse(textarea.value || "[]");
      if (Array.isArray(parsed)) {
        filters = parsed;
      }
    } catch (error) {
      filters = [];
    }

    clear(container);
    filters.forEach(function (filter) {
      buildRow(form, metricType, tagKeys, filter);
    });
    syncFilters(form);
  }

  function initForm() {
    var form = document.querySelector('[data-role="widget-form"]');
    if (!form) {
      return;
    }

    var metricSelect = form.querySelector('[data-role="metric-select"]');
    var groupSelect = form.querySelector('[data-role="group-by-select"]');
    var tagKeys = [];

    function refreshTagKeys() {
      var name = metricSelect.value;
      if (!name) {
        tagKeys = [];
        if (groupSelect) {
          setOptions(groupSelect, [{ value: "", label: "No grouping" }], "");
        }
        return Promise.resolve();
      }

      return fetch("/api/metrics/" + encodeURIComponent(name) + "/tags", {
        headers: { Accept: "application/json" }
      })
        .then(function (response) {
          return response.json().catch(function () {
            return null;
          });
        })
        .then(function (payload) {
          tagKeys = payload && Array.isArray(payload.keys) ? payload.keys : [];
          if (groupSelect) {
            var previous = groupSelect.value;
            var options = [{ value: "", label: "No grouping" }];
            tagKeys.forEach(function (key) {
              options.push({ value: key, label: key });
            });
            setOptions(
              groupSelect,
              options,
              options.some(function (option) {
                return option.value === previous;
              })
                ? previous
                : ""
            );
          }
          return tagKeys;
        })
        .catch(function () {
          tagKeys = [];
          return tagKeys;
        });
    }

    function refreshEverything() {
      refreshAggregations(form);
      return refreshTagKeys().then(function () {
        var metricType = currentMetricType(form);
        var rows = form.querySelectorAll('[data-role="filter-row"]');
        Array.prototype.forEach.call(rows, function (row) {
          applyFieldOptions(form, row, metricType, tagKeys);
        });
        return tagKeys;
      });
    }

    if (metricSelect) {
      metricSelect.addEventListener("change", refreshEverything);
    }

    var addButton = form.querySelector('[data-role="filter-add"]');
    if (addButton) {
      addButton.addEventListener("click", function () {
        var metricType = currentMetricType(form);
        var row = buildRow(form, metricType, tagKeys, null);
        syncFilters(form);
        var input = row.querySelector('[data-role="filter-value"]');
        if (input) {
          input.focus();
        }
      });
    }

    var textarea = form.querySelector('[data-role="filters-json"]');
    if (textarea) {
      textarea.addEventListener("change", function () {
        rebuildRows(form, currentMetricType(form), tagKeys);
      });
    }

    // The rows are rebuilt from the JSON document, so the form always shows
    // exactly what will be sent to the query API.
    refreshEverything().then(function () {
      rebuildRows(form, currentMetricType(form), tagKeys);
    });
  }

  /* ---------------------------------------------------------------- start */

  function init() {
    initTimePicker();
    initConfirmDialog();
    initWidgetModal();
    Array.prototype.forEach.call(
      document.querySelectorAll("[data-widget-id]"),
      function (widget) {
        renderWidget(widget);
      }
    );
    initGrid();
    initForm();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
