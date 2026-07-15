FROM eclipse-temurin:21.0.5_11-jdk-alpine AS builder
COPY . /app
WORKDIR /app
# Image builds intentionally exclude .git, so disable build-time features that require it.
RUN sed -i 's/alias(libs.plugins.gitSemVer)//' build.gradle.kts && \
    sed -i '/createHooks()/d' settings.gradle.kts
RUN ./gradlew shadowJar

FROM eclipse-temurin:21.0.5_11-jre-alpine AS app
RUN addgroup -S -g 10001 bot && adduser -S -D -H -u 10001 -G bot bot
COPY --from=builder --chown=10001:10001 /app/build/libs/*-all.jar /app/app.jar
USER 10001:10001
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
