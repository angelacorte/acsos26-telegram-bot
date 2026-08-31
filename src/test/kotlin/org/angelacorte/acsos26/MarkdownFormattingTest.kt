package org.angelacorte.acsos26

import io.kotest.core.spec.style.StringSpec
import io.kotest.matchers.shouldBe

class MarkdownFormattingTest :
    StringSpec({
        "converts bold and italic markdown to HTML tags" {
            "**bold text** and *italic text*".markdownToTelegramHtml() shouldBe
                "<b>bold text</b> and <i>italic text</i>"
        }

        "converts links and code to HTML tags" {
            "[ACSOS](https://2026.acsos.org) and `code snippet`".markdownToTelegramHtml() shouldBe
                """<a href="https://2026.acsos.org">ACSOS</a> and <code>code snippet</code>"""
        }

        "escapes HTML special characters in regular text" {
            "Room 2.4 & 2.10 <test>".markdownToTelegramHtml() shouldBe "Room 2.4 &amp; 2.10 &lt;test&gt;"
        }

        "converts schematic field markdown cleanly" {
            val input =
                """
                **Title:** Multi-Target Tracking
                • **Track:** Main Track
                • **Room:** Aula Magna "Carmen Tura"
                """.trimIndent()
            val expected =
                """
                <b>Title:</b> Multi-Target Tracking
                • <b>Track:</b> Main Track
                • <b>Room:</b> Aula Magna "Carmen Tura"
                """.trimIndent()
            input.markdownToTelegramHtml() shouldBe expected
        }
    })
