package com.portraithub.sdk;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class PortraitHubClientTest {

    private HttpServer server;
    private final AtomicReference<Map<String, String>> lastHeaders = new AtomicReference<>();
    private final AtomicReference<String> lastPath = new AtomicReference<>();

    @BeforeEach
    void startServer() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", exchange -> {
            Map<String, String> headers = new LinkedHashMap<>();
            exchange.getRequestHeaders().forEach((key, values) -> headers.put(key, String.join(",", values)));
            lastHeaders.set(headers);
            lastPath.set(exchange.getRequestURI().getRawPath()
                    + (exchange.getRequestURI().getRawQuery() == null ? "" : "?" + exchange.getRequestURI().getRawQuery()));
            byte[] body = "{\"status\":\"ok\"}".getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.sendResponseHeaders(200, body.length);
            try (OutputStream out = exchange.getResponseBody()) {
                out.write(body);
            }
        });
        server.createContext("/error", exchange -> {
            byte[] body = "{\"error\":{\"code\":\"rate_limited\"}}".getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.sendResponseHeaders(429, body.length);
            try (OutputStream out = exchange.getResponseBody()) {
                out.write(body);
            }
        });
        server.start();
    }

    @AfterEach
    void stopServer() {
        server.stop(0);
    }

    private String baseUrl() {
        return "http://127.0.0.1:" + server.getAddress().getPort();
    }

    @Test
    void headersSupportBearerAndApplicationApiKey() {
        PortraitHubClient bearer = PortraitHubClient.builder(baseUrl())
                .apiToken("token").tenantId("tenant-a").build();
        bearer.health();
        assertEquals("Bearer token", lastHeaders.get().get("Authorization"));
        assertEquals("tenant-a", lastHeaders.get().get("X-tenant-id"));
        assertEquals("portrait-hub-sdk-java/" + PortraitHubClient.SDK_VERSION,
                lastHeaders.get().get("User-agent"));

        PortraitHubClient apiKey = PortraitHubClient.builder(baseUrl())
                .apiToken("phk_secret").authScheme("api-key").build();
        apiKey.health();
        assertEquals("phk_secret", lastHeaders.get().get("X-api-key"));
        assertTrue(lastHeaders.get().get("Authorization") == null);

        assertThrows(IllegalArgumentException.class,
                () -> PortraitHubClient.builder(baseUrl()).authScheme("basic").build());
    }

    @Test
    void httpErrorKeepsStructuredPayload() {
        PortraitHubClient client = PortraitHubClient.builder(baseUrl() + "/error").build();
        PortraitHubClient.PortraitHubHttpException error =
                assertThrows(PortraitHubClient.PortraitHubHttpException.class, client::health);
        assertEquals(429, error.statusCode());
        assertTrue(error.payload().contains("rate_limited"));
    }

    @Test
    void pathSegmentEncodingProtectsReservedCharacters() {
        PortraitHubClient client = PortraitHubClient.builder(baseUrl()).build();
        client.getModel("portrait/arcface r100");
        assertEquals("/v1/models/portrait%2Farcface%20r100", lastPath.get());
    }

    @Test
    void queryParametersSkipNullsAndFormatBooleans() {
        PortraitHubClient client = PortraitHubClient.builder(baseUrl()).build();
        client.listStreams(10, null, null);
        assertEquals("/v1/streams?limit=10", lastPath.get());
        client.reindexGallery(null, null, true);
        assertEquals("/v1/gallery/reindex?dry_run=true", lastPath.get());
    }

    @Test
    void jsonSerializationEscapesStrings() {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("name", "quote\"backslash\\newline\n");
        payload.put("value", 0.75);
        payload.put("flag", true);
        assertEquals("{\"name\":\"quote\\\"backslash\\\\newline\\n\",\"value\":0.75,\"flag\":true}",
                PortraitHubClient.toJson(payload));
    }
}
