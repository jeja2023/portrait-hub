(function registerConsoleVisuals(global) {
  const modules = (global.PortraitConsoleModules = global.PortraitConsoleModules || {});

  function isImageData(value) {
    return typeof value === "string" && (value.startsWith("data:image/") || value.startsWith("blob:"));
  }

  function filesSignature(files) {
    return Array.from(files || []).map((file) => `${file.name}:${file.size}:${file.lastModified}`).join("|");
  }

  function imageRecord(src, label, meta) {
    return { src, label: label || "image", meta: meta || "" };
  }

  modules.visuals = { filesSignature, imageRecord, isImageData };
})(window);
