"use strict";

const fs = require("node:fs");
const path = require("node:path");

const entry = require.resolve("@redocly/openapi-core");
const target = path.join(path.dirname(entry), "js-yaml", "index.js");
const source = fs.readFileSync(target, "utf8");

const legacyBlock =
  "js_yaml_1.JSON_SCHEMA.extend({\n    implicit: [js_yaml_1.types.merge],\n    explicit: [js_yaml_1.types.binary, js_yaml_1.types.omap, js_yaml_1.types.pairs, js_yaml_1.types.set],\n})";
const replacement = "js_yaml_1.JSON_SCHEMA.withTags";

if (source.includes(legacyBlock)) {
  const patched = source.replace(
    legacyBlock,
    "js_yaml_1.JSON_SCHEMA.withTags([\n    js_yaml_1.mergeTag,\n    js_yaml_1.binaryTag,\n    js_yaml_1.omapTag,\n    js_yaml_1.pairsTag,\n    js_yaml_1.setTag,\n])",
  );
  if (patched === source) {
    throw new Error(`Failed to patch Redocly js-yaml adapter: ${target}`);
  }
  fs.writeFileSync(target, patched);
} else if (!source.includes(replacement)) {
  throw new Error(`Unexpected Redocly js-yaml adapter format: ${target}`);
}
