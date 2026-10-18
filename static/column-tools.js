"use strict";
// Compare decimal strings without converting precise results to binary floats.
(() => {
  function decimalParts(value) {
    const match = String(value).match(/^([+-]?)(\d*)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/);
    if (!match || !(match[2] || match[3])) return null;
    const digits = (match[2] + (match[3] || "")).replace(/^0+/, "");
    if (!digits) return {sign: 0, digits: "0", magnitude: 0};
    return {sign: match[1] === "-" ? -1 : 1, digits,
      magnitude: digits.length + Number(match[4] || 0) - (match[3] || "").length};
  }
  function compareDecimal(left, right) {
    const a = decimalParts(left), b = decimalParts(right);
    if (!a || !b) throw new TypeError("Expected finite decimal strings");
    if (a.sign !== b.sign) return a.sign - b.sign;
    if (!a.sign) return 0;
    if (a.magnitude !== b.magnitude) return Math.sign(a.magnitude - b.magnitude) * a.sign;
    const size = Math.max(a.digits.length, b.digits.length);
    const x = a.digits.padEnd(size, "0"), y = b.digits.padEnd(size, "0");
    return x === y ? 0 : (x < y ? -1 : 1) * a.sign;
  }
  function selectColumns(columns, query = "", sort = "original") {
    const needle = query.trim().toLowerCase();
    const items = columns.map((column, index) => ({column, index}))
      .filter(({column}) => column.name.toLowerCase().includes(needle));
    items.sort((a, b) => {
      let difference = 0;
      if (sort.startsWith("name_")) {
        difference = a.column.name.localeCompare(b.column.name, undefined, {sensitivity: "base", numeric: true});
        if (sort === "name_desc") difference *= -1;
      } else if (sort.startsWith("missing_")) {
        difference = a.column.missing_values - b.column.missing_values;
        if (sort === "missing_desc") difference *= -1;
      } else if (sort.startsWith("mean_")) {
        const x = a.column.numeric, y = b.column.numeric;
        if (!x || !y) return x ? -1 : y ? 1 : a.index - b.index;
        difference = compareDecimal(x.mean, y.mean);
        if (sort === "mean_desc") difference *= -1;
      }
      return difference || a.index - b.index;
    });
    return items.map(({column}) => column);
  }
  const api = Object.freeze({compareDecimal, selectColumns});
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else globalThis.FlowForgeColumns = api;
})();
