// Opt-in CCR gateway compatibility transforms. See docs/compatibility.md.

const DEFAULTS = {
  providers: [],
  placeholder: '[image omitted]',
  binaryPlaceholder: '[{type} omitted{details}]',
  maxInlinePayloadBytes: 65_536,
  maxTextPayloadBytes: 131_072,
};
const BINARY_TYPES = new Set(['image', 'document', 'audio', 'video', 'file', 'container_upload']);
const BINARY_FIELD_NAMES = new Set(['data', 'file_data', 'base64', 'bytes', 'blob', 'content_bytes']);
const DATA_URL_RE = /^data:[^,]*;base64,/i;
const ROUTE_WARNING_INTERVAL_MS = 300_000;
const EMBEDDED_BINARY_MIN_RUN_CHARS = 2048;
const EMBEDDED_BINARY_SCAN_WINDOW_CHARS = 8192;
const EMBEDDED_BINARY_SCAN_STEP_CHARS = 4096;
const EMBEDDED_BINARY_FIELD_PADDING_CHARS = 64;
const TEXT_BINARY_FIELD_NAMES = ['"data"', "'data'", '"file_data"', "'file_data'", '"base64"', "'base64'", '"bytes"', "'bytes'", '"blob"', "'blob'", '"content_bytes"', "'content_bytes'"];
const TRANSFORM_IDS = ['strip-tool-images', 'fix-partial-tool-rounds'];

function routeInfo(input) {
  const route = input?.routedModel;
  if (route == null || route === '') return { issue: 'missingRoutedModel' };
  if (typeof route !== 'string' || !/^[a-zA-Z0-9_.-]+\/[a-zA-Z0-9_.:-]+$/.test(route)) {
    return { issue: 'malformedRoutedModel' };
  }
  return { provider: route.slice(0, route.indexOf('/')).toLowerCase() };
}

function list(value, field) {
  if (value == null) return [];
  const values = typeof value === 'string' ? value.split(',') : value;
  if (!Array.isArray(values) || values.some((v) => typeof v !== 'string')) throw new TypeError(`Invalid ${field}`);
  return values.map((v) => v.trim()).filter(Boolean);
}

function configuration(value) {
  if (value != null && (typeof value !== 'object' || Array.isArray(value))) throw new TypeError('Invalid pluginConfig');
  const cfg = { ...DEFAULTS, ...value };
  cfg.providers = list(cfg.providers, 'providers').map((p) => p.toLowerCase());
  if (cfg.providers.some((p) => !/^[a-z0-9_.-]+$/.test(p))) throw new TypeError('Invalid providers');
  const transforms = list(cfg.transforms, 'transforms');
  if (transforms.some((id) => !TRANSFORM_IDS.includes(id))) throw new TypeError('Invalid transforms');
  for (const key of ['maxInlinePayloadBytes', 'maxTextPayloadBytes']) {
    if (!Number.isSafeInteger(cfg[key]) || cfg[key] < 1 || cfg[key] > 16_777_216) throw new TypeError(`Invalid ${key}`);
  }
  for (const key of ['placeholder', 'binaryPlaceholder']) {
    if (typeof cfg[key] !== 'string' || cfg[key].length > 512 || /[\x00-\x1f\x7f]/.test(cfg[key])) throw new TypeError(`Invalid ${key}`);
  }
  return { cfg, transforms: new Set(transforms.length ? transforms : TRANSFORM_IDS) };
}

function byteLength(value) {
  return Buffer.byteLength(String(value ?? ""), "utf8");
}

function looksLikeLargeBase64(value, minBytes) {
  if (typeof value !== "string" || byteLength(value) < minBytes) return false;
  if (DATA_URL_RE.test(value)) return true;
  return hasLongBase64Run(value, 0, minBytes);
}

function containsLargeBinaryField(value, minBytes, seen = new Set()) {
  if (!value || typeof value !== "object") return false;
  if (seen.has(value)) return false;
  seen.add(value);
  if (Array.isArray(value)) {
    return value.some((item) => {
      if (typeof item === "string") return textContainsLargeBinary(item, minBytes);
      return containsLargeBinaryField(item, minBytes, seen);
    });
  }
  for (const [key, child] of Object.entries(value)) {
    if (typeof child === "string") {
      if (
        (BINARY_FIELD_NAMES.has(String(key).toLowerCase()) && looksLikeLargeBase64(child, minBytes)) ||
        textContainsLargeBinary(child, minBytes)
      ) {
        return true;
      }
      continue;
    }
    if (child && typeof child === "object" && containsLargeBinaryField(child, minBytes, seen)) {
      return true;
    }
  }
  return false;
}

function hasLongBase64Run(value, start, minChars, end = value.length) {
  let run = 0;
  const limit = Math.min(value.length, Math.max(0, end));
  for (let i = Math.max(0, start); i < limit; i++) {
    const code = value.charCodeAt(i);
    const isBase64 =
      (code >= 48 && code <= 57) ||
      (code >= 65 && code <= 90) ||
      (code >= 97 && code <= 122) ||
      code === 43 ||
      code === 45 ||
      code === 47 ||
      code === 61 ||
      code === 95;
    if (isBase64) {
      run++;
      if (run >= minChars) return true;
      continue;
    }
    if (code === 10 || code === 13) continue;
    if (code === 92 && i + 1 < value.length && "nr".includes(value[i + 1].toLowerCase())) {
      i++;
      continue;
    }
    run = 0;
  }
  return false;
}

function textContainsLargeBinary(text, maxBytes) {
  if (typeof text !== "string" || byteLength(text) <= maxBytes) return false;
  const trimmed = text.trim();
  if (looksLikeLargeBase64(trimmed, maxBytes)) return true;

  let lower = text.toLowerCase();
  if (lower.length !== text.length) {
    lower = text.replace(/[A-Z]/g, (char) => char.toLowerCase());
  }
  let dataOffset = 0;
  while (dataOffset < lower.length) {
    const dataUrl = lower.indexOf(";base64,", dataOffset);
    if (dataUrl < 0) break;
    let valueStart = dataUrl + 8;
    while (valueStart < text.length && /\s/.test(text[valueStart])) valueStart++;
    if (hasLongBase64Run(
      text,
      valueStart,
      EMBEDDED_BINARY_MIN_RUN_CHARS,
      valueStart + EMBEDDED_BINARY_SCAN_WINDOW_CHARS
    )) return true;
    dataOffset = valueStart + EMBEDDED_BINARY_SCAN_STEP_CHARS;
  }

  for (const field of TEXT_BINARY_FIELD_NAMES) {
    let offset = 0;
    while (offset < lower.length) {
      const fieldIndex = lower.indexOf(field, offset);
      if (fieldIndex < 0) break;
      const afterField = fieldIndex + field.length;
      const colonLimit = Math.min(
        lower.length,
        afterField + EMBEDDED_BINARY_FIELD_PADDING_CHARS + 1
      );
      const colonRelative = lower.slice(afterField, colonLimit).indexOf(":");
      if (colonRelative < 0) {
        offset = afterField;
        continue;
      }
      const colon = afterField + colonRelative;
      const between = text.slice(afterField, colon);
      if (!/^[ \t]*$/.test(between)) {
        offset = afterField;
        continue;
      }
      let valueStart = colon + 1;
      while (valueStart < text.length && /\s/.test(text[valueStart])) valueStart++;
      if (text[valueStart] === '"' || text[valueStart] === "'") valueStart++;
      if (hasLongBase64Run(
        text,
        valueStart,
        EMBEDDED_BINARY_MIN_RUN_CHARS,
        valueStart + EMBEDDED_BINARY_SCAN_WINDOW_CHARS
      )) return true;
      offset = Math.max(
        afterField,
        valueStart + EMBEDDED_BINARY_SCAN_STEP_CHARS
      );
    }
  }
  return false;
}

function mediaDetails(block) {
  const source = block && typeof block.source === "object" ? block.source : {};
  const mediaType = block?.media_type || source?.media_type || block?.mime_type || source?.mime_type;
  const name = block?.title || block?.name || block?.filename || source?.name || source?.filename;
  const details = [];
  if (mediaType) details.push(String(mediaType).slice(0, 96));
  if (name) details.push(String(name).slice(0, 160));
  return details.length ? `: ${details.join(", ")}` : "";
}

function placeholderFor(block, cfg) {
  const type = typeof block?.type === "string" && block.type ? block.type : "payload";
  if (type === "image") return { type: "text", text: String(cfg.placeholder) };
  return {
    type: "text",
    text: String(cfg.binaryPlaceholder)
      .replaceAll("{type}", type)
      .replaceAll("{details}", mediaDetails(block)),
  };
}

function sanitizeContentItem(item, cfg, mutate) {
  if (typeof item === "string") {
    if (!textContainsLargeBinary(item, cfg.maxTextPayloadBytes)) {
      return { changed: false, value: item };
    }
    return {
      changed: true,
      value: mutate ? { type: "text", text: "[embedded binary payload omitted]" } : item,
    };
  }

  if (Array.isArray(item)) {
    let changed = false;
    const output = [];
    for (const child of item) {
      const result = sanitizeContentItem(child, cfg, mutate);
      changed ||= result.changed;
      if (mutate) output.push(result.value);
    }
    return { changed, value: mutate ? output : item };
  }
  if (!item || typeof item !== "object") return { changed: false, value: item };

  if (item.type === "text") {
    if (!textContainsLargeBinary(item.text, cfg.maxTextPayloadBytes)) {
      return { changed: false, value: item };
    }
    return {
      changed: true,
      value: mutate ? { type: "text", text: "[embedded binary payload omitted]" } : item,
    };
  }

  if (item.type === "tool_reference") return { changed: false, value: item };

  const mustOmit = BINARY_TYPES.has(item.type) || containsLargeBinaryField(item, cfg.maxInlinePayloadBytes);
  if (mustOmit) {
    return { changed: true, value: mutate ? placeholderFor(item, cfg) : item };
  }
  return { changed: false, value: item };
}

function sanitizeToolPayloads(body, cfg, mutate) {
  let changed = false;
  if (!body || !Array.isArray(body.messages)) return changed;
  for (const msg of body.messages) {
    if (!Array.isArray(msg?.content)) continue;
    for (const block of msg.content) {
      if (block?.type !== "tool_result") continue;

      const original = block.content;
      const items = Array.isArray(original) ? original : [original];
      const output = [];
      let blockChanged = false;

      for (const item of items) {
        const result = sanitizeContentItem(item, cfg, mutate);
        blockChanged ||= result.changed;
        if (mutate) output.push(result.value);
      }

      if (original === undefined || original === null || (Array.isArray(original) && original.length === 0)) {
        blockChanged = true;
        if (mutate) {
          output.length = 0;
          output.push({ type: "text", text: "[empty tool result]" });
        }
      }

      if (!blockChanged) continue;
      changed = true;
      if (mutate) block.content = output;
    }
  }
  return changed;
}
function fixPartialToolRounds(body, mutate) {
  if (!body || !Array.isArray(body.messages)) return false;
  let changed = false;
  const messages = body.messages.map((msg, index) => {
    if (msg?.role !== 'assistant' || !Array.isArray(msg.content)) return msg;
    const next = body.messages[index + 1];
    const answered = new Set(
      next?.role === 'user' && Array.isArray(next.content)
        ? next.content.filter((b) => b?.type === 'tool_result' && typeof b.tool_use_id === 'string').map((b) => b.tool_use_id)
        : []
    );
    const filtered = msg.content.filter((b) => b?.type !== 'tool_use' || answered.has(b.id));
    if (filtered.length === msg.content.length) return msg;
    changed = true;
    if (!mutate) return msg;
    return { ...msg, content: filtered.length ? filtered : [{ type: 'text', text: '[pending tool calls omitted]' }] };
  });
  if (changed && mutate) body.messages = messages;
  return changed;
}

export function setup(ctx = {}) {
  const { cfg, transforms } = configuration(ctx.pluginConfig);
  const providers = new Set(cfg.providers);
  const diagnostics = { missingRoutedModel: 0, malformedRoutedModel: 0, providerNotConfigured: 0, warningsEmitted: 0 };
  const warningTimes = new Map();
  function handles(input) {
    const info = routeInfo(input);
    const issue = info.issue || (providers.has(info.provider) ? '' : 'providerNotConfigured');
    if (!issue) return true;
    diagnostics[issue]++;
    const now = Date.now();
    if (!warningTimes.has(issue) || now - warningTimes.get(issue) >= ROUTE_WARNING_INTERVAL_MS) {
      warningTimes.set(issue, now);
      diagnostics.warningsEmitted++;
      // Never include route strings, request bodies or arbitrary provider values.
      const message = `[tool-result-compat] no-op: ${issue}; count=${diagnostics[issue]}`;
      try {
        if (typeof ctx.logger?.warn === 'function') ctx.logger.warn(message);
        else console.warn(message);
      } catch { /* Logging must not break forwarding. */ }
    }
    return false;
  }
  function transform(id, operation) {
    return {
      id,
      async transform(input) {
        if (!transforms.has(id) || !handles(input)) return;
        if (!operation(input?.body, cfg, false)) return;
        const body = JSON.parse(JSON.stringify(input.body));
        operation(body, cfg, true);
        return { body };
      },
    };
  }
  return {
    diagnostics,
    gatewayRequestTransforms: [
      transform('strip-tool-images', sanitizeToolPayloads),
      transform('fix-partial-tool-rounds', (body, _cfg, mutate) => fixPartialToolRounds(body, mutate)),
    ],
  };
}
