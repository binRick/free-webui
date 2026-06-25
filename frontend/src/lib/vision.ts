// Best-effort detection of whether a model can accept image input. Used to warn
// before sending an image to a text-only model — Ollama silently turns the image
// into an "[img-0]" placeholder the model can't read, which is confusing.
//
// This is a heuristic by model-id family. It is deliberately used only to WARN
// (never to block), so a vision model we don't recognise is never locked out —
// the worst case is a missing warning, not a disabled feature.

const VISION_PATTERNS: RegExp[] = [
  /vision/, //            llama3.2-vision, granite3.2-vision, …
  /llava|bakllava/, //    llava, llava-llama3, llava-phi3, bakllava
  /moondream/,
  /minicpm-?v/, //        minicpm-v
  /qwen[\d.]*-?vl/, //    qwen2-vl, qwen2.5-vl, qwen2.5vl, qwenvl
  /pixtral/,
  /cogvlm/,
  /internvl/,
  /glm-?4v/, //           glm-4v / glm4v
  /mistral-small3\.?1/, // mistral-small3.1 is multimodal
  /llama-?4/, //          Llama 4 is natively multimodal
  // hosted providers
  /gpt-4o|gpt-4\.1|gpt-4-turbo|gpt-4-vision|chatgpt-4o/,
  /claude-3|claude-opus|claude-sonnet|claude-[45]/,
  /gemini/
];

export function modelSupportsVision(model: string | null | undefined): boolean {
  if (!model) return false;
  const id = model.toLowerCase();
  // Gemma 3 is multimodal at 4b/12b/27b, but the 1b and 270m variants are text-only.
  if (/gemma-?3/.test(id)) return !/[:-](1b|270m)\b/.test(id);
  return VISION_PATTERNS.some((re) => re.test(id));
}
