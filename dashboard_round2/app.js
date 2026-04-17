"use strict";

const BUILTIN_DATASETS = [
  {
    key: "round2-day-1",
    label: "Round 2 / Day 1 / ASH_COATED_OSMIUM + INTARIAN_PEPPER_ROOT",
    pricePath: "../data/round2/prices_round_2_day_1.csv",
    tradePath: "../data/round2/trades_round_2_day_1.csv",
  },
  {
    key: "round2-day-0",
    label: "Round 2 / Day 0 / ASH_COATED_OSMIUM + INTARIAN_PEPPER_ROOT",
    pricePath: "../data/round2/prices_round_2_day_0.csv",
    tradePath: "../data/round2/trades_round_2_day_0.csv",
  },
  {
    key: "round2-day--1",
    label: "Round 2 / Day -1 / ASH_COATED_OSMIUM + INTARIAN_PEPPER_ROOT",
    pricePath: "../data/round2/prices_round_2_day_-1.csv",
    tradePath: "../data/round2/trades_round_2_day_-1.csv",
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
  noAsksBand: "rgba(32, 92, 201, 0.14)",
  noBidsBand: "rgba(194, 59, 59, 0.14)",
  noAsksStroke: "rgba(32, 92, 201, 0.32)",
  noBidsStroke: "rgba(194, 59, 59, 0.32)",
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

const SYNTHETIC_PRODUCTS = ["ASH_COATED_OSMIUM", "INTARIAN_PEPPER_ROOT"];

const SYNTHETIC_SOURCE_OPTIONS = [
  {
    key: "all-days",
    label: "All Round 2 Days (-1, 0, 1)",
    datasetKeys: ["round2-day--1", "round2-day-0", "round2-day-1"],
  },
  {
    key: "round2-day--1",
    label: "Day -1 Only",
    datasetKeys: ["round2-day--1"],
  },
  {
    key: "round2-day-0",
    label: "Day 0 Only",
    datasetKeys: ["round2-day-0"],
  },
  {
    key: "round2-day-1",
    label: "Day 1 Only",
    datasetKeys: ["round2-day-1"],
  },
];

const SYNTHETIC_METHOD_TEXT = [
  "approach: block bootstrap rather than curve fitting",
  "source blocks: contiguous slices from the real Round 2 tapes",
  "state carried over: mid-price deltas, spread regime, depth offsets, and trade bursts",
  "re-centering: each borrowed book shape is moved onto a synthetic mid path",
  "why this helps: local microstructure stays realistic, but the exact day path is no longer memorized",
  "guardrail: blocks are sampled within a single day so we do not splice across the public day reset",
].join("\n");

const state = {
  datasets: new Map(),
  activeDatasetKey: BUILTIN_DATASETS[0].key,
  activeTab: "replay",
  selectedProduct: "",
  normalization: "none",
  downsample: 1,
  showBids: true,
  showAsks: true,
  showOneSidedHighlights: true,
  showMarketTrades: true,
  showOwnTrades: true,
  showStrategyTrades: true,
  visibleLevels: { 1: true, 2: true, 3: true },
  visibleIndicators: new Set(["midPrice", "wallMid"]),
  hoveredTimestamp: null,
  hoveredPnlTimestamp: null,
  strategyOverlay: null,
  zoomDrag: null,
  mainPanDrag: null,
  mainChartYOffset: 0,
  pnlPanDrag: null,
  pnlChartYOffset: 0,
  miniTimeRange: null,
  syntheticSourceCache: new Map(),
  syntheticLab: {
    sourceKey: "all-days",
    product: "INTARIAN_PEPPER_ROOT",
    scenarioCount: 24,
    horizonRows: 600,
    blockLength: 24,
    seed: 7,
    selectedScenarioId: "",
    generated: null,
    hoveredTimestamp: null,
  },
};

const els = {};

document.addEventListener("DOMContentLoaded", () => {
  bindElements();
  bindEvents();
  populateDatasetSelect();
  populateSyntheticSourceSelect();
  populateSyntheticProductSelect();
  renderSyntheticLab();
  loadDataset(state.activeDatasetKey);
});

function bindElements() {
  els.tabButtons = [...document.querySelectorAll("[data-tab-target]")];
  els.tabPanels = [...document.querySelectorAll("[data-tab-panel]")];
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
  els.showOneSidedToggle = document.getElementById("show-one-sided-toggle");
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
  els.pnlChartWrapper = document.getElementById("pnl-chart-wrapper");
  els.pnlChart = document.getElementById("pnl-chart");
  els.positionChart = document.getElementById("position-chart");
  els.mainTooltip = document.getElementById("main-tooltip");
  els.pnlTooltip = document.getElementById("pnl-tooltip");
  els.uploadPriceInput = document.getElementById("upload-price-input");
  els.uploadTradeInput = document.getElementById("upload-trade-input");
  els.uploadStrategyTradesInput = document.getElementById("upload-strategy-trades-input");
  els.uploadIndicatorInput = document.getElementById("upload-indicator-input");
  els.uploadLogInput = document.getElementById("upload-log-input");
  els.loadUploadedButton = document.getElementById("load-uploaded-button");
  els.loadStrategyOverlayButton = document.getElementById("load-strategy-overlay-button");
  els.clearStrategyOverlayButton = document.getElementById("clear-strategy-overlay-button");
  els.syntheticDatasetSummary = document.getElementById("synthetic-dataset-summary");
  els.syntheticSourceSelect = document.getElementById("synthetic-source-select");
  els.syntheticProductSelect = document.getElementById("synthetic-product-select");
  els.syntheticScenarioCountInput = document.getElementById("synthetic-scenario-count-input");
  els.syntheticHorizonInput = document.getElementById("synthetic-horizon-input");
  els.syntheticBlockInput = document.getElementById("synthetic-block-input");
  els.syntheticSeedInput = document.getElementById("synthetic-seed-input");
  els.syntheticScenarioSelect = document.getElementById("synthetic-scenario-select");
  els.syntheticGenerateButton = document.getElementById("synthetic-generate-button");
  els.syntheticLoadButton = document.getElementById("synthetic-load-button");
  els.syntheticDownloadPricesButton = document.getElementById("synthetic-download-prices-button");
  els.syntheticDownloadTradesButton = document.getElementById("synthetic-download-trades-button");
  els.syntheticChartSummary = document.getElementById("synthetic-chart-summary");
  els.syntheticLegendNote = document.getElementById("synthetic-legend-note");
  els.syntheticChartWrapper = document.getElementById("synthetic-chart-wrapper");
  els.syntheticChart = document.getElementById("synthetic-chart");
  els.syntheticTooltip = document.getElementById("synthetic-tooltip");
  els.syntheticSpreadChart = document.getElementById("synthetic-spread-chart");
  els.syntheticActivityChart = document.getElementById("synthetic-activity-chart");
  els.syntheticSpreadNote = document.getElementById("synthetic-spread-note");
  els.syntheticActivityNote = document.getElementById("synthetic-activity-note");
  els.syntheticSourceCard = document.getElementById("synthetic-source-card");
  els.syntheticSelectedCard = document.getElementById("synthetic-selected-card");
  els.syntheticMethodCard = document.getElementById("synthetic-method-card");
}

function bindEvents() {
  els.tabButtons.forEach((button) => {
    button.addEventListener("click", () => switchDashboardTab(button.dataset.tabTarget || "replay"));
  });

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
    state.mainChartYOffset = 0;
    renderAll();
  });

  els.downsampleSelect.addEventListener("change", (event) => {
    state.downsample = clampNumber(toNumber(event.target.value), 1, 100) || 1;
    renderAll();
  });

  [els.timeMinInput, els.timeMaxInput].forEach((input) =>
    input.addEventListener("change", () => {
      state.mainChartYOffset = 0;
      state.hoveredTimestamp = null;
      state.hoveredPnlTimestamp = null;
      els.mainTooltip.classList.add("hidden");
      els.pnlTooltip.classList.add("hidden");
      renderAll();
    }),
  );

  [els.tradeMinInput, els.tradeMaxInput].forEach((input) =>
    input.addEventListener("change", () => renderAll()),
  );

  els.resetRangeButton.addEventListener("click", () => {
    resetMainTimeRange();
    state.mainChartYOffset = 0;
    state.hoveredTimestamp = null;
    state.hoveredPnlTimestamp = null;
    els.mainTooltip.classList.add("hidden");
    els.pnlTooltip.classList.add("hidden");
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

  els.showOneSidedToggle.addEventListener("change", (event) => {
    state.showOneSidedHighlights = event.target.checked;
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

  els.mainChartWrapper.addEventListener("mousedown", handleChartMouseDown);
  els.mainChartWrapper.addEventListener("mousemove", handleChartHover);
  els.mainChartWrapper.addEventListener("wheel", handleChartWheel, { passive: false });
  els.mainChartWrapper.addEventListener("dblclick", handleChartDoubleClick);
  els.mainChartWrapper.addEventListener("mouseleave", () => {
    if (state.zoomDrag) {
      els.mainTooltip.classList.add("hidden");
      return;
    }
    state.hoveredTimestamp = null;
    els.mainTooltip.classList.add("hidden");
    renderAll();
  });
  els.pnlChartWrapper.addEventListener("mousedown", handlePnlChartMouseDown);
  els.pnlChartWrapper.addEventListener("mousemove", handlePnlChartHover);
  els.pnlChartWrapper.addEventListener("wheel", handlePnlChartWheel, { passive: false });
  els.pnlChartWrapper.addEventListener("dblclick", handlePnlChartDoubleClick);
  els.pnlChartWrapper.addEventListener("mouseleave", () => {
    if (state.pnlPanDrag) {
      els.pnlTooltip.classList.add("hidden");
      return;
    }
    state.hoveredPnlTimestamp = null;
    els.pnlTooltip.classList.add("hidden");
    renderAll();
  });

  [
    els.syntheticSourceSelect,
    els.syntheticProductSelect,
    els.syntheticScenarioCountInput,
    els.syntheticHorizonInput,
    els.syntheticBlockInput,
    els.syntheticSeedInput,
  ].forEach((input) =>
    input.addEventListener("change", () => {
      syncSyntheticConfigFromInputs();
      clearSyntheticResults();
      renderSyntheticLab();
    }),
  );

  els.syntheticScenarioSelect.addEventListener("change", (event) => {
    state.syntheticLab.selectedScenarioId = event.target.value;
    state.syntheticLab.hoveredTimestamp = null;
    els.syntheticTooltip.classList.add("hidden");
    renderSyntheticLab();
  });

  els.syntheticGenerateButton.addEventListener("click", () => generateSyntheticScenarios());
  els.syntheticLoadButton.addEventListener("click", () => loadSelectedSyntheticScenarioIntoReplay());
  els.syntheticDownloadPricesButton.addEventListener("click", () => downloadSelectedSyntheticScenario("prices"));
  els.syntheticDownloadTradesButton.addEventListener("click", () => downloadSelectedSyntheticScenario("trades"));
  els.syntheticChartWrapper.addEventListener("mousemove", handleSyntheticChartHover);
  els.syntheticChartWrapper.addEventListener("mouseleave", () => {
    state.syntheticLab.hoveredTimestamp = null;
    els.syntheticTooltip.classList.add("hidden");
    renderSyntheticMainChart();
  });

  window.addEventListener("mousemove", handleChartDragMove);
  window.addEventListener("mouseup", handleChartMouseUp);
  window.addEventListener("resize", () => {
    if (state.activeTab === "synthetic") {
      renderSyntheticLab();
      return;
    }
    renderAll();
  });
}

function switchDashboardTab(tabKey) {
  state.activeTab = tabKey === "synthetic" ? "synthetic" : "replay";

  els.tabButtons.forEach((button) => {
    const isActive = button.dataset.tabTarget === state.activeTab;
    button.classList.toggle("is-active", isActive);
    button.setAttribute("aria-selected", isActive ? "true" : "false");
  });

  els.tabPanels.forEach((panel) => {
    panel.classList.toggle("is-active", panel.dataset.tabPanel === state.activeTab);
  });

  if (state.activeTab === "synthetic") {
    if (!state.syntheticLab.generated) {
      generateSyntheticScenarios();
      return;
    }
    renderSyntheticLab();
    return;
  }

  renderAll();
}

function syncSyntheticConfigFromInputs() {
  state.syntheticLab.sourceKey = els.syntheticSourceSelect.value || state.syntheticLab.sourceKey;
  state.syntheticLab.product = els.syntheticProductSelect.value || state.syntheticLab.product;
  state.syntheticLab.scenarioCount = readIntegerInput(els.syntheticScenarioCountInput, 24, 4, 96);
  state.syntheticLab.horizonRows = readIntegerInput(els.syntheticHorizonInput, 600, 60, 5000);
  state.syntheticLab.blockLength = readIntegerInput(els.syntheticBlockInput, 24, 4, 500);
  state.syntheticLab.seed = readIntegerInput(els.syntheticSeedInput, 7, 0, 1000000);
}

function clearSyntheticResults() {
  state.syntheticLab.generated = null;
  state.syntheticLab.selectedScenarioId = "";
  state.syntheticLab.hoveredTimestamp = null;
  if (els.syntheticTooltip) {
    els.syntheticTooltip.classList.add("hidden");
  }
}

function populateSyntheticSourceSelect() {
  els.syntheticSourceSelect.innerHTML = SYNTHETIC_SOURCE_OPTIONS.map(
    (option) => `<option value="${escapeHtml(option.key)}">${escapeHtml(option.label)}</option>`,
  ).join("");
  els.syntheticSourceSelect.value = state.syntheticLab.sourceKey;
}

function populateSyntheticProductSelect() {
  els.syntheticProductSelect.innerHTML = SYNTHETIC_PRODUCTS.map(
    (product) => `<option value="${escapeHtml(product)}">${escapeHtml(product)}</option>`,
  ).join("");
  els.syntheticProductSelect.value = state.syntheticLab.product;
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
  const builtinKeys = new Set(BUILTIN_DATASETS.map((dataset) => dataset.key));
  const extraDatasets = [...state.datasets.values()]
    .filter((dataset) => !builtinKeys.has(dataset.key))
    .sort((left, right) => left.label.localeCompare(right.label));

  extraDatasets.forEach((dataset) => {
    options.push({ key: dataset.key, label: dataset.label });
  });

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
  resetMainTimeRange();
  resetMiniTimeRange();
  state.mainChartYOffset = 0;
  state.pnlChartYOffset = 0;
  state.hoveredTimestamp = null;
  state.hoveredPnlTimestamp = null;
  if (els.mainTooltip) {
    els.mainTooltip.classList.add("hidden");
  }
  if (els.pnlTooltip) {
    els.pnlTooltip.classList.add("hidden");
  }
}

function resetMainTimeRange() {
  const productData = getActiveProductData();
  if (!productData || !productData.rows.length) {
    return;
  }

  const firstTimestamp = productData.rows[0].timestamp;
  const lastTimestamp = productData.rows[productData.rows.length - 1].timestamp;
  els.timeMinInput.value = String(firstTimestamp);
  els.timeMaxInput.value = String(lastTimestamp);
}

function resetMiniTimeRange() {
  const productData = getActiveProductData();
  if (!productData || !productData.rows.length) {
    state.miniTimeRange = null;
    return;
  }

  state.miniTimeRange = {
    min: productData.rows[0].timestamp,
    max: productData.rows[productData.rows.length - 1].timestamp,
  };
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

function renderSyntheticLab() {
  if (!els.syntheticMethodCard) {
    return;
  }

  els.syntheticMethodCard.textContent = SYNTHETIC_METHOD_TEXT;

  if (!state.syntheticLab.generated) {
    els.syntheticDatasetSummary.textContent = "Resample the real Round 2 tape into new market scenarios.";
    els.syntheticChartSummary.textContent = "Generate a batch to begin.";
    els.syntheticLegendNote.textContent =
      "The lab draws a percentile band plus sample paths from the generated ensemble. Open a scenario in replay when you want to inspect the synthetic order book row by row.";
    els.syntheticSourceCard.textContent =
      "No synthetic batch yet.\n\nPick a source window, product, scenario count, horizon, and block length, then generate.";
    els.syntheticSelectedCard.textContent =
      "No scenario selected.\n\nOnce a batch is ready, this panel will summarize the chosen path, its spread profile, and trade activity.";
    els.syntheticSpreadNote.textContent = "Waiting for scenarios";
    els.syntheticActivityNote.textContent = "Waiting for scenarios";
    populateSyntheticScenarioSelect();
    renderSyntheticMainChart();
    renderSeriesChart(
      els.syntheticSpreadChart,
      buildSeriesChartView([]),
      COLORS.wallMid,
      "Generate scenarios to inspect synthetic spread regimes.",
    );
    renderSeriesChart(
      els.syntheticActivityChart,
      buildSeriesChartView([]),
      COLORS.tradeBuy,
      "Generate scenarios to inspect synthetic trade bursts.",
    );
    return;
  }

  const generated = state.syntheticLab.generated;
  const selectedScenario = getSelectedSyntheticScenario();
  const aggregate = generated.aggregate;

  els.syntheticDatasetSummary.textContent =
    `${formatInteger(generated.source.rowCount)} source rows, ${formatInteger(generated.source.tradeCount)} trades, ` +
    `${generated.source.label}, ${generated.source.product}`;
  els.syntheticChartSummary.textContent =
    `${formatInteger(generated.scenarios.length)} scenarios, ${formatInteger(generated.config.horizonRows)} rows each`;
  els.syntheticLegendNote.textContent =
    "Light lines are individual scenarios, the filled band is the 10th-90th percentile envelope, the dashed line is the median path, and the highlighted line is the selected scenario. This is meant for regime exploration, not for proving a fitted alpha.";

  const sourceLines = [
    `source: ${generated.source.label}`,
    `product: ${generated.source.product}`,
    `source rows: ${formatInteger(generated.source.rowCount)}`,
    `source trades: ${formatInteger(generated.source.tradeCount)}`,
    `base step: ${formatInteger(generated.source.stepSize)}`,
    `tick size: ${formatMaybe(generated.source.tickSize)}`,
    `anchor mid: ${formatMaybe(generated.source.anchorMid)}`,
    `scenario count: ${formatInteger(generated.scenarios.length)}`,
    `horizon rows: ${formatInteger(generated.config.horizonRows)}`,
    `block length: ${formatInteger(generated.config.blockLength)}`,
    `seed: ${formatInteger(generated.config.seed)}`,
    `median final mid: ${formatMaybe(aggregate.finalMidP50)}`,
    `10-90% final mid: ${formatMaybe(aggregate.finalMidP10)} -> ${formatMaybe(aggregate.finalMidP90)}`,
  ];
  els.syntheticSourceCard.textContent = sourceLines.join("\n");

  if (selectedScenario) {
    const lines = [
      `scenario: ${selectedScenario.label}`,
      `rows: ${formatInteger(selectedScenario.pricesRaw.length)}`,
      `trades: ${formatInteger(selectedScenario.tradeRowsRaw.length)}`,
      `trade qty: ${formatInteger(selectedScenario.stats.totalTradeQuantity)}`,
      `start mid: ${formatMaybe(selectedScenario.stats.startMid)}`,
      `end mid: ${formatMaybe(selectedScenario.stats.endMid)}`,
      `path change: ${formatMaybe(selectedScenario.stats.pathChange)}`,
      `realized vol (step): ${formatMaybe(selectedScenario.stats.realizedVol)}`,
      `avg spread: ${formatMaybe(selectedScenario.stats.avgSpread)}`,
      `max drawdown: ${formatMaybe(selectedScenario.stats.maxDrawdown)}`,
      `trades / 100 rows: ${formatMaybe(selectedScenario.stats.tradeDensityPer100)}`,
      `blocks used: ${formatInteger(selectedScenario.stats.blockCount)}`,
    ];
    els.syntheticSelectedCard.textContent = lines.join("\n");
  } else {
    els.syntheticSelectedCard.textContent = "No scenario selected.";
  }

  renderSyntheticMainChart();

  if (selectedScenario) {
    renderSeriesChart(
      els.syntheticSpreadChart,
      buildSeriesChartView(selectedScenario.spreadSeries),
      COLORS.wallMid,
      "No spread series available.",
    );
    renderSeriesChart(
      els.syntheticActivityChart,
      buildSeriesChartView(selectedScenario.activitySeries),
      COLORS.tradeBuy,
      "No synthetic trade activity available.",
    );
    els.syntheticSpreadNote.textContent =
      `${formatInteger(selectedScenario.spreadSeries.length)} points, avg ${formatMaybe(selectedScenario.stats.avgSpread)}`;
    els.syntheticActivityNote.textContent =
      `${formatInteger(selectedScenario.activitySeries.length)} points, ${formatInteger(selectedScenario.tradeRowsRaw.length)} trades`;
  }
}

function populateSyntheticScenarioSelect() {
  const generated = state.syntheticLab.generated;
  if (!generated || !generated.scenarios.length) {
    els.syntheticScenarioSelect.innerHTML = '<option value="">Generate scenarios first</option>';
    els.syntheticScenarioSelect.value = "";
    return;
  }

  els.syntheticScenarioSelect.innerHTML = generated.scenarios
    .map(
      (scenario) =>
        `<option value="${escapeHtml(scenario.id)}">${escapeHtml(
          `${scenario.label} | end ${formatMaybe(scenario.stats.endMid)} | dd ${formatMaybe(scenario.stats.maxDrawdown)}`,
        )}</option>`,
    )
    .join("");

  if (!generated.scenarios.some((scenario) => scenario.id === state.syntheticLab.selectedScenarioId)) {
    state.syntheticLab.selectedScenarioId = generated.scenarios[0].id;
  }
  els.syntheticScenarioSelect.value = state.syntheticLab.selectedScenarioId;
}

function getSelectedSyntheticScenario() {
  const generated = state.syntheticLab.generated;
  if (!generated) {
    return null;
  }

  const scenario = generated.scenarios.find(
    (candidate) => candidate.id === state.syntheticLab.selectedScenarioId,
  );
  return scenario || generated.scenarios[0] || null;
}

async function generateSyntheticScenarios() {
  syncSyntheticConfigFromInputs();

  try {
    setStatus("Generating synthetic scenarios...");
    const source = await loadSyntheticSourceBundle(
      state.syntheticLab.sourceKey,
      state.syntheticLab.product,
    );
    const config = {
      sourceKey: state.syntheticLab.sourceKey,
      product: state.syntheticLab.product,
      scenarioCount: state.syntheticLab.scenarioCount,
      horizonRows: state.syntheticLab.horizonRows,
      blockLength: Math.min(state.syntheticLab.blockLength, state.syntheticLab.horizonRows),
      seed: state.syntheticLab.seed,
    };
    const rng = createSeededRandom(config.seed);
    const scenarios = [];

    for (let scenarioIndex = 0; scenarioIndex < config.scenarioCount; scenarioIndex += 1) {
      scenarios.push(
        generateSyntheticScenario(source, {
          scenarioIndex,
          horizonRows: config.horizonRows,
          blockLength: config.blockLength,
          rng,
        }),
      );
    }

    state.syntheticLab.generated = {
      createdAt: Date.now(),
      config,
      source,
      scenarios,
      aggregate: buildSyntheticAggregate(scenarios),
    };
    state.syntheticLab.selectedScenarioId = scenarios[0]?.id || "";
    state.syntheticLab.hoveredTimestamp = null;
    populateSyntheticScenarioSelect();
    renderSyntheticLab();
    setStatus(
      `Generated ${formatInteger(config.scenarioCount)} synthetic ${config.product} scenarios from ${source.label}.`,
    );
  } catch (error) {
    console.error(error);
    clearSyntheticResults();
    renderSyntheticLab();
    setStatus(`Could not generate synthetic scenarios: ${error.message}`);
  }
}

function getSyntheticSourceOption(sourceKey) {
  return SYNTHETIC_SOURCE_OPTIONS.find((option) => option.key === sourceKey) || null;
}

async function loadSyntheticSourceBundle(sourceKey, product) {
  const cacheKey = `${sourceKey}::${product}`;
  if (state.syntheticSourceCache.has(cacheKey)) {
    return state.syntheticSourceCache.get(cacheKey);
  }

  const sourceOption = getSyntheticSourceOption(sourceKey);
  if (!sourceOption) {
    throw new Error("Synthetic source configuration not found.");
  }

  const datasets = await Promise.all(sourceOption.datasetKeys.map((key) => ensureDatasetLoaded(key)));
  const segments = [];
  const allRows = [];
  let tradeCount = 0;

  datasets.forEach((dataset) => {
    const productData = dataset?.products.get(product);
    if (!productData || !productData.rows.length) {
      return;
    }

    const segment = buildSyntheticSegment(dataset, productData);
    segments.push(segment);
    allRows.push(...productData.rows);
    tradeCount += productData.trades.length;
  });

  if (!segments.length) {
    throw new Error(`No source rows found for ${product}.`);
  }

  const mids = allRows.map((row) => row.midPrice).filter((value) => Number.isFinite(value)).sort((a, b) => a - b);
  const anchorMid = mids.length ? quantileSorted(mids, 0.5) : 0;

  const bundle = {
    key: cacheKey,
    label: sourceOption.label,
    product,
    segments,
    rowCount: allRows.length,
    tradeCount,
    stepSize: inferSyntheticStepSize(segments),
    tickSize: inferTickSize(allRows),
    anchorMid,
  };

  state.syntheticSourceCache.set(cacheKey, bundle);
  return bundle;
}

function buildSyntheticSegment(dataset, productData) {
  const tradesByTimestamp = groupBy(productData.trades, (trade) => trade.timestamp);
  const templates = productData.rows.map((row, index) =>
    buildSyntheticTemplate(
      productData.rows[index - 1] || null,
      row,
      tradesByTimestamp.get(row.timestamp) || [],
    ),
  );

  return {
    key: dataset.key,
    label: dataset.label,
    rows: productData.rows,
    templates,
  };
}

function buildSyntheticTemplate(previousRow, row, trades) {
  const centerPrice = Number.isFinite(row.midPrice) ? row.midPrice : row.wallMid;
  const previousMid = previousRow && Number.isFinite(previousRow.midPrice) ? previousRow.midPrice : centerPrice;
  const midDelta =
    Number.isFinite(centerPrice) && Number.isFinite(previousMid) ? centerPrice - previousMid : 0;

  return {
    product: row.product,
    midDelta,
    bids: row.bids.map((level) => ({
      level: level.level,
      offset: level.price - centerPrice,
      volume: level.volume,
    })),
    asks: row.asks.map((level) => ({
      level: level.level,
      offset: level.price - centerPrice,
      volume: level.volume,
    })),
    trades: trades.map((trade) => ({
      offset: trade.price - centerPrice,
      quantity: trade.quantity,
      currency: trade.currency || "XIRECS",
    })),
  };
}

function inferSyntheticStepSize(segments) {
  const diffs = [];
  segments.forEach((segment) => {
    for (let index = 1; index < segment.rows.length; index += 1) {
      const diff = segment.rows[index].timestamp - segment.rows[index - 1].timestamp;
      if (diff > 0) {
        diffs.push(diff);
      }
    }
  });

  if (!diffs.length) {
    return 100;
  }

  diffs.sort((left, right) => left - right);
  return quantileSorted(diffs, 0.5);
}

function inferTickSize(rows) {
  let best = Infinity;
  rows.forEach((row) => {
    const prices = [
      row.bestBid,
      row.bestAsk,
      row.wallBid,
      row.wallAsk,
      row.midPrice,
      row.wallMid,
    ].filter((value) => Number.isFinite(value));
    prices.forEach((price) => {
      const fractional = Math.abs(price - Math.round(price));
      if (fractional > 1e-6) {
        best = Math.min(best, fractional);
      }
    });
  });

  if (!Number.isFinite(best)) {
    return 1;
  }

  if (Math.abs(best - 0.5) < 1e-6) {
    return 0.5;
  }

  return best;
}

function generateSyntheticScenario(source, options) {
  const pricesRaw = [];
  const tradeRowsRaw = [];
  const midSeries = [];
  const spreadSeries = [];
  const activitySeries = [];
  let currentTimestamp = 0;
  let currentMid = source.anchorMid;
  let activeBlock = null;
  let offsetInBlock = 0;
  let blockCount = 0;

  for (let stepIndex = 0; stepIndex < options.horizonRows; stepIndex += 1) {
    if (!activeBlock || offsetInBlock >= activeBlock.length) {
      activeBlock = sampleSyntheticBlock(source, options.blockLength, options.rng);
      offsetInBlock = 0;
      blockCount += 1;
    }

    const template = activeBlock.segment.templates[activeBlock.startIndex + offsetInBlock];
    if (stepIndex > 0) {
      currentMid += template.midDelta;
    }

    const priceRowRaw = buildSyntheticPriceRowRaw(
      template,
      currentTimestamp,
      currentMid,
      source.tickSize,
    );
    const tradesAtStep = buildSyntheticTradeRowsRaw(
      template,
      currentTimestamp,
      currentMid,
      source.tickSize,
    );

    pricesRaw.push(priceRowRaw);
    tradeRowsRaw.push(...tradesAtStep);
    midSeries.push({ timestamp: currentTimestamp, value: toNumber(priceRowRaw.mid_price) ?? currentMid });
    spreadSeries.push({
      timestamp: currentTimestamp,
      value: (toNumber(priceRowRaw.ask_price_1) ?? NaN) - (toNumber(priceRowRaw.bid_price_1) ?? NaN),
    });
    activitySeries.push({
      timestamp: currentTimestamp,
      value: tradesAtStep.reduce((sum, trade) => sum + (trade.quantity || 0), 0),
    });

    currentTimestamp += source.stepSize;
    offsetInBlock += 1;
  }

  const stats = computeSyntheticScenarioStats({
    midSeries,
    spreadSeries,
    activitySeries,
    tradeRowsRaw,
    blockCount,
  });

  return {
    id: `synthetic-scenario-${options.scenarioIndex + 1}`,
    label: `Scenario ${options.scenarioIndex + 1}`,
    pricesRaw,
    tradeRowsRaw,
    midSeries,
    spreadSeries,
    activitySeries,
    stats,
  };
}

function sampleSyntheticBlock(source, blockLength, rng) {
  const segment = source.segments[Math.floor(rng() * source.segments.length)];
  const maxStart = Math.max(0, segment.templates.length - blockLength);
  const startIndex = maxStart > 0 ? Math.floor(rng() * (maxStart + 1)) : 0;
  const length = Math.min(blockLength, segment.templates.length - startIndex);

  return { segment, startIndex, length };
}

function buildSyntheticPriceRowRaw(template, timestamp, centerPrice, tickSize) {
  const bids = materializeSyntheticSide(template.bids, centerPrice, tickSize);
  const asks = materializeSyntheticSide(template.asks, centerPrice, tickSize);
  const safeTick = Math.max(tickSize || 1, 0.5);
  const bestBid = bids[0]?.price;
  const bestAsk = asks[0]?.price;

  if (Number.isFinite(bestBid) && Number.isFinite(bestAsk) && bestAsk <= bestBid) {
    asks[0].price = quantizePrice(bestBid + safeTick, safeTick);
  }

  const resolvedBestBid = bids[0]?.price;
  const resolvedBestAsk = asks[0]?.price;
  const resolvedMid =
    Number.isFinite(resolvedBestBid) && Number.isFinite(resolvedBestAsk)
      ? (resolvedBestBid + resolvedBestAsk) / 2
      : centerPrice;

  const row = {
    day: 1,
    timestamp,
    product: template.product,
    mid_price: quantizePrice(resolvedMid, safeTick / 2 || safeTick),
    profit_and_loss: 0,
  };

  for (let level = 1; level <= 3; level += 1) {
    const bid = bids[level - 1];
    const ask = asks[level - 1];
    row[`bid_price_${level}`] = bid ? bid.price : "";
    row[`bid_volume_${level}`] = bid ? bid.volume : "";
    row[`ask_price_${level}`] = ask ? ask.price : "";
    row[`ask_volume_${level}`] = ask ? ask.volume : "";
  }

  return row;
}

function materializeSyntheticSide(levels, centerPrice, tickSize) {
  return levels
    .map((level) => ({
      level: level.level,
      price: quantizePrice(centerPrice + level.offset, tickSize),
      volume: Math.max(1, Math.round(level.volume)),
    }))
    .filter((level) => Number.isFinite(level.price));
}

function buildSyntheticTradeRowsRaw(template, timestamp, centerPrice, tickSize) {
  return template.trades.map((trade) => ({
    timestamp,
    buyer: "",
    seller: "",
    symbol: template.product,
    currency: trade.currency || "XIRECS",
    price: quantizePrice(centerPrice + trade.offset, tickSize),
    quantity: Math.max(1, Math.round(trade.quantity)),
  }));
}

function quantizePrice(value, tickSize) {
  const safeTick = Number.isFinite(tickSize) && tickSize > 0 ? tickSize : 1;
  return Math.round(value / safeTick) * safeTick;
}

function computeSyntheticScenarioStats({ midSeries, spreadSeries, activitySeries, tradeRowsRaw, blockCount }) {
  const mids = midSeries.map((point) => point.value).filter((value) => Number.isFinite(value));
  const spreads = spreadSeries.map((point) => point.value).filter((value) => Number.isFinite(value));
  const deltas = [];
  for (let index = 1; index < mids.length; index += 1) {
    deltas.push(mids[index] - mids[index - 1]);
  }

  const tradeQuantity = activitySeries.reduce((sum, point) => sum + point.value, 0);

  return {
    startMid: mids[0] ?? NaN,
    endMid: mids[mids.length - 1] ?? NaN,
    pathChange:
      Number.isFinite(mids[0]) && Number.isFinite(mids[mids.length - 1])
        ? mids[mids.length - 1] - mids[0]
        : NaN,
    realizedVol: standardDeviation(deltas),
    avgSpread: mean(spreads),
    totalTradeQuantity: tradeQuantity,
    tradeDensityPer100: midSeries.length ? (tradeRowsRaw.length / midSeries.length) * 100 : NaN,
    maxDrawdown: computeMaxDrawdown(mids),
    blockCount,
  };
}

function computeMaxDrawdown(values) {
  let peak = -Infinity;
  let drawdown = 0;
  values.forEach((value) => {
    if (!Number.isFinite(value)) {
      return;
    }
    peak = Math.max(peak, value);
    drawdown = Math.max(drawdown, peak - value);
  });
  return drawdown;
}

function buildSyntheticAggregate(scenarios) {
  if (!scenarios.length) {
    return {
      bandSeries: [],
      finalMidP10: NaN,
      finalMidP50: NaN,
      finalMidP90: NaN,
    };
  }

  const bandSeries = scenarios[0].midSeries.map((point, index) => {
    const values = scenarios
      .map((scenario) => scenario.midSeries[index]?.value)
      .filter((value) => Number.isFinite(value))
      .sort((left, right) => left - right);

    return {
      timestamp: point.timestamp,
      p10: quantileSorted(values, 0.1),
      p50: quantileSorted(values, 0.5),
      p90: quantileSorted(values, 0.9),
    };
  });

  const finalMids = scenarios
    .map((scenario) => scenario.stats.endMid)
    .filter((value) => Number.isFinite(value))
    .sort((left, right) => left - right);

  return {
    bandSeries,
    finalMidP10: quantileSorted(finalMids, 0.1),
    finalMidP50: quantileSorted(finalMids, 0.5),
    finalMidP90: quantileSorted(finalMids, 0.9),
  };
}

function renderSyntheticMainChart() {
  const generated = state.syntheticLab.generated;
  const selectedScenario = getSelectedSyntheticScenario();
  const prepared = prepareCanvas(els.syntheticChart);
  const ctx = prepared.ctx;
  const width = prepared.width;
  const height = prepared.height;
  const plot = getSeriesChartPlot(width, height);

  ctx.clearRect(0, 0, width, height);

  if (!generated || !selectedScenario) {
    renderEmptyCanvasMessage(ctx, width, height, "Generate a synthetic batch to visualize the ensemble.");
    return;
  }

  const xMin = selectedScenario.midSeries[0]?.timestamp ?? 0;
  const xMax = selectedScenario.midSeries[selectedScenario.midSeries.length - 1]?.timestamp ?? 1;
  const yValues = [
    ...selectedScenario.midSeries.map((point) => point.value),
    ...generated.aggregate.bandSeries.flatMap((point) => [point.p10, point.p50, point.p90]),
  ].filter((value) => Number.isFinite(value));

  let yMin = Math.min(...yValues);
  let yMax = Math.max(...yValues);
  if (yMin === yMax) {
    yMin -= 1;
    yMax += 1;
  }
  const padding = Math.max((yMax - yMin) * 0.08, 1);
  yMin -= padding;
  yMax += padding;

  drawGridAndAxes(ctx, plot, xMin, xMax, yMin, yMax);

  ctx.save();
  clipToPlot(ctx, plot);

  generated.scenarios.forEach((scenario) => {
    if (scenario.id === selectedScenario.id) {
      return;
    }
    drawSimpleLine(
      ctx,
      scenario.midSeries,
      xMin,
      xMax,
      yMin,
      yMax,
      plot,
      "rgba(12, 92, 123, 0.08)",
      1.1,
    );
  });

  drawSyntheticBand(ctx, generated.aggregate.bandSeries, xMin, xMax, yMin, yMax, plot);
  drawSimpleLine(
    ctx,
    generated.aggregate.bandSeries.map((point) => ({ timestamp: point.timestamp, value: point.p50 })),
    xMin,
    xMax,
    yMin,
    yMax,
    plot,
    "rgba(25, 29, 36, 0.72)",
    1.6,
    [7, 6],
  );
  drawSimpleLine(
    ctx,
    selectedScenario.midSeries,
    xMin,
    xMax,
    yMin,
    yMax,
    plot,
    "rgba(12, 92, 123, 1)",
    2.4,
  );

  const hoveredPoint = getHoveredSyntheticPoint(selectedScenario);
  if (hoveredPoint) {
    const x = scale(hoveredPoint.timestamp, xMin, xMax, plot.left, plot.right);
    const y = scale(hoveredPoint.value, yMin, yMax, plot.bottom, plot.top);
    ctx.save();
    ctx.strokeStyle = "rgba(25, 29, 36, 0.48)";
    ctx.setLineDash([6, 6]);
    ctx.beginPath();
    ctx.moveTo(x, plot.top);
    ctx.lineTo(x, plot.bottom);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "rgba(12, 92, 123, 1)";
    ctx.strokeStyle = "rgba(255, 255, 255, 0.95)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(x, y, 5, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    ctx.restore();
  }

  ctx.restore();
}

function drawSimpleLine(ctx, series, xMin, xMax, yMin, yMax, plot, color, lineWidth, dash = []) {
  if (!series.length) {
    return;
  }

  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = lineWidth;
  if (dash.length) {
    ctx.setLineDash(dash);
  }
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

function drawSyntheticBand(ctx, bandSeries, xMin, xMax, yMin, yMax, plot) {
  if (!bandSeries.length) {
    return;
  }

  ctx.save();
  ctx.fillStyle = "rgba(12, 92, 123, 0.12)";
  ctx.beginPath();
  bandSeries.forEach((point, index) => {
    const x = scale(point.timestamp, xMin, xMax, plot.left, plot.right);
    const y = scale(point.p90, yMin, yMax, plot.bottom, plot.top);
    if (index === 0) {
      ctx.moveTo(x, y);
    } else {
      ctx.lineTo(x, y);
    }
  });
  for (let index = bandSeries.length - 1; index >= 0; index -= 1) {
    const point = bandSeries[index];
    const x = scale(point.timestamp, xMin, xMax, plot.left, plot.right);
    const y = scale(point.p10, yMin, yMax, plot.bottom, plot.top);
    ctx.lineTo(x, y);
  }
  ctx.closePath();
  ctx.fill();
  ctx.restore();
}

function getHoveredSyntheticPoint(selectedScenario) {
  if (!selectedScenario || state.syntheticLab.hoveredTimestamp == null) {
    return null;
  }

  return (
    selectedScenario.midSeries.find(
      (point) => point.timestamp === state.syntheticLab.hoveredTimestamp,
    ) || null
  );
}

function handleSyntheticChartHover(event) {
  const generated = state.syntheticLab.generated;
  const selectedScenario = getSelectedSyntheticScenario();
  if (!generated || !selectedScenario || !selectedScenario.midSeries.length) {
    return;
  }

  const pointer = getSeriesChartPointer(event, els.syntheticChart);
  if (!isPointerInsidePlot(pointer)) {
    state.syntheticLab.hoveredTimestamp = null;
    els.syntheticTooltip.classList.add("hidden");
    renderSyntheticMainChart();
    return;
  }

  const xMin = selectedScenario.midSeries[0].timestamp;
  const xMax = selectedScenario.midSeries[selectedScenario.midSeries.length - 1].timestamp;
  const timestamp = scale(pointer.rawX, pointer.plot.left, pointer.plot.right, xMin, xMax);
  const point = findNearestRow(selectedScenario.midSeries, timestamp);
  if (!point) {
    return;
  }

  state.syntheticLab.hoveredTimestamp = point.timestamp;
  renderSyntheticMainChart();
  showSyntheticTooltip(event, generated, selectedScenario, point);
}

function showSyntheticTooltip(event, generated, selectedScenario, point) {
  const bandPoint =
    generated.aggregate.bandSeries.find((candidate) => candidate.timestamp === point.timestamp) || null;
  const spreadPoint =
    selectedScenario.spreadSeries.find((candidate) => candidate.timestamp === point.timestamp) || null;
  const activityPoint =
    selectedScenario.activitySeries.find((candidate) => candidate.timestamp === point.timestamp) || null;
  const tradesAtTimestamp = selectedScenario.tradeRowsRaw.filter(
    (trade) => trade.timestamp === point.timestamp,
  ).length;

  const lines = [
    `t=${formatInteger(point.timestamp)}`,
    `selected mid=${formatMaybe(point.value)}`,
    `band p10-p90=${formatMaybe(bandPoint?.p10)} -> ${formatMaybe(bandPoint?.p90)}`,
    `spread=${formatMaybe(spreadPoint?.value)}`,
    `trade qty=${formatMaybe(activityPoint?.value)} trades=${formatInteger(tradesAtTimestamp)}`,
  ];

  els.syntheticTooltip.textContent = lines.join("\n");
  els.syntheticTooltip.classList.remove("hidden");

  const wrapperRect = els.syntheticChartWrapper.getBoundingClientRect();
  const maxLeft = Math.max(10, wrapperRect.width - 280);
  const maxTop = Math.max(10, wrapperRect.height - 110);
  const left = clampNumber(event.clientX - wrapperRect.left + 10, 10, maxLeft);
  const top = clampNumber(event.clientY - wrapperRect.top + 10, 10, maxTop);
  els.syntheticTooltip.style.left = `${left}px`;
  els.syntheticTooltip.style.top = `${top}px`;
}

async function loadSelectedSyntheticScenarioIntoReplay() {
  const generated = state.syntheticLab.generated;
  const selectedScenario = getSelectedSyntheticScenario();
  if (!generated || !selectedScenario) {
    setStatus("Generate and select a synthetic scenario first.");
    return;
  }

  const datasetKey = `synthetic-${generated.config.product.toLowerCase()}-${generated.config.seed}-${Date.now()}`;
  const label = `Synthetic / ${generated.source.product} / ${selectedScenario.label} / seed ${generated.config.seed}`;
  const dataset = buildDataset({
    key: datasetKey,
    label,
    priceRowsRaw: selectedScenario.pricesRaw,
    tradeRowsRaw: selectedScenario.tradeRowsRaw,
    indicatorRowsRaw: [],
    logRowsRaw: [],
  });

  state.datasets.set(datasetKey, dataset);
  state.activeDatasetKey = datasetKey;
  state.selectedProduct = generated.source.product;
  state.strategyOverlay = null;
  if (els.uploadStrategyTradesInput) {
    els.uploadStrategyTradesInput.value = "";
  }
  populateDatasetSelect();
  els.datasetSelect.value = datasetKey;
  switchDashboardTab("replay");
  await loadDataset(datasetKey);
  setStatus(`Loaded ${selectedScenario.label} into replay as a synthetic dataset.`);
}

function downloadSelectedSyntheticScenario(kind) {
  const generated = state.syntheticLab.generated;
  const selectedScenario = getSelectedSyntheticScenario();
  if (!generated || !selectedScenario) {
    setStatus("Generate and select a synthetic scenario first.");
    return;
  }

  if (kind === "prices") {
    const filename = `synthetic_prices_${generated.source.product.toLowerCase()}_${selectedScenario.label.replaceAll(" ", "_").toLowerCase()}.csv`;
    downloadTextFile(filename, serializeSyntheticPrices(selectedScenario.pricesRaw));
    setStatus(`Downloaded price tape for ${selectedScenario.label}.`);
    return;
  }

  const filename = `synthetic_trades_${generated.source.product.toLowerCase()}_${selectedScenario.label.replaceAll(" ", "_").toLowerCase()}.csv`;
  downloadTextFile(filename, serializeSyntheticTrades(selectedScenario.tradeRowsRaw));
  setStatus(`Downloaded trade tape for ${selectedScenario.label}.`);
}

function serializeSyntheticPrices(rows) {
  const headers = [
    "day",
    "timestamp",
    "product",
    "bid_price_1",
    "bid_volume_1",
    "bid_price_2",
    "bid_volume_2",
    "bid_price_3",
    "bid_volume_3",
    "ask_price_1",
    "ask_volume_1",
    "ask_price_2",
    "ask_volume_2",
    "ask_price_3",
    "ask_volume_3",
    "mid_price",
    "profit_and_loss",
  ];
  return serializeDelimitedRows(rows, headers, ";");
}

function serializeSyntheticTrades(rows) {
  const headers = ["timestamp", "buyer", "seller", "symbol", "currency", "price", "quantity"];
  return serializeDelimitedRows(rows, headers, ";");
}

function serializeDelimitedRows(rows, headers, delimiter) {
  const lines = [headers.join(delimiter)];
  rows.forEach((row) => {
    const values = headers.map((header) => serializeCell(row[header]));
    lines.push(values.join(delimiter));
  });
  return lines.join("\n");
}

function serializeCell(value) {
  if (value == null) {
    return "";
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return String(value);
  }
  const stringValue = String(value);
  if (stringValue.includes(";") || stringValue.includes(",") || stringValue.includes('"')) {
    return `"${stringValue.replaceAll('"', '""')}"`;
  }
  return stringValue;
}

function downloadTextFile(filename, text) {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

function renderEmptyCanvasMessage(ctx, width, height, message) {
  ctx.save();
  ctx.fillStyle = COLORS.axis;
  ctx.font = "13px Menlo, Consolas, monospace";
  ctx.fillText(message, 18, height / 2);
  ctx.restore();
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
  const exclusiveBookHighlightBands = buildExclusiveBookHighlightBands(rawRows);
  const noBidsRowCount = exclusiveBookHighlightBands.filter((band) => band.state === "noBids").length;
  const noAsksRowCount = exclusiveBookHighlightBands.filter((band) => band.state === "noAsks").length;
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

  let { min: autoYMin, max: autoYMax } = computeRobustPlotRange(yValues);
  if (autoYMin === autoYMax) {
    autoYMin -= 1;
    autoYMax += 1;
  }

  const padding = Math.max((autoYMax - autoYMin) * 0.08, 1);
  autoYMin -= padding;
  autoYMax += padding;

  const yOffset = clampVerticalOffset(state.mainChartYOffset, autoYMax - autoYMin);
  const yMin = autoYMin + yOffset;
  const yMax = autoYMax + yOffset;

  return {
    dataset,
    productData,
    strategyProduct,
    fullMin,
    fullMax,
    rangeMin,
    rangeMax,
    filteredRows,
    exclusiveBookHighlightBands,
    oneSidedRowCount: exclusiveBookHighlightBands.length,
    noBidsRowCount,
    noAsksRowCount,
    visibleTrades,
    visibleStrategyTrades,
    selectedLevels,
    indicatorSeries,
    autoYMin,
    autoYMax,
    yMin,
    yMax,
  };
}

function renderMainChart(view = buildView(getActiveDataset(), getActiveProductData())) {
  const prepared = prepareCanvas(els.mainChart);
  const ctx = prepared.ctx;
  const width = prepared.width;
  const height = prepared.height;
  const plot = getChartPlot(width, height);

  ctx.clearRect(0, 0, width, height);
  drawGridAndAxes(ctx, plot, view.rangeMin, view.rangeMax, view.yMin, view.yMax);

  const xScale = (timestamp) => scale(timestamp, view.rangeMin, view.rangeMax, plot.left, plot.right);
  const yScale = (price) => scale(price, view.yMin, view.yMax, plot.bottom, plot.top);

  ctx.save();
  clipToPlot(ctx, plot);

  if (state.showOneSidedHighlights) {
    drawExclusiveBookHighlightBands(ctx, view.exclusiveBookHighlightBands, plot, xScale);
  }

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

  if (state.zoomDrag) {
    drawZoomSelection(ctx, plot, state.zoomDrag.startX, state.zoomDrag.currentX);
  }

  ctx.restore();
}

function renderMiniCharts(view) {
  const pnlSeries = buildPnlSeries(view.productData, view.strategyProduct);
  const positionSeries = buildPositionSeries(view.productData, view.strategyProduct);
  const miniRange = getMiniVisibleTimeRange(view);
  const pnlChartView = buildSeriesChartView(pnlSeries, {
    xMin: miniRange.min,
    xMax: miniRange.max,
    yOffset: state.pnlChartYOffset,
  });
  const positionChartView = buildSeriesChartView(positionSeries, {
    xMin: miniRange.min,
    xMax: miniRange.max,
  });
  const hoveredPnlPoint = getHoveredSeriesPoint(pnlChartView, state.hoveredPnlTimestamp);

  if (state.hoveredPnlTimestamp != null && !hoveredPnlPoint) {
    state.hoveredPnlTimestamp = null;
    els.pnlTooltip.classList.add("hidden");
  }

  renderSeriesChart(
    els.pnlChart,
    pnlChartView,
    COLORS.pnl,
    "No backtest PnL available yet. Load a backtest overlay or own trades to populate this panel.",
    { hoveredPoint: hoveredPnlPoint },
  );

  renderSeriesChart(
    els.positionChart,
    positionChartView,
    COLORS.position,
    "No strategy position data yet. Load a backtest overlay or set your trader ID in the trade export.",
  );

  if (pnlSeries.length) {
    const windowLabel = pnlChartView.visibleSeries.length
      ? `${formatInteger(pnlChartView.visibleSeries.length)} visible`
      : "No visible points";
    els.pnlNote.textContent = `${formatInteger(pnlSeries.length)} points, ${windowLabel}`;
  } else {
    els.pnlNote.textContent = "Waiting for strategy data";
  }

  if (positionSeries.length) {
    const windowLabel = positionChartView.visibleSeries.length
      ? `${formatInteger(positionChartView.visibleSeries.length)} visible`
      : "No visible points";
    els.positionNote.textContent = `${formatInteger(positionSeries.length)} points, ${windowLabel}`;
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
    `one-sided rows in range: ${formatInteger(view.oneSidedRowCount)} (bid-only ${formatInteger(view.noAsksRowCount)}, ask-only ${formatInteger(view.noBidsRowCount)})`,
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
    `book state: ${describeBookAvailabilityState(getBookAvailabilityState(row))}`,
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
  if (state.zoomDrag) {
    updateZoomDrag(event);
    return;
  }
  if (state.mainPanDrag) {
    updateMainPanDrag(event);
    return;
  }

  const view = buildView(getActiveDataset(), getActiveProductData());
  if (!view.filteredRows.length) {
    return;
  }

  const pointer = getChartPointer(event);
  if (!isPointerInsidePlot(pointer)) {
    state.hoveredTimestamp = null;
    els.mainTooltip.classList.add("hidden");
    updateSnapshotAndLogCards(view, view.filteredRows[0] || null);
    renderMainChart(view);
    return;
  }

  const timestamp = scale(pointer.rawX, pointer.plot.left, pointer.plot.right, view.rangeMin, view.rangeMax);
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
    `book=${describeBookAvailabilityState(getBookAvailabilityState(row))}`,
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

function handleChartMouseDown(event) {
  if (event.button !== 0) {
    return;
  }

  const view = buildView(getActiveDataset(), getActiveProductData());
  if (!view.filteredRows.length) {
    return;
  }

  const pointer = getChartPointer(event);
  if (!isPointerInsidePlot(pointer)) {
    return;
  }

  state.hoveredTimestamp = null;
  state.hoveredPnlTimestamp = null;
  els.mainTooltip.classList.add("hidden");
  els.pnlTooltip.classList.add("hidden");

  if (event.shiftKey) {
    state.zoomDrag = {
      startX: pointer.clampedX,
      currentX: pointer.clampedX,
    };
    els.mainChartWrapper.classList.add("is-zooming");
    renderMainChart(view);
  } else {
    state.mainPanDrag = {
      startX: pointer.clampedX,
      startY: pointer.clampedY,
      initialRangeMin: view.rangeMin,
      initialRangeMax: view.rangeMax,
      initialYSpan: view.yMax - view.yMin,
      initialYOffset: state.mainChartYOffset,
      fullMin: view.fullMin,
      fullMax: view.fullMax,
      productData: view.productData,
      plot: pointer.plot,
    };
    els.mainChartWrapper.classList.add("is-panning");
  }

  event.preventDefault();
}

function handleChartDragMove(event) {
  if (state.zoomDrag) {
    updateZoomDrag(event);
    return;
  }

  if (state.mainPanDrag) {
    updateMainPanDrag(event);
    return;
  }

  if (state.pnlPanDrag) {
    updatePnlPanDrag(event);
  }
}

function handleChartMouseUp(event) {
  if (state.zoomDrag) {
    const view = buildView(getActiveDataset(), getActiveProductData());
    updateZoomDrag(event);

    const pointer = getChartPointer(event);
    const startX = state.zoomDrag.startX;
    const endX = pointer.clampedX;
    const pixelWidth = Math.abs(endX - startX);

    state.zoomDrag = null;
    els.mainChartWrapper.classList.remove("is-zooming");

    if (pixelWidth < 8) {
      renderMainChart(view);
      return;
    }

    const dragMinX = Math.min(startX, endX);
    const dragMaxX = Math.max(startX, endX);
    const nextMin = scale(dragMinX, pointer.plot.left, pointer.plot.right, view.rangeMin, view.rangeMax);
    const nextMax = scale(dragMaxX, pointer.plot.left, pointer.plot.right, view.rangeMin, view.rangeMax);
    setVisibleTimeRange(nextMin, nextMax, view);
    state.mainChartYOffset = 0;
    state.hoveredTimestamp = null;
    state.hoveredPnlTimestamp = null;
    els.pnlTooltip.classList.add("hidden");
    renderAll();
    setStatus(`Zoomed to ${formatInteger(nextMin)} -> ${formatInteger(nextMax)}.`);
    return;
  }

  if (state.mainPanDrag) {
    const drag = state.mainPanDrag;
    updateMainPanDrag(event);
    state.mainPanDrag = null;
    els.mainChartWrapper.classList.remove("is-panning");

    const pointer = getChartPointer(event);
    const movement = Math.abs(pointer.clampedX - drag.startX) + Math.abs(pointer.clampedY - drag.startY);
    if (movement < 4) {
      handleChartHover(event);
    }
    return;
  }

  if (state.pnlPanDrag) {
    const drag = state.pnlPanDrag;
    updatePnlPanDrag(event);
    state.pnlPanDrag = null;
    els.pnlChartWrapper.classList.remove("is-panning");

    const pointer = getSeriesChartPointer(event, els.pnlChart);
    const movement = Math.abs(pointer.clampedX - drag.startX) + Math.abs(pointer.clampedY - drag.startY);
    if (movement < 4) {
      handlePnlChartHover(event);
    }
  }
}

function handleChartWheel(event) {
  const view = buildView(getActiveDataset(), getActiveProductData());
  if (!view.filteredRows.length) {
    return;
  }

  const pointer = getChartPointer(event);
  if (!isPointerInsidePlot(pointer)) {
    return;
  }

  event.preventDefault();
  zoomTimeRangeAtPointer(view, pointer, event.deltaY);
  state.mainChartYOffset = 0;
  state.hoveredTimestamp = null;
  state.hoveredPnlTimestamp = null;
  els.mainTooltip.classList.add("hidden");
  els.pnlTooltip.classList.add("hidden");
  renderAll();
}

function handleChartDoubleClick() {
  state.zoomDrag = null;
  state.mainPanDrag = null;
  resetMainTimeRange();
  state.mainChartYOffset = 0;
  state.hoveredTimestamp = null;
  state.hoveredPnlTimestamp = null;
  els.mainChartWrapper.classList.remove("is-zooming");
  els.mainChartWrapper.classList.remove("is-panning");
  els.mainTooltip.classList.add("hidden");
  els.pnlTooltip.classList.add("hidden");
  renderAll();
  setStatus("Reset chart zoom.");
}

function updateZoomDrag(event) {
  if (!state.zoomDrag) {
    return;
  }

  const pointer = getChartPointer(event);
  state.zoomDrag.currentX = pointer.clampedX;
  els.mainTooltip.classList.add("hidden");
  renderMainChart(buildView(getActiveDataset(), getActiveProductData()));
}

function updateMainPanDrag(event) {
  if (!state.mainPanDrag) {
    return;
  }

  const drag = state.mainPanDrag;
  const pointer = getChartPointer(event);
  const plotWidth = Math.max(1, drag.plot.right - drag.plot.left);
  const plotHeight = Math.max(1, drag.plot.bottom - drag.plot.top);
  const xSpan = drag.initialRangeMax - drag.initialRangeMin;
  const ySpan = drag.initialYSpan;
  const deltaX = pointer.clampedX - drag.startX;
  const deltaY = pointer.clampedY - drag.startY;
  const shiftedRange = buildShiftedTimeRange(
    drag.initialRangeMin,
    drag.initialRangeMax,
    -(deltaX / plotWidth) * xSpan,
    drag.fullMin,
    drag.fullMax,
  );

  setVisibleTimeRange(shiftedRange.min, shiftedRange.max, drag);
  state.mainChartYOffset = drag.initialYOffset + (deltaY / plotHeight) * ySpan;
  renderAll();
}

function handlePnlChartHover(event) {
  if (state.pnlPanDrag) {
    updatePnlPanDrag(event);
    return;
  }

  const chartContext = buildPnlChartContext();
  if (!chartContext) {
    return;
  }
  if (!chartContext.chartView.visibleSeries.length) {
    state.hoveredPnlTimestamp = null;
    els.pnlTooltip.classList.add("hidden");
    renderMiniCharts(chartContext.view);
    return;
  }

  const pointer = getSeriesChartPointer(event, els.pnlChart);
  if (!isPointerInsidePlot(pointer)) {
    state.hoveredPnlTimestamp = null;
    els.pnlTooltip.classList.add("hidden");
    renderMiniCharts(chartContext.view);
    return;
  }

  const timestamp = scale(
    pointer.rawX,
    pointer.plot.left,
    pointer.plot.right,
    chartContext.chartView.xMin,
    chartContext.chartView.xMax,
  );
  const point = findNearestRow(chartContext.chartView.visibleSeries, timestamp);
  if (!point) {
    return;
  }

  state.hoveredPnlTimestamp = point.timestamp;
  renderMiniCharts(chartContext.view);
  showPnlTooltip(event, point);
}

function showPnlTooltip(event, point) {
  const lines = [
    `t=${formatInteger(point.timestamp)}`,
    `profit=${formatMaybe(point.value)}`,
  ];

  els.pnlTooltip.textContent = lines.join("\n");
  els.pnlTooltip.classList.remove("hidden");

  const wrapperRect = els.pnlChartWrapper.getBoundingClientRect();
  const maxLeft = Math.max(10, wrapperRect.width - 220);
  const maxTop = Math.max(10, wrapperRect.height - 96);
  const left = clampNumber(event.clientX - wrapperRect.left + 10, 10, maxLeft);
  const top = clampNumber(event.clientY - wrapperRect.top + 10, 10, maxTop);
  els.pnlTooltip.style.left = `${left}px`;
  els.pnlTooltip.style.top = `${top}px`;
}

function handlePnlChartMouseDown(event) {
  if (event.button !== 0) {
    return;
  }

  const chartContext = buildPnlChartContext();
  if (!chartContext || !chartContext.chartView.series.length) {
    return;
  }

  const pointer = getSeriesChartPointer(event, els.pnlChart);
  if (!isPointerInsidePlot(pointer)) {
    return;
  }

  state.hoveredPnlTimestamp = null;
  state.hoveredTimestamp = null;
  els.mainTooltip.classList.add("hidden");
  els.pnlTooltip.classList.add("hidden");
  state.pnlPanDrag = {
    startX: pointer.clampedX,
    startY: pointer.clampedY,
    initialRangeMin: chartContext.chartView.xMin,
    initialRangeMax: chartContext.chartView.xMax,
    initialYSpan: chartContext.chartView.yMax - chartContext.chartView.yMin,
    initialYOffset: state.pnlChartYOffset,
    fullMin: chartContext.view.fullMin,
    fullMax: chartContext.view.fullMax,
    productData: chartContext.view.productData,
    plot: pointer.plot,
  };
  els.pnlChartWrapper.classList.add("is-panning");
  event.preventDefault();
}

function updatePnlPanDrag(event) {
  if (!state.pnlPanDrag) {
    return;
  }

  const drag = state.pnlPanDrag;
  const pointer = getSeriesChartPointer(event, els.pnlChart);
  const plotWidth = Math.max(1, drag.plot.right - drag.plot.left);
  const plotHeight = Math.max(1, drag.plot.bottom - drag.plot.top);
  const xSpan = drag.initialRangeMax - drag.initialRangeMin;
  const ySpan = drag.initialYSpan;
  const deltaX = pointer.clampedX - drag.startX;
  const deltaY = pointer.clampedY - drag.startY;
  const shiftedRange = buildShiftedTimeRange(
    drag.initialRangeMin,
    drag.initialRangeMax,
    -(deltaX / plotWidth) * xSpan,
    drag.fullMin,
    drag.fullMax,
  );

  setVisibleTimeRange(shiftedRange.min, shiftedRange.max, drag, "mini");
  state.pnlChartYOffset = drag.initialYOffset + (deltaY / plotHeight) * ySpan;
  renderAll();
}

function handlePnlChartWheel(event) {
  const chartContext = buildPnlChartContext();
  if (!chartContext || !chartContext.chartView.series.length) {
    return;
  }

  const pointer = getSeriesChartPointer(event, els.pnlChart);
  if (!isPointerInsidePlot(pointer)) {
    return;
  }

  event.preventDefault();
  zoomTimeRangeAtPointer(chartContext.view, pointer, event.deltaY, {
    rangeMin: chartContext.chartView.xMin,
    rangeMax: chartContext.chartView.xMax,
    target: "mini",
  });
  state.pnlChartYOffset = 0;
  state.hoveredPnlTimestamp = null;
  state.hoveredTimestamp = null;
  els.mainTooltip.classList.add("hidden");
  els.pnlTooltip.classList.add("hidden");
  renderAll();
}

function handlePnlChartDoubleClick() {
  state.pnlPanDrag = null;
  resetMiniTimeRange();
  state.pnlChartYOffset = 0;
  state.hoveredPnlTimestamp = null;
  state.hoveredTimestamp = null;
  els.pnlChartWrapper.classList.remove("is-panning");
  els.mainTooltip.classList.add("hidden");
  els.pnlTooltip.classList.add("hidden");
  renderAll();
  setStatus("Reset PnL view.");
}

function getChartPointer(event) {
  const rect = els.mainChart.getBoundingClientRect();
  const plot = getChartPlot(rect.width, rect.height);
  const rawX = event.clientX - rect.left;
  const rawY = event.clientY - rect.top;

  return {
    plot,
    rawX,
    rawY,
    clampedX: clampNumber(rawX, plot.left, plot.right),
    clampedY: clampNumber(rawY, plot.top, plot.bottom),
  };
}

function getChartPlot(width, height) {
  return { left: 66, top: 20, right: width - 24, bottom: height - 38 };
}

function getSeriesChartPointer(event, canvas) {
  const rect = canvas.getBoundingClientRect();
  const plot = getSeriesChartPlot(rect.width, rect.height);
  const rawX = event.clientX - rect.left;
  const rawY = event.clientY - rect.top;

  return {
    plot,
    rawX,
    rawY,
    clampedX: clampNumber(rawX, plot.left, plot.right),
    clampedY: clampNumber(rawY, plot.top, plot.bottom),
  };
}

function getSeriesChartPlot(width, height) {
  return { left: 62, top: 20, right: width - 24, bottom: height - 36 };
}

function isPointerInsidePlot(pointer) {
  return (
    pointer.rawX >= pointer.plot.left &&
    pointer.rawX <= pointer.plot.right &&
    pointer.rawY >= pointer.plot.top &&
    pointer.rawY <= pointer.plot.bottom
  );
}

function buildPnlChartContext() {
  const dataset = getActiveDataset();
  const productData = getActiveProductData();
  if (!dataset || !productData) {
    return null;
  }

  const view = buildView(dataset, productData);
  const miniRange = getMiniVisibleTimeRange(view);
  const series = buildPnlSeries(view.productData, view.strategyProduct);
  const chartView = buildSeriesChartView(series, {
    xMin: miniRange.min,
    xMax: miniRange.max,
    yOffset: state.pnlChartYOffset,
  });

  return { view, chartView };
}

function getMiniVisibleTimeRange(view) {
  let rangeMin = state.miniTimeRange?.min;
  let rangeMax = state.miniTimeRange?.max;

  rangeMin = rangeMin == null ? view.fullMin : clampNumber(rangeMin, view.fullMin, view.fullMax);
  rangeMax = rangeMax == null ? view.fullMax : clampNumber(rangeMax, view.fullMin, view.fullMax);

  if (rangeMin > rangeMax) {
    [rangeMin, rangeMax] = [rangeMax, rangeMin];
  }

  return { min: rangeMin, max: rangeMax };
}

function buildShiftedTimeRange(rangeMin, rangeMax, delta, fullMin, fullMax) {
  const span = rangeMax - rangeMin;
  if (span >= fullMax - fullMin) {
    return { min: fullMin, max: fullMax };
  }

  let nextMin = rangeMin + delta;
  let nextMax = rangeMax + delta;

  if (nextMin < fullMin) {
    nextMax += fullMin - nextMin;
    nextMin = fullMin;
  }
  if (nextMax > fullMax) {
    nextMin -= nextMax - fullMax;
    nextMax = fullMax;
  }

  return { min: nextMin, max: nextMax };
}

function zoomTimeRangeAtPointer(view, pointer, deltaY, options = {}) {
  const rangeMin = options.rangeMin ?? view.rangeMin;
  const rangeMax = options.rangeMax ?? view.rangeMax;
  const target = options.target ?? "main";
  const fullSpan = view.fullMax - view.fullMin;
  const currentSpan = rangeMax - rangeMin;
  const minimumSpan = Math.max(getBaseTimeStep(view.productData.rows) * 8, 200);
  const zoomFactor = deltaY < 0 ? 0.82 : 1.22;
  const nextSpan = clampNumber(currentSpan * zoomFactor, minimumSpan, fullSpan);

  if (Math.abs(nextSpan - currentSpan) < 1) {
    return;
  }

  const pivotTimestamp = scale(
    pointer.clampedX,
    pointer.plot.left,
    pointer.plot.right,
    rangeMin,
    rangeMax,
  );
  const pivotRatio = (pointer.clampedX - pointer.plot.left) / Math.max(1, pointer.plot.right - pointer.plot.left);

  let nextMin = pivotTimestamp - pivotRatio * nextSpan;
  let nextMax = nextMin + nextSpan;

  if (nextMin < view.fullMin) {
    nextMax += view.fullMin - nextMin;
    nextMin = view.fullMin;
  }
  if (nextMax > view.fullMax) {
    nextMin -= nextMax - view.fullMax;
    nextMax = view.fullMax;
  }

  setVisibleTimeRange(nextMin, nextMax, view, target);
}

function drawZoomSelection(ctx, plot, startX, currentX) {
  const left = Math.min(startX, currentX);
  const right = Math.max(startX, currentX);
  const width = Math.max(1, right - left);

  ctx.save();
  ctx.fillStyle = "rgba(12, 92, 123, 0.16)";
  ctx.strokeStyle = "rgba(12, 92, 123, 0.72)";
  ctx.lineWidth = 1.5;
  ctx.fillRect(left, plot.top, width, plot.bottom - plot.top);
  ctx.strokeRect(left, plot.top, width, plot.bottom - plot.top);
  ctx.restore();
}

function setVisibleTimeRange(rawMin, rawMax, view, target = "main") {
  const step = getBaseTimeStep(view.productData.rows);
  const minimumSpan = Math.max(step * 4, 200);
  let nextMin = snapToStep(rawMin, step);
  let nextMax = snapToStep(rawMax, step);

  nextMin = clampNumber(nextMin, view.fullMin, view.fullMax);
  nextMax = clampNumber(nextMax, view.fullMin, view.fullMax);

  if (nextMax <= nextMin) {
    nextMax = Math.min(view.fullMax, nextMin + minimumSpan);
  }

  if (nextMax - nextMin < minimumSpan) {
    const midpoint = (nextMin + nextMax) / 2;
    nextMin = midpoint - minimumSpan / 2;
    nextMax = midpoint + minimumSpan / 2;
  }

  if (nextMin < view.fullMin) {
    nextMax += view.fullMin - nextMin;
    nextMin = view.fullMin;
  }
  if (nextMax > view.fullMax) {
    nextMin -= nextMax - view.fullMax;
    nextMax = view.fullMax;
  }

  nextMin = clampNumber(snapToStep(nextMin, step), view.fullMin, view.fullMax);
  nextMax = clampNumber(snapToStep(nextMax, step), view.fullMin, view.fullMax);

  if (nextMax <= nextMin) {
    nextMax = Math.min(view.fullMax, nextMin + minimumSpan);
  }

  if (target === "mini") {
    state.miniTimeRange = {
      min: Math.round(nextMin),
      max: Math.round(nextMax),
    };
    return;
  }

  els.timeMinInput.value = String(Math.round(nextMin));
  els.timeMaxInput.value = String(Math.round(nextMax));
}

function getBaseTimeStep(rows) {
  for (let index = 1; index < rows.length; index += 1) {
    const diff = rows[index].timestamp - rows[index - 1].timestamp;
    if (diff > 0) {
      return diff;
    }
  }
  return 100;
}

function snapToStep(value, step) {
  if (!Number.isFinite(value) || !Number.isFinite(step) || step <= 0) {
    return value;
  }
  return Math.round(value / step) * step;
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

function drawExclusiveBookHighlightBands(ctx, bands, plot, xScale) {
  if (!bands.length) {
    return;
  }

  ctx.save();
  bands.forEach((band) => {
    const left = xScale(band.startTimestamp);
    const right = xScale(band.endTimestamp);
    const width = Math.max(1, right - left);
    ctx.fillStyle = band.state === "noAsks" ? COLORS.noAsksBand : COLORS.noBidsBand;
    ctx.fillRect(left, plot.top, width, plot.bottom - plot.top);
    ctx.strokeStyle = band.state === "noAsks" ? COLORS.noAsksStroke : COLORS.noBidsStroke;
    ctx.lineWidth = 1;
    ctx.strokeRect(left, plot.top, width, plot.bottom - plot.top);
  });
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

  if (view.noAsksRowCount) {
    items.push(
      buildLegendItem(
        drawLegendBand(COLORS.noAsksBand, COLORS.noAsksStroke),
        "Bid-only snapshot (no asks)",
        !state.showOneSidedHighlights,
      ),
    );
  }

  if (view.noBidsRowCount) {
    items.push(
      buildLegendItem(
        drawLegendBand(COLORS.noBidsBand, COLORS.noBidsStroke),
        "Ask-only snapshot (no bids)",
        !state.showOneSidedHighlights,
      ),
    );
  }

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
  let oneSidedLabel = "One-sided snapshot highlighting is turned off.";
  if (state.showOneSidedHighlights) {
    oneSidedLabel = view.oneSidedRowCount
      ? "Blue bands mark bid-only snapshots with no asks, and red bands mark ask-only snapshots with no bids."
      : "No one-sided snapshots are visible in the current window.";
  }

  let overlayLabel = "No backtest overlay loaded.";
  if (state.strategyOverlay) {
    overlayLabel = `Backtest overlay loaded: ${state.strategyOverlay.label}. Strategy fills are drawn last so they sit on top of the market plot.`;
  }

  els.legendNote.textContent = `${normalizationLabel} Visible book levels: ${levelsLabel}. ${oneSidedLabel} Quote dot size scales with quoted volume. Market trade direction is inferred from price vs. the current book unless the trade matches one of your trader IDs. Drag to pan, hold Shift while dragging to zoom into a window, use the mouse wheel to zoom, zoom actions recenter vertically, isolated price spikes are de-emphasized in auto-fit until you zoom close to them, and double-click to reset. ${overlayLabel}`;
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

function drawLegendBand(fillColor, strokeColor) {
  return `<svg width="20" height="16" viewBox="0 0 20 16" aria-hidden="true"><rect x="4" y="1.5" width="12" height="13" rx="2" fill="${fillColor}" stroke="${strokeColor}" stroke-width="1.2"></rect></svg>`;
}

function drawLegendSquare(color) {
  return `<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><rect x="3" y="3" width="10" height="10" fill="${color}"></rect></svg>`;
}

function drawLegendDiamond(color) {
  return `<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><polygon points="8,1 15,8 8,15 1,8" fill="${color}" stroke="rgba(25, 29, 36, 0.95)" stroke-width="1.25"></polygon></svg>`;
}

function buildSeriesChartView(series, options = {}) {
  if (!series.length) {
    return {
      series,
      visibleSeries: [],
      fullMin: 0,
      fullMax: 0,
      xMin: 0,
      xMax: 1,
      autoYMin: -1,
      autoYMax: 1,
      yMin: -1,
      yMax: 1,
    };
  }

  const fullMin = series[0].timestamp;
  const fullMax = series[series.length - 1].timestamp;
  let xMin = options.xMin == null ? fullMin : options.xMin;
  let xMax = options.xMax == null ? fullMax : options.xMax;

  if (xMin > xMax) {
    [xMin, xMax] = [xMax, xMin];
  }

  const visibleSeries = series.filter((point) => point.timestamp >= xMin && point.timestamp <= xMax);
  const ySource = visibleSeries.length ? visibleSeries : series;
  const values = ySource.map((point) => point.value);
  let autoYMin = Math.min(...values);
  let autoYMax = Math.max(...values);
  if (autoYMin === autoYMax) {
    autoYMin -= 1;
    autoYMax += 1;
  }

  const yPadding = Math.max((autoYMax - autoYMin) * 0.1, 1);
  autoYMin -= yPadding;
  autoYMax += yPadding;

  const yOffset = clampVerticalOffset(options.yOffset ?? 0, autoYMax - autoYMin);
  const yMin = autoYMin + yOffset;
  const yMax = autoYMax + yOffset;

  return {
    series,
    visibleSeries,
    fullMin,
    fullMax,
    xMin,
    xMax,
    autoYMin,
    autoYMax,
    yMin,
    yMax,
  };
}

function getHoveredSeriesPoint(chartView, hoveredTimestamp) {
  if (hoveredTimestamp == null || !chartView.visibleSeries.length) {
    return null;
  }

  return chartView.visibleSeries.find((point) => point.timestamp === hoveredTimestamp) || null;
}

function renderSeriesChart(canvas, chartView, color, emptyMessage, options = {}) {
  const prepared = prepareCanvas(canvas);
  const ctx = prepared.ctx;
  const width = prepared.width;
  const height = prepared.height;
  const plot = getSeriesChartPlot(width, height);

  ctx.clearRect(0, 0, width, height);

  if (!chartView.series.length) {
    ctx.fillStyle = COLORS.axis;
    ctx.font = "13px Menlo, Consolas, monospace";
    ctx.fillText(emptyMessage, 18, height / 2);
    return;
  }

  drawGridAndAxes(ctx, plot, chartView.xMin, chartView.xMax, chartView.yMin, chartView.yMax);

  if (!chartView.visibleSeries.length) {
    ctx.save();
    ctx.fillStyle = COLORS.axis;
    ctx.font = "13px Menlo, Consolas, monospace";
    ctx.fillText("No points in the visible time window.", 18, height / 2);
    ctx.restore();
    return;
  }

  ctx.save();
  clipToPlot(ctx, plot);

  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.beginPath();
  chartView.visibleSeries.forEach((point, index) => {
    const x = scale(point.timestamp, chartView.xMin, chartView.xMax, plot.left, plot.right);
    const y = scale(point.value, chartView.yMin, chartView.yMax, plot.bottom, plot.top);
    if (index === 0) {
      ctx.moveTo(x, y);
    } else {
      ctx.lineTo(x, y);
    }
  });
  ctx.stroke();
  ctx.restore();

  if (options.hoveredPoint) {
    const x = scale(options.hoveredPoint.timestamp, chartView.xMin, chartView.xMax, plot.left, plot.right);
    const y = scale(options.hoveredPoint.value, chartView.yMin, chartView.yMax, plot.bottom, plot.top);
    ctx.save();
    ctx.strokeStyle = "rgba(25, 29, 36, 0.48)";
    ctx.setLineDash([6, 6]);
    ctx.beginPath();
    ctx.moveTo(x, plot.top);
    ctx.lineTo(x, plot.bottom);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = color;
    ctx.strokeStyle = "rgba(255, 255, 255, 0.95)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(x, y, 4.5, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    ctx.restore();
  }

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
  if (state.hoveredTimestamp == null) {
    return null;
  }

  return view.filteredRows.find((row) => row.timestamp === state.hoveredTimestamp) || null;
}

function getBookAvailabilityState(row) {
  const hasBids = Boolean(row?.bids?.length);
  const hasAsks = Boolean(row?.asks?.length);
  if (hasBids && hasAsks) {
    return "twoSided";
  }
  if (hasBids) {
    return "noAsks";
  }
  if (hasAsks) {
    return "noBids";
  }
  return "empty";
}

function describeBookAvailabilityState(state) {
  switch (state) {
    case "noAsks":
      return "bid-only (no asks)";
    case "noBids":
      return "ask-only (no bids)";
    case "empty":
      return "empty (no bids or asks)";
    default:
      return "two-sided";
  }
}

function buildExclusiveBookHighlightBands(rows) {
  if (!rows.length) {
    return [];
  }

  const baseStep = getBaseTimeStep(rows);
  const bands = [];
  rows.forEach((row, index) => {
    const stateLabel = getBookAvailabilityState(row);
    if (stateLabel !== "noAsks" && stateLabel !== "noBids") {
      return;
    }

    const previousTimestamp = rows[index - 1]?.timestamp;
    const nextTimestamp = rows[index + 1]?.timestamp;
    bands.push({
      state: stateLabel,
      startTimestamp: Number.isFinite(previousTimestamp)
        ? (previousTimestamp + row.timestamp) / 2
        : row.timestamp - baseStep / 2,
      endTimestamp: Number.isFinite(nextTimestamp)
        ? (row.timestamp + nextTimestamp) / 2
        : row.timestamp + baseStep / 2,
    });
  });

  return bands;
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

function mean(values) {
  const finiteValues = values.filter((value) => Number.isFinite(value));
  if (!finiteValues.length) {
    return NaN;
  }
  return finiteValues.reduce((sum, value) => sum + value, 0) / finiteValues.length;
}

function standardDeviation(values) {
  const finiteValues = values.filter((value) => Number.isFinite(value));
  if (finiteValues.length < 2) {
    return 0;
  }
  const average = mean(finiteValues);
  const variance =
    finiteValues.reduce((sum, value) => sum + (value - average) ** 2, 0) / finiteValues.length;
  return Math.sqrt(variance);
}

function readIntegerInput(input, fallback, min, max) {
  const parsed = Number.parseInt(input?.value ?? "", 10);
  const safeValue = Number.isFinite(parsed) ? parsed : fallback;
  return clampNumber(safeValue, min, max);
}

function createSeededRandom(seed) {
  let stateValue = (Math.trunc(seed) >>> 0) || 1;
  return () => {
    stateValue += 0x6d2b79f5;
    let t = stateValue;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
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

function clipToPlot(ctx, plot) {
  ctx.beginPath();
  ctx.rect(plot.left, plot.top, plot.right - plot.left, plot.bottom - plot.top);
  ctx.clip();
}

function computeRobustPlotRange(values) {
  const finiteValues = values.filter((value) => Number.isFinite(value)).sort((left, right) => left - right);
  if (!finiteValues.length) {
    return { min: -1, max: 1 };
  }

  const fullMin = finiteValues[0];
  const fullMax = finiteValues[finiteValues.length - 1];
  if (finiteValues.length < 60) {
    return { min: fullMin, max: fullMax };
  }

  const trimmedMin = quantileSorted(finiteValues, 0.01);
  const trimmedMax = quantileSorted(finiteValues, 0.99);
  const fullSpan = Math.max(fullMax - fullMin, 1e-9);
  const trimmedSpan = Math.max(trimmedMax - trimmedMin, 1e-9);

  if (fullSpan > trimmedSpan * 2.5) {
    return { min: trimmedMin, max: trimmedMax };
  }

  return { min: fullMin, max: fullMax };
}

function quantileSorted(sortedValues, ratio) {
  if (!sortedValues.length) {
    return NaN;
  }

  const clampedRatio = clampNumber(ratio, 0, 1);
  const rawIndex = (sortedValues.length - 1) * clampedRatio;
  const lowerIndex = Math.floor(rawIndex);
  const upperIndex = Math.ceil(rawIndex);
  const lowerValue = sortedValues[lowerIndex];
  const upperValue = sortedValues[upperIndex];

  if (lowerIndex === upperIndex) {
    return lowerValue;
  }

  const weight = rawIndex - lowerIndex;
  return lowerValue + (upperValue - lowerValue) * weight;
}

function clampVerticalOffset(offset, span) {
  const safeSpan = Math.max(Math.abs(span), 1);
  const limit = safeSpan * 2;
  return clampNumber(offset, -limit, limit);
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
