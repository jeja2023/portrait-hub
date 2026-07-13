#!/usr/bin/env node

const { PortraitHubClient } = require("../../sdk/node/portraitHubClient");

const DEFAULT_BASE_URL = "http://127.0.0.1:9001";

function argValue(name, fallback = null) {
  const index = process.argv.indexOf(name);
  if (index === -1 || index + 1 >= process.argv.length) return fallback;
  return process.argv[index + 1];
}

function hasFlag(name) {
  return process.argv.includes(name);
}

function buildClient() {
  return new PortraitHubClient({
    baseUrl: process.env.PORTRAIT_HUB_BASE_URL || DEFAULT_BASE_URL,
    tenantId: process.env.PORTRAIT_HUB_TENANT_ID || "tenant-b",
    apiToken: process.env.PORTRAIT_HUB_API_TOKEN || null,
    authScheme: process.env.PORTRAIT_HUB_AUTH_SCHEME || "api_key",
  });
}

function summarize(step, payload) {
  const data = payload && typeof payload.data === "object" && payload.data !== null ? payload.data : null;
  return {
    step,
    status: payload?.status ?? null,
    request_id: payload?.request_id ?? null,
    data_keys: data ? Object.keys(data).sort() : [],
  };
}

async function runDemo() {
  const client = buildClient();
  const image = argValue("--image");
  const imageB = argValue("--image-b");
  const video = argValue("--video");
  const personId = argValue("--person-id", "demo-node-person");
  const topK = Number(argValue("--top-k", "5"));
  const thresholdProfile = argValue("--threshold-profile", "normal");
  const frameInterval = Number(argValue("--frame-interval", "15"));
  const maxFrames = Number(argValue("--max-frames", "64"));
  const planned = ["health", "models", "thresholds"];
  if (image) planned.push("enroll", "search");
  if (image && imageB) planned.push("comparePersons");
  if (video) planned.push("createVideoJob");

  if (hasFlag("--dry-run")) {
    return {
      ok: true,
      dry_run: true,
      base_url: client.baseUrl,
      tenant_id: client.tenantId,
      auth_scheme: client.authScheme,
      planned_steps: planned,
    };
  }

  const steps = [];
  steps.push(summarize("health", await client.health()));
  steps.push(summarize("models", await client.models()));
  steps.push(summarize("thresholds", await client.thresholds()));
  if (image) {
    steps.push(summarize("enroll", await client.enroll(personId, [image], "body")));
    steps.push(summarize("search", await client.search(image, "body", topK)));
  }
  if (image && imageB) {
    steps.push(summarize("comparePersons", await client.comparePersons(image, imageB, thresholdProfile)));
  }
  if (video) {
    steps.push(summarize("createVideoJob", await client.createVideoJob(video, { frameInterval, maxFrames })));
  }
  return { ok: true, dry_run: false, steps };
}

runDemo()
  .then((payload) => console.log(JSON.stringify(payload, null, 2)))
  .catch((error) => {
    console.error(JSON.stringify({ ok: false, error: error.message || String(error) }, null, 2));
    process.exit(1);
  });