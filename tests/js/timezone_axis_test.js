/*
 * Regression test of the local time and of the time axis of the charts.
 *
 * The script exercises the *shipped* source of ``app/static/app.js``: the
 * constants and the helpers are extracted from the file and evaluated, so the
 * test cannot drift away from the code it checks.
 *
 * The two defects it guards:
 *
 *   1. every timestamp the user reads was rendered in UTC, five hours ahead of
 *      the local time of Colombia;
 *   2. the axis of a chart was built from the timestamps the server sent, so
 *      a period without a measurement collapsed the axis and the silence of
 *      the metric became invisible.
 *
 * Run it with ``node tests/js/timezone_axis_test.js``.
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
    extractVar("TIMEZONE_OFFSET_MS"),
    extract("toEpochMs"),
    extract("formatTime"),
    extract("buildTimeAxis"),
    extract("tooltipTitle"),
    "return {",
    "  formatTime: formatTime,",
    "  buildTimeAxis: buildTimeAxis,",
    "  tooltipTitle: tooltipTitle",
    "};"
  ].join("\n")
)();

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

/* The epoch of 2023-11-14T22:13:20Z, the timestamp the other tests use. */
const EPOCH_MS = 1_700_000_000_000;

/* A window aligned to the minute, so the buckets of the axis are exact. */
const ALIGNED_MS = 1_700_000_040_000;

/* One minute, in milliseconds. */
const MINUTE_MS = 60 * 1000;

// The timestamps the user reads are the local time of Colombia, five hours
// behind UTC.
check(
  "a timestamp is rendered in the local time",
  helpers.formatTime(EPOCH_MS),
  "2023-11-14 17:13:20 GMT-5"
);

check(
  "a timestamp of another day keeps its local date",
  helpers.formatTime(EPOCH_MS + 12 * 60 * 60 * 1000),
  "2023-11-15 05:13:20 GMT-5"
);

check(
  "a broken timestamp is shown as it is",
  helpers.formatTime("not a timestamp"),
  "not a timestamp"
);

// Chart.js may hand the label over as a string: it is still a timestamp.
check(
  "a timestamp as a numeric string is rendered too",
  helpers.formatTime(String(EPOCH_MS)),
  "2023-11-14 17:13:20 GMT-5"
);

// The tooltip of a point shows the local time, not the raw epoch number.
check(
  "the tooltip title is the local time of the point",
  helpers.tooltipTitle([{ label: EPOCH_MS, formattedValue: "22.0" }]),
  "2023-11-14 17:13:20 GMT-5"
);
check(
  "the tooltip title accepts the string label of Chart.js",
  helpers.tooltipTitle([{ label: String(EPOCH_MS), formattedValue: "22.0" }]),
  "2023-11-14 17:13:20 GMT-5"
);
check("a tooltip without a point has no title", helpers.tooltipTitle([]), "");

// The axis holds one entry per bucket of the window, the empty ones
// included: the silence of the metric stays visible.
const axis = helpers.buildTimeAxis(
  ALIGNED_MS,
  ALIGNED_MS + 4 * MINUTE_MS,
  60
);
check("the axis holds every bucket of the window", axis.length, 5);
check("the axis starts on the first bucket", axis[0], ALIGNED_MS);
check("the axis ends on the last bucket", axis[4], ALIGNED_MS + 4 * MINUTE_MS);
check(
  "the buckets of the axis are one minute apart",
  axis[1] - axis[0],
  MINUTE_MS
);

// A window whose start is not on a bucket boundary still starts on the
// bucket the server would use: the buckets are aligned to the epoch.
const unaligned = helpers.buildTimeAxis(EPOCH_MS, EPOCH_MS + 5 * MINUTE_MS, 60);
check("the axis of an unaligned window holds every bucket", unaligned.length, 6);
check(
  "the axis of an unaligned window starts on the epoch aligned bucket",
  unaligned[0] % MINUTE_MS,
  0
);

// A pathological window is refused: the axis is not built and the chart
// falls back to the timestamps the server sent.
check(
  "a window of more than five thousand buckets is refused",
  helpers.buildTimeAxis(0, 300000000, 60),
  null
);
check(
  "a window of exactly five thousand buckets is built",
  helpers.buildTimeAxis(0, 299940000, 60).length,
  5000
);
check(
  "a bucket of zero seconds is refused",
  helpers.buildTimeAxis(0, 1000, 0),
  null
);
check(
  "a negative bucket is refused",
  helpers.buildTimeAxis(0, 1000, -60),
  null
);

if (failures) {
  console.error("\n" + failures + " failure(s)");
  process.exit(1);
}

console.log("\nall the cases passed");
