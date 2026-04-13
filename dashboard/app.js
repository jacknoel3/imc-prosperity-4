"use strict";

const BUILTIN_DATASETS = [
  {
    key: "round0-day--1",
    label: "Round 0 / Day -1",
    pricePath: "../data/round0/prices_round_0_day_-1.csv",
    tradePath: "../data/round0/trades_round_0_day_-1.csv",
  },
  {
    key: "round0-day--2",
    label: "Round 0 / Day -2",
    pricePath: "../data/round0/prices_round_0_day_-2.csv",
    tradePath: "../data/round0/trades_round_0_day_-2.csv",
  },
];

const BUILTIN_INDICATORS = [
  { key: "midPrice", label: "Mid Price" },
  { key: "wallMid", label: "Wall Mid" },
  { key: "bestBid", label: "Best Bid" },
  { key: "bestAsk", label: "Best Ask" },
  { key: "wallBid", label: "Wall Bid" },
  { key: "wallAsk", label: "Wall Ask" },
];

const COLORS = {
  bid: "rgba(32, 92, 201, 0.58)",
  ask: "rgba(194, 59, 59, 0.58)",
  tradeBuy: "rgba(215, 116, 24, 0.85)",
  tradeSell: "rgba(16, 129, 118, 0.85)",
  tradeOwn: "rgba(25, 29, 36, 0.92)",
  strategyBuy: "rgba(229, 147, 0, 0.98)",
  strategySell: "rgba(192, 72, 72, 0.98)",
  strategyUnknown: "rgba(74, 84, 96, 0.98)",
  grid: "rgba(47, 62, 82, 0.12)",
  axis: "rgba(41, 55, 71, 0.75)",
  midPrice: "rgba(47, 62, 82, 0.85)",
  wallMid: "rgba(204, 114, 44, 0.85)",
  bestBid: "rgba(32, 92, 201, 0.9)",
  bestAsk: "rgba(194, 59, 59, 0.9)",
  wallBid: "rgba(87, 126, 214, 0.95)",
  wallAsk: "rgba(212, 99, 99, 0.95)",
  pnl: "rgba(12, 92, 123, 1)",
  position: "rgba(194, 59, 59, 1)",
};

const CUSTOM_LINE_PALETTE = [
  "rgba(79, 91, 176, 0.92)",
  "rgba(196, 103, 51, 0.92)",
  "rgba(30, 127, 99, 0.92)",
  "rgba(148, 86, 179, 0.92)",
  "rgba(191, 82, 126, 0.92)",
  "rgba(118, 104, 57, 0.92)",
];

const state = {
  datasets: new Map(),
  activeDatasetKey: BUILTIN_DATASETS[0].key,
  selectedProduct: "",
  normalization: "none",
  downsample: 1,
  showBids: true,
  showAsks: true,
  showMarketTrades: true,
  showOwnTrades: true,
  showStrategyTrades: true,
  visibleLevels: { 1: true, 2: true, 3: true },
  visibleIndicators: new Set(["midPrice", "wallMid"]),
  hoveredTimestamp: null,
  strategyOverlay: null,
};

const els = {};

document.addEventListener("DOMContentLoaded", () => {
  bindElements();
  bindEvents();
  populateDatasetSelect();
  loadDataset(state.activeDatasetKey);
});

function bindElements() {
  els.datasetSelect = document.getElementById("dataset-select");
  els.productSelect = document.getElementById("product-select");
  els.normalizationSelect = document.getElementById("normalization-select");
  els.downsampleSelect = document.getElementById("downsample-select");
  els.timeMinInput = document.getElementById("time-min-input");
  els.timeMaxInput = document.getElementById("time-max-input");
  els.tradeMinInput = document.getElementById("trade-min-input");
  els.tradeMaxInput = document.getElementById("trade-max-input");
  els.resetRangeButton = document.getElementById("reset-range-button");
  els.showBidsToggle = document.getElementById("show-bids-toggle");
  els.showAsksToggle = document.getElementById("show-asks-toggle");
  els.showMarketTradesToggle = document.getElementById("show-market-trades-toggle");
  els.showOwnTradesToggle = document.getElementById("show-own-trades-toggle");
  els.showStrategyTradesToggle = document.getElementById("show-strategy-trades-toggle");
  els.level1Toggle = document.getElementById("level-1-toggle");
  els.level2Toggle = document.getElementById("level-2-toggle");
  els.level3Toggle = document.getElementById("level-3-toggle");
  els.indicatorToggles = document.getElementById("indicator-toggles");
  els.ownTraderIdsInput = document.getElementById("own-trader-ids-input");
  els.datasetSummary = document.getElementById("dataset-summary");
  els.chartSummary = document.getElementById("chart-summary");
  els.statusBadge = document.getElementById("status-badge");
  els.mainChartLegend = document.getElementById("main-chart-legend");
  els.legendNote = document.getElementById("legend-note");
  els.snapshotCard = document.getElementById("snapshot-card");
  els.statsCard = document.getElementById("stats-card");
  els.logCard = document.getElementById("log-card");
  els.pnlNote = document.getElementById("pnl-note");
  els.positionNote = document.getElementById("position-note");
  els.mainChartWrapper = document.getElementById("main-chart-wrapper");
  els.mainChart = document.getElementById("main-chart");
  els.pnlChart = document.getElementById("pnl-chart");
  els.positionChart = document.getElementById("position-chart");
  els.mainTooltip = document.getElementById("main-tooltip");
  els.uploadPriceInput = document.getElementById("upload-price-input");
  els.uploadTradeInput = document.getElementById("upload-trade-input");
  els.uploadStrategyTradesInput = document.getElementById("upload-strategy-trades-input");
  els.uploadIndicatorInput = document.getElementById("upload-indicator-input");
  els.uploadLogInput = document.getElementById("upload-log-input");
  els.loadUploadedButton = document.getElementById("load-uploaded-button");
  els.loadStrategyOverlayButton = document.getElementById("load-strategy-overlay-button");
  els.clearStrategyOverlayButton = document.getElementById("clear-strategy-overlay-button");
}

function bindEvents() {
  els.datasetSelect.addEventListener("change", async (event) => {
    state.activeDatasetKey = event.target.value;
    state.hoveredTimestamp = null;
    await loadDataset(state.activeDatasetKey);
  });

  els.productSelect.addEventListener("change", (event) => {
    state.selectedProduct = event.target.value;
    state.hoveredTimestamp = null;
    initializeTimeRange();
    renderIndicatorToggles();
    renderAll();
  });

  els.normalizationSelect.addEventListener("change", (event) => {
    state.normalization = event.target.value;
    renderAll();
  });

  els.downsampleSelect.addEventListener("change", (event) => {
    state.downsample = clampNumber(toNumber(event.target.value), 1, 100) || 1;
    renderAll();
  });

  [els.timeMinInput, els.timeMaxInput, els.tradeMinInput, els.tradeMaxInput].forEach(
    (input) => input.addEventListener("change", () => renderAll()),
  );

  els.resetRangeButton.addEventListener("click", () => {
    initializeTimeRange();
    renderAll();
  });

  els.showBidsToggle.addEventListener("change", (event) => {
    state.showBids = event.target.checked;
    renderAll();
  });

  els.showAsksToggle.addEventListener("change", (event) => {
    state.showAsks = event.target.checked;
    renderAll();
  });

  els.showMarketTradesToggle.addEventListener("change", (event) => {
    state.showMarketTrades = event.target.checked;
    renderAll();
  });

  els.showOwnTradesToggle.addEventListener("change", (event) => {
    state.showOwnTrades = event.target.checked;
    renderAll();
  });

  els.showStrategyTradesToggle.addEventListener("change", (event) => {
    state.showStrategyTrades = event.target.checked;
    renderAll();
  });

  els.level1Toggle.addEventListener("change", (event) => {
    state.visibleLevels[1] = event.target.checked;
    renderAll();
  });

  els.level2Toggle.addEventListener("change", (event) => {
    state.visibleLevels[2] = event.target.checked;
    renderAll();
  });

  els.level3Toggle.addEventListener("change", (event) => {
    state.visibleLevels[3] = event.target.checked;
    renderAll();
  });

  els.ownTraderIdsInput.addEventListener("change", () => renderAll());
  els.ownTraderIdsInput.addEventListener("keyup", () => renderAll());

  els.loadUploadedButton.addEventListener("click", () => loadUploadedDataset());
  els.loadStrategyOverlayButton.addEventListener("click", () => loadStrategyOverlay());
  els.clearStrategyOverlayButton.addEventListener("click", () => clearStrategyOverlay());

  els.mainChartWrapper.addEventListener("mousemove", handleChartHover);
  els.mainChartWrapper.addEventListener("mouseleave", () => {
    state.hoveredTimestamp = null;
    els.mainTooltip.classList.add("hidden");
    renderAll();
  });

  window.addEventListener("resize", () => renderAll());
}

async function loadDataset(key) {
  try {
    setStatus("Loading dataset...");
    const dataset = await ensureDatasetLoaded(key);
    if (!dataset) {
      throw new Error("Dataset definition not found.");
    }

    if (!dataset.products.size) {
      throw new Error("Dataset contains no products.");
    }

    state.selectedProduct = dataset.products.has(state.selectedProduct)
      ? state.selectedProduct
      : [...dataset.products.keys()][0];

    populateProductSelect(dataset);
    initializeTimeRange();
    renderIndicatorToggles();
    renderAll();
    setStatus(`Loaded ${dataset.label}`);
  } catch (error) {
    console.error(error);
    setStatus(`Failed to load dataset: ${error.message}`);
  }
}

async function ensureDatasetLoaded(key) {
  if (state.datasets.has(key)) {
    return state.datasets.get(key);
  }

  if (key === "uploaded") {
    return state.datasets.get(key) || null;
  }

  const definition = BUILTIN_DATASETS.find((item) => item.key === key);
  if (!definition) {
    return null;
  }

  const [priceText, tradeText] = await Promise.all([
    fetchText(definition.pricePath),
    fetchText(definition.tradePath),
  ]);

  const dataset = buildDataset({
    key: definition.key,
    label: definition.label,
    priceRowsRaw: parseDelimitedText(priceText),
    tradeRowsRaw: parseDelimitedText(tradeText),
    indicatorRowsRaw: [],
    logRowsRaw: [],
  });

  state.datasets.set(definition.key, dataset);
  return dataset;
}

async function loadUploadedDataset() {
  const priceFile = els.uploadPriceInput.files[0];
  if (!priceFile) {
    setStatus("Choose a price CSV before loading uploaded files.");
    return;
  }

  try {
    setStatus("Parsing uploaded files...");

    const [priceText, tradeText, indicatorText, logText] = await Promise.all([
      priceFile.text(),
      readOptionalFile(els.uploadTradeInput.files[0]),
      readOptionalFile(els.uploadIndicatorInput.files[0]),
      readOptionalFile(els.uploadLogInput.files[0]),
    ]);

    const dataset = buildDataset({
      key: "uploaded",
      label: `Uploaded / ${priceFile.name}`,
      priceRowsRaw: parseDelimitedText(priceText),
      tradeRowsRaw: tradeText ? parseDelimitedText(tradeText) : [],
      indicatorRowsRaw: indicatorText ? parseIndicatorText(indicatorText) : [],
      logRowsRaw: logText ? parseLogText(logText) : [],
    });

    state.datasets.set("uploaded", dataset);
    populateDatasetSelect();
    els.datasetSelect.value = "uploaded";
    state.activeDatasetKey = "uploaded";
    state.selectedProduct = [...dataset.products.keys()][0] || "";
    populateProductSelect(dataset);
    initializeTimeRange();
    renderIndicatorToggles();
    renderAll();
    setStatus(`Loaded uploaded dataset: ${priceFile.name}`);
  } catch (error) {
    console.error(error);
    setStatus(`Could not parse uploaded files: ${error.message}`);
  }
}

async function loadStrategyOverlay() {
  const strategyFile = els.uploadStrategyTradesInput.files[0];
  if (!strategyFile) {
    setStatus("Choose a backtest trades CSV before loading the overlay.");
    return;
  }

  try {
    setStatus("Parsing backtest overlay...");
    const strategyText = await strategyFile.text();
    const overlay = buildStrategyOverlay({
      label: strategyFile.name,
      strategyRowsRaw: parseDelimitedText(strategyText),
    });

    if (!overlay.products.size) {
      throw new Error("No usable strategy trades were found.");
    }

    state.strategyOverlay = overlay;
    const overlayProducts = [...overlay.products.keys()];
    if (
      overlayProducts.length === 1 &&
      overlayProducts[0] !== state.selectedProduct &&
      getActiveDataset()?.products.has(overlayProducts[0])
    ) {
      state.selectedProduct = overlayProducts[0];
      if (els.productSelect) {
        els.productSelect.value = overlayProducts[0];
      }
      initializeTimeRange();
      renderIndicatorToggles();
    }

    renderAll();
    if (overlayProducts.length === 1) {
      setStatus(`Loaded backtest overlay: ${strategyFile.name} for ${overlayProducts[0]}.`);
    } else {
      setStatus(`Loaded backtest overlay: ${strategyFile.name}`);
    }
  } catch (error) {
    console.error(error);
    setStatus(`Could not parse backtest overlay: ${error.message}`);
  }
}

function clearStrategyOverlay() {
  state.strategyOverlay = null;
  if (els.uploadStrategyTradesInput) {
    els.uploadStrategyTradesInput.value = "";
  }
  renderAll();
  setStatus("Cleared backtest overlay.");
}

function populateDatasetSelect() {
  const options = [...BUILTIN_DATASETS];
  if (state.datasets.has("uploaded")) {
    const uploaded = state.datasets.get("uploaded");
    options.push({ key: "uploaded", label: uploaded.label });
  }

  els.datasetSelect.innerHTML = options
    .map((option) => `<option value="${escapeHtml(option.key)}">${escapeHtml(option.label)}</option>`)
    .join("");

  els.datasetSelect.value = state.activeDatasetKey;
}

function populateProductSelect(dataset) {
  const products = [...dataset.products.keys()];
  els.productSelect.innerHTML = products
    .map((product) => `<option value="${escapeHtml(product)}">${escapeHtml(product)}</option>`)
    .join("");

  els.productSelect.value = state.selectedProduct;
}

function initializeTimeRange() {
  const productData = getActiveProductData();
  if (!productData || !productData.rows.length) {
    return;
  }

  const firstTimestamp = productData.rows[0].timestamp;
  const lastTimestamp = productData.rows[productData.rows.length - 1].timestamp;
  els.timeMinInput.value = String(firstTimestamp);
  els.timeMaxInput.value = String(lastTimestamp);
}

function renderIndicatorToggles() {
  const productData = getActiveProductData();
  if (!productData) {
    els.indicatorToggles.innerHTML = "";
    return;
  }

  const customKeys = [...productData.indicatorsByName.keys()].map((name) => ({
    key: `custom:${name}`,
    label: name,
  }));

  const items = [...BUILTIN_INDICATORS, ...customKeys];
  els.indicatorToggles.innerHTML = items
    .map((item) => {
      const checked = state.visibleIndicators.has(item.key) ? "checked" : "";
      return `
        <label class="toggle-pill">
          <input type="checkbox" data-indicator-key="${escapeHtml(item.key)}" ${checked} />
          <span>${escapeHtml(item.label)}</span>
        </label>
      `;
    })
    .join("");

  els.indicatorToggles.querySelectorAll("input[type='checkbox']").forEach((input) => {
    input.addEventListener("change", (event) => {
      const key = event.target.getAttribute("data-indicator-key");
      if (!key) {
        return;
      }

      if (event.target.checked) {
        state.visibleIndicators.add(key);
      } else {
        state.visibleIndicators.delete(key);
      }
      renderAll();
    });
  });
}

function renderAll() {
  const dataset = getActiveDataset();
  const productData = getActiveProductData();

  if (!dataset || !productData) {
    return;
  }

  const view = buildView(dataset, productData);
  els.datasetSummary.textContent = `${formatInteger(dataset.priceRows.length)} book rows, ${formatInteger(
    dataset.tradeRows.length,
  )} trades, ${dataset.products.size} products`;
  const summaryParts = [
    `${formatInteger(view.filteredRows.length)} snapshots in view`,
    `${formatInteger(view.visibleTrades.length)} market trades shown`,
  ];
  if (state.strategyOverlay) {
    summaryParts.push(`${formatInteger(view.visibleStrategyTrades.length)} backtest trades shown`);
  }
  els.chartSummary.textContent = summaryParts.join(", ");

  updateStatsCard(view);
  updateSnapshotAndLogCards(view, getHoveredRow(view) || view.filteredRows[0] || null);
  renderLegend(view);
  renderMainChart(view);
  renderMiniCharts(view);
}

function buildView(dataset, productData) {
  const fullMin = productData.rows[0]?.timestamp ?? 0;
  const fullMax = productData.rows[productData.rows.length - 1]?.timestamp ?? 0;
  let rangeMin = toNumber(els.timeMinInput.value);
  let rangeMax = toNumber(els.timeMaxInput.value);
  rangeMin = rangeMin == null ? fullMin : clampNumber(rangeMin, fullMin, fullMax);
  rangeMax = rangeMax == null ? fullMax : clampNumber(rangeMax, fullMin, fullMax);

  if (rangeMin > rangeMax) {
    [rangeMin, rangeMax] = [rangeMax, rangeMin];
  }

  const selectedLevels = Object.entries(state.visibleLevels)
    .filter(([, visible]) => visible)
    .map(([level]) => Number(level));

  const rawRows = productData.rows.filter(
    (row) => row.timestamp >= rangeMin && row.timestamp <= rangeMax,
  );
  const filteredRows = rawRows.filter((_, index) => index % state.downsample === 0);
  const tradeMin = Math.max(0, toNumber(els.tradeMinInput.value) ?? 0);
  const tradeMax = Math.max(tradeMin, toNumber(els.tradeMaxInput.value) ?? 999999);

  const ownIds = getOwnTraderIds();
  const visibleTrades = productData.trades
    .filter((trade) => trade.timestamp >= rangeMin && trade.timestamp <= rangeMax)
    .map((trade) => decorateTrade(trade, productData.rowByTimestamp.get(trade.timestamp), ownIds))
    .filter((trade) => trade.quantity >= tradeMin && trade.quantity <= tradeMax)
    .filter((trade) => {
      if (trade.isOwn) {
        return state.showOwnTrades;
      }
      return state.showMarketTrades;
    });

  const strategyProduct = getStrategyProductData();
  const visibleStrategyTrades = strategyProduct && state.showStrategyTrades
    ? strategyProduct.trades
        .filter((trade) => trade.timestamp >= rangeMin && trade.timestamp <= rangeMax)
        .filter((trade) => trade.quantity >= tradeMin && trade.quantity <= tradeMax)
    : [];

  const yValues = [];
  const indicatorSeries = [];

  for (const indicatorKey of state.visibleIndicators) {
    const series = buildIndicatorSeries(productData, filteredRows, indicatorKey);
    if (series.length) {
      indicatorSeries.push({
        key: indicatorKey,
        label: indicatorKey.startsWith("custom:")
          ? indicatorKey.slice(7)
          : BUILTIN_INDICATORS.find((item) => item.key === indicatorKey)?.label || indicatorKey,
        values: series,
      });
      for (const point of series) {
        const row = productData.rowByTimestamp.get(point.timestamp);
        if (row) {
          yValues.push(normalizePrice(row, point.value));
        }
      }
    }
  }

  for (const row of filteredRows) {
    if (state.showBids) {
      row.bids
        .filter((level) => selectedLevels.includes(level.level))
        .forEach((level) => yValues.push(normalizePrice(row, level.price)));
    }
    if (state.showAsks) {
      row.asks
        .filter((level) => selectedLevels.includes(level.level))
        .forEach((level) => yValues.push(normalizePrice(row, level.price)));
    }
  }

  visibleTrades.forEach((trade) => {
    const row = productData.rowByTimestamp.get(trade.timestamp);
    if (row) {
      yValues.push(normalizePrice(row, trade.price));
    }
  });

  visibleStrategyTrades.forEach((trade) => {
    const row = productData.rowByTimestamp.get(trade.timestamp);
    if (row) {
      yValues.push(normalizePrice(row, trade.price));
    }
  });

  if (!yValues.length) {
    filteredRows.forEach((row) => yValues.push(normalizePrice(row, row.midPrice)));
  }

  let yMin = Math.min(...yValues);
  let yMax = Math.max(...yValues);
  if (yMin === yMax) {
    yMin -= 1;
    yMax += 1;
  }

  const padding = Math.max((yMax - yMin) * 0.08, 1);
  yMin -= padding;
  yMax += padding;

  return {
    dataset,
    productData,
    strategyProduct,
    fullMin,
    fullMax,
    rangeMin,
    rangeMax,
    filteredRows,
    visibleTrades,
    visibleStrategyTrades,
    selectedLevels,
    indicatorSeries,
    yMin,
    yMax,
  };
}

function renderMainChart(view = buildView(getActiveDataset(), getActiveProductData())) {
  const prepared = prepareCanvas(els.mainChart);
  const ctx = prepared.ctx;
  const width = prepared.width;
  const height = prepared.height;
  const plot = { left: 66, top: 20, right: width - 24, bottom: height - 38 };

  ctx.clearRect(0, 0, width, height);
  drawGridAndAxes(ctx, plot, view.rangeMin, view.rangeMax, view.yMin, view.yMax);

  const xScale = (timestamp) => scale(timestamp, view.rangeMin, view.rangeMax, plot.left, plot.right);
  const yScale = (price) => scale(price, view.yMin, view.yMax, plot.bottom, plot.top);

  if (state.showBids) {
    drawBookPoints(ctx, view.filteredRows, plot, xScale, yScale, "bids", view.selectedLevels, COLORS.bid);
  }

  if (state.showAsks) {
    drawBookPoints(ctx, view.filteredRows, plot, xScale, yScale, "asks", view.selectedLevels, COLORS.ask);
  }

  drawIndicatorLines(ctx, view.indicatorSeries, xScale, yScale, view.productData);
  drawTrades(ctx, view.visibleTrades, xScale, yScale, view.productData);
  drawStrategyTrades(ctx, view.visibleStrategyTrades, xScale, yScale, view.productData);

  const hoveredRow = getHoveredRow(view);
  if (hoveredRow) {
    const x = xScale(hoveredRow.timestamp);
    ctx.save();
    ctx.strokeStyle = "rgba(25, 29, 36, 0.48)";
    ctx.setLineDash([6, 6]);
    ctx.beginPath();
    ctx.moveTo(x, plot.top);
    ctx.lineTo(x, plot.bottom);
    ctx.stroke();
    ctx.restore();
  }
}

function renderMiniCharts(view) {
  const pnlSeries = buildPnlSeries(view.productData, view.strategyProduct);
  const positionSeries = buildPositionSeries(view.productData, view.strategyProduct);

  renderSeriesChart(
    els.pnlChart,
    pnlSeries,
    COLORS.pnl,
    "No backtest PnL available yet. Load a backtest overlay or own trades to populate this panel.",
  );

  renderSeriesChart(
    els.positionChart,
    positionSeries,
    COLORS.position,
    "No strategy position data yet. Load a backtest overlay or set your trader ID in the trade export.",
  );

  if (pnlSeries.length) {
    els.pnlNote.textContent = `${formatInteger(pnlSeries.length)} points`;
  } else {
    els.pnlNote.textContent = "Waiting for strategy data";
  }

  if (positionSeries.length) {
    els.positionNote.textContent = `${formatInteger(positionSeries.length)} points`;
  } else {
    els.positionNote.textContent = "Waiting for own trades";
  }
}

function updateStatsCard(view) {
  const spreads = view.filteredRows
    .map((row) => row.spread)
    .filter((value) => Number.isFinite(value));

  const mids = view.filteredRows
    .map((row) => row.midPrice)
    .filter((value) => Number.isFinite(value));

  const avgSpread = spreads.length ? spreads.reduce((sum, value) => sum + value, 0) / spreads.length : null;
  const minMid = mids.length ? Math.min(...mids) : null;
  const maxMid = mids.length ? Math.max(...mids) : null;

  const lines = [
    `dataset: ${view.dataset.label}`,
    `product: ${state.selectedProduct}`,
    `range: ${formatInteger(view.rangeMin)} -> ${formatInteger(view.rangeMax)}`,
    `rows in view: ${formatInteger(view.filteredRows.length)}`,
    `trades shown: ${formatInteger(view.visibleTrades.length)}`,
    `backtest trades shown: ${formatInteger(view.visibleStrategyTrades.length)}`,
    `avg spread: ${formatMaybe(avgSpread)}`,
    `mid range: ${formatMaybe(minMid)} -> ${formatMaybe(maxMid)}`,
    `normalization: ${state.normalization}`,
    `downsample: ${state.downsample}x`,
  ];

  if (state.strategyOverlay) {
    lines.push(`backtest overlay: ${state.strategyOverlay.label}`);
  }

  els.statsCard.textContent = lines.join("\n");
}

function updateSnapshotAndLogCards(view, row) {
  if (!row) {
    els.snapshotCard.textContent = "No row selected.";
    els.logCard.textContent = "No logs loaded.";
    return;
  }

  const trades = view.visibleTrades
    .filter((trade) => trade.timestamp === row.timestamp)
    .slice(0, 8);
  const strategyTrades = view.visibleStrategyTrades
    .filter((trade) => trade.timestamp === row.timestamp)
    .slice(0, 8);

  const snapshotLines = [
    `timestamp: ${formatInteger(row.timestamp)}`,
    `mid: ${formatMaybe(row.midPrice)}`,
    `wall mid: ${formatMaybe(row.wallMid)}`,
    `spread: ${formatMaybe(row.spread)}`,
    "",
    "bids:",
    ...formatBookSide(row.bids),
    "",
    "asks:",
    ...formatBookSide(row.asks),
  ];

  if (trades.length) {
    snapshotLines.push("", "trades:");
    trades.forEach((trade) => {
      const label = trade.isOwn ? "own" : trade.side;
      snapshotLines.push(`- ${label} ${trade.quantity} @ ${formatMaybe(trade.price)}`);
    });
  }

  if (strategyTrades.length) {
    snapshotLines.push("", "backtest fills:");
    strategyTrades.forEach((trade) => {
      const label = trade.side || "unknown";
      let suffix = "";
      if (Number.isFinite(trade.position)) {
        suffix += ` pos=${formatMaybe(trade.position)}`;
      }
      if (Number.isFinite(trade.pnl)) {
        suffix += ` pnl=${formatMaybe(trade.pnl)}`;
      }
      snapshotLines.push(`- ${label} ${trade.quantity} @ ${formatMaybe(trade.price)}${suffix}`);
    });
  }

  els.snapshotCard.textContent = snapshotLines.join("\n");

  const logs = view.productData.logsByTimestamp.get(row.timestamp) || [];
  if (!logs.length) {
    els.logCard.textContent =
      "No logs at this timestamp.\n\nLoad a log file with timestamp, product, message to sync your notes with the chart.";
    return;
  }

  els.logCard.textContent = logs.map((log) => `- ${log.message}`).join("\n");
}

function handleChartHover(event) {
  const view = buildView(getActiveDataset(), getActiveProductData());
  if (!view.filteredRows.length) {
    return;
  }

  const rect = els.mainChart.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const width = rect.width;
  const plotLeft = 66;
  const plotRight = width - 24;

  if (x < plotLeft || x > plotRight) {
    state.hoveredTimestamp = null;
    els.mainTooltip.classList.add("hidden");
    updateSnapshotAndLogCards(view, view.filteredRows[0] || null);
    renderMainChart(view);
    return;
  }

  const timestamp = scale(x, plotLeft, plotRight, view.rangeMin, view.rangeMax);
  const row = findNearestRow(view.filteredRows, timestamp);
  if (!row) {
    return;
  }

  state.hoveredTimestamp = row.timestamp;
  updateSnapshotAndLogCards(view, row);
  renderMainChart(view);
  showTooltip(event, view, row);
}

function showTooltip(event, view, row) {
  const trades = view.visibleTrades.filter((trade) => trade.timestamp === row.timestamp);
  const strategyTrades = view.visibleStrategyTrades.filter((trade) => trade.timestamp === row.timestamp);
  const lines = [
    `t=${formatInteger(row.timestamp)}`,
    `mid=${formatMaybe(row.midPrice)} wall=${formatMaybe(row.wallMid)}`,
    `spread=${formatMaybe(row.spread)}`,
  ];

  if (trades.length) {
    lines.push(`trades=${trades.length}`);
    trades.slice(0, 5).forEach((trade) => {
      const marker = trade.isOwn ? "own" : trade.side;
      lines.push(`${marker}: ${trade.quantity} @ ${formatMaybe(trade.price)}`);
    });
  }

  if (strategyTrades.length) {
    lines.push(`backtest=${strategyTrades.length}`);
    strategyTrades.slice(0, 5).forEach((trade) => {
      lines.push(`bt ${trade.side}: ${trade.quantity} @ ${formatMaybe(trade.price)}`);
    });
  }

  els.mainTooltip.textContent = lines.join("\n");
  els.mainTooltip.classList.remove("hidden");

  const wrapperRect = els.mainChartWrapper.getBoundingClientRect();
  const maxLeft = Math.max(10, wrapperRect.width - 250);
  const maxTop = Math.max(10, wrapperRect.height - 120);
  const left = clampNumber(event.clientX - wrapperRect.left + 10, 10, maxLeft);
  const top = clampNumber(event.clientY - wrapperRect.top + 10, 10, maxTop);
  els.mainTooltip.style.left = `${left}px`;
  els.mainTooltip.style.top = `${top}px`;
}

function drawGridAndAxes(ctx, plot, xMin, xMax, yMin, yMax) {
  ctx.save();
  ctx.strokeStyle = COLORS.grid;
  ctx.lineWidth = 1;
  ctx.fillStyle = COLORS.axis;
  ctx.font = "12px Menlo, Consolas, monospace";

  const yTicks = 6;
  for (let index = 0; index <= yTicks; index += 1) {
    const ratio = index / yTicks;
    const y = plot.top + ratio * (plot.bottom - plot.top);
    const value = yMax - ratio * (yMax - yMin);
    ctx.beginPath();
    ctx.moveTo(plot.left, y);
    ctx.lineTo(plot.right, y);
    ctx.stroke();
    ctx.fillText(formatAxisValue(value), 10, y + 4);
  }

  const xTicks = 6;
  for (let index = 0; index <= xTicks; index += 1) {
    const ratio = index / xTicks;
    const x = plot.left + ratio * (plot.right - plot.left);
    const value = xMin + ratio * (xMax - xMin);
    ctx.beginPath();
    ctx.moveTo(x, plot.top);
    ctx.lineTo(x, plot.bottom);
    ctx.stroke();
    ctx.fillText(formatAxisInteger(value), x - 18, plot.bottom + 22);
  }

  ctx.restore();
}

function drawBookPoints(ctx, rows, plot, xScale, yScale, sideKey, selectedLevels, color) {
  ctx.save();
  ctx.fillStyle = color;

  for (const row of rows) {
    const x = xScale(row.timestamp);
    const levels = row[sideKey].filter((level) => selectedLevels.includes(level.level));
    for (const level of levels) {
      const y = yScale(normalizePrice(row, level.price));
      const radius = 1.5 + Math.sqrt(Math.max(level.volume, 1)) * 0.7;
      ctx.beginPath();
      ctx.arc(x, y, radius, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  ctx.restore();
}

function drawIndicatorLines(ctx, indicatorSeries, xScale, yScale, productData) {
  ctx.save();
  ctx.lineWidth = 1.6;

  for (const indicator of indicatorSeries) {
    ctx.strokeStyle = getIndicatorColor(indicator.key);
    ctx.beginPath();
    let started = false;
    for (const point of indicator.values) {
      const row = productData.rowByTimestamp.get(point.timestamp);
      if (!row) {
        continue;
      }

      const x = xScale(point.timestamp);
      const y = yScale(normalizePrice(row, point.value));
      if (!started) {
        ctx.moveTo(x, y);
        started = true;
      } else {
        ctx.lineTo(x, y);
      }
    }
    ctx.stroke();
  }

  ctx.restore();
}

function renderLegend(view) {
  const items = [];
  items.push(
    buildLegendItem(drawLegendDot(COLORS.bid), "Bid quotes", !state.showBids || !view.selectedLevels.length),
  );
  items.push(
    buildLegendItem(drawLegendDot(COLORS.ask), "Ask quotes", !state.showAsks || !view.selectedLevels.length),
  );
  items.push(
    buildLegendItem(drawLegendTriangle(COLORS.tradeBuy, true), "Market buy trade", !state.showMarketTrades),
  );
  items.push(
    buildLegendItem(drawLegendTriangle(COLORS.tradeSell, false), "Market sell trade", !state.showMarketTrades),
  );

  if (view.visibleTrades.some((trade) => !trade.isOwn && trade.side === "unknown")) {
    items.push(
      buildLegendItem(
        drawLegendSquare(COLORS.tradeBuy),
        "Unclassified market trade",
        !state.showMarketTrades,
      ),
    );
  }

  items.push(
    buildLegendItem(drawLegendCross(COLORS.tradeOwn), "Own trade", !state.showOwnTrades),
  );

  items.push(
    buildLegendItem(
      drawLegendDiamond(COLORS.strategyBuy),
      "Backtest buy fill",
      !state.showStrategyTrades || !view.visibleStrategyTrades.some((trade) => trade.side === "buy"),
    ),
  );
  items.push(
    buildLegendItem(
      drawLegendDiamond(COLORS.strategySell),
      "Backtest sell fill",
      !state.showStrategyTrades || !view.visibleStrategyTrades.some((trade) => trade.side === "sell"),
    ),
  );

  if (view.visibleStrategyTrades.some((trade) => trade.side === "unknown")) {
    items.push(
      buildLegendItem(
        drawLegendDiamond(COLORS.strategyUnknown),
        "Backtest fill (unknown side)",
        !state.showStrategyTrades,
      ),
    );
  }

  if (view.indicatorSeries.length) {
    view.indicatorSeries.forEach((indicator) => {
      items.push(
        buildLegendItem(
          drawLegendLine(getIndicatorColor(indicator.key)),
          `${indicator.label} line`,
          false,
        ),
      );
    });
  }

  els.mainChartLegend.innerHTML = items.join("");

  const normalizationLabel =
    state.normalization === "none"
      ? "Prices are shown raw."
      : `Prices are shown relative to ${describeNormalization(state.normalization)}.`;
  const levelsLabel = view.selectedLevels.length
    ? view.selectedLevels.map((level) => `L${level}`).join(", ")
    : "none";

  let overlayLabel = "No backtest overlay loaded.";
  if (state.strategyOverlay) {
    overlayLabel = `Backtest overlay loaded: ${state.strategyOverlay.label}. Strategy fills are drawn last so they sit on top of the market plot.`;
  }

  els.legendNote.textContent = `${normalizationLabel} Visible book levels: ${levelsLabel}. Quote dot size scales with quoted volume. Market trade direction is inferred from price vs. the current book unless the trade matches one of your trader IDs. ${overlayLabel}`;
}

function drawTrades(ctx, trades, xScale, yScale, productData) {
  for (const trade of trades) {
    const row = productData.rowByTimestamp.get(trade.timestamp);
    if (!row) {
      continue;
    }

    const x = xScale(trade.timestamp);
    const y = yScale(normalizePrice(row, trade.price));
    if (trade.isOwn) {
      drawCross(ctx, x, y, 6, COLORS.tradeOwn);
    } else if (trade.side === "buy") {
      drawTriangle(ctx, x, y, 7, COLORS.tradeBuy, true);
    } else if (trade.side === "sell") {
      drawTriangle(ctx, x, y, 7, COLORS.tradeSell, false);
    } else {
      drawSquare(ctx, x, y, 6, COLORS.tradeBuy);
    }
  }
}

function drawCross(ctx, x, y, size, color) {
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.8;
  ctx.beginPath();
  ctx.moveTo(x - size, y - size);
  ctx.lineTo(x + size, y + size);
  ctx.moveTo(x - size, y + size);
  ctx.lineTo(x + size, y - size);
  ctx.stroke();
  ctx.restore();
}

function drawTriangle(ctx, x, y, size, color, upwards) {
  ctx.save();
  ctx.fillStyle = color;
  ctx.beginPath();
  if (upwards) {
    ctx.moveTo(x, y - size);
    ctx.lineTo(x + size, y + size);
    ctx.lineTo(x - size, y + size);
  } else {
    ctx.moveTo(x, y + size);
    ctx.lineTo(x + size, y - size);
    ctx.lineTo(x - size, y - size);
  }
  ctx.closePath();
  ctx.fill();
  ctx.restore();
}

function drawSquare(ctx, x, y, size, color) {
  ctx.save();
  ctx.fillStyle = color;
  ctx.fillRect(x - size / 2, y - size / 2, size, size);
  ctx.restore();
}

function drawStrategyTrades(ctx, trades, xScale, yScale, productData) {
  for (const trade of trades) {
    const row = productData.rowByTimestamp.get(trade.timestamp);
    if (!row) {
      continue;
    }

    const x = xScale(trade.timestamp);
    const y = yScale(normalizePrice(row, trade.price));
    const color =
      trade.side === "buy"
        ? COLORS.strategyBuy
        : trade.side === "sell"
          ? COLORS.strategySell
          : COLORS.strategyUnknown;
    drawDiamond(ctx, x, y, 9, color, "rgba(25, 29, 36, 0.95)");
  }
}

function drawDiamond(ctx, x, y, size, fillColor, strokeColor) {
  ctx.save();
  ctx.fillStyle = fillColor;
  ctx.strokeStyle = strokeColor;
  ctx.lineWidth = 1.8;
  ctx.beginPath();
  ctx.moveTo(x, y - size);
  ctx.lineTo(x + size, y);
  ctx.lineTo(x, y + size);
  ctx.lineTo(x - size, y);
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
  ctx.restore();
}

function buildLegendItem(icon, label, hidden) {
  const hiddenClass = hidden ? " is-hidden" : "";
  return `<div class="legend-item${hiddenClass}">${icon}<span>${escapeHtml(label)}</span></div>`;
}

function drawLegendDot(color) {
  return `<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="5" fill="${color}"></circle></svg>`;
}

function drawLegendLine(color) {
  return `<svg width="22" height="16" viewBox="0 0 22 16" aria-hidden="true"><line x1="1" y1="8" x2="21" y2="8" stroke="${color}" stroke-width="2.5" stroke-linecap="round"></line></svg>`;
}

function drawLegendTriangle(color, upwards) {
  const points = upwards ? "8,2 14,14 2,14" : "2,2 14,2 8,14";
  return `<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><polygon points="${points}" fill="${color}"></polygon></svg>`;
}

function drawLegendCross(color) {
  return `<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><line x1="3" y1="3" x2="13" y2="13" stroke="${color}" stroke-width="2"></line><line x1="3" y1="13" x2="13" y2="3" stroke="${color}" stroke-width="2"></line></svg>`;
}

function drawLegendSquare(color) {
  return `<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><rect x="3" y="3" width="10" height="10" fill="${color}"></rect></svg>`;
}

function drawLegendDiamond(color) {
  return `<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><polygon points="8,1 15,8 8,15 1,8" fill="${color}" stroke="rgba(25, 29, 36, 0.95)" stroke-width="1.25"></polygon></svg>`;
}

function renderSeriesChart(canvas, series, color, emptyMessage) {
  const prepared = prepareCanvas(canvas);
  const ctx = prepared.ctx;
  const width = prepared.width;
  const height = prepared.height;
  const plot = { left: 62, top: 20, right: width - 24, bottom: height - 36 };

  ctx.clearRect(0, 0, width, height);

  if (!series.length) {
    ctx.fillStyle = COLORS.axis;
    ctx.font = "13px Menlo, Consolas, monospace";
    ctx.fillText(emptyMessage, 18, height / 2);
    return;
  }

  const xMin = series[0].timestamp;
  const xMax = series[series.length - 1].timestamp;
  const values = series.map((point) => point.value);
  let yMin = Math.min(...values);
  let yMax = Math.max(...values);
  if (yMin === yMax) {
    yMin -= 1;
    yMax += 1;
  }

  const yPadding = Math.max((yMax - yMin) * 0.1, 1);
  yMin -= yPadding;
  yMax += yPadding;

  drawGridAndAxes(ctx, plot, xMin, xMax, yMin, yMax);

  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.beginPath();
  series.forEach((point, index) => {
    const x = scale(point.timestamp, xMin, xMax, plot.left, plot.right);
    const y = scale(point.value, yMin, yMax, plot.bottom, plot.top);
    if (index === 0) {
      ctx.moveTo(x, y);
    } else {
      ctx.lineTo(x, y);
    }
  });
  ctx.stroke();
  ctx.restore();
}

function buildPnlSeries(productData, strategyProduct) {
  if (!productData) {
    return [];
  }

  const strategySeries = buildStrategyMetricSeries(productData, strategyProduct, "pnl");
  if (strategySeries.length) {
    return strategySeries;
  }

  const computedFromStrategy = buildStrategyMarkToMidSeries(productData, strategyProduct);
  if (computedFromStrategy.length) {
    return computedFromStrategy;
  }

  const hasNonZeroPnl = productData.rows.some((row) => Math.abs(row.pnl) > 1e-9);
  if (hasNonZeroPnl) {
    return productData.rows.map((row) => ({ timestamp: row.timestamp, value: row.pnl }));
  }

  const ownTrades = productData.trades
    .map((trade) => decorateTrade(trade, productData.rowByTimestamp.get(trade.timestamp), getOwnTraderIds()))
    .filter((trade) => trade.isOwn)
    .sort((left, right) => left.timestamp - right.timestamp);

  if (!ownTrades.length) {
    return [];
  }

  const tradesByTimestamp = groupBy(ownTrades, (trade) => trade.timestamp);
  const series = [];
  let cash = 0;
  let position = 0;

  for (const row of productData.rows) {
    const trades = tradesByTimestamp.get(row.timestamp) || [];
    trades.forEach((trade) => {
      if (trade.side === "buy") {
        position += trade.quantity;
        cash -= trade.quantity * trade.price;
      } else if (trade.side === "sell") {
        position -= trade.quantity;
        cash += trade.quantity * trade.price;
      }
    });

    series.push({
      timestamp: row.timestamp,
      value: cash + position * row.midPrice,
    });
  }

  return series;
}

function buildPositionSeries(productData, strategyProduct) {
  if (!productData) {
    return [];
  }

  const strategySeries = buildStrategyMetricSeries(productData, strategyProduct, "position");
  if (strategySeries.length) {
    return strategySeries;
  }

  const computedFromStrategy = buildStrategyPositionSeries(productData, strategyProduct);
  if (computedFromStrategy.length) {
    return computedFromStrategy;
  }

  const ownTrades = productData.trades
    .map((trade) => decorateTrade(trade, productData.rowByTimestamp.get(trade.timestamp), getOwnTraderIds()))
    .filter((trade) => trade.isOwn)
    .sort((left, right) => left.timestamp - right.timestamp);

  if (!ownTrades.length) {
    return [];
  }

  const tradesByTimestamp = groupBy(ownTrades, (trade) => trade.timestamp);
  const series = [];
  let position = 0;

  for (const row of productData.rows) {
    const trades = tradesByTimestamp.get(row.timestamp) || [];
    trades.forEach((trade) => {
      if (trade.side === "buy") {
        position += trade.quantity;
      } else if (trade.side === "sell") {
        position -= trade.quantity;
      }
    });

    series.push({
      timestamp: row.timestamp,
      value: position,
    });
  }

  return series;
}

function buildStrategyMetricSeries(productData, strategyProduct, field) {
  if (!strategyProduct) {
    return [];
  }

  const trades = strategyProduct.trades.filter((trade) => Number.isFinite(trade[field]));
  if (!trades.length) {
    return [];
  }

  const series = [];
  let index = 0;
  let currentValue = 0;
  let hasSeenValue = false;

  for (const row of productData.rows) {
    while (index < strategyProduct.trades.length && strategyProduct.trades[index].timestamp <= row.timestamp) {
      const trade = strategyProduct.trades[index];
      if (Number.isFinite(trade[field])) {
        currentValue = trade[field];
        hasSeenValue = true;
      }
      index += 1;
    }

    if (hasSeenValue) {
      series.push({ timestamp: row.timestamp, value: currentValue });
    }
  }

  return series;
}

function buildStrategyMarkToMidSeries(productData, strategyProduct) {
  if (!strategyProduct) {
    return [];
  }

  const executableTrades = strategyProduct.trades.filter((trade) => Number.isFinite(trade.signedQuantity));
  if (!executableTrades.length) {
    return [];
  }

  const tradesByTimestamp = groupBy(executableTrades, (trade) => trade.timestamp);
  const series = [];
  let cash = 0;
  let position = 0;

  for (const row of productData.rows) {
    const trades = tradesByTimestamp.get(row.timestamp) || [];
    trades.forEach((trade) => {
      position += trade.signedQuantity;
      cash -= trade.signedQuantity * trade.price;
    });

    series.push({
      timestamp: row.timestamp,
      value: cash + position * row.midPrice,
    });
  }

  return series;
}

function buildStrategyPositionSeries(productData, strategyProduct) {
  if (!strategyProduct) {
    return [];
  }

  const executableTrades = strategyProduct.trades.filter((trade) => Number.isFinite(trade.signedQuantity));
  if (!executableTrades.length) {
    return [];
  }

  const tradesByTimestamp = groupBy(executableTrades, (trade) => trade.timestamp);
  const series = [];
  let position = 0;

  for (const row of productData.rows) {
    const trades = tradesByTimestamp.get(row.timestamp) || [];
    trades.forEach((trade) => {
      position += trade.signedQuantity;
    });

    series.push({
      timestamp: row.timestamp,
      value: position,
    });
  }

  return series;
}

function buildIndicatorSeries(productData, filteredRows, indicatorKey) {
  if (indicatorKey.startsWith("custom:")) {
    const name = indicatorKey.slice(7);
    const values = productData.indicatorsByName.get(name) || [];
    return values
      .filter((item) => item.timestamp >= filteredRows[0]?.timestamp && item.timestamp <= filteredRows[filteredRows.length - 1]?.timestamp)
      .map((item) => ({ timestamp: item.timestamp, value: item.value }));
  }

  return filteredRows
    .map((row) => {
      const value = getRowIndicatorValue(row, indicatorKey);
      if (!Number.isFinite(value)) {
        return null;
      }

      return { timestamp: row.timestamp, value };
    })
    .filter(Boolean);
}

function getRowIndicatorValue(row, indicatorKey) {
  switch (indicatorKey) {
    case "midPrice":
      return row.midPrice;
    case "wallMid":
      return row.wallMid;
    case "bestBid":
      return row.bestBid;
    case "bestAsk":
      return row.bestAsk;
    case "wallBid":
      return row.wallBid;
    case "wallAsk":
      return row.wallAsk;
    default:
      return NaN;
  }
}

function buildDataset({ key, label, priceRowsRaw, tradeRowsRaw, indicatorRowsRaw, logRowsRaw }) {
  const priceRows = priceRowsRaw
    .map((row) => normalizePriceRow(row))
    .filter((row) => row.product && Number.isFinite(row.timestamp))
    .sort((left, right) => {
      if (left.product === right.product) {
        return left.timestamp - right.timestamp;
      }
      return left.product.localeCompare(right.product);
    });

  const tradeRows = tradeRowsRaw
    .map((row) => normalizeTradeRow(row))
    .filter((row) => row.symbol && Number.isFinite(row.timestamp) && Number.isFinite(row.price))
    .sort((left, right) => left.timestamp - right.timestamp);

  const indicatorRows = indicatorRowsRaw
    .map((row) => normalizeIndicatorRow(row))
    .filter((row) => row.product && row.name && Number.isFinite(row.timestamp) && Number.isFinite(row.value));

  const logRows = logRowsRaw
    .map((row) => normalizeLogRow(row))
    .filter((row) => row.product && Number.isFinite(row.timestamp) && row.message);

  const products = new Map();

  for (const row of priceRows) {
    if (!products.has(row.product)) {
      products.set(row.product, {
        rows: [],
        rowByTimestamp: new Map(),
        trades: [],
        indicatorsByName: new Map(),
        logsByTimestamp: new Map(),
      });
    }

    const product = products.get(row.product);
    product.rows.push(row);
    product.rowByTimestamp.set(row.timestamp, row);
  }

  for (const trade of tradeRows) {
    const product = products.get(trade.symbol);
    if (product) {
      product.trades.push(trade);
    }
  }

  for (const indicator of indicatorRows) {
    const product = products.get(indicator.product);
    if (!product) {
      continue;
    }

    if (!product.indicatorsByName.has(indicator.name)) {
      product.indicatorsByName.set(indicator.name, []);
    }

    product.indicatorsByName.get(indicator.name).push(indicator);
  }

  for (const logRow of logRows) {
    const product = products.get(logRow.product);
    if (!product) {
      continue;
    }

    if (!product.logsByTimestamp.has(logRow.timestamp)) {
      product.logsByTimestamp.set(logRow.timestamp, []);
    }
    product.logsByTimestamp.get(logRow.timestamp).push(logRow);
  }

  products.forEach((product) => {
    product.trades.sort((left, right) => left.timestamp - right.timestamp);
    product.indicatorsByName.forEach((series) => series.sort((left, right) => left.timestamp - right.timestamp));
  });

  return { key, label, priceRows, tradeRows, indicatorRows, logRows, products };
}

function buildStrategyOverlay({ label, strategyRowsRaw }) {
  const strategyRows = strategyRowsRaw
    .map((row) => normalizeStrategyTradeRow(row))
    .filter(
      (row) =>
        row.product &&
        Number.isFinite(row.timestamp) &&
        Number.isFinite(row.price) &&
        Number.isFinite(row.quantity) &&
        row.quantity > 0,
    )
    .sort((left, right) => {
      if (left.product === right.product) {
        return left.timestamp - right.timestamp;
      }
      return left.product.localeCompare(right.product);
    });

  const products = new Map();
  strategyRows.forEach((row) => {
    if (!products.has(row.product)) {
      products.set(row.product, { trades: [] });
    }
    products.get(row.product).trades.push(row);
  });

  return { label, trades: strategyRows, products };
}

function normalizePriceRow(row) {
  const bids = [];
  const asks = [];
  for (let level = 1; level <= 3; level += 1) {
    const bidPrice = toNumber(row[`bid_price_${level}`]);
    const bidVolume = toNumber(row[`bid_volume_${level}`]);
    const askPrice = toNumber(row[`ask_price_${level}`]);
    const askVolume = toNumber(row[`ask_volume_${level}`]);

    if (Number.isFinite(bidPrice) && Number.isFinite(bidVolume)) {
      bids.push({ level, price: bidPrice, volume: Math.abs(bidVolume) });
    }

    if (Number.isFinite(askPrice) && Number.isFinite(askVolume)) {
      asks.push({ level, price: askPrice, volume: Math.abs(askVolume) });
    }
  }

  const bestBid = bids[0]?.price ?? NaN;
  const bestAsk = asks[0]?.price ?? NaN;
  const wallBid = bids.length
    ? bids.reduce((best, level) => (level.volume > best.volume ? level : best)).price
    : bestBid;
  const wallAsk = asks.length
    ? asks.reduce((best, level) => (level.volume > best.volume ? level : best)).price
    : bestAsk;
  const spread =
    Number.isFinite(bestBid) && Number.isFinite(bestAsk) ? bestAsk - bestBid : NaN;
  const midPrice =
    toNumber(row.mid_price) ??
    (Number.isFinite(bestBid) && Number.isFinite(bestAsk) ? (bestBid + bestAsk) / 2 : NaN);
  const wallMid =
    Number.isFinite(wallBid) && Number.isFinite(wallAsk) ? (wallBid + wallAsk) / 2 : midPrice;

  return {
    day: toNumber(row.day),
    timestamp: toNumber(row.timestamp),
    product: String(row.product || row.symbol || "").trim(),
    bids,
    asks,
    bestBid,
    bestAsk,
    wallBid,
    wallAsk,
    midPrice,
    wallMid,
    spread,
    pnl: toNumber(row.profit_and_loss) ?? 0,
  };
}

function normalizeTradeRow(row) {
  return {
    timestamp: toNumber(row.timestamp),
    buyer: String(row.buyer || "").trim(),
    seller: String(row.seller || "").trim(),
    symbol: String(row.symbol || row.product || "").trim(),
    currency: String(row.currency || "").trim(),
    price: toNumber(row.price),
    quantity: Math.abs(toNumber(row.quantity) ?? 0),
  };
}

function normalizeIndicatorRow(row) {
  return {
    timestamp: toNumber(row.timestamp),
    product: String(row.product || row.symbol || "").trim(),
    name: String(row.name || row.indicator || "").trim(),
    value: toNumber(row.value),
  };
}

function normalizeLogRow(row) {
  return {
    timestamp: toNumber(row.timestamp),
    product: String(row.product || row.symbol || "").trim(),
    message: String(row.message || row.text || row.log || "").trim(),
  };
}

function normalizeStrategyTradeRow(row) {
  const product = String(
    firstDefinedValue(row, ["product", "symbol", "instrument", "asset"]) || "",
  ).trim();
  const timestamp = firstNumberValue(row, ["timestamp", "time", "ts"]);
  const price = firstNumberValue(row, ["price", "trade_price", "fill_price", "execution_price"]);
  const explicitSignedQuantity = firstNumberValue(row, [
    "signed_quantity",
    "signed_qty",
    "net_quantity",
    "signed_volume",
  ]);
  const rawQuantity =
    explicitSignedQuantity ??
    firstNumberValue(row, ["quantity", "qty", "volume", "size", "filled_quantity"]);
  const side =
    normalizeTradeSide(firstDefinedValue(row, ["side", "action", "direction", "trade_side", "order_side"])) ||
    normalizeBooleanSide(firstDefinedValue(row, ["is_buy", "buy"]));
  let signedQuantity = null;
  if (Number.isFinite(explicitSignedQuantity)) {
    signedQuantity = explicitSignedQuantity;
  } else if (Number.isFinite(rawQuantity) && rawQuantity < 0) {
    signedQuantity = rawQuantity;
  } else if (Number.isFinite(rawQuantity) && side === "buy") {
    signedQuantity = rawQuantity;
  } else if (Number.isFinite(rawQuantity) && side === "sell") {
    signedQuantity = -rawQuantity;
  }

  const quantity = Number.isFinite(rawQuantity)
    ? Math.abs(rawQuantity)
    : Number.isFinite(signedQuantity)
      ? Math.abs(signedQuantity)
      : NaN;
  const normalizedSide =
    side ||
    (Number.isFinite(signedQuantity)
      ? signedQuantity > 0
        ? "buy"
        : signedQuantity < 0
          ? "sell"
          : "unknown"
      : "unknown");

  return {
    timestamp,
    product,
    price,
    quantity,
    signedQuantity,
    side: normalizedSide,
    pnl: firstNumberValue(row, ["pnl", "profit_and_loss", "profit", "realized_pnl", "total_pnl"]),
    position: firstNumberValue(row, ["position", "pos", "inventory", "net_position"]),
  };
}

function decorateTrade(trade, bookRow, ownIds) {
  const normalizedBuyer = normalizeId(trade.buyer);
  const normalizedSeller = normalizeId(trade.seller);

  if (ownIds.has(normalizedBuyer)) {
    return { ...trade, isOwn: true, side: "buy" };
  }

  if (ownIds.has(normalizedSeller)) {
    return { ...trade, isOwn: true, side: "sell" };
  }

  let side = "unknown";
  if (bookRow) {
    if (Number.isFinite(bookRow.bestAsk) && trade.price >= bookRow.bestAsk) {
      side = "buy";
    } else if (Number.isFinite(bookRow.bestBid) && trade.price <= bookRow.bestBid) {
      side = "sell";
    } else if (Number.isFinite(bookRow.midPrice)) {
      side = trade.price >= bookRow.midPrice ? "buy" : "sell";
    }
  }

  return { ...trade, isOwn: false, side };
}

function parseDelimitedText(text) {
  const trimmed = text.trim();
  if (!trimmed) {
    return [];
  }

  const lines = trimmed.split(/\r?\n/).filter(Boolean);
  const delimiter = lines[0].includes(";") ? ";" : ",";
  const headers = splitDelimitedLine(lines[0], delimiter).map((header) => header.trim());

  return lines.slice(1).map((line) => {
    const values = splitDelimitedLine(line, delimiter);
    const row = {};
    headers.forEach((header, index) => {
      const value = (values[index] || "").trim();
      row[header] = value;
      const lowerKey = header.toLowerCase();
      if (!(lowerKey in row)) {
        row[lowerKey] = value;
      }
    });
    return row;
  });
}

function parseIndicatorText(text) {
  return parseDelimitedText(text);
}

function parseLogText(text) {
  const trimmed = text.trim();
  if (!trimmed) {
    return [];
  }

  if (trimmed.startsWith("{")) {
    return trimmed
      .split(/\r?\n/)
      .filter(Boolean)
      .map((line) => JSON.parse(line));
  }

  if (trimmed.startsWith("[")) {
    return JSON.parse(trimmed);
  }

  return parseDelimitedText(trimmed);
}

function splitDelimitedLine(line, delimiter) {
  const cells = [];
  let current = "";
  let inQuotes = false;

  for (let index = 0; index < line.length; index += 1) {
    const character = line[index];

    if (character === '"') {
      if (inQuotes && line[index + 1] === '"') {
        current += '"';
        index += 1;
      } else {
        inQuotes = !inQuotes;
      }
      continue;
    }

    if (character === delimiter && !inQuotes) {
      cells.push(current);
      current = "";
      continue;
    }

    current += character;
  }

  cells.push(current);
  return cells;
}

function fetchText(path) {
  return fetch(path).then((response) => {
    if (!response.ok) {
      throw new Error(`Request failed for ${path}`);
    }
    return response.text();
  });
}

function readOptionalFile(file) {
  return file ? file.text() : Promise.resolve("");
}

function prepareCanvas(canvas) {
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(1, Math.floor(rect.width));
  const height = Math.max(1, Math.floor(rect.height));
  const dpr = window.devicePixelRatio || 1;

  if (canvas.width !== Math.floor(width * dpr) || canvas.height !== Math.floor(height * dpr)) {
    canvas.width = Math.floor(width * dpr);
    canvas.height = Math.floor(height * dpr);
  }

  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, width, height };
}

function getActiveDataset() {
  return state.datasets.get(state.activeDatasetKey) || null;
}

function getActiveProductData() {
  const dataset = getActiveDataset();
  if (!dataset) {
    return null;
  }

  return dataset.products.get(state.selectedProduct) || null;
}

function getStrategyProductData() {
  if (!state.strategyOverlay) {
    return null;
  }

  return state.strategyOverlay.products.get(state.selectedProduct) || null;
}

function getHoveredRow(view) {
  if (!state.hoveredTimestamp) {
    return null;
  }

  return view.filteredRows.find((row) => row.timestamp === state.hoveredTimestamp) || null;
}

function findNearestRow(rows, timestamp) {
  if (!rows.length) {
    return null;
  }

  let low = 0;
  let high = rows.length - 1;

  while (low < high) {
    const mid = Math.floor((low + high) / 2);
    if (rows[mid].timestamp < timestamp) {
      low = mid + 1;
    } else {
      high = mid;
    }
  }

  const candidate = rows[low];
  const previous = rows[Math.max(0, low - 1)];
  if (!previous) {
    return candidate;
  }

  return Math.abs(candidate.timestamp - timestamp) < Math.abs(previous.timestamp - timestamp)
    ? candidate
    : previous;
}

function normalizePrice(row, price) {
  if (!Number.isFinite(price)) {
    return NaN;
  }

  if (state.normalization === "midPrice" && Number.isFinite(row.midPrice)) {
    return price - row.midPrice;
  }

  if (state.normalization === "wallMid" && Number.isFinite(row.wallMid)) {
    return price - row.wallMid;
  }

  return price;
}

function getOwnTraderIds() {
  return new Set(
    els.ownTraderIdsInput.value
      .split(",")
      .map((value) => normalizeId(value))
      .filter(Boolean),
  );
}

function normalizeId(value) {
  return String(value || "").trim().toUpperCase();
}

function formatBookSide(levels) {
  if (!levels.length) {
    return ["- none"];
  }

  return levels.map((level) => `- L${level.level}: ${formatMaybe(level.price)} x ${formatInteger(level.volume)}`);
}

function groupBy(items, getKey) {
  const map = new Map();
  items.forEach((item) => {
    const key = getKey(item);
    if (!map.has(key)) {
      map.set(key, []);
    }
    map.get(key).push(item);
  });
  return map;
}

function scale(value, domainMin, domainMax, rangeMin, rangeMax) {
  if (domainMax === domainMin) {
    return (rangeMin + rangeMax) / 2;
  }
  const ratio = (value - domainMin) / (domainMax - domainMin);
  return rangeMin + ratio * (rangeMax - rangeMin);
}

function toNumber(value) {
  if (value == null || value === "") {
    return null;
  }

  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function firstDefinedValue(row, keys) {
  for (const key of keys) {
    if (Object.hasOwn(row, key) && row[key] !== "") {
      return row[key];
    }
  }
  return null;
}

function firstNumberValue(row, keys) {
  for (const key of keys) {
    if (Object.hasOwn(row, key)) {
      const number = toNumber(row[key]);
      if (Number.isFinite(number)) {
        return number;
      }
    }
  }
  return null;
}

function normalizeTradeSide(value) {
  const normalized = String(value || "").trim().toLowerCase();
  if (!normalized) {
    return null;
  }

  if (["buy", "bid", "b", "long", "1"].includes(normalized)) {
    return "buy";
  }

  if (["sell", "ask", "s", "short", "-1"].includes(normalized)) {
    return "sell";
  }

  return null;
}

function normalizeBooleanSide(value) {
  const normalized = String(value || "").trim().toLowerCase();
  if (["true", "yes", "1"].includes(normalized)) {
    return "buy";
  }

  if (["false", "no", "0"].includes(normalized)) {
    return "sell";
  }

  return null;
}

function clampNumber(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function setStatus(message) {
  els.statusBadge.textContent = message;
}

function formatMaybe(value) {
  return Number.isFinite(value) ? value.toFixed(2) : "n/a";
}

function formatInteger(value) {
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(
    Number.isFinite(value) ? value : 0,
  );
}

function formatAxisValue(value) {
  if (Math.abs(value) >= 1000) {
    return value.toFixed(0);
  }
  if (Math.abs(value) >= 100) {
    return value.toFixed(1);
  }
  return value.toFixed(2);
}

function formatAxisInteger(value) {
  const absolute = Math.abs(value);
  if (absolute >= 1000000) {
    return `${(value / 1000000).toFixed(1)}m`;
  }
  if (absolute >= 1000) {
    return `${(value / 1000).toFixed(0)}k`;
  }
  return value.toFixed(0);
}

function describeNormalization(key) {
  switch (key) {
    case "midPrice":
      return "mid price";
    case "wallMid":
      return "wall mid";
    default:
      return "raw price";
  }
}

function getIndicatorColor(indicatorKey) {
  if (COLORS[indicatorKey]) {
    return COLORS[indicatorKey];
  }

  if (indicatorKey.startsWith("custom:")) {
    const paletteIndex = Math.abs(hashString(indicatorKey)) % CUSTOM_LINE_PALETTE.length;
    return CUSTOM_LINE_PALETTE[paletteIndex];
  }

  return COLORS.midPrice;
}

function hashString(value) {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash << 5) - hash + value.charCodeAt(index);
    hash |= 0;
  }
  return hash;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}
