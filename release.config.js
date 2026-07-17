var verifyConditionsCmd = `
echo "$DOCKER_PASSWORD" | docker login -u angelacortecchia --password-stdin || exit 1
`
var publishCmd = `
git tag -a -f \${nextRelease.version} \${nextRelease.version} -F CHANGELOG.md || exit 1
git push --force origin \${nextRelease.version} || exit 2
docker build -t angelacorte/acsos26-telegram-bot:\${nextRelease.version} . || exit 3
docker push angelacorte/acsos26-telegram-bot:\${nextRelease.version} || exit 4
docker build -f llm_service/Dockerfile -t angelacorte/acsos26-telegram-bot-llm:\${nextRelease.version} . || exit 5
docker push angelacorte/acsos26-telegram-bot-llm:\${nextRelease.version} || exit 6
`
var config = require('semantic-release-preconfigured-conventional-commits');
config.plugins.push(
    [
        "@semantic-release/exec",
        {
            "verifyConditionsCmd": verifyConditionsCmd,
            "publishCmd": publishCmd,
        }
    ],
    "@semantic-release/github",
    "@semantic-release/git",
)
module.exports = config
