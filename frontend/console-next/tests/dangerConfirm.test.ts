/* eslint-disable vue/one-component-per-file */
import { defineComponent } from "vue";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import { apiRequest } from "../src/api/client";
import DangerConfirm from "../src/components/DangerConfirm.vue";

vi.mock("../src/api/client", () => ({ apiRequest: vi.fn() }));

const DialogStub = defineComponent({
  template: "<section><slot name='header' /><slot /><slot name='footer' /></section>",
});
const InputStub = defineComponent({
  props: { modelValue: { type: String, default: "" } },
  emits: ["update:modelValue"],
  template: "<input :value='modelValue' @input=\"$emit('update:modelValue', $event.target.value)\" />",
});
const ButtonStub = defineComponent({
  props: { disabled: Boolean, loading: Boolean },
  emits: ["click"],
  template: "<button :disabled='disabled' @click=\"$emit('click')\"><slot /></button>",
});
const AlertStub = defineComponent({
  props: { title: { type: String, default: "" } },
  template: "<div>{{ title }}</div>",
});

describe("DangerConfirm", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("reauthenticates an expired local session before confirming", async () => {
    const request = vi.mocked(apiRequest);
    request
      .mockResolvedValueOnce({
        authenticated: true,
        auth_kind: "local",
        recent: false,
        seconds_remaining: 0,
        max_age_seconds: 300,
      })
      .mockResolvedValueOnce({ authenticated: true })
      .mockResolvedValueOnce({
        authenticated: true,
        auth_kind: "local",
        recent: true,
        seconds_remaining: 300,
        max_age_seconds: 300,
      });
    const wrapper = mount(DangerConfirm, {
      props: {
        modelValue: true,
        title: "发布模型",
        description: "将模型发布到生产环境",
        confirmationText: "确认发布",
        highRisk: true,
      },
      global: {
        stubs: {
          ElDialog: DialogStub,
          ElInput: InputStub,
          ElButton: ButtonStub,
          ElAlert: AlertStub,
        },
      },
    });
    await flushPromises();

    const inputs = wrapper.findAll("input");
    expect(inputs).toHaveLength(2);
    await inputs.at(0)!.setValue("确认发布");
    await inputs.at(1)!.setValue("correct-password");
    await wrapper.findAll("button").at(-1)!.trigger("click");
    await flushPromises();

    expect(request).toHaveBeenNthCalledWith(1, "/v1/auth/step-up/status");
    expect(request).toHaveBeenNthCalledWith(2, "/v1/auth/local/step-up", {
      method: "POST",
      body: JSON.stringify({ password: "correct-password" }),
    });
    expect(request).toHaveBeenNthCalledWith(3, "/v1/auth/step-up/status");
    expect(wrapper.emitted("confirm")).toHaveLength(1);
  });
});
