package org.angelacorte.acsos26

import io.kotest.core.spec.style.StringSpec
import io.kotest.matchers.nulls.shouldBeNull
import io.kotest.matchers.shouldBe

class GroupReplyTest :
    StringSpec({
        "group answers reply to the originating message" {
            val reply = groupReplyParameters("group", 42L)

            reply?.messageId shouldBe 42L
            reply?.allowSendingWithoutReply shouldBe true
        }

        "supergroup answers reply to the originating message" {
            groupReplyParameters("supergroup", 73L)?.messageId shouldBe 73L
        }

        "private answers are sent without reply metadata" {
            groupReplyParameters("private", 42L).shouldBeNull()
        }
    })
