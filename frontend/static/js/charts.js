// @ts-check
// Every chart goes through this module (docs/design/visual-system.md#charts). It sets
// the shared Chart.js defaults once; page code never configures Chart.js from scratch.
// Chart.js is loaded as a classic script (static/vendor/chartjs) and read from window.

import { delta } from "./format.js";

/** @returns {any} */
function lib() {
  const Chart = /** @type {any} */ (window).Chart;
  if (!Chart) throw new Error("Chart.js is not loaded; add the vendor script to the page.");
  return Chart;
}

/** A design token's value, e.g. token("--swim"). Colours never come from hex in JS. */
export function token(/** @type {string} */ name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

let configured = false;
function configure() {
  if (configured) return;
  const Chart = lib();
  Chart.defaults.font.family = token("--font");
  Chart.defaults.font.size = 12;
  Chart.defaults.color = token("--ink-muted");
  Chart.defaults.borderColor = token("--hairline");
  Chart.defaults.animation = false;
  Chart.defaults.responsive = true;
  Chart.defaults.maintainAspectRatio = false; // the .chart container fixes the ratio
  Chart.defaults.plugins.legend.display = false; // lines are labelled directly
  Chart.defaults.plugins.tooltip.backgroundColor = token("--ink");
  Chart.defaults.plugins.tooltip.titleColor = token("--bg"); // inverse of the ink, in both themes
  Chart.defaults.plugins.tooltip.bodyColor = token("--bg");
  Chart.defaults.plugins.tooltip.mode = "index";
  Chart.defaults.plugins.tooltip.intersect = false;
  Chart.defaults.datasets.line.spanGaps = false; // a gap in the data is a gap in the line
  Chart.defaults.datasets.line.tension = 0;
  Chart.defaults.datasets.line.pointRadius = 2;
  configured = true;
}

/** Draws a label at the right end of each dataset that has `endLabel`. */
const endLabels = {
  id: "endLabels",
  /** @param {any} chart */
  afterDatasetsDraw(chart) {
    const { ctx } = chart;
    chart.data.datasets.forEach((/** @type {any} */ ds, /** @type {number} */ i) => {
      if (!ds.endLabel) return;
      const points = chart.getDatasetMeta(i).data.filter((/** @type {any} */ p) => !p.skip);
      const last = points[points.length - 1];
      if (!last) return;
      ctx.save();
      ctx.fillStyle = ds.borderColor;
      ctx.font = `600 12px ${token("--font")}`;
      ctx.textAlign = "right";
      ctx.textBaseline = "bottom";
      ctx.fillText(ds.endLabel, last.x, last.y - 6);
      ctx.restore();
    });
  },
};

/** Vertical rules with text labels at segment boundaries (hue is taken by the fill). */
const boundaries = {
  id: "boundaries",
  /** @param {any} chart @param {any} _args @param {{lines?: {x: number, label: string}[]}} opts */
  afterDraw(chart, _args, opts) {
    const lines = opts?.lines ?? [];
    const { ctx, chartArea, scales } = chart;
    ctx.save();
    ctx.strokeStyle = token("--hairline");
    ctx.fillStyle = token("--ink-muted");
    ctx.font = `11px ${token("--font")}`;
    ctx.textAlign = "center";
    let lastLabel = -Infinity;
    for (const line of lines) {
      const x = scales.x.getPixelForValue(line.x);
      ctx.beginPath();
      ctx.moveTo(x, chartArea.top);
      ctx.lineTo(x, chartArea.bottom);
      ctx.stroke();
      // Transitions are narrow: skip a label that would collide with the previous one.
      const width = ctx.measureText(line.label).width;
      if (x - width / 2 > lastLabel + 4) {
        ctx.fillText(line.label, x, chartArea.top - 4);
        lastLabel = x + width / 2;
      }
    }
    ctx.restore();
  },
};

/**
 * A line chart over laps or points. Series with no values are left out entirely.
 * @param {HTMLCanvasElement} canvas
 * @param {{labels: string[], series: {label: string, values: (number|null)[],
 *   colour: string, axis?: string, reverse?: boolean, format?: (v: number) => string}[]}} spec
 */
export function lineChart(canvas, spec) {
  configure();
  const series = spec.series.filter((s) => s.values.some((v) => v !== null && v !== undefined));
  /** @type {Record<string, any>} */
  const scales = { x: { grid: { display: false } } };
  series.forEach((s, i) => {
    const id = s.axis ?? `y${i}`;
    scales[id] = {
      position: i % 2 ? "right" : "left",
      reverse: !!s.reverse,
      grid: { display: i === 0 },
      title: { display: true, text: s.label },
      ticks: s.format ? { callback: (/** @type {number} */ v) => s.format?.(v) } : {},
    };
  });
  return new (lib())(canvas, {
    type: "line",
    data: {
      labels: spec.labels,
      datasets: series.map((s, i) => ({
        label: s.label,
        data: s.values,
        yAxisID: s.axis ?? `y${i}`,
        borderColor: s.colour,
        backgroundColor: s.colour,
        endLabel: series.length > 1 ? s.label : undefined,
      })),
    },
    options: {
      scales,
      plugins: {
        tooltip: {
          callbacks: {
            label: (/** @type {any} */ item) => {
              const s = series[item.datasetIndex];
              return `${s.label}: ${s.format ? s.format(item.raw) : item.raw}`;
            },
          },
        },
      },
    },
    plugins: [endLabels],
  });
}

/**
 * The gap chart (docs/features/comparison.md#the-gap-chart): the reference race flat at
 * zero, the other race as its gap, time behind increasing downward.
 * @param {HTMLCanvasElement} canvas
 * @param {import("./types.js").Comparison} cmp
 * @param {{referenceName: string, otherName: string}} names
 */
export function gapChart(canvas, cmp, names) {
  configure();
  const points = cmp.checkpoints;
  const data = points.map((p) => ({ x: p.x, y: p.gap_s }));
  const last = [...points].reverse().find((p) => p.gap_s !== null);
  const labelled = new Map(points.map((p) => [p.x.toFixed(5), p.label]));
  return new (lib())(canvas, {
    type: "line",
    data: {
      datasets: [
        {
          label: names.otherName,
          data,
          borderColor: token("--ink"),
          pointRadius: points.map((p) => (p.boundary ? 3 : 2)),
          // Teal where the compared race was faster, brick where slower. Never red/green.
          // `above`/`below` are on screen, and the axis is reversed: above is faster.
          fill: { target: "origin", above: fade(token("--faster")), below: fade(token("--slower")) },
          endLabel: `${names.otherName} ${delta(last?.gap_s)}`,
        },
        {
          label: names.referenceName,
          data: points.map((p) => ({ x: p.x, y: 0 })),
          borderColor: token("--neutral"),
          borderDash: [4, 3],
          pointRadius: 0,
          endLabel: names.referenceName,
        },
      ],
    },
    options: {
      layout: { padding: { top: 18 } },
      scales: {
        // linear, not category: uneven checkpoint spacing stays honest
        x: {
          type: "linear", min: 0, max: 1, grid: { display: false },
          afterBuildTicks: (/** @type {any} */ axis) => {
            axis.ticks = spaced(points.filter((p) => !p.boundary).map((p) => p.x), 0.045)
              .map((value) => ({ value }));
          },
          ticks: { maxRotation: 0, callback: (/** @type {number} */ v) => labelled.get(v.toFixed(5)) ?? "" },
        },
        y: {
          reverse: true, // falling away means losing time
          title: { display: true, text: `time behind ${names.referenceName}` },
          ticks: { callback: (/** @type {number} */ v) => delta(v) },
        },
      },
      plugins: {
        tooltip: {
          callbacks: {
            title: (/** @type {any[]} */ items) => points[items[0].dataIndex]?.label ?? "",
            label: (/** @type {any} */ item) =>
              item.datasetIndex === 0 ? `${names.otherName}: ${delta(item.raw.y)}` : null,
          },
        },
        boundaries: {
          lines: points.filter((p) => p.boundary && p.segment && p.segment !== "run")
            .map((p) => ({ x: p.x, label: p.label })),
        },
      },
    },
    plugins: [endLabels, boundaries],
  });
}

/**
 * Points on two linear axes: pace over time (x = day number) or pace against
 * temperature. One series, one colour. Points without a value are simply not drawn.
 * @param {HTMLCanvasElement} canvas
 * @param {{points: {x: number, y: number, label: string}[], colour: string,
 *   line?: boolean, xTitle: string, yTitle: string, reverse?: boolean,
 *   xFormat: (v: number) => string, yFormat: (v: number) => string}} spec
 */
export function xyChart(canvas, spec) {
  configure();
  return new (lib())(canvas, {
    type: "scatter",
    data: {
      datasets: [{
        label: spec.yTitle,
        data: spec.points,
        borderColor: spec.colour,
        backgroundColor: spec.colour,
        showLine: !!spec.line,
        pointRadius: 3,
        pointHoverRadius: 5,
      }],
    },
    options: {
      scales: {
        x: {
          type: "linear", grid: { display: false },
          title: { display: true, text: spec.xTitle },
          ticks: { maxRotation: 0, callback: (/** @type {number} */ v) => spec.xFormat(v) },
        },
        y: {
          reverse: !!spec.reverse,
          title: { display: true, text: spec.yTitle },
          ticks: { callback: (/** @type {number} */ v) => spec.yFormat(v) },
        },
      },
      plugins: {
        tooltip: {
          mode: "nearest",
          intersect: true,
          callbacks: {
            title: (/** @type {any[]} */ items) => items[0]?.raw?.label ?? "",
            label: (/** @type {any} */ item) =>
              `${spec.xFormat(item.raw.x)} · ${spec.yFormat(item.raw.y)}`,
          },
        },
      },
    },
  });
}

/** Keep values at least `gap` apart (on a 0..1 axis), so tick labels don't collide. */
function spaced(/** @type {number[]} */ values, /** @type {number} */ gap) {
  /** @type {number[]} */
  const kept = [];
  for (const v of values) if (!kept.length || v - kept[kept.length - 1] >= gap) kept.push(v);
  return kept;
}

/** A token colour at low opacity, for fills. */
function fade(/** @type {string} */ colour) {
  return `color-mix(in srgb, ${colour} 22%, transparent)`;
}
