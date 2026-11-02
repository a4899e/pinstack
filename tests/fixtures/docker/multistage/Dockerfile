FROM golang@sha256:a1b2c3d4a1b2c3d4a1b2c3d4a1b2c3d4a1b2c3d4a1b2c3d4a1b2c3d4a1b2c3d4 AS builder
RUN go build -o app .

FROM builder AS tester
RUN go test ./...

FROM alpine@sha256:a1b2c3d4a1b2c3d4a1b2c3d4a1b2c3d4a1b2c3d4a1b2c3d4a1b2c3d4a1b2c3d4 AS runner
COPY --from=builder /app /app
COPY --from=0 /etc/ssl/certs /etc/ssl/certs
RUN --mount=type=bind,from=tester,target=/t ls /t
CMD ["/app"]
