package org.angelacorte.acsos26

import io.kotest.core.spec.style.StringSpec
import io.kotest.matchers.shouldBe

class BotStartupTest :
    StringSpec({
        "messages received after startup are processed" {
            val gate = StartupMessageGate(startupEpochSeconds = 100L)

            gate.actionFor(chatId = 1L, messageEpochSeconds = 101L) shouldBe StartupMessageAction.PROCESS
        }

        "the first queued message greets its chat and is skipped" {
            val gate = StartupMessageGate(startupEpochSeconds = 100L)

            gate.actionFor(chatId = 1L, messageEpochSeconds = 99L) shouldBe StartupMessageAction.GREET_AND_SKIP
            gate.actionFor(chatId = 1L, messageEpochSeconds = 98L) shouldBe StartupMessageAction.SKIP
        }

        "messages timestamped in the startup second are skipped" {
            val gate = StartupMessageGate(startupEpochSeconds = 100L)

            gate.actionFor(chatId = 1L, messageEpochSeconds = 100L) shouldBe StartupMessageAction.GREET_AND_SKIP
        }

        "each chat with queued messages receives one greeting" {
            val gate = StartupMessageGate(startupEpochSeconds = 100L)

            gate.actionFor(chatId = 1L, messageEpochSeconds = 90L) shouldBe StartupMessageAction.GREET_AND_SKIP
            gate.actionFor(chatId = 2L, messageEpochSeconds = 90L) shouldBe StartupMessageAction.GREET_AND_SKIP
            gate.actionFor(chatId = 1L, messageEpochSeconds = 91L) shouldBe StartupMessageAction.SKIP
            gate.actionFor(chatId = 2L, messageEpochSeconds = 91L) shouldBe StartupMessageAction.SKIP
        }
    })
