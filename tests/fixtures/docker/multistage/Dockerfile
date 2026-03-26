FROM golang@sha256:a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2 AS builder
RUN go build -o app .

FROM alpine AS runner
COPY --from=builder /app /app
CMD ["/app"]
