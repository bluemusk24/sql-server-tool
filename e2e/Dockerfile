# Playwright runner for the compose `e2e` service.
#
# The base image ships browsers matching @playwright/test 1.64.0 — keep
# the tag in lockstep with e2e/package.json when bumping either.
FROM mcr.microsoft.com/playwright:v1.64.0-noble

WORKDIR /e2e

# Browsers are already in the base image; skip the duplicate download.
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1

COPY e2e/package.json e2e/package-lock.json ./
RUN npm ci

COPY e2e/playwright.config.ts ./
COPY e2e/tests ./tests

CMD ["npx", "playwright", "test"]
