const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..", "..");
const FRONTEND_ROOT = path.resolve(ROOT, "src", "frontend");
const { chromium } = require(require.resolve("playwright", { paths: [FRONTEND_ROOT] }));

const DEFAULT_VIEWPORT = { width: 1728, height: 1117 };
const EDGE_CANDIDATES = [
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
];

async function probeUrl(url) {
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 1500);
    const response = await fetch(url, { method: "GET", signal: controller.signal });
    clearTimeout(timeout);
    return response.ok;
  } catch {
    return false;
  }
}

async function resolveBaseUrl() {
  if (process.env.SHOTWRIGHT_BASE_URL) {
    return process.env.SHOTWRIGHT_BASE_URL;
  }

  const candidates = ["http://127.0.0.1:3100", "http://127.0.0.1:3000"];
  for (const candidate of candidates) {
    if (await probeUrl(candidate)) {
      return candidate;
    }
  }

  throw new Error("Unable to reach the Shotwright UI. Set SHOTWRIGHT_BASE_URL to the running frontend URL.");
}

function resolveLaunchOptions() {
  const explicitBrowserPath = process.env.SHOTWRIGHT_BROWSER_PATH;
  if (explicitBrowserPath) {
    return { headless: true, executablePath: explicitBrowserPath };
  }

  const edgePath = EDGE_CANDIDATES.find((candidate) => fs.existsSync(candidate));
  return edgePath ? { headless: true, executablePath: edgePath } : { headless: true };
}

function ensureDirectory(filePath) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
}

async function openWorkbench(options = {}) {
  const resolvedBaseUrl = options.baseUrl || (await resolveBaseUrl());
  const browser = await chromium.launch(resolveLaunchOptions());
  const page = await browser.newPage({
    viewport: options.viewport || DEFAULT_VIEWPORT,
    deviceScaleFactor: 1,
  });

  if (options.beforeGoto) {
    await options.beforeGoto(page, resolvedBaseUrl);
  }

  await page.goto(resolvedBaseUrl, { waitUntil: "networkidle" });
  await page.waitForSelector(".workbench");

  return { browser, page, baseUrl: resolvedBaseUrl };
}

async function captureScreenshot(page, relativePath, options = {}) {
  const absolutePath = path.resolve(ROOT, relativePath);
  ensureDirectory(absolutePath);
  await page.screenshot({
    path: absolutePath,
    fullPage: options.fullPage ?? true,
  });
  return absolutePath;
}

function captureRequested() {
  const value = (process.env.SHOTWRIGHT_UI_CAPTURE || "").toLowerCase();
  return value === "1" || value === "true" || value === "yes";
}

module.exports = {
  ROOT,
  captureRequested,
  captureScreenshot,
  openWorkbench,
  resolveBaseUrl,
};