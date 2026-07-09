package org.angelacorte.acsos26

import com.sun.net.httpserver.HttpServer
import io.kotest.core.spec.style.StringSpec
import io.kotest.matchers.shouldBe
import io.kotest.matchers.string.shouldContain
import java.net.InetSocketAddress
import java.net.URI

class LlmClientTest :
    StringSpec({
        "http llm client sends json body, api key, and uses http 1.1" {
            var receivedBody = ""
            var receivedApiKey = ""
            var receivedProtocol = ""
            val server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
            server.createContext("/ask") { exchange ->
                receivedProtocol = exchange.protocol
                receivedApiKey = exchange.requestHeaders.getFirst("X-LLM-API-Key").orEmpty()
                receivedBody = exchange.requestBody.bufferedReader().use { it.readText() }
                val response = """{"answer":"ok","sources":[],"mode":"test"}"""
                exchange.responseHeaders.add("Content-Type", "application/json")
                exchange.sendResponseHeaders(200, response.toByteArray().size.toLong())
                exchange.responseBody.use { it.write(response.toByteArray()) }
            }
            server.start()
            try {
                val endpoint = URI("http://127.0.0.1:${server.address.port}/ask")
                val answer = HttpLlmClient(endpoint, apiKey = "service-key").ask("hello")
                answer.getOrThrow() shouldBe "ok"
                receivedProtocol shouldBe "HTTP/1.1"
                receivedApiKey shouldBe "service-key"
                receivedBody shouldContain """"question":"hello""""
            } finally {
                server.stop(0)
            }
        }
    })
