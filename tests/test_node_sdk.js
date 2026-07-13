const assert = require("assert");

const { PortraitHubClient, PortraitHubHTTPError } = require("../sdk/node/portraitHubClient");

function response({ ok, status, body, contentType = "application/json" }) {
  return {
    ok,
    status,
    headers: {
      get(name) {
        return name.toLowerCase() === "content-type" ? contentType : "";
      },
    },
    async text() {
      return body;
    },
  };
}

async function testHeadersSupportBearerAndApplicationApiKey() {
  const bearer = new PortraitHubClient({ baseUrl: "http://testserver", apiToken: "token", tenantId: "tenant-a" });
  assert.deepStrictEqual(bearer.headers(), {
    "X-Tenant-ID": "tenant-a",
    Authorization: "Bearer token",
  });

  const apiKey = new PortraitHubClient({
    baseUrl: "http://testserver",
    apiToken: "phk_secret",
    tenantId: "tenant-a",
    authScheme: "api_key",
  });
  assert.deepStrictEqual(apiKey.headers(), {
    "X-Tenant-ID": "tenant-a",
    "X-API-Key": "phk_secret",
  });

  assert.throws(
    () => new PortraitHubClient({ baseUrl: "http://testserver", authScheme: "basic" }),
    /authScheme/,
  );
}

async function testBadJsonHttpErrorKeepsStructuredException() {
  const client = new PortraitHubClient({ baseUrl: "http://testserver" });

  try {
    await client.parseResponse(response({ ok: false, status: 502, body: "{bad-json" }));
    assert.fail("expected PortraitHubHTTPError");
  } catch (error) {
    assert(error instanceof PortraitHubHTTPError);
    assert.strictEqual(error.status, 502);
    assert.strictEqual(error.payload, "{bad-json");
  }
}

async function testEmptySuccessBodyIsObjectPayload() {
  const client = new PortraitHubClient({ baseUrl: "http://testserver" });

  const payload = await client.parseResponse(response({ ok: true, status: 204, body: "" }));

  assert.deepStrictEqual(payload, {});
}

async function testPathsAndQueriesAreEncoded() {
  const client = new PortraitHubClient({ baseUrl: "http://testserver" });

  assert.strictEqual(
    client.pathWithQuery("/v1/streams", { limit: 2, cursor: "abc/123", offset: null }),
    "/v1/streams?limit=2&cursor=abc%2F123",
  );
  assert.strictEqual(
    `/v1/models/${client.pathSegment("portrait_hub/yolov8n.onnx")}/load`,
    "/v1/models/portrait_hub%2Fyolov8n.onnx/load",
  );
}

async function testBatchMethodsSendExpectedMultipartFields() {
  const client = new PortraitHubClient({ baseUrl: "http://testserver", apiToken: "token", tenantId: "tenant-a" });
  const calls = [];
  client.multipart = async (path, fields, files) => {
    calls.push({ path, fields, files });
    return { status: "ok" };
  };

  assert.deepStrictEqual(await client.search("query.jpg", "face", 3, "strict"), { status: "ok" });
  assert.deepStrictEqual(calls.at(-1), {
    path: "/v1/gallery/search",
    fields: { modality: "face", top_k: 3, threshold_profile: "strict" },
    files: [["file", "query.jpg"]],
  });

  assert.deepStrictEqual(
    await client.searchBatch(["query-a.jpg", "query-b.jpg"], {
      modality: "body",
      topK: 10,
      thresholdProfile: "normal",
      asyncMode: true,
    }),
    { status: "ok" },
  );
  assert.deepStrictEqual(calls.at(-1), {
    path: "/v1/gallery/search/batch",
    fields: { modality: "body", top_k: 10, threshold_profile: "normal", async_mode: true },
    files: [["files", "query-a.jpg"], ["files", "query-b.jpg"]],
  });

  assert.deepStrictEqual(
    await client.compareBatch(["a1.jpg", "a2.jpg"], ["b1.jpg", "b2.jpg"], {
      modality: "appearance",
      thresholdProfile: "loose",
      includeVectors: true,
      asyncMode: true,
    }),
    { status: "ok" },
  );
  assert.deepStrictEqual(calls.at(-1), {
    path: "/v1/compare/batch",
    fields: {
      modality: "appearance",
      threshold_profile: "loose",
      include_vectors: true,
      async_mode: true,
    },
    files: [
      ["image_a", "a1.jpg"],
      ["image_a", "a2.jpg"],
      ["image_b", "b1.jpg"],
      ["image_b", "b2.jpg"],
    ],
  });
}
async function main() {
  await testHeadersSupportBearerAndApplicationApiKey();
  await testBadJsonHttpErrorKeepsStructuredException();
  await testEmptySuccessBodyIsObjectPayload();
  await testPathsAndQueriesAreEncoded();
  await testBatchMethodsSendExpectedMultipartFields();
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
