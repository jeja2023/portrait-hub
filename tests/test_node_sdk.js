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

async function main() {
  await testBadJsonHttpErrorKeepsStructuredException();
  await testEmptySuccessBodyIsObjectPayload();
  await testPathsAndQueriesAreEncoded();
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
