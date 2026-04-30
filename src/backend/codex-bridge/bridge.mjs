#!/usr/bin/env node

import { Codex } from "@openai/codex-sdk";
import process from "node:process";

function writeRecord(record) {
  process.stdout.write(`${JSON.stringify(record)}\n`);
}

function toObject(value, fallback = {}) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : fallback;
}

async function readRequest() {
  let payload = "";
  process.stdin.setEncoding("utf8");
  for await (const chunk of process.stdin) {
    payload += chunk;
  }

  const trimmedPayload = payload.trim();
  if (!trimmedPayload) {
    throw new Error("Codex bridge request was empty.");
  }

  const request = JSON.parse(trimmedPayload);
  if (!request || typeof request !== "object" || Array.isArray(request)) {
    throw new Error("Codex bridge request must be a JSON object.");
  }
  return request;
}

function normalizeInput(input) {
  if (typeof input === "string") {
    return input;
  }

  if (!Array.isArray(input) || !input.length) {
    throw new Error("Codex bridge request input must be a string or non-empty input array.");
  }

  return input.map((item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      throw new Error("Codex bridge input array items must be objects.");
    }

    if (item.type === "text") {
      const text = typeof item.text === "string" ? item.text : "";
      return { type: "text", text };
    }

    if (item.type === "local_image") {
      if (typeof item.path !== "string" || !item.path.trim()) {
        throw new Error("Codex bridge local_image input requires a path.");
      }
      return { type: "local_image", path: item.path };
    }

    throw new Error(`Unsupported Codex bridge input item type: ${String(item.type)}`);
  });
}

function buildCodexOptions(request) {
  const options = {};

  if (typeof request.codex_path_override === "string" && request.codex_path_override.trim()) {
    options.codexPathOverride = request.codex_path_override.trim();
  }
  if (typeof request.base_url === "string" && request.base_url.trim()) {
    options.baseUrl = request.base_url.trim();
  }
  if (typeof request.api_key === "string" && request.api_key.trim()) {
    options.apiKey = request.api_key.trim();
  }
  if (request.config && typeof request.config === "object" && !Array.isArray(request.config)) {
    options.config = request.config;
  }
  if (request.env && typeof request.env === "object" && !Array.isArray(request.env)) {
    options.env = Object.fromEntries(
      Object.entries(request.env)
        .filter(([key, value]) => typeof key === "string" && typeof value === "string")
    );
  }

  return options;
}

function buildThreadOptions(request) {
  const options = {};

  const stringFields = [
    ["model", "model"],
    ["sandbox_mode", "sandboxMode"],
    ["approval_policy", "approvalPolicy"],
    ["web_search_mode", "webSearchMode"],
  ];
  for (const [sourceKey, targetKey] of stringFields) {
    if (typeof request[sourceKey] === "string" && request[sourceKey].trim()) {
      options[targetKey] = request[sourceKey].trim();
    }
  }

  if (typeof request.model_reasoning_effort === "string" && request.model_reasoning_effort.trim()) {
    options.modelReasoningEffort = request.model_reasoning_effort.trim();
  }
  if (typeof request.working_directory === "string" && request.working_directory.trim()) {
    options.workingDirectory = request.working_directory.trim();
  }
  if (typeof request.skip_git_repo_check === "boolean") {
    options.skipGitRepoCheck = request.skip_git_repo_check;
  }
  if (typeof request.network_access_enabled === "boolean") {
    options.networkAccessEnabled = request.network_access_enabled;
  }
  if (typeof request.web_search_enabled === "boolean") {
    options.webSearchEnabled = request.web_search_enabled;
  }
  if (Array.isArray(request.additional_directories)) {
    options.additionalDirectories = request.additional_directories
      .filter((value) => typeof value === "string" && value.trim())
      .map((value) => value.trim());
  }

  return options;
}

function buildTurnOptions(request, signal) {
  const turnOptions = { signal };
  if (request.output_schema) {
    turnOptions.outputSchema = request.output_schema;
  }
  return turnOptions;
}

function updateTurnResult(event, result) {
  if (event.type === "thread.started" && typeof event.thread_id === "string") {
    result.threadId = event.thread_id;
    return;
  }

  if (event.type === "turn.completed") {
    result.usage = event.usage || null;
    result.completed = true;
    return;
  }

  if ((event.type === "item.completed" || event.type === "item.updated") && event.item?.type === "agent_message") {
    result.finalResponse = typeof event.item.text === "string" ? event.item.text : result.finalResponse;
    return;
  }

  if (event.type === "turn.failed") {
    result.fatalError = event.error?.message || "Codex turn failed.";
    return;
  }

  if (event.type === "error") {
    result.lastStreamError = event.message || "Codex bridge stream failed.";
  }
}

async function runTurn(request) {
  const abortController = new AbortController();
  const abort = () => abortController.abort();
  process.once("SIGINT", abort);
  process.once("SIGTERM", abort);

  const codex = new Codex(buildCodexOptions(request));
  const threadOptions = buildThreadOptions(request);
  const threadId = typeof request.thread_id === "string" && request.thread_id.trim()
    ? request.thread_id.trim()
    : "";
  const thread = threadId ? codex.resumeThread(threadId, threadOptions) : codex.startThread(threadOptions);
  const result = {
    threadId: thread.id || threadId || null,
    finalResponse: "",
    usage: null,
    completed: false,
    fatalError: null,
    lastStreamError: null,
  };

  const streamedTurn = await thread.runStreamed(
    normalizeInput(request.input),
    buildTurnOptions(request, abortController.signal)
  );

  for await (const event of streamedTurn.events) {
    updateTurnResult(event, result);
    writeRecord({ type: "event", event });
  }

  result.threadId = thread.id || result.threadId;
  if (result.fatalError) {
    throw new Error(result.fatalError);
  }

  if (!result.completed && !result.finalResponse && result.lastStreamError) {
    throw new Error(result.lastStreamError);
  }

  writeRecord({
    type: "complete",
    thread_id: result.threadId,
    final_response: result.finalResponse,
    usage: result.usage,
  });
}

async function main() {
  const request = await readRequest();
  const command = typeof request.command === "string" ? request.command : "run_turn";

  if (command === "health") {
    writeRecord({ type: "complete", ok: true });
    return;
  }

  if (command !== "run_turn") {
    throw new Error(`Unsupported Codex bridge command: ${command}`);
  }

  await runTurn(toObject(request));
}

main().catch((error) => {
  writeRecord({
    type: "error",
    message: error instanceof Error ? error.message : String(error),
    stack: error instanceof Error ? error.stack : undefined,
  });
  process.exitCode = 1;
});
