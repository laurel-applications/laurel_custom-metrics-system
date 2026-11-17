/*
 * Regression test of the filter value coercion of the front end.
 *
 * The script exercises the *shipped* source of ``app/static/app.js``: the
 * helper functions are extracted from the file and evaluated, so the test
 * cannot drift away from the code it checks.
 *
 * The defect it guards: a filter value of a textual field (the value of a
 * string metric or a tag) used to be turned into a number whenever it looked
 * like one.  The server then rejected the whole widget with
 * ``INVALID_FILTER_VALUE``, because a textual operator needs a string.
 *
 * Run it with ``node tests/js/filter_value_test.js``.
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

/* The helpers under test, taken verbatim from the shipped file. */
const helpers = new Function(
  [
    extract("valueKind"),
    extract("fieldKind"),
    extract("toNumber"),
    extract("readRow"),
    "return { valueKind: valueKind, fieldKind: fieldKind,",
    "         toNumber: toNumber, readRow: readRow };"
  ].join("\n")
)();

/* Return a fake filter row holding the given selection. */
function fakeRow(field, op, raw) {
  return {
    querySelector(selector) {
      if (selector.indexOf("filter-field") !== -1) {
        return { value: field };
      }
      if (selector.indexOf("filter-op") !== -1) {
        return { value: op };
      }
      if (selector.indexOf("filter-value") !== -1) {
        return { value: raw };
      }
      return null;
    }
  };
}

const CASES = [
  // field, operator, raw text, metric type, expected value.
  ["value", ">", "10", "counter", 10],
  ["value", ">", "10.5", "counter", 10.5],
  ["value", ">", "-3", "counter", -3],
  ["value", ">", "abc", "counter", "abc"],
  ["value", "contains", "10", "string", "10"],
  ["value", "=", "1.2", "string", "1.2"],
  ["tags.version", "=", "1.2", "counter", "1.2"],
  ["tags.version", "contains", "10", "counter", "10"],
  ["value", "between", "1, 10", "counter", [1, 10]],
  ["value", "between", "1, abc", "counter", [1, "abc"]],
  ["value", "is_true", "ignored", "bool", null],
  ["value", "is_false", "ignored", "bool", null],
  ["value", "=", "", "counter", ""]
];

let failures = 0;

CASES.forEach(function (testCase) {
  const field = testCase[0];
  const op = testCase[1];
  const raw = testCase[2];
  const metricType = testCase[3];
  const expected = testCase[4];

  const actual = helpers.readRow(fakeRow(field, op, raw), metricType).value;
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if (!ok) {
    failures++;
  }
  console.log(
    (ok ? "PASS" : "FAIL") +
      " field=" +
      field +
      " op=" +
      op +
      " raw=" +
      JSON.stringify(raw) +
      " type=" +
      metricType +
      " -> " +
      JSON.stringify(actual) +
      " (expected " +
      JSON.stringify(expected) +
      ")"
  );
});

if (failures) {
  console.error("\n" + failures + " failure(s)");
  process.exit(1);
}

console.log("\nall the cases passed");
