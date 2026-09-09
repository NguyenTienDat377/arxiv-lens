FROM eclipse-temurin:21-jdk AS builder
WORKDIR /build
# proto/ is a sibling of query-service/ so that build.gradle's `srcDir '../proto'`
# resolves the same way it does in the repository.
COPY proto/ proto/
COPY query-service/gradlew query-service/build.gradle query-service/settings.gradle query-service/
COPY query-service/gradle query-service/gradle
WORKDIR /build/query-service
RUN ./gradlew dependencies --no-daemon      # cacheable dependency layer
COPY query-service/src src
RUN ./gradlew bootJar --no-daemon -x test

FROM eclipse-temurin:21-jre
WORKDIR /app
COPY --from=builder /build/query-service/build/libs/*.jar app.jar

# 10001 rather than 1000: this image is Ubuntu-based and already ships a user
# at 1000, so useradd fails with exit 4. Nothing here is bind-mounted, so the
# id only has to be free, not match a host user.
RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app

EXPOSE 8082
ENTRYPOINT ["java", "-jar", "/app/app.jar"]