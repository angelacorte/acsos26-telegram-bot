package org.angelacorte.acsos26

import com.fasterxml.jackson.databind.ObjectMapper
import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.time.Duration

/**
 * Client for the optional Python conference assistant service.
 */
internal interface LlmClient {
    /**
     * Asks the LLM service a free-form conference question.
     */
    fun ask(question: String): Result<String>

    companion object {
        /**
         * Builds an LLM client from LLM_API_URL, or a disabled client when the URL is absent.
         */
        fun fromEnvironment(): LlmClient =
            System
                .getenv("LLM_API_URL")
                ?.takeIf { it.isNotBlank() }
                ?.let { HttpLlmClient(URI(it), apiKey = System.getenv("LLM_API_KEY")) }
                ?: DisabledLlmClient
    }
}

private object DisabledLlmClient : LlmClient {
    override fun ask(question: String): Result<String> =
        Result.failure(IllegalStateException("Set LLM_API_URL to enable /ask."))
}

internal class HttpLlmClient(
    private val endpoint: URI,
    private val apiKey: String?,
    private val client: HttpClient =
        HttpClient
            .newBuilder()
            .version(HttpClient.Version.HTTP_1_1)
            .connectTimeout(Duration.ofSeconds(5))
            .build(),
    private val mapper: ObjectMapper = ObjectMapper(),
) : LlmClient {
    override fun ask(question: String): Result<String> =
        runCatching {
            val body = mapper.writeValueAsString(mapOf("question" to question))
            val requestBuilder =
                HttpRequest
                    .newBuilder(endpoint)
                    .version(HttpClient.Version.HTTP_1_1)
                    .timeout(Duration.ofSeconds(45))
                    .header("Content-Type", "application/json")
            if (!apiKey.isNullOrBlank()) {
                requestBuilder.header("X-LLM-API-Key", apiKey)
            }
            val request =
                requestBuilder
                    .POST(HttpRequest.BodyPublishers.ofString(body))
                    .build()
            val response = client.send(request, HttpResponse.BodyHandlers.ofString())
            check(response.statusCode() in 200..299) {
                "LLM service returned HTTP ${response.statusCode()}: ${response.body()}"
            }
            val tree = mapper.readTree(response.body())
            tree.path("answer").asText().ifBlank {
                error("LLM service returned an empty answer.")
            }
        }
}
