# Java MQTT5 publisher/subscriber (repo root context):
#   docker build -f deploy/aws/Dockerfile.java -t mqtt5sr-java:latest .

FROM maven:3.9.9-eclipse-temurin-11 AS build
WORKDIR /app
COPY pom.xml .
COPY src ./src
RUN mvn -q -DskipTests package dependency:copy-dependencies -DincludeScope=runtime

FROM eclipse-temurin:11-jre-jammy
WORKDIR /app
ARG JAR_FILE=mqtt5-examples-1.0.0.jar
COPY --from=build /app/target/${JAR_FILE} /app/app.jar
COPY --from=build /app/target/dependency /app/dependency
ENV JAVA_MAIN_CLASS=MQTT5Publisher
ENTRYPOINT ["sh", "-c", "exec java --add-opens=java.base/java.lang=ALL-UNNAMED --add-opens=java.base/java.util=ALL-UNNAMED -cp /app/app.jar:/app/dependency/* ${JAVA_MAIN_CLASS}"]
