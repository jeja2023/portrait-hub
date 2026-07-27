import { readdirSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";

import { describe, expect, it } from "vitest";

const sourceRoot = resolve(import.meta.dirname, "../src");

function vueFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    return entry.isDirectory() ? vueFiles(path) : entry.name.endsWith(".vue") ? [path] : [];
  });
}

describe("data table sequence columns", () => {
  it("adds a sequence column to every data table", () => {
    const nativeTables: string[] = [];
    const elementTables: string[] = [];

    for (const file of vueFiles(sourceRoot)) {
      const content = readFileSync(file, "utf8");
      nativeTables.push(...[...content.matchAll(/<table class="data-table">[\s\S]*?<\/table>/g)].map((match) => match[0]));
      elementTables.push(...[...content.matchAll(/<ElTable\b[\s\S]*?<\/ElTable>/g)].map((match) => match[0]));
    }

    expect(nativeTables.length).toBeGreaterThan(0);
    expect(elementTables.length).toBeGreaterThan(0);

    for (const table of nativeTables) {
      expect(table).toContain('<th class="sequence-column">序号</th>');
      expect(table).toContain('<td class="sequence-column">');
    }

    for (const table of elementTables) {
      expect(table).toContain('<ElTableColumn label="序号"');
    }
  });
  it("keeps the configuration table sorted and paginated", () => {
    const content = readFileSync(resolve(sourceRoot, "views/admin/ConfigurationView.vue"), "utf8");

    expect(content).toContain('<ElTable border :data="configurationPager.items"');
    expect(content).toContain('<div class="table-wrap configuration-table">');
    expect(content).toContain("const categoryOrder");
    expect(content).toContain('left.category.localeCompare(right.category, "zh-CN")');
    expect(content).toContain("const configurationPager = useTablePagination(sortedItems, 20)");
    expect(content).toContain("<DataTablePagination");
    expect(content).toContain("configurationPager.startIndex + $index + 1");
    expect(content).not.toContain("configuration-mobile-list");
    expect(content).not.toContain('fixed="left"');
    expect(content).not.toContain('fixed="right"');
  });

  it("uses shared table and pagination implementations across every audited view", () => {
    const viewRoot = resolve(sourceRoot, "views");
    const viewSources = vueFiles(viewRoot).map((file) => readFileSync(file, "utf8"));
    const nativeTableTags = viewSources.flatMap((content) => [...content.matchAll(/<table\b[^>]*>/g)]);

    expect(nativeTableTags.length).toBeGreaterThan(0);
    for (const [tag] of nativeTableTags) expect(tag).toMatch(/class="[^"]*\bdata-table\b[^"]*"/);
    for (const content of viewSources) expect(content).not.toContain("<ElPagination");

    const serviceQuality = readFileSync(resolve(viewRoot, "operations/ServiceQualityView.vue"), "utf8");
    expect(serviceQuality).toContain("const slaDefinitionsPager = useTablePagination(slaDefinitions)");
    expect(serviceQuality).toContain('v-model:page="slaDefinitionsPager.page"');
    expect(serviceQuality).toContain("slaDefinitionsPager.startIndex + index + 1");

    const modelRegistry = readFileSync(resolve(viewRoot, "admin/ModelRegistryView.vue"), "utf8");
    expect(modelRegistry).toContain("const shadowPager = useTablePagination(shadowResults)");
    expect(modelRegistry).toContain('v-model:page="shadowPager.page"');
    expect(modelRegistry).toContain("shadowPager.startIndex + index + 1");

    for (const path of ["analysis/ImageAnalysisView.vue", "analysis/AnalysisResultsView.vue"]) {
      const content = readFileSync(resolve(viewRoot, path), "utf8");
      expect(content).toContain("<DataTablePagination");
      expect(content).toContain(":has-more=");
      expect(content).toContain('@load-more="');
      expect(content).not.toContain('class="load-more"');
    }
  });

});
