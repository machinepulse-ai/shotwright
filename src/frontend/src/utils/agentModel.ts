import { AgentProvider, CopilotModelOption } from "../types";

export type AgentModelDescriptor = {
  runtimeLabel: string;
  modelLabel: string;
  brandLabel: string;
  submodelLabel: string;
  combinedLabel: string;
  toneClass: string;
};

function titleSegment(segment: string) {
  if (!segment) return segment;
  const lowered = segment.toLowerCase();
  if (["gpt", "api", "llm"].includes(lowered)) return lowered.toUpperCase();
  if (["mini", "nano", "flash", "pro", "max"].includes(lowered)) return lowered;
  return `${segment.charAt(0).toUpperCase()}${segment.slice(1)}`;
}

export function getAgentRuntimeLabel(provider: AgentProvider | string | null | undefined) {
  const normalized = (provider || "").trim().toLowerCase();
  if (normalized === "codex") return "Codex";
  if (normalized === "copilot") return "Copilot";
  return normalized ? titleSegment(normalized) : "Agent";
}

export function formatSessionModelLabel(model: string | null | undefined) {
  const normalized = (model || "").trim();
  if (!normalized) return "Unknown";

  if (/^gpt[-_]/i.test(normalized)) {
    return normalized.replace(/^gpt[-_]/i, "GPT-").replace(/-mini$/i, " mini");
  }

  return normalized
    .split(/[-_\s]+/)
    .filter(Boolean)
    .map((segment) => {
      if (/^claude$/i.test(segment)) return "Claude";
      if (/^gemini$/i.test(segment)) return "Gemini";
      if (/^qwen$/i.test(segment)) return "Qwen";
      return titleSegment(segment);
    })
    .join(" ");
}

export function getSessionModelToneClass(model: string | null | undefined) {
  const normalized = (model || "").trim().toLowerCase();
  if (normalized.includes("gpt-5.4-mini")) return "tone-gpt-54-mini";
  if (normalized.includes("gpt-5.4")) return "tone-gpt-54";
  if (normalized.startsWith("gpt") || normalized.includes("openai")) return "tone-gpt";
  if (normalized.includes("claude") && normalized.includes("haiku")) return "tone-claude-haiku";
  if (normalized.includes("claude") && normalized.includes("sonnet")) return "tone-claude-sonnet";
  if (normalized.includes("claude") || normalized.includes("anthropic")) return "tone-claude";
  if (normalized.includes("gemini") || normalized.includes("google")) return "tone-gemini";
  if (normalized.includes("qwen")) return "tone-qwen";
  return "tone-neutral";
}

export function inferModelBrand(model: string | null | undefined, modelProvider?: string | null) {
  const normalized = `${model || ""} ${modelProvider || ""}`.toLowerCase();
  if (normalized.includes("claude") || normalized.includes("anthropic")) return "Claude";
  if (normalized.includes("gemini") || normalized.includes("google")) return "Gemini";
  if (normalized.includes("qwen") || normalized.includes("dashscope")) return "Qwen";
  if (normalized.includes("gpt") || normalized.includes("openai") || /\bo[34]\b/.test(normalized)) return "GPT";
  return "Model";
}

export function inferModelSubmodel(model: string | null | undefined, brand?: string | null) {
  const label = formatSessionModelLabel(model);
  const normalizedBrand = (brand || inferModelBrand(model)).trim();
  if (normalizedBrand && label.toLowerCase().startsWith(normalizedBrand.toLowerCase())) {
    const submodel = label.slice(normalizedBrand.length).trim().replace(/^[-\s]+/, "");
    return submodel || label;
  }
  return label;
}

export function getAgentModelDescriptor(
  provider: AgentProvider | string | null | undefined,
  model: string | null | undefined,
  option?: CopilotModelOption | null,
): AgentModelDescriptor {
  const runtimeLabel = getAgentRuntimeLabel(provider ?? option?.provider);
  const modelSource = option?.display_name || option?.name || model || "";
  const modelLabel = formatSessionModelLabel(modelSource);
  const brandLabel = option?.brand || inferModelBrand(modelSource || model, option?.model_provider);
  const submodelLabel = option?.submodel || inferModelSubmodel(modelSource || model, brandLabel);

  return {
    runtimeLabel,
    modelLabel,
    brandLabel,
    submodelLabel,
    combinedLabel: `${runtimeLabel} · ${modelLabel}`,
    toneClass: getSessionModelToneClass(modelSource || model),
  };
}

export function formatAgentModelLabel(
  provider: AgentProvider | string | null | undefined,
  model: string | null | undefined,
  option?: CopilotModelOption | null,
) {
  return getAgentModelDescriptor(provider, model, option).combinedLabel;
}

export function formatModelOptionLabel(option: CopilotModelOption) {
  return option.display_name || formatSessionModelLabel(option.name || option.id);
}
