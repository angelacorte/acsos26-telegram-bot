package org.angelacorte.acsos26

import io.kotest.assertions.withClue
import io.kotest.core.spec.style.StringSpec
import io.kotest.matchers.ints.shouldBeLessThan
import io.kotest.matchers.shouldBe
import io.kotest.matchers.string.shouldContain
import io.kotest.matchers.string.shouldNotContain
import java.time.LocalDate

class CommandRouterTest :
    StringSpec({
        val conference = ConferenceRepository.load()
        val router = CommandRouter(conference, fixedLlmClient("LLM answer"))

        fun todayRouter(date: LocalDate) = CommandRouter(conference, fixedLlmClient("LLM answer"), today = { date })

        "help lists deterministic commands" {
            val answer = router.answer("/help")
            answer shouldContain "/program"
            answer shouldContain "/ask"
            answer shouldContain "/site"
            answer shouldContain "/links"
            answer shouldContain "/group"
        }

        "link commands return their configured destinations" {
            val routerWithGroup =
                CommandRouter(
                    conference,
                    fixedLlmClient("LLM answer"),
                    groupInviteUrl = "https://telegram.me/+example",
                )

            routerWithGroup.answer("/site") shouldBe conference.website
            routerWithGroup.answer("/links") shouldBe "https://linktr.ee/acsosconf"
            routerWithGroup.answer("/group") shouldBe "https://telegram.me/+example"
        }

        "group command reports missing configuration" {
            router.answer("/group") shouldContain "not configured"
        }

        "program shows today when the conference is running" {
            val answer = todayRouter(LocalDate.of(2026, 9, 9)).answer("/program")
            answer shouldContain "Wednesday, 9 September"
            answer shouldContain "/program all"
            answer shouldContain "/ask"
            // Coffee breaks and lunches are noise in a summary view.
            answer!!.shouldNotContain("Coffee break")
        }

        "program accepts a named day" {
            val answer = todayRouter(LocalDate.of(2026, 9, 9)).answer("/program fri")
            answer shouldContain "Friday, 11 September"
        }

        "program all lists every day" {
            val answer = router.answer("/program all")
            listOf("Monday, 7", "Tuesday, 8", "Wednesday, 9", "Thursday, 10", "Friday, 11").forEach {
                answer shouldContain it
            }
        }

        "program falls back to the week outside the conference dates" {
            val answer = todayRouter(LocalDate.of(2026, 1, 1)).answer("/program")
            answer shouldContain "Monday, 7 September"
            answer shouldContain "Friday, 11 September"
        }

        "sessions is an alias of program" {
            val date = LocalDate.of(2026, 9, 9)
            todayRouter(date).answer("/sessions") shouldBe todayRouter(date).answer("/program")
        }

        "every command reply fits one Telegram message" {
            val commands =
                listOf(
                    "/help",
                    "/about",
                    "/tracks",
                    "/program",
                    "/program all",
                    "/sessions",
                    "/maintrack",
                    "/artifacts",
                    "/doctoral",
                    "/posters",
                    "/tutorials",
                    "/workshops",
                    "/inpractice",
                    "/socialprogram",
                    "/venue",
                    "/registration",
                    "/social",
                    "/site",
                    "/links",
                )
            commands.forEach { command ->
                val answer = router.answer(command).orEmpty()
                withClue("$command rendered ${answer.length} chars") {
                    answer.length shouldBeLessThan TELEGRAM_MESSAGE_LIMIT
                }
            }
        }

        "no command claims the program is unpublished" {
            val commands =
                listOf(
                    "/program",
                    "/program all",
                    "/sessions",
                    "/maintrack",
                    "/workshops",
                    "/doctoral",
                    "/tutorials",
                    "/artifacts",
                    "/posters",
                    "/inpractice",
                    "/socialprogram",
                    "/tracks",
                    "/about",
                    "/social",
                )
            commands.forEach { command ->
                val answer = router.answer(command).orEmpty().lowercase()
                withClue(command) {
                    listOf("tentative", "not available yet", "not published", "subject to change")
                        .forEach { answer.shouldNotContain(it) }
                }
            }
        }

        "main track command summarises the track and points at /ask" {
            val answer = router.answer("/maintrack")
            answer shouldContain "Main Track"
            answer shouldContain "Sessions:"
            answer shouldContain "accepted contributions"
            // Paper titles are /ask territory now, so the reply stays inside one message.
            answer shouldContain "/ask"
        }

        "doctoral command shows sessions and accepted papers" {
            val answer = router.answer("/doctoral")
            answer shouldContain "Doctoral Symposium"
            answer shouldContain "Sessions:"
            answer shouldContain "Doctoral Symposium breakouts"
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

        "group reply to the bot delegates to the llm client without a mention" {
            router.answer(
                message = "When is the main track?",
                chatType = "supergroup",
                addressed = true,
            ) shouldBe "LLM answer"
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
