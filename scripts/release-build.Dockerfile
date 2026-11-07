# Reuse the same static-link builder as mautrix/ci/gov2-as-default.yml.
ARG BUILDER_IMAGE=dock.mau.dev/tulir/gomuks-build-docker:linux-amd64
FROM ${BUILDER_IMAGE} AS builder
WORKDIR /src
COPY . .
ARG SOURCE_SHA
ARG RELEASE_VERSION
ENV CI=true CI_COMMIT_SHA=${SOURCE_SHA} CI_COMMIT_TAG=${RELEASE_VERSION} MAU_STATIC_BUILD=true
RUN test "$(git rev-parse HEAD)" = "$SOURCE_SHA" \
    && go test ./... \
    && mkdir /out \
    && ./build.sh -o /out/mautrix-telegram \
    && readelf -l /out/mautrix-telegram > /out/program-headers.txt \
    && ! grep -q INTERP /out/program-headers.txt
RUN /out/mautrix-telegram --version > /out/version.txt \
    && go version > /out/go-version.txt
FROM scratch AS artifacts
COPY --from=builder /out/ /
