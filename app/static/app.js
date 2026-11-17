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
    "1h": 60 * 60 * 1000,
    "6h": 6 * 60 * 60 * 1000,
    "24h": 24 * 60 * 60 * 1000,
    "7d": 7 * 24 * 60 * 60 * 1000,
    "30d": 30 * 24 * 60 * 60 * 1000
  };

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

  function formatTime(ts) {
    var date = new Date(ts);
    if (isNaN(date.getTime())) {
      return String(ts);
    }
    return date.toISOString().replace("T", " ").slice(0, 19) + " UTC";
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
      ["Hint", envelope.hint]
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

  function buildPayload(config) {
    var now = Date.now();
    var span = RANGE_MS[config.time_range] || RANGE_MS["24h"];
    var bucket = config.bucket;

    return {
      metric: config.metric,
      from: now - span,
      to: now,
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
      config.time_range;

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
        spanGaps: true
      };
    });

    var type = config.chart_type === "bar" ? "bar" : "line";
    var options = {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: {
        legend: { display: datasets.length > 1 }
      },
      scales: {
        x: {
          ticks: {
            maxTicksLimit: 8,
            callback: function (value) {
              var label = this.getLabelForValue(value);
              return formatTime(label).slice(11, 19);
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

  function readRow(row) {
    var field = row.querySelector('[data-role="filter-field"]').value;
    var op = row.querySelector('[data-role="filter-op"]').value;
    var raw = row.querySelector('[data-role="filter-value"]').value;
    var value = raw;

    if (op === "between") {
      var bounds = String(raw)
        .split(",")
        .map(function (part) {
          return part.trim();
        })
        .filter(function (part) {
          return part.length > 0;
        });
      value = bounds.map(Number);
    } else if (op === "is_true" || op === "is_false") {
      value = null;
    } else if (raw !== "" && /^-?\d+(\.\d+)?$/.test(raw)) {
      value = Number(raw);
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
    Array.prototype.forEach.call(rows, function (row) {
      filters.push(readRow(row));
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
      opSelect.value = filter.op;
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
