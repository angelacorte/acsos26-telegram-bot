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

        "program resolves track names tolerantly" {
            router.answer("/program poster") shouldContain "Posters and Demos"
            router.answer("/program demos") shouldContain "Posters and Demos"
            router.answer("/program phd") shouldContain "Doctoral Symposium"
            router.answer("/program workshop") shouldContain "Workshops"
            router.answer("/program artifact") shouldContain "Artifacts"
            router.answer("/program tutorial") shouldContain "Tutorials"
        }

        "ask delegates to the llm client" {
            router.answer("/ask When is the main track?") shouldBe "LLM answer"
        }

        "free-form messages delegate to the llm client" {
            router.answer("When is the main track?") shouldBe "LLM answer"
        }

        "group free-form messages without mention are ignored" {
            router.answer("When is the main track?", chatType = "group") shouldBe null
        }

        "group ask command delegates to the llm client" {
            router.answer("/ask When is the main track?", chatType = "group") shouldBe "LLM answer"
        }

        "mentions without slash delegate to the llm client" {
            router.answer(
                message = "@acsos_26_bot When is the main track?",
                botUsername = "acsos_26_bot",
            ) shouldBe "LLM answer"
        }

        "group mentions without slash delegate to the llm client without the mention" {
            val echoRouter = CommandRouter(conference, echoLlmClient())

            echoRouter.answer(
                message = "@acsos_26_bot When is the main track?",
                botUsername = "acsos_26_bot",
                chatType = "supergroup",
            ) shouldBe "When is the main track?"
        }

        "private mode requires access key before commands work" {
            val privateRouter =
                CommandRouter(
                    conference,
                    fixedLlmClient("LLM answer"),
                    AccessControl("secret"),
                )
            privateRouter.answer("/ask When is the main track?", chatId = 1L) shouldContain "private"
            privateRouter.answer("When is the main track?", chatId = 1L) shouldContain "private"
            privateRouter.answer("When is the main track?", chatId = 1L, chatType = "group") shouldBe null
            privateRouter.answer(
                message = "@acsos_26_bot When is the main track?",
                botUsername = "acsos_26_bot",
                chatId = 1L,
                chatType = "group",
            ) shouldContain "private"
            privateRouter.answer("/start wrong", chatId = 1L) shouldBe "Invalid access key."
            privateRouter.answer("/start secret", chatId = 1L) shouldContain "Access granted"
            privateRouter.answer("/ask When is the main track?", chatId = 1L) shouldBe "LLM answer"
            privateRouter.answer("When is the main track?", chatId = 1L) shouldBe "LLM answer"
        }

        "commands addressed to another bot are ignored" {
            router.answer("/help@another_bot", "acsos_26_bot") shouldBe null
        }
    })

private fun fixedLlmClient(answer: String): LlmClient =
    object : LlmClient {
        override fun ask(question: String): Result<String> = Result.success(answer)
    }

private fun echoLlmClient(): LlmClient =
    object : LlmClient {
        override fun ask(question: String): Result<String> = Result.success(question)
    }
