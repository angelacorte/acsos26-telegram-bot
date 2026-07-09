package org.angelacorte.acsos26

import io.kotest.core.spec.style.StringSpec
import io.kotest.matchers.shouldBe
import io.kotest.matchers.string.shouldContain

class CommandRouterTest :
    StringSpec({
        val conference = ConferenceRepository.load()
        val router = CommandRouter(conference, fixedLlmClient("LLM answer"))

        "help lists deterministic commands" {
            val answer = router.answer("/help")
            answer shouldContain "/program"
            answer shouldContain "/ask"
        }

        "program does not invent unpublished sessions" {
            val answer = router.answer("/program")
            answer shouldContain "Timed sessions, rooms, and paper-to-session assignments are not available"
        }

        "main track command shows accepted papers" {
            val answer = router.answer("/maintrack")
            answer shouldContain "Main Track"
            answer shouldContain "A Multi-Agent LLM Architecture"
        }

        "ask delegates to the llm client" {
            router.answer("/ask When is the main track?") shouldBe "LLM answer"
        }

        "private mode requires access key before commands work" {
            val privateRouter =
                CommandRouter(
                    conference,
                    fixedLlmClient("LLM answer"),
                    AccessControl("secret"),
                )
            privateRouter.answer("/ask When is the main track?", chatId = 1L) shouldContain "private"
            privateRouter.answer("/start wrong", chatId = 1L) shouldBe "Invalid access key."
            privateRouter.answer("/start secret", chatId = 1L) shouldContain "Access granted"
            privateRouter.answer("/ask When is the main track?", chatId = 1L) shouldBe "LLM answer"
        }

        "commands addressed to another bot are ignored" {
            router.answer("/help@another_bot", "acsos_26_bot") shouldBe null
        }
    })

private fun fixedLlmClient(answer: String): LlmClient =
    object : LlmClient {
        override fun ask(question: String): Result<String> = Result.success(answer)
    }
