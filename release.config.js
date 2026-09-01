const dockerHubNamespace = "angelacortecchia";
const publishCmd = `
set -eu

publish_image() {
    image="$1"
    dockerfile="$2"
    docker build \
        --file "$dockerfile" \
        --tag "$image:\${nextRelease.version}" \
        --tag "$image:latest" \
        .
    docker push "$image:\${nextRelease.version}"
    docker push "$image:latest"
}

publish_image "${dockerHubNamespace}/acsos26-telegram-bot" Dockerfile
publish_image "${dockerHubNamespace}/acsos26-telegram-bot-llm" llm_service/Dockerfile
`;

const config = require("semantic-release-preconfigured-conventional-commits");
config.plugins.push(
    [
        "@semantic-release/exec",
        {
            publishCmd,
        },
    ],
    "@semantic-release/github",
    "@semantic-release/git",
);

module.exports = config;
