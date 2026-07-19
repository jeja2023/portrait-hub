import { describe, expect, it } from "vitest";

import {
  actionLabel,
  artifactLabel,
  auditCategoryLabel,
  backendAreaLabel,
  backendLabel,
  capabilityLabel,
  confidenceLabel,
  comparisonReasonLabel,
  datasetNameLabel,
  datasetPurposeLabel,
  eventLabel,
  modalityLabel,
  outcomeLabel,
  recommendationReasonLabel,
  reviewLabel,
  riskLabel,
  statusLabel,
  thresholdProfileLabel,
} from "../src/utils/format";

describe("中文界面显示映射", () => {
  it("翻译状态、风险、模态和阈值方案", () => {
    expect(statusLabel("running")).toBe("处理中");
    expect(riskLabel("low_quality")).toBe("低质量");
    expect(modalityLabel("appearance")).toBe("衣着");
    expect(thresholdProfileLabel("normal")).toBe("标准");
  });

  it("翻译评估、审计和运行后端字段", () => {
    expect(reviewLabel("false_positive")).toBe("误检");
    expect(actionLabel("raise_threshold")).toBe("提高阈值");
    expect(confidenceLabel("medium")).toBe("中");
    expect(datasetNameLabel("review_all_annotations")).toBe("全部复核标注");
    expect(datasetPurposeLabel("regression_holdout")).toBe("回归验证保留集");
    expect(auditCategoryLabel("model_versions")).toBe("模型版本");
    expect(outcomeLabel("success")).toBe("成功");
    expect(backendAreaLabel("object_storage")).toBe("对象存储");
    expect(backendLabel("local")).toBe("本地");
  });

  it("翻译能力、事件和英文推荐原因", () => {
    expect(capabilityLabel("person_detection")).toBe("人体检测");
    expect(eventLabel("stream_analysis_completed")).toBe("流分析已完成");
    expect(
      recommendationReasonLabel("confirmed samples dominate current review pool"),
    ).toBe("已确认样本在当前复核池中占主导");
  });

  it("空值使用中文兜底", () => {
    expect(statusLabel(undefined)).toBe("未知状态");
    expect(riskLabel(null)).toBe("未知风险");
    expect(modalityLabel("")).toBe("未知模态");
  });

  it("未知业务枚举不会直接显示英文值", () => {
    expect(statusLabel("unexpected_state")).toBe("未知状态");
    expect(modalityLabel("unexpected_modality")).toBe("未知模态");
    expect(eventLabel("unexpected_event")).toBe("其他事件");
    expect(comparisonReasonLabel("unexpected_reason")).toBe("其他原因");
    expect(backendLabel("Milvus")).toBe("Milvus");
    expect(artifactLabel("1. image-1")).toBe("第 1 张图片");
    expect(artifactLabel("preview-item", 2)).toBe("预览 3");
  });
});