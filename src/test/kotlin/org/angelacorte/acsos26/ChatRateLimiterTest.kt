package org.angelacorte.acsos26

import io.kotest.core.spec.style.StringSpec
import io.kotest.matchers.shouldBe
import java.time.Duration

class ChatRateLimiterTest :
    StringSpec({
        "requests from the same chat are throttled during the cooldown" {
            var now = 0L
            val limiter = ChatRateLimiter(Duration.ofSeconds(3)) { now }

            limiter.tryAcquire(10L) shouldBe true
            limiter.tryAcquire(10L) shouldBe false
            now = Duration.ofSeconds(3).toNanos()
            limiter.tryAcquire(10L) shouldBe true
        }

        "different chats have independent cooldowns" {
            val limiter = ChatRateLimiter(Duration.ofSeconds(3)) { 0L }

            limiter.tryAcquire(10L) shouldBe true
            limiter.tryAcquire(20L) shouldBe true
        }

        "a zero cooldown allows every request" {
            val limiter = ChatRateLimiter(Duration.ZERO) { 0L }

            limiter.tryAcquire(10L) shouldBe true
            limiter.tryAcquire(10L) shouldBe true
        }
    })
