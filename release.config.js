const exportReleaseCmd = `
echo "published=true" >> "$GITHUB_OUTPUT"
echo "version=\${nextRelease.version}" >> "$GITHUB_OUTPUT"
`;

const config = require("semantic-release-preconfigured-conventional-commits");
config.plugins.push(
    "@semantic-release/github",
    "@semantic-release/git",
    [
        "@semantic-release/exec",
        {
            successCmd: exportReleaseCmd,
        },
    ],
);

module.exports = config;
