import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import FrameGrid from "../src/components/FrameGrid.vue";

const apiRequest = vi.fn();

vi.mock("../src/api/client", () => ({
  apiRequest: (...args: unknown[]) => apiRequest(...args),
}));

vi.mock("@lucide/vue", () => ({
  ImageOff: { template: "<span />" },
}));

describe("FrameGrid", () => {
  beforeEach(() => apiRequest.mockReset());

  it("loads persisted frame previews from an analysis archive", async () => {
    apiRequest.mockResolvedValue({
      result: {
        payload: {
          frames: [
            {
              frame_index: 0,
              source_seconds: 0,
              person_count: 1,
              thumbnail: "data:image/jpeg;base64,preview",
            },
          ],
        },
      },
    });

    const wrapper = mount(FrameGrid, {
      props: {
        data: { frames: [{ frame_index: 0, source_seconds: 0, person_count: 1 }] },
        archiveId: "archive_video_job_123",
      },
    });
    await flushPromises();

    expect(apiRequest).toHaveBeenCalledWith(
      "/v1/analysis/results/archive_video_job_123",
    );
    expect(wrapper.get("img").attributes("src")).toBe("data:image/jpeg;base64,preview");
    expect(wrapper.text()).toContain("帧 0");
    expect(wrapper.text()).toContain("人员 1");
  });

  it("keeps the original result when the archive has no visible preview", async () => {
    apiRequest.mockResolvedValue({
      result: {
        payload: {
          frames: [{ frame_index: 0, source_seconds: 0, person_count: 0 }],
        },
      },
    });

    const wrapper = mount(FrameGrid, {
      props: {
        data: {
          frames: [
            {
              frame_index: 3,
              thumbnail: "data:image/jpeg;base64,in-memory",
            },
          ],
        },
        archiveId: "archive_stream_123",
      },
    });
    await flushPromises();

    expect(wrapper.get("img").attributes("src")).toBe("data:image/jpeg;base64,in-memory");
  });
});
