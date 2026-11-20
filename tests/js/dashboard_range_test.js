/*
 * Regression test of the dashboard level time range.
 *
 * The script exercises the *shipped* source of ``app/static/app.js``: the
 * constants, the helpers and the payload builder are extracted from the file
 * and evaluated, so the test cannot drift away from the code it checks.
 *
 * The defect it guards: every widget used to build the window of its query
 * from the ``time_range`` stored in the widget.  A widget configured with
 * ``30d`` therefore scanned and serialized a month of raw points on every
 * dashboard load, which is what exhausted the workers.  The range is a
 * dashboard level decision now, and it has to override the stored one.
 *
 * Run it with ``node tests/js/dashboard_range_test.js``.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const SOURCE = process.argv[2]
  ? path.resolve(process.argv[2])
  : path.join(__dirname, "..", "..", "app", "static", "app.js");
const source = fs.readFileSync(SOURCE, "utf8");

/* Extract a top level function of the source file. */
function extract(name) {
  const start = source.indexOf("function " + name + "(");
  if (start === -1) {
    throw new Error("the function " + name + " is not in " + SOURCE);
  }
  let depth = 0;
  for (let index = source.indexOf("{", start); index < source.length; index++) {
    if (source[index] === "{") {
      depth++;
    } else if (source[index] === "}") {
      depth--;
      if (depth === 0) {
        return source.slice(start, index + 1);
      }
    }
  }
  throw new Error("the function " + name + " is not balanced in " + SOURCE);
}

/* Extract the declaration of a top level variable of the source file. */
function extractVar(name) {
  const marker = "var " + name + " =";
  const start = source.indexOf(marker);
  if (start === -1) {
    throw new Error("the variable " + name + " is not in " + SOURCE);
  }
  let depth = 0;
  for (let index = source.indexOf("=", start); index < source.length; index++) {
    const character = source[index];
    if (character === "{" || character === "[" || character === "(") {
      depth++;
    } else if (character === "}" || character === "]" || character === ")") {
      depth--;
    } else if (character === ";" && depth === 0) {
      return source.slice(start, index + 1);
    }
  }
  throw new Error("the variable " + name + " is not balanced in " + SOURCE);
}

/* The helpers under test, taken verbatim from the shipped file. */
const helpers = new Function(
  [
    extractVar("RANGE_MS"),
    extractVar("GLOBAL_RANGES"),
    extractVar("DEFAULT_RANGE"),
    extractVar("activeRange"),
    extract("buildPayload"),
    extract("rangeLabel"),
    extract("rangeFromUrl"),
    extract("rangeToUrl"),
    "return {",
    "  buildPayload: buildPayload,",
    "  rangeLabel: rangeLabel,",
    "  rangeFromUrl: rangeFromUrl,",
    "  rangeToUrl: rangeToUrl,",
    "  setRange: function (range) { activeRange = range; },",
    "  getRange: function () { return activeRange; }",
    "};"
  ].join("\n")
)();

/* The browser globals the extracted helpers read. */
const browser = {
  location: {
    search: "",
    href: "http://localhost/dashboards/1"
  }
};
global.window = browser;

/* The window of a range, in milliseconds, as the picker defines it. */
const SPAN_MS = {
  "15m": 15 * 60 * 1000,
  "1h": 60 * 60 * 1000,
  "6h": 6 * 60 * 60 * 1000,
  "24h": 24 * 60 * 60 * 1000,
  "7d": 7 * 24 * 60 * 60 * 1000
};

/* A widget configuration, with the range it stores. */
function widgetConfig(timeRange) {
  return {
    id: 1,
    metric: "requests",
    agg: "sum",
    bucket: "auto",
    time_range: timeRange,
    group_by: null,
    filters: []
  };
}

let failures = 0;

function check(label, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if (!ok) {
    failures++;
  }
  console.log(
    (ok ? "PASS" : "FAIL") +
      " " +
      label +
      " -> " +
      JSON.stringify(actual) +
      " (expected " +
      JSON.stringify(expected) +
      ")"
  );
}

/* The clock is frozen so the window of the payload is predictable. */
const NOW = 1_700_000_000_000;
const originalNow = Date.now;
Date.now = function () {
  return NOW;
};

try {
  // The range of the dashboard overrides the range stored in the widget.
  helpers.setRange("1h");
  check("the active range is the one of the dashboard", helpers.getRange(), "1h");

  const payload = helpers.buildPayload(widgetConfig("30d"));
  check(
    "a widget stored with 30d is queried over the dashboard range",
    payload.from,
    NOW - SPAN_MS["1h"]
  );
  check("the end of the window is now", payload.to, NOW);

  // The window follows the range the user picks.
  helpers.setRange("15m");
  const short = helpers.buildPayload(widgetConfig("30d"));
  check(
    "the window follows the range of the picker",
    short.from,
    NOW - SPAN_MS["15m"]
  );

  helpers.setRange("7d");
  const wide = helpers.buildPayload(widgetConfig("24h"));
  check(
    "the window follows the range of the picker",
    wide.from,
    NOW - SPAN_MS["7d"]
  );

  // Two widgets with different stored ranges are queried over the same window.
  helpers.setRange("6h");
  const first = helpers.buildPayload(widgetConfig("30d"));
  const second = helpers.buildPayload(widgetConfig("24h"));
  check(
    "the stored range of a widget does not change the window",
    [first.from, first.to],
    [second.from, second.to]
  );

  // The label of the picker is the human one.
  check("the label of a range", helpers.rangeLabel("1h"), "Last 1 hour");
  check("the label of the widest range", helpers.rangeLabel("7d"), "Last 7 days");
  check(
    "a range the picker does not offer keeps its own name",
    helpers.rangeLabel("30d"),
    "30d"
  );

  // The range travels in the URL, so the view can be shared and restored.
  browser.location.search = "?range=6h";
  check("the range of the URL", helpers.rangeFromUrl(), "6h");

  browser.location.search = "?range=30d";
  check(
    "a range the picker does not offer falls back to the default",
    helpers.rangeFromUrl(),
    "1h"
  );

  browser.location.search = "";
  check(
    "a silent URL falls back to the default",
    helpers.rangeFromUrl(),
    "1h"
  );

  browser.location.href = "http://localhost/dashboards/1?range=1h";
  check(
    "the range is written in the URL",
    helpers.rangeToUrl("24h"),
    "http://localhost/dashboards/1?range=24h"
  );
} finally {
  Date.now = originalNow;
}

if (failures) {
  console.error("\n" + failures + " failure(s)");
  process.exit(1);
}

console.log("\nall the cases passed");
